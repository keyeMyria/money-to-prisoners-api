from django.conf.urls import patterns, url, include

from rest_framework import routers

from . import views

router = routers.DefaultRouter()
router.register(r'batches', views.BatchView)

urlpatterns = patterns('',
    url(r'^', include(router.urls)),
)
