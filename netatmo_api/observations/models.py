# Create your models here.
from django.db import models


class NetatmoStation(models.Model):
    device_id = models.CharField(primary_key=True, max_length=64)
    lon = models.FloatField()
    lat = models.FloatField()
    height = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = 'netatmo_stations'
        managed = False


class StationData(models.Model):
    fid = models.CharField(primary_key=True, max_length=64)

    device = models.ForeignKey(
        NetatmoStation,
        to_field='device_id',
        db_column='id',
        on_delete=models.CASCADE,
        related_name='data'
    )

    datetime = models.DateTimeField()
    temperature = models.FloatField()
    temp_valid = models.CharField(max_length=10, null=True, blank=True)

    column_11 = models.FloatField(null=True, blank=True)
    column_12 = models.FloatField(null=True, blank=True)
    column_13 = models.FloatField(null=True, blank=True)
    column_14 = models.FloatField(null=True, blank=True)

    h3_res_5 = models.BigIntegerField(null=True, blank=True)
    h3_res_7 = models.BigIntegerField(null=True, blank=True)
    h3_res_9 = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = 'stations_data'
        managed = False

class StationMonthAvg(models.Model):
    station_id = models.CharField(max_length=64)
    year_num = models.SmallIntegerField()
    month_num = models.SmallIntegerField()

    h3_res_5 = models.TextField(null=True, blank=True)
    h3_res_7 = models.TextField(null=True, blank=True)
    h3_res_9 = models.TextField(null=True, blank=True)

    avg_temp_all = models.FloatField(null=True, blank=True)
    avg_temp_v1 = models.FloatField(null=True, blank=True)
    avg_temp_v2 = models.FloatField(null=True, blank=True)
    avg_temp_v3 = models.FloatField(null=True, blank=True)
    avg_temp_v4 = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = 'station_month_avg'
        managed = False

class SensorStation(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    longitude = models.FloatField()
    latitude = models.FloatField()

    class Meta:
        db_table = 'sensor_stations'
        managed = False

class SensorStationData(models.Model):
    fid = models.CharField(primary_key=True, max_length=64)

    device = models.ForeignKey(
        SensorStation,
        to_field='id',
        db_column='id',
        on_delete=models.CASCADE,
        related_name='data'
    )

    datetime = models.DateTimeField()
    temperature = models.FloatField()

    h3_res_5 = models.BigIntegerField(null=True, blank=True)
    h3_res_7 = models.BigIntegerField(null=True, blank=True)
    h3_res_9 = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = 'sensor_stations_data'
        managed = False
