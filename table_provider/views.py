#Django Framework imports
from django.http import JsonResponse
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
from table_provider.serializers import GpcrStatisticsSummarySerializer, PdbStructureSummarySerializer
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
        effector = self.request.GET.get('effector', 'no_effector')
        exclude_non_interacting = True if self.request.GET.get('exclude_non_interacting') == 'true' else False

        filters = FilterSet(self.request, PdbStructureSummarySerializer)
        filters.add_filter(TextFilter('query_effector', effector))
        if exclude_non_interacting:
            filters.add_filter(BooleanFilter('is_complex', exclude_non_interacting))

        queryset = self.pdb_structure_table_fetch(filters)
        return queryset

    def get_queryset_base(self):
        """Returns the unfiltered queryset count for the PDB structure summary table, based on the effector and exclude_non_interacting parameters."""
        effector = self.request.GET.get('effector', 'no_effector')
        exclude_non_interacting = True if self.request.GET.get('exclude_non_interacting') == 'true' else False

        filters = FilterSet(None, PdbStructureSummarySerializer) #Pass None for request to avoid parsing filters from query parameters
        filters.add_filter(TextFilter('query_effector', effector))
        if exclude_non_interacting:
            filters.add_filter(BooleanFilter('is_complex', exclude_non_interacting))

        queryset = self.pdb_structure_table_fetch(filters)
        return queryset

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
        effector = request.GET.get('effector', 'no_effector')
        exclude_non_interacting = True if request.GET.get('exclude_non_interacting') == 'true' else False
        
        #Map any complex table columns back to their primary data column for the query (e.g. weblinks with custom renderers)
        if column in PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map:
            column = PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map[column]

        try:    
            queryset_base = PdbTable.objects.all()
    
            queryset_base = queryset_base.filter(TextFilter('query_effector', effector).format_query())
            if exclude_non_interacting:
                queryset_base = queryset_base.filter(BooleanFilter('is_complex', exclude_non_interacting).format_query())

            queryset = queryset_base.values_list(column, flat=True).distinct().order_by(column)
        except FieldError:
            return JsonResponse({'error': f'Invalid column requested: {column}'}, status=400)

        return JsonResponse(list(queryset), safe=False)


    @staticmethod
    def get_count_distinct_values(request):
        effector = request.GET.get('effector', 'no_effector')
        exclude_non_interacting = True if request.GET.get('exclude_non_interacting') == 'true' else False

        queryset_base = PdbTable.objects.all()

        queryset_base = queryset_base.filter(TextFilter('query_effector', effector).format_query())
        if exclude_non_interacting:
            queryset_base = queryset_base.filter(BooleanFilter('is_complex', exclude_non_interacting).format_query())

        url_filters = FilterSet(request, PdbStructureSummarySerializer)
        queryset_filtered = queryset_base
        for query_filter in url_filters.get_filters():
            queryset_filtered = queryset_filtered.filter(query_filter.format_query())

        #Helper to map complex/compund columns back to their json/datatable id
        def reverse_map(column_name):
            if (column_name in PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map.values()):
                for key, value in PdbStructureSummarySerializer.Meta.serializer_method_to_filter_field_map.items():
                    if value == column_name: return key
            return column_name

        # Convert queryset to pandas DataFrame and extract unique values
        df = pd.DataFrame.from_records(queryset_base.values())
        unique_values_by_column_base = {reverse_map(column): len(df[column].dropna().unique().tolist()) for column in df.columns}
        df = pd.DataFrame.from_records(queryset_filtered.values())
        unique_values_by_column_filtered = {reverse_map(column): len(df[column].dropna().unique().tolist()) for column in df.columns}

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