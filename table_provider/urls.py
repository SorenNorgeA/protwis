from django.conf.urls import url
from django.views.decorators.cache import cache_page
from table_provider.views import GpcrStructureStatisticsSummaryTable, PdbStructureSummaryTable, GpcrListSummaryTable, ConfigurationFactoryView


def datatable_urls(path, view_class, name, cached_path=None):
    """URL patterns for every endpoint of a DataTablesView table, rooted at `path`.

    path/                         rows (non-cached, for server-side processing)
    cached_path/                  rows (cached, for client-side processing), if given
    path/options/<column>         distinct values of a column (select filters), or checkbox rows for "checkbox"
    path/range/<column>           min/max of a numeric column
    path/countdistinct            distinct value counts per column
    """
    path = path.strip('/')
    patterns = [url(r'^%s/?$' % path, view_class.as_view(), name=name)]
    if cached_path:
        patterns.append(url(r'^%s/?$' % cached_path.strip('/'), cache_page(60 * 60 * 24)(view_class.as_view()), name=name + '_cached'))
    patterns += [
        url(r'^%s/options/(?P<column>[^/]+)/?$' % path, view_class.as_view(action='options'), name=name + '_options'),
        url(r'^%s/range/(?P<column>[^/]+)/?$' % path, view_class.as_view(action='range'), name=name + '_range'),
        url(r'^%s/countdistinct/?$' % path, view_class.as_view(action='countdistinct'), name=name + '_count_distinct'),
    ]
    return patterns


urlpatterns = [
    *datatable_urls('structure_statistics_table/gpcr', GpcrStructureStatisticsSummaryTable, 'structure_statistics_table',
                    cached_path='structure_statistics_table_cached/gpcr'),
    *datatable_urls('pdb_data_table', PdbStructureSummaryTable, 'pdb_structure_table',
                    cached_path='pdb_data_table_cached'),
    *datatable_urls('gpcr_list_table', GpcrListSummaryTable, 'gpcr_list_table',
                    cached_path='gpcr_list_table_cached'),

    ######################################
    # TableManager configuration factory #
    ######################################

    url(r'^configuration/(?P<configuration_type>[^/]+)/(?P<table_name>[^/]+)/(?P<configuration_variant>[^/]+)/?$', ConfigurationFactoryView.as_view(), name='configuration_factory'),
]
