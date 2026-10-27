from datetime import datetime, timedelta
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.db import connection

from .helpers import aggregate_to_target_resolution, extract_h3_rows_and_station_ids
from .models import NetatmoStation

class ClimateParameterMixin:
    ALLOWED_PARAMETERS = {"temperature", "humidity", "pressure"}
    def get_parameter(self, request):
        parameter = request.data.get("parameter", "temperature")
        if parameter not in self.ALLOWED_PARAMETERS:
            return None, Response(
                {"error": "parameter must be one of: temperature, humidity, pressure"},
                status=status.HTTP_400_BAD_REQUEST
            )
        return parameter, None

class NetatmoStationListView(APIView):
    def get(self, request):
        stations = NetatmoStation.objects.only('device_id', 'lon', 'lat', 'height')
        features = []
        for station in stations:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [station.lon, station.lat]
                },
                "properties": {
                    "device_id": station.device_id,
                    "height": station.height
                }
            })
        geojson = {
            "type": "FeatureCollection",
            "features": features
        }
        return Response(geojson)

    def post(self, request):
        station_ids = request.data.get("station_ids")
        if not isinstance(station_ids, list):
            return Response(
                {"error": "station_ids must be a list"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(station_ids) > 20000:
            return Response(
                {"error": "Too many station IDs"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not all(isinstance(sid, str) for sid in station_ids):
            return Response(
                {"error": "Netatmo station IDs must be strings"},
                status=status.HTTP_400_BAD_REQUEST
            )
        stations = NetatmoStation.objects.filter(
            device_id__in=station_ids
        ).only(
            "device_id", "lon", "lat", "height"
        )
        features = []
        for station in stations:
            if station.lon is None or station.lat is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        float(station.lon),
                        float(station.lat)
                    ]
                },
                "properties": {
                    "device_id": station.device_id,
                    "height": station.height
                }
            })

        return Response({
            "type": "FeatureCollection",
            "features": features
        })

class H3AggregationView(APIView):
    SOURCE_RESOLUTIONS = {
        3: (5, "h3_res_5"),
        4: (5, "h3_res_5"),
        5: (5, "h3_res_5"),
        6: (7, "h3_res_7"),
        7: (7, "h3_res_7"),
        8: (9, "h3_res_9"),
        9: (9, "h3_res_9"),
    }
    DATA_VAL_TEMP_COLUMN_MAP = {
        None: "avg_temp_all",
        "": "avg_temp_all",
        "null": "avg_temp_all",
        1: "avg_temp_v1",
        2: "avg_temp_v2",
        3: "avg_temp_v3",
        4: "avg_temp_v4",
    }
    NON_TEMP_PARAMETER_COLUMN_MAP = {
        "humidity": "avg_humi",
        "pressure": "avg_pres",
    }
    def get(self, request):
        return Response(
            {"detail": "Use POST for this endpoint."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )
    def post(self, request):
        try:
            target_res = int(request.data.get("resolution"))
        except (TypeError, ValueError):
            return Response(
                {"error": "resolution must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if target_res not in self.SOURCE_RESOLUTIONS:
            return Response(
                {"error": "Resolution must be between 3 and 9"},
                status=status.HTTP_400_BAD_REQUEST
            )
        year = request.data.get("year")
        month = request.data.get("month")
        day = request.data.get("day")
        data_val = request.data.get("data_val")
        parameter = request.data.get("parameter")
        if parameter not in ("temperature", "humidity", "pressure"):
            return Response(
                {"error": "parameter must be one of: temperature, humidity, pressure"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if year in (None, "", "null"):
            return Response(
                {"error": "year is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if month in (None, "", "null"):
            return Response(
                {"error": "month is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            year = int(year)
            month = int(month)
        except (TypeError, ValueError):
            return Response(
                {"error": "year and month must be integers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if day not in (None, "", "null"):
            return Response(
                {"error": "day filter is not supported for station_month_avg"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if data_val not in (None, "", "null"):
            try:
                data_val = int(data_val)
            except (TypeError, ValueError):
                return Response(
                    {"error": "data_val must be integer 1..4"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if data_val not in (1, 2, 3, 4):
                return Response(
                    {"error": "data_val must be integer 1..4"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        source_res, source_col = self.SOURCE_RESOLUTIONS[target_res]
        if parameter == "temperature":
            if data_val not in (None, "", "null"):
                try:
                    data_val = int(data_val)
                except (TypeError, ValueError):
                    return Response(
                        {"error": "data_val must be integer 1..4"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if data_val not in (1, 2, 3, 4):
                    return Response(
                        {"error": "data_val must be integer 1..4"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            value_col = self.DATA_VAL_TEMP_COLUMN_MAP[data_val]
        else:
            value_col = self.NON_TEMP_PARAMETER_COLUMN_MAP[parameter]
        params = [year, month]
        query = f"""
            SELECT
                {source_col} AS hex_id,
                AVG({value_col}) AS avg_value,
                COUNT(*) AS cnt,
                ARRAY_AGG(DISTINCT station_id) AS station_ids
            FROM station_month_avg
            WHERE year_num = %s
              AND month_num = %s
              AND {source_col} IS NOT NULL
              AND {value_col} IS NOT NULL
            GROUP BY {source_col}
        """
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        h3_rows, station_ids = extract_h3_rows_and_station_ids(
            rows,
            source="netatmo"
        )
        aggregated = aggregate_to_target_resolution(
            h3_rows,
            source_res,
            target_res
        )
        features = []
        for hex_id, avg_temp, cnt in aggregated:
            if hex_id is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "parameter": parameter,
                    "avg_value": round(float(avg_temp), 2),
                    "count": int(cnt)
                }
            })
        return Response({
            "type": "FeatureCollection",
            "features": features,
            "station_ids": station_ids
        })

class H3DateAggregationView(APIView):
    SOURCE_RESOLUTIONS = {
        3: (5, "h3_res_5"),
        4: (5, "h3_res_5"),
        5: (5, "h3_res_5"),
        6: (7, "h3_res_7"),
        7: (7, "h3_res_7"),
        8: (9, "h3_res_9"),
        9: (9, "h3_res_9"),
    }
    DATA_VAL_COLUMN_MAP = {
        1: "column_11",
        2: "column_12",
        3: "column_13",
        4: "column_14",
    }
    def get(self, request):
        return Response(
            {"detail": "Use POST for this endpoint."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )
    def post(self, request):
        try:
            target_res = int(request.data.get("resolution"))
        except (TypeError, ValueError):
            return Response(
                {"error": "resolution must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if target_res not in self.SOURCE_RESOLUTIONS:
            return Response(
                {"error": "Resolution must be between 3 and 9"},
                status=status.HTTP_400_BAD_REQUEST
            )
        year = request.data.get("year")
        month = request.data.get("month")
        day = request.data.get("day")
        hour = request.data.get("hour")
        data_val = request.data.get("data_val")
        parameter = request.data.get("parameter", "temperature")
        if parameter not in ("temperature", "humidity", "pressure"):
            return Response(
                {"error": "parameter must be one of: temperature, humidity, pressure"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if year in (None, "", "null"):
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)
        if month in (None, "", "null"):
            return Response({"error": "month is required"}, status=status.HTTP_400_BAD_REQUEST)
        if day in (None, "", "null"):
            return Response({"error": "day is required for date aggregation"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            year = int(year)
            month = int(month)
            day = int(day)
        except (TypeError, ValueError):
            return Response(
                {"error": "year, month, day must be integers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if hour in (None, "", "null"):
            hour = None
        else:
            try:
                hour = int(hour)
            except (TypeError, ValueError):
                return Response(
                    {"error": "hour must be integer from 0 to 23"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if hour < 0 or hour > 23:
                return Response(
                    {"error": "hour must be integer from 0 to 23"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        data_val_filter = ""
        value_col = parameter
        if parameter == "temperature":
            if data_val not in (None, "", "null"):
                try:
                    data_val = int(data_val)
                except (TypeError, ValueError):
                    return Response(
                        {"error": "data_val must be integer 1..4"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                col = self.DATA_VAL_COLUMN_MAP.get(data_val)
                if not col:
                    return Response(
                        {"error": "data_val must be integer 1..4"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                data_val_filter = f"AND sd.{col} = 1"
        source_res, source_col = self.SOURCE_RESOLUTIONS[target_res]
        if hour is not None:
            try:
                target_dt = datetime(year, month, day, hour, 0, 0)
            except ValueError:
                return Response(
                    {"error": "year, month, day, hour must define a valid datetime"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            params = [target_dt]
            query = f"""
                SELECT
                    sd.{source_col} AS hex_id,
                    AVG(sd.{value_col}) AS avg_value,
                    COUNT(*) AS cnt,
                    ARRAY_AGG(DISTINCT sd.id) AS station_ids
                FROM stations_data sd
                WHERE sd.datetime = %s
                  AND sd.{value_col} IS NOT NULL
                  AND sd.{source_col} IS NOT NULL
                  {data_val_filter}
                GROUP BY sd.{source_col}
            """
        else:
            try:
                day_start = datetime(year, month, day, 0, 0, 0)
                day_end = day_start + timedelta(days=1)
            except ValueError:
                return Response(
                    {"error": "year, month, day must define a valid date"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            params = [day_start, day_end]
            query = f"""
                WITH station_day_avg AS (
                    SELECT
                        sd.id AS station_id,
                        sd.{source_col} AS hex_id,
                        AVG(sd.{value_col}) AS station_avg_value
                    FROM stations_data sd
                    WHERE sd.datetime >= %s
                      AND sd.datetime < %s
                      AND sd.{value_col} IS NOT NULL
                      AND sd.{source_col} IS NOT NULL
                      {data_val_filter}
                    GROUP BY sd.id, sd.{source_col}
                )
                SELECT
                    hex_id,
                    AVG(station_avg_value) AS avg_value,
                    COUNT(*) AS cnt,
                    ARRAY_AGG(station_id) AS station_ids
                FROM station_day_avg
                GROUP BY hex_id
            """
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        h3_rows, station_ids = extract_h3_rows_and_station_ids(
            rows,
            source="netatmo"
        )
        aggregated = aggregate_to_target_resolution(
            h3_rows,
            source_res,
            target_res
        )
        features = []
        for hex_id, avg_temp, cnt in aggregated:
            if hex_id is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "parameter": parameter,
                    "avg_value": round(float(avg_temp), 2),
                    "count": int(cnt)
                }
            })
        return Response({
            "type": "FeatureCollection",
            "features": features,
            "station_ids": station_ids
        })

class SensorStationListView(APIView):
    def get(self, request):
        query = """
            SELECT
                id,
                longitude,
                latitude
            FROM sensor_stations
            WHERE longitude IS NOT NULL
              AND latitude IS NOT NULL
        """
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
        features = []
        for station_id, lon, lat in rows:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)]
                },
                "properties": {
                    "sensor_id": int(station_id)
                }
            })
        return Response({
            "type": "FeatureCollection",
            "features": features
        })
    def post(self, request):
        station_ids = request.data.get("station_ids")

        if not isinstance(station_ids, list):
            return Response(
                {"error": "station_ids must be a list"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(station_ids) > 20000:
            return Response(
                {"error": "Too many station IDs"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            station_ids = list(set(int(sid) for sid in station_ids))
        except (TypeError, ValueError):
            return Response(
                {"error": "Sensor station IDs must be integers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not station_ids:
            return Response({
                "type": "FeatureCollection",
                "features": []
            })
        query = """
            SELECT
                id,
                longitude,
                latitude
            FROM sensor_stations
            WHERE longitude IS NOT NULL
              AND latitude IS NOT NULL
              AND id = ANY(%s)
        """
        with connection.cursor() as cursor:
            cursor.execute(query, [station_ids])
            rows = cursor.fetchall()
        features = []
        for station_id, lon, lat in rows:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)]
                },
                "properties": {
                    "sensor_id": int(station_id)
                }
            })
        return Response({
            "type": "FeatureCollection",
            "features": features
        })

class SensorStationsDataH3AggregationView(APIView):
    SOURCE_RESOLUTIONS = {
        3: (5, "h3_res_5"),
        4: (5, "h3_res_5"),
        5: (5, "h3_res_5"),
        6: (7, "h3_res_7"),
        7: (7, "h3_res_7"),
        8: (9, "h3_res_9"),
        9: (9, "h3_res_9"),
    }
    def get(self, request):
        return Response(
            {"detail": "Use POST for this endpoint."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )
    def post(self, request):
        try:
            target_res = int(request.data.get("resolution"))
        except (TypeError, ValueError):
            return Response(
                {"error": "resolution must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if target_res not in self.SOURCE_RESOLUTIONS:
            return Response(
                {"error": "Resolution must be between 3 and 9"},
                status=status.HTTP_400_BAD_REQUEST
            )
        year = request.data.get("year")
        month = request.data.get("month")
        day = request.data.get("day")
        hour = request.data.get("hour")
        min_confidence = request.data.get("min_confidence", 1)
        parameter = request.data.get("parameter", "temperature")
        if parameter not in ("temperature", "humidity", "pressure"):
            return Response(
                {"error": "parameter must be one of: temperature, humidity, pressure"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            min_confidence = int(min_confidence)
        except (TypeError, ValueError):
            return Response(
                {"error": "min_confidence must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if min_confidence < 1:
            return Response(
                {"error": "min_confidence must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if year in (None, "", "null"):
            return Response(
                {"error": "year is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            year = int(year)
        except (TypeError, ValueError):
            return Response(
                {"error": "year must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        month = None if month in (None, "", "null") else month
        day = None if day in (None, "", "null") else day
        hour = None if hour in (None, "", "null") else hour
        value_col = parameter
        try:
            if month is not None:
                month = int(month)
                if month < 1 or month > 12:
                    return Response(
                        {"error": "month must be from 1 to 12"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            if day is not None:
                if month is None:
                    return Response(
                        {"error": "month is required when day is provided"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                day = int(day)
            if hour is not None:
                if day is None:
                    return Response(
                        {"error": "day is required when hour is provided"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                hour = int(hour)
                if hour < 0 or hour > 23:
                    return Response(
                        {"error": "hour must be from 0 to 23"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
        except (TypeError, ValueError):
            return Response(
                {"error": "month, day and hour must be valid integers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            if month is None:
                period_start = datetime(year, 1, 1, 0, 0, 0)
                period_end = datetime(year + 1, 1, 1, 0, 0, 0)

            elif day is None:
                period_start = datetime(year, month, 1, 0, 0, 0)

                if month == 12:
                    period_end = datetime(year + 1, 1, 1, 0, 0, 0)
                else:
                    period_end = datetime(year, month + 1, 1, 0, 0, 0)

            elif hour is None:
                period_start = datetime(year, month, day, 0, 0, 0)
                period_end = period_start + timedelta(days=1)

            else:
                period_start = datetime(year, month, day, hour, 0, 0)
                period_end = period_start + timedelta(hours=1)

        except ValueError:
            return Response(
                {"error": "year, month, day and hour must define a valid time period"},
                status=status.HTTP_400_BAD_REQUEST
            )
        source_res, source_col = self.SOURCE_RESOLUTIONS[target_res]
        query = f"""
            WITH station_period_avg AS (
                SELECT
                    ssd.id AS station_id,
                    ssd.{source_col} AS hex_id,
                    AVG(ssd.{value_col}) AS station_avg_value
                FROM sensor_stations_data ssd
                WHERE ssd.datetime >= %s
                  AND ssd.datetime < %s
                  AND ssd.{value_col} IS NOT NULL
                  AND ssd.{source_col} IS NOT NULL
                GROUP BY ssd.id, ssd.{source_col}
            )
            SELECT
                hex_id,
                AVG(station_avg_value) AS avg_value,
                COUNT(*) AS cnt,
                ARRAY_AGG(station_id) AS station_ids
            FROM station_period_avg
            GROUP BY hex_id
        """
        params = [period_start, period_end]
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        h3_rows, station_ids = extract_h3_rows_and_station_ids(
            rows,
            source="sensor"
        )
        aggregated = aggregate_to_target_resolution(
            h3_rows,
            source_res,
            target_res
        )
        features = []
        for hex_id, avg_temp, cnt in aggregated:
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "parameter": parameter,
                    "avg_value": round(float(avg_temp), 2),
                    "count": int(cnt)
                }
            })
        return Response({
            "type": "FeatureCollection",
            "features": features,
            "station_ids": station_ids
        })

class SensorStationsValidDataH3AggregationView(APIView):
    SOURCE_RESOLUTIONS = {
        3: (5, "h3_res_5"),
        4: (5, "h3_res_5"),
        5: (5, "h3_res_5"),
        6: (7, "h3_res_7"),
        7: (7, "h3_res_7"),
        8: (9, "h3_res_9"),
        9: (9, "h3_res_9"),
    }
    DATA_VAL_FLAG_MAP = {
        1: "flag_0",
        2: "flag_1",
        3: "flag_2",
        4: "flag_3",
        5: "flag_4",
        6: "flag_5",
    }
    def get(self, request):
        return Response(
            {"detail": "Use POST for this endpoint."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )
    def post(self, request):
        try:
            target_res = int(request.data.get("resolution"))
        except (TypeError, ValueError):
            return Response(
                {"error": "resolution must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if target_res not in self.SOURCE_RESOLUTIONS:
            return Response(
                {"error": "Resolution must be between 3 and 9"},
                status=status.HTTP_400_BAD_REQUEST
            )
        year = request.data.get("year")
        month = request.data.get("month")
        day = request.data.get("day")
        hour = request.data.get("hour")
        min_confidence = request.data.get("min_confidence", 1)
        parameter = request.data.get("parameter", "temperature")
        data_val = request.data.get("data_val")
        if parameter not in ("temperature", "humidity", "pressure"):
            return Response(
                {"error": "parameter must be one of: temperature, humidity, pressure"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            min_confidence = int(min_confidence)
        except (TypeError, ValueError):
            return Response(
                {"error": "min_confidence must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if min_confidence < 1:
            return Response(
                {"error": "min_confidence must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if year in (None, "", "null"):
            return Response(
                {"error": "year is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            year = int(year)
        except (TypeError, ValueError):
            return Response(
                {"error": "year must be integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        month = None if month in (None, "", "null") else month
        day = None if day in (None, "", "null") else day
        hour = None if hour in (None, "", "null") else hour
        try:
            if month is not None:
                month = int(month)
                if month < 1 or month > 12:
                    return Response(
                        {"error": "month must be from 1 to 12"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            if day is not None:
                if month is None:
                    return Response(
                        {"error": "month is required when day is provided"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                day = int(day)

            if hour is not None:
                if day is None:
                    return Response(
                        {"error": "day is required when hour is provided"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                hour = int(hour)

                if hour < 0 or hour > 23:
                    return Response(
                        {"error": "hour must be from 0 to 23"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
        except (TypeError, ValueError):
            return Response(
                {"error": "month, day and hour must be valid integers"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            if month is None:
                period_start = datetime(year, 1, 1, 0, 0, 0)
                period_end = datetime(year + 1, 1, 1, 0, 0, 0)

            elif day is None:
                period_start = datetime(year, month, 1, 0, 0, 0)

                if month == 12:
                    period_end = datetime(year + 1, 1, 1, 0, 0, 0)
                else:
                    period_end = datetime(year, month + 1, 1, 0, 0, 0)

            elif hour is None:
                period_start = datetime(year, month, day, 0, 0, 0)
                period_end = period_start + timedelta(days=1)

            else:
                period_start = datetime(year, month, day, hour, 0, 0)
                period_end = period_start + timedelta(hours=1)
        except ValueError:
            return Response(
                {"error": "year, month, day and hour must define a valid time period"},
                status=status.HTTP_400_BAD_REQUEST
            )
        source_res, source_col = self.SOURCE_RESOLUTIONS[target_res]
        value_col = parameter
        data_val_filter = ""
        if parameter == "temperature":
            if data_val not in (None, "", "null"):
                try:
                    data_val = int(data_val)
                except (TypeError, ValueError):
                    return Response(
                        {"error": "data_val must be integer 1..5"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                flag_col = self.DATA_VAL_FLAG_MAP.get(data_val)
                if not flag_col:
                    return Response(
                        {"error": "data_val must be integer 1..4"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                data_val_filter = f"AND ssd.{flag_col} = 1"
        query = f"""
            WITH station_period_avg AS (
                SELECT
                    ssd.id AS station_id,
                    ssd.{source_col} AS hex_id,
                    AVG(ssd.{value_col}) AS station_avg_value
                FROM sensor_stations_data_valid ssd
                WHERE ssd.datetime >= %s
                  AND ssd.datetime < %s
                  AND ssd.{value_col} IS NOT NULL
                  AND ssd.{source_col} IS NOT NULL
                  {data_val_filter}
                GROUP BY ssd.id, ssd.{source_col}
            )
            SELECT
                hex_id,
                AVG(station_avg_value) AS avg_value,
                COUNT(*) AS cnt,
                ARRAY_AGG(station_id) AS station_ids
            FROM station_period_avg
            GROUP BY hex_id
            HAVING COUNT(*) >= %s
        """
        params = [period_start, period_end, min_confidence]
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        h3_rows, station_ids = extract_h3_rows_and_station_ids(
            rows,
            source="sensor"
        )
        aggregated = aggregate_to_target_resolution(
            h3_rows,
            source_res,
            target_res
        )
        features = []
        for hex_id, avg_value, cnt in aggregated:
            if hex_id is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "parameter": parameter,
                    "avg_value": round(float(avg_value), 2),
                    "count": int(cnt),
                    "data_val": data_val if parameter == "temperature" else None,
                    "source_table": "sensor_stations_data_valid",
                }
            })
        return Response({
            "type": "FeatureCollection",
            "features": features,
            "station_ids": station_ids
        })