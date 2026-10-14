#Django Framework imports
from django.db.migrations import serializer
from django.http import JsonResponse, request
from django.core.exceptions import FieldError
from django.db.models import Min, Max

#Rest Framework imports
from rest_framework import generics, views
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination

#Data processing imports
import pandas as pd

#Table provider imports
from table_provider.configuration_provider.configuration_factory import ColumnConfigurationFactory, DataTablesConfigurationFactory
from table_provider.models import GpcrStructureStatisticsTable, PdbTable
from table_provider.serializers import GpcrStatisticsSummarySerializer, PdbStructureCheckboxSerializer, PdbStructureSummarySerializer
from table_provider.serverside.filters import FilterSet, TextFilter, BooleanFilter
from table_provider.serverside.querystringprocessing import DatatablesQueryStringProcessor
from structure.tables.structure_coverage_statistics_query import GpcrStructureCoverageStatisticsQuery
from contactnetwork.tables.pdb_table_query import PdbTableDataSource

#Pagination handler for dataTables server side processing
class DataTablesLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 30
    max_limit = 200
    limit_query_param = "length"
    offset_query_param = "start"

class GpcrStructureStatisticsSummaryTable(generics.ListCreateAPIView):
    """API endpoint for summary statistics for GPCR entries"""

    #############
    # API setup #
    #############

    schema = None
    serializer_class = GpcrStatisticsSummarySerializer
    pagination_class = DataTablesLimitOffsetPagination

    def get_queryset(self):
        filters = FilterSet(self.request, GpcrStatisticsSummarySerializer)
        queryset = self.statistics_table_fetch(filters)
        return queryset

    def list(self, request, *args, **kwargs):

        table_qs = GpcrStructureStatisticsTable.objects.all()
        
        unfilteredCount = table_qs.count()

        queryset = self.get_queryset()
        url_params = DatatablesQueryStringProcessor(self.request).query_set

        # server side datatables processing response format
        if 'draw' in url_params:

            response = {}

            filteredCount = queryset.count()

            page = self.paginate_queryset(queryset)
            rows = page if page is not None else queryset

            serializer = self.get_serializer(rows, many=True)

            response["draw"] = url_params["draw"]
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

        queryset = GpcrStructureStatisticsTable.objects.all()

        for query_filter in filters.get_filters():
            queryset = queryset.filter(query_filter.format_query())

        if filters.get_ordering():
            queryset = queryset.order_by(*filters.get_ordering())

        return queryset

    @staticmethod
    def get_select_options(request, column):
        #Map any complex table columns back to their primary data column for the query (e.g. weblinks with custom renderers)
        if column in GpcrStatisticsSummarySerializer.Meta.serializer_method_to_filter_field_map:
            column = GpcrStatisticsSummarySerializer.Meta.serializer_method_to_filter_field_map[column]

        try:
            queryset = GpcrStructureStatisticsTable.objects.values_list(column, flat=True).distinct().order_by(column)
        except FieldError:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)

        return JsonResponse(list(queryset), safe=False)

    def build_statistics_summary_table(self):
        stat_data_model = [ GpcrStructureStatisticsTable(**data_item) for data_item in GpcrStructureCoverageStatisticsQuery() ]
        GpcrStructureStatisticsTable.objects.bulk_create(stat_data_model, batch_size=10000)


class PdbStructureSummaryTable(generics.ListCreateAPIView):
    """API endpoint for PDB structure browser data"""

    #############
    # API setup #
    #############

    serializer_class = PdbStructureSummarySerializer
    pagination_class = DataTablesLimitOffsetPagination

    def get_queryset(self):
        """Fetches the PDB structure summary table queryset based on the request parameters and applied filters."""
        return self._apply_filterset(
                self._scoped_queryset(self.request),
                FilterSet(self.request, PdbStructureSummarySerializer))

    def get_queryset_base(self):
        """Returns the unfiltered queryset count for the PDB structure summary table, based on the effector and exclude_non_interacting parameters."""
        return self._scoped_queryset(self.request) 

    def list(self, request, *args, **kwargs):

        table_qs = PdbTable.objects.all()

        unfilteredCount = self.get_queryset_base().count()

        queryset = self.get_queryset()
        url_params = DatatablesQueryStringProcessor(self.request).query_set

        # server side datatables processing response format
        if 'draw' in url_params:

            response = {}

            filteredCount = queryset.count()

            page = self.paginate_queryset(queryset)
            rows = page if page is not None else queryset

            serializer = self.get_serializer(rows, many=True)

            response["draw"] = url_params["draw"]
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

    def pdb_structure_table_fetch(self, filters):

        queryset = PdbTable.objects.all()

        for query_filter in filters.get_filters():
            queryset = queryset.filter(query_filter.format_query())

        if filters.get_ordering():
            queryset = queryset.order_by(*filters.get_ordering())

        return queryset

    @staticmethod
    def get_select_options(request, column):
        """Get the unique values for a given column in the PdbTable."""
        #Map any complex table columns back to their primary data column for the query (e.g. weblinks with custom renderers)
        if column in PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map:
            column = PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map[column]

        try:    
            base = PdbStructureSummaryTable._scoped_queryset(request)
            filtered = PdbStructureSummaryTable._apply_filterset(base, FilterSet(request, PdbStructureSummarySerializer))
            
            if column == "checkbox":
                serializer = PdbStructureCheckboxSerializer(filtered.only('id', 'pdb_id'), many=True)
                return JsonResponse(serializer.data, safe=False)
            else:
                queryset = filtered.values_list(column, flat=True).distinct().order_by(column)
                return JsonResponse(list(queryset), safe=False)
            
        except FieldError:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)

    @staticmethod
    def _scoped_queryset(request):
        """PdbTable restricted by effector / exclude_non_interacting only (no user filters)."""
        effector = request.GET.get('effector', 'no_effector')
        exclude_non_interacting = request.GET.get('exclude_non_interacting') == 'true'

        queryset = PdbTable.objects.filter(TextFilter('query_effector', effector).format_query())
        if exclude_non_interacting:
            queryset = queryset.filter(BooleanFilter('is_complex', True).format_query())
        return queryset

    @staticmethod
    def _apply_filterset(queryset, filterset):
        for query_filter in filterset.get_filters():
            queryset = queryset.filter(query_filter.format_query())
        if filterset.get_ordering():
            queryset = queryset.order_by(*filterset.get_ordering())
        return queryset

    @staticmethod
    def get_count_distinct_values(request):
        base = PdbStructureSummaryTable._scoped_queryset(request)
        filtered = PdbStructureSummaryTable._apply_filterset(base, FilterSet(request, PdbStructureSummarySerializer))

        # Convert queryset to pandas DataFrame and extract unique values
        df = pd.DataFrame.from_records(base.values())
        unique_values_by_column_base = {PdbStructureSummarySerializer.Meta.reverse_map(column): len(df[column].dropna().unique().tolist()) for column in df.columns}
        df = pd.DataFrame.from_records(filtered.values())
        unique_values_by_column_filtered = {PdbStructureSummarySerializer.Meta.reverse_map(column): len(df[column].dropna().unique().tolist()) for column in df.columns}

        unique_values_by_column = {
            'base': unique_values_by_column_base,
            'filtered': unique_values_by_column_filtered
        }

        return JsonResponse(unique_values_by_column, safe=False)

    @staticmethod
    def get_numeric_range(request, column):
        #Map any complex table columns back to their primary data column for the query (e.g. weblinks with custom renderers)
        if column in PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map:
            column = PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map[column]

        if column in PdbStructureSummarySerializer.Meta.datatypes and PdbStructureSummarySerializer.Meta.datatypes[column] == 'numeric':
            try:
                queryset = PdbTable.objects.aggregate(min=Min(column), max=Max(column))
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