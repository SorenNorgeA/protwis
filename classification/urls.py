from django.conf.urls import url
from django.views.generic import RedirectView

from classification import views


urlpatterns = [
    # Redirect /classification/ to the main Classification page
    url(
        r'^$',
        RedirectView.as_view(
            url='/classification/Classification',
            permanent=False,
        ),
        name='classification-index',
    ),

    # Canonical URLs
    url(r'^GPCRBrowser[/]?$', views.GPCRBrowser.as_view(), name='classification-gpcrbrowser'),
    url(r'^Classification[/]?$', views.Classification.as_view(), name='classification-classification'),
    url(r'^ClassificationWheel[/]?$', views.ClassificationWheel.as_view(), name='classification-wheel'),
    url(r'^visualizations[/]?$', views.ClassificationVisualizationsLanding.as_view(), name='classification-visualizations'),
    url(
        r'^visualizations/class/(?P<class_key>[A-Za-z0-9]+)[/]?$',
        views.ClassificationVisualizationDetail.as_view(),
        name='classification-visualizations-class',
    ),
    url(
        r'^visualizations/tree[/]?$',
        views.ClassificationTreeVisualizationDetail.as_view(),
        name='classification-visualizations-tree',
    ),
    url(
        r'^visualizations/superfamily[/]?$',
        views.GPCRSuperfamilyVisualizationDetail.as_view(),
        name='classification-visualizations-superfamily',
    ),
    url(
        r'^visualizations/superfamily/tree[/]?$',
        views.SuperfamilyCircularTree.as_view(),
        name='classification-superfamily-tree',
    ),
    url(
        r'^visualizations/family/(?P<family_key>[-a-z0-9]+)[/]?$',
        views.ReceptorFamilyVisualizationDetail.as_view(),
        name='classification-visualizations-family',
    ),
    url(r'^StructureSim[/]?$', views.StructureSim.as_view(), name='classification-structuresim'),
    url(r'^Classification_tree[/]?$', views.Classification_tree.as_view(), name='classification-tree'),
    url(r'^CrossClassSimilarity[/]?$', views.CrossClassSimilarity.as_view(), name='classification-crossclass'),
    url(r'^NewClassClusterTree[/]?$', views.NewClassClusterTree.as_view(), name='classification-newclassclustertree'),
]

