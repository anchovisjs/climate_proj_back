from rest_framework import serializers
from .models import NetatmoStation, StationData


class StationDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = StationData
        fields = ['device', 'datetime', 'temperature', 'temp_valid', 'column_11',
                  'column_12', 'column_13', 'column_14']


class NetatmoStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = NetatmoStation
        fields = ['device_id', 'lon', 'lat', 'height']
