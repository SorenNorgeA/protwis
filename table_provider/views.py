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
from table_provider.serializers import GpcrStatisticsSummarySerializer, PdbStructureCheckboxSerializer, PdbStructureSummarySerializer
from table_provider.serverside.filters import FilterSet, FilterError, TextFilter, BooleanFilter
from table_provider.serverside.querystringprocessing import DatatablesQueryStringProcessor, QueryStringProcessingError
from structure.tables.structure_coverage_statistics_query import GpcrStructureCoverageStatisticsQuery
from contactnetwork.tables.pdb_table_query import PdbTableDataSource

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

class GpcrStructureStatisticsSummaryTable(generics.ListAPIView):
    """API endpoint for summary statistics for GPCR entries"""

    #############
    # API setup #
    #############

    schema = None
    serializer_class = GpcrStatisticsSummarySerializer
    pagination_class = DataTablesLimitOffsetPagination

    def get_queryset(self):
        try:
            filters = FilterSet(self.request, GpcrStatisticsSummarySerializer)
        except FilterError as e:
            raise ParseError(str(e))
        queryset = self.statistics_table_fetch(filters)
        return queryset

    def list(self, request, *args, **kwargs):

        unfilteredCount = GpcrStructureStatisticsTable.objects.count()

        try:
            url_params = DatatablesQueryStringProcessor(self.request).query_set
        except QueryStringProcessingError as e:
            return JsonResponse({'error': str(e)}, status=400)

        queryset = self.get_queryset()

        # server side datatables processing response format
        if 'draw' in url_params:

            response = {}

            filteredCount = queryset.count()

            page = self.paginate_queryset(queryset)
            rows = page if page is not None else queryset

            serializer = self.get_serializer(rows, many=True)

            response["draw"] = _parse_draw(url_params)
            response["recordsTotal"] = unfilteredCount
            response["recordsFiltered"] = filteredCount
            response["data"] = serializer.data

            return JsonResponse(response)

        else:
            # client side datatables processing response format
            serializer = self.get_serializer(queryset, many=True)
            # for consistency with server side processing response format,
            # wrap data in "data" key, even though client side processing doesn't require this
            response = {}
            response["data"] = serializer.data
            return Response(response)

    def statistics_table_fetch(self, filters):

        return apply_filterset(GpcrStructureStatisticsTable.objects.all(), filters)

    @staticmethod
    def get_select_options(request, column):
        # Only columns the serializer exposes may be queried; map them to their database column
        sources = GpcrStatisticsSummarySerializer.source_mapping()
        if column not in sources:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)
        column = sources[column]

        try:
            queryset = GpcrStructureStatisticsTable.objects.values_list(column, flat=True).distinct().order_by(column)
        except FieldError:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)

        return JsonResponse(list(queryset), safe=False)

    def build_statistics_summary_table(self):
        stat_data_model = [ GpcrStructureStatisticsTable(**data_item) for data_item in GpcrStructureCoverageStatisticsQuery() ]
        GpcrStructureStatisticsTable.objects.bulk_create(stat_data_model, batch_size=10000)


class PdbStructureSummaryTable(generics.ListAPIView):
    """API endpoint for PDB structure browser data"""

    #############
    # API setup #
    #############

    serializer_class = PdbStructureSummarySerializer
    pagination_class = DataTablesLimitOffsetPagination

    def get_queryset(self):
        """Fetches the PDB structure summary table queryset based on the request parameters and applied filters."""
        try:
            filterset = FilterSet(self.request, PdbStructureSummarySerializer)
        except FilterError as e:
            raise ParseError(str(e))
        return apply_filterset(self._scoped_queryset(self.request), filterset)

    def get_queryset_base(self):
        """Returns the unfiltered queryset count for the PDB structure summary table, based on the effector and exclude_non_interacting parameters."""
        return self._scoped_queryset(self.request) 

    def list(self, request, *args, **kwargs):

        unfilteredCount = self.get_queryset_base().count()

        try:
            url_params = DatatablesQueryStringProcessor(self.request).query_set
        except QueryStringProcessingError as e:
            return JsonResponse({'error': str(e)}, status=400)

        queryset = self.get_queryset()

        # server side datatables processing response format
        if 'draw' in url_params:

            response = {}

            filteredCount = queryset.count()

            page = self.paginate_queryset(queryset)
            rows = page if page is not None else queryset

            serializer = self.get_serializer(rows, many=True)

            response["draw"] = _parse_draw(url_params)
            response["recordsTotal"] = unfilteredCount
            response["recordsFiltered"] = filteredCount
            response["data"] = serializer.data

            return JsonResponse(response)

        else:
            # client side datatables processing response format
            serializer = self.get_serializer(queryset, many=True)
            # for consistency with server side processing response format,
            # wrap data in "data" key, even though client side processing doesn't require this
            response = {}
            response["data"] = serializer.data
            return Response(response)

    @staticmethod
    def get_select_options(request, column):
        """Get the unique values for a given column in the PdbTable."""
        # Only columns the serializer exposes may be queried; map them to their database column
        sources = PdbStructureSummarySerializer.source_mapping()
        if column not in sources:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)
        column = sources[column]

        try:
            base = PdbStructureSummaryTable._scoped_queryset(request)
            filtered = apply_filterset(base, FilterSet(request, PdbStructureSummarySerializer))
            
            if column == "checkbox":
                serializer = PdbStructureCheckboxSerializer(filtered.only('id', 'pdb_id'), many=True)
                return JsonResponse(serializer.data, safe=False)
            else:
                queryset = filtered.values_list(column, flat=True).distinct().order_by(column)
                return JsonResponse(list(queryset), safe=False)
            
        except FieldError:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)
        except FilterError as e:
            return JsonResponse({'error': str(e)}, status=400)

    @staticmethod
    def _scoped_queryset(request):
        """PdbTable restricted by effector / exclude_non_interacting only (no user filters)."""
        effector = request.GET.get('effector', 'no_effector')
        exclude_non_interacting = request.GET.get('exclude_non_interacting') == 'true'

        queryset = PdbTable.objects.filter(TextFilter('query_effector', [effector]).format_query())
        if exclude_non_interacting:
            queryset = queryset.filter(BooleanFilter('is_complex', True).format_query())
        return queryset

    @staticmethod
    def get_count_distinct_values(request):
        base = PdbStructureSummaryTable._scoped_queryset(request)
        try:
            filterset = FilterSet(request, PdbStructureSummarySerializer)
        except FilterError as e:
            return JsonResponse({'error': str(e)}, status=400)
        filtered = apply_filterset(base, filterset)

        # One aggregate query per queryset, counting distinct non-null values in the database.
        # Results are keyed by serializer field name (the table's json_id), skipping fields with no
        # database column (e.g. checkbox).
        aggregates = {}
        for name, source in PdbStructureSummarySerializer.source_mapping().items():
            try:
                PdbTable._meta.get_field(source)
            except FieldDoesNotExist:
                continue
            aggregates[name] = Count(source, distinct=True)

        unique_values_by_column = {
            'base': base.aggregate(**aggregates),
            'filtered': filtered.aggregate(**aggregates),
        }

        return JsonResponse(unique_values_by_column, safe=False)

    @staticmethod
    def get_numeric_range(request, column):
        # Only columns the serializer exposes may be queried; map them to their database column
        sources = PdbStructureSummarySerializer.source_mapping()
        if column not in sources:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)
        is_numeric = PdbStructureSummarySerializer.column_datatypes()[column] == 'numeric'
        column = sources[column]

        if is_numeric:
            try:
                # Scope by effector / exclude_non_interacting like every other PDB endpoint, but not by user filters,
                # so the gradient stays stable while the user filters the table.
                queryset = PdbStructureSummaryTable._scoped_queryset(request).aggregate(min=Min(column), max=Max(column))
            except FieldError:
                return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)
        else:
            return JsonResponse({'error': f'Column {column} is not a numeric column'}, status=400)

        return JsonResponse(queryset, safe=False)


    def build_pdb_structure_table(self):
        """Builds the PDB structure summary table by fetching data from the PdbTableDataSource and bulk creating PdbTable instances."""        
        pdb_data_model = []
        for entries in PdbTableDataSource().values():
            for entry in entries.values():
                pdb_data_model.append(PdbTable(**entry))

        PdbTable.objects.bulk_create(pdb_data_model, batch_size=10000)


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