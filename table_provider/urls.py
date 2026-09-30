from django.conf.urls import url
from django.views.generic import TemplateView
from table_provider.views import GpcrStructureStatisticsSummaryTable, PdbStructureSummaryTable, ConfigurationFactoryView
from django.views.decorators.cache import cache_page

urlpatterns = [
    ###################################
    # GPCR Structure Statistics Table #
    ###################################
    
    #DataTables/TableManager data source - Non-cached URL for server-side processing
    url(r'^structure_statistics_table/gpcr/?$', (GpcrStructureStatisticsSummaryTable.as_view()), name='structure_statistics_table'),
    #DataTables/TableManager data source - Cached URL for client side processing
    url(r'^structure_statistics_table_cached/gpcr/?$', cache_page(60*60*24)(GpcrStructureStatisticsSummaryTable.as_view()), name='structure_statistics_table_cached'),  

    #AJAX source for dropdown select options for filtering various columns in the GPCR Structure Statistics Table
    url(r'^structure_statistics_table/gpcr/options/(?P<column>[^/]+)/?$', (GpcrStructureStatisticsSummaryTable.get_select_options), name='structure_statistics_table_options'),

    ##########################
    # PDB Structure Table    #
    ##########################

    #DataTables/TableManager data source - Non-cached URL for server-side processing
    url(r'^pdb_data_table/?$', (PdbStructureSummaryTable.as_view()), name='pdb_structure_table'),
    #DataTables/TableManager data source - Cached URL for client side processing
    url(r'^pdb_data_table_cached/?$', cache_page(60*60*24)(PdbStructureSummaryTable.as_view()), name='pdb_structure_table_cached'),

    #AJAX source for dropdown select options for filtering various columns in the PDB Structure Table
    url(r'^pdb_data_table/options/(?P<column>[^/]+)/?$', (PdbStructureSummaryTable.get_select_options), name='pdb_structure_table_options'),

    #AJAX request for numeric range (min, max) of data for a given column in the PDB Structure Table
    url(r'^pdb_data_table/range/(?P<column>[^/]+)/?$', (PdbStructureSummaryTable.get_numeric_range), name='pdb_structure_table_options'),

    #AJAX request for counts of distinct values for all columns in the PDB Structure Table 
    url(r'^pdb_data_table/countdistinct/?$', (PdbStructureSummaryTable.get_count_distinct_values), name='pdb_structure_table_count_distinct'),


    ######################################
    # TableManager configuration factory #
    ######################################

    url(r'^configuration/(?P<configuration_type>[^/]+)/(?P<table_name>[^/]+)/(?P<configuration_variant>[^/]+)/?$', ConfigurationFactoryView.as_view(), name='configuration_factory'),
]