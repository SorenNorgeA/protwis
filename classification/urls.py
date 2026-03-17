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
    url(r'^StructureSim[/]?$', views.StructureSim.as_view(), name='classification-structuresim'),
    url(r'^Classification_tree[/]?$', views.Classification_tree.as_view(), name='classification-tree'),
    url(r'^CrossClassSimilarity[/]?$', views.CrossClassSimilarity.as_view(), name='classification-crossclass'),
    url(r'^NewClassClusterTree[/]?$', views.NewClassClusterTree.as_view(), name='classification-newclassclustertree'),

    # Backwards-compatible aliases (old URL shape)
    url(
        r'^class_similarity/GPCRBrowser[/]?$',
        RedirectView.as_view(url='/classification/GPCRBrowser', permanent=False),
    ),
    url(
        r'^class_similarity/Classification[/]?$',
        RedirectView.as_view(url='/classification/Classification', permanent=False),
    ),
    url(
        r'^class_similarity/ClassificationWheel[/]?$',
        RedirectView.as_view(url='/classification/ClassificationWheel', permanent=False),
    ),
    url(
        r'^class_similarity/StructureSim[/]?$',
        RedirectView.as_view(url='/classification/StructureSim', permanent=False),
    ),
    url(
        r'^class_similarity/Classification_tree[/]?$',
        RedirectView.as_view(url='/classification/Classification_tree', permanent=False),
    ),
    url(
        r'^class_similarity/CrossClassSimilarity[/]?$',
        RedirectView.as_view(url='/classification/CrossClassSimilarity', permanent=False),
    ),
    url(
        r'^class_similarity/NewClassClusterTree[/]?$',
        RedirectView.as_view(url='/classification/NewClassClusterTree', permanent=False),
    ),
]

