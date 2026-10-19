#Django Framework imports
from django.http import JsonResponse
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db.models import Count, Min, Max

#Rest Framework imports
from rest_framework import generics, views
from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination

#Table provider imports
from table_provider.configuration_provider.configuration_factory import ColumnConfigurationFactory, DataTablesConfigurationFactory
from table_provider.models import GpcrStructureStatisticsTable, PdbTable
from table_provider.serializers import GpcrStatisticsSummarySerializer, PdbStructureSummarySerializer
from table_provider.serverside.filters import FilterSet, FilterError, TextFilter, BooleanFilter
from table_provider.serverside.querystringprocessing import DatatablesQueryStringProcessor, QueryStringProcessingError

#Pagination handler for dataTables server side processing
class DataTablesLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 30
    max_limit = 200
    limit_query_param = "length"
    offset_query_param = "start"

    def get_limit(self, request):
        # DataTables sends length=-1 for "All": return every row (no pagination) rather than silently falling back to the default
        if request.query_params.get(self.limit_query_param) == "-1":
            return None
        return super().get_limit(request)


def apply_filterset(queryset, filterset):
    """Applies every filter and the ordering of a FilterSet to a queryset."""
    for query_filter in filterset.get_filters():
        queryset = queryset.filter(query_filter.format_query())
    if filterset.get_ordering():
        queryset = queryset.order_by(*filterset.get_ordering())
    return queryset


def _parse_draw(url_params):
    """DataTables expects the draw counter echoed back as an integer; reject non-integers as a bad request."""
    try:
        return int(url_params["draw"])
    except (TypeError, ValueError):
        raise ParseError("Invalid draw parameter: expected an integer")

class DataTablesView(generics.ListAPIView):
    """Generic DataTables / TableManager data source for a flat table model.

    Subclasses set `model` and `serializer_class` (a DataTablesSerializerMixin serializer), and may set `checkbox_fields`
    and override `scope_queryset`. The same class serves every endpoint of a table; `as_view(action=...)` selects which one
    (see `datatable_urls` in urls.py):
      list          - table rows (DataTables server-side format when `draw` is sent, otherwise {"data": [...]})
      options       - distinct values of a column, or the checkbox rows for the pseudo column "checkbox"
      range         - min/max of a numeric column
      countdistinct - distinct value counts per column, for the scoped table and for the filtered table
    """

    schema = None
    pagination_class = DataTablesLimitOffsetPagination

    model = None
    checkbox_fields = ()  # model fields the serializer's checkbox column reads, to avoid loading whole rows
    action = 'list'

    def scope_queryset(self, request):
        """Rows this endpoint may expose before any user filters apply (e.g. restricted by request parameters)."""
        return self.model.objects.all()

    def get_queryset(self):
        return apply_filterset(self.scope_queryset(self.request), FilterSet(self.request, self.serializer_class))

    def get(self, request, *args, **kwargs):
        handlers = {
            'list': self.list,
            'options': self.options,
            'range': self.numeric_range,
            'countdistinct': self.count_distinct,
        }
        try:
            return handlers[self.action](request, *args, **kwargs)
        except (FilterError, QueryStringProcessingError) as e:
            return JsonResponse({'error': str(e)}, status=400)

    def list(self, request, *args, **kwargs):
        url_params = DatatablesQueryStringProcessor(request).query_set
        queryset = self.get_queryset()

        if 'draw' not in url_params:
            # Client side processing. Wrapped in "data" for consistency with the server side format.
            return Response({"data": self.get_serializer(queryset, many=True).data})

        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        return JsonResponse({
            "draw": _parse_draw(url_params),
            "recordsTotal": self.scope_queryset(request).count(),
            "recordsFiltered": queryset.count(),
            "data": self.get_serializer(rows, many=True).data,
        })

    def _source_column(self, column):
        """Database column behind a serializer column; only columns the serializer exposes may be queried."""
        sources = self.serializer_class.source_mapping()
        if column not in sources:
            raise FilterError(f'Invalid column requested: {column}')
        return sources[column]

    def options(self, request, column):
        source = self._source_column(column)
        filtered = self.get_queryset()

        if column == "checkbox":
            # Only the checkbox column is serialized, so any serializer declaring a `checkbox` field supports this
            rows = filtered.only(*self.checkbox_fields) if self.checkbox_fields else filtered
            return JsonResponse(self.get_serializer().serialize_column('checkbox', rows), safe=False)

        try:
            values = filtered.values_list(source, flat=True).distinct().order_by(source)
            return JsonResponse(list(values), safe=False)
        except FieldError:
            raise FilterError(f'Invalid column requested: {column}')

    def numeric_range(self, request, column):
        source = self._source_column(column)
        if self.serializer_class.column_datatypes()[column] != 'numeric':
            raise FilterError(f'Column {column} is not a numeric column')

        # Scoped but not user filtered, so a gradient stays stable while the user filters the table
        try:
            bounds = self.scope_queryset(request).aggregate(min=Min(source), max=Max(source))
        except FieldError:
            raise FilterError(f'Invalid column requested: {column}')
        return JsonResponse(bounds, safe=False)

    def count_distinct(self, request):
        base = self.scope_queryset(request)
        filtered = self.get_queryset()

        # One aggregate query per queryset, keyed by serializer field name (the table's json_id).
        # Fields with no database column (e.g. checkbox) are skipped.
        aggregates = {}
        for name, source in self.serializer_class.source_mapping().items():
            try:
                self.model._meta.get_field(source)
            except FieldDoesNotExist:
                continue
            aggregates[name] = Count(source, distinct=True)

        return JsonResponse({
            'base': base.aggregate(**aggregates),
            'filtered': filtered.aggregate(**aggregates),
        }, safe=False)


class GpcrStructureStatisticsSummaryTable(DataTablesView):
    """API endpoint for summary statistics for GPCR entries"""

    model = GpcrStructureStatisticsTable
    serializer_class = GpcrStatisticsSummarySerializer


class PdbStructureSummaryTable(DataTablesView):
    """API endpoint for PDB structure browser data"""

    model = PdbTable
    serializer_class = PdbStructureSummarySerializer
    checkbox_fields = ('id', 'pdb_id')

    def scope_queryset(self, request):
        """PdbTable restricted by effector / exclude_non_interacting only (no user filters)."""
        effector = request.GET.get('effector', 'no_effector')
        exclude_non_interacting = request.GET.get('exclude_non_interacting') == 'true'

        queryset = PdbTable.objects.filter(TextFilter('query_effector', [effector]).format_query())
        if exclude_non_interacting:
            queryset = queryset.filter(BooleanFilter('is_complex', True).format_query())
        return queryset


class ConfigurationFactoryView(views.APIView):
    """Request handler for TableManager configuration factory.

    Loads configuration file from disk and returns a JSON response containing the requested configuration for a specific table and variant.
    """

    schema = None

    def get(self, request, *args, **kwargs):
        configuration_type = self.kwargs.get('configuration_type')
        table_name = self.kwargs.get('table_name')
        configuration_variant = self.kwargs.get('configuration_variant')

        if configuration_type == 'column':
            config_factory = ColumnConfigurationFactory(table_name, configuration_variant)
        elif configuration_type == 'datatable':
            config_factory = DataTablesConfigurationFactory(table_name, configuration_variant)
        else:
            return JsonResponse({'error': 'Invalid configuration type requested. Config: ' + configuration_type}, status=400)

        try:
            config = config_factory.fetch()
        except FileNotFoundError as e:
            return JsonResponse({'error': str(e)}, status=400)

        return JsonResponse(config, safe=False)