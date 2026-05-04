from django.urls import path
from . import views

urlpatterns = [
    path('stations/', views.NetatmoStationListView.as_view()),
    path('stations/<str:device_id>/data/', views.StationDataListView.as_view()),
    #path('sensor-community-h3/', views.SensorCommunityH3AggregationView.as_view()),
    path("h3-aggregation/", views.H3AggregationView.as_view(), name="h3-aggregation"),
    path("h3-date-aggregation/", views.H3DateAggregationView.as_view(), name="h3-date-aggregation"),
    #path("sensor-community-stations/", views.SensorCommunityStationsView.as_view(), name="sensor-community-stations"),
    path("sensor-stations/", views.SensorStationListView.as_view(), name="sensor-stations"),
    path("sensor-stations-h3-aggregation/", views.SensorStationsDataH3AggregationView.as_view(), name="sensor-stations-h3-aggregation"),
]