from datetime import datetime, timedelta
import requests
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.db import connection
import h3
from .models import NetatmoStation, StationData
from .serializers import StationDataSerializer
from collections import defaultdict

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

class StationDataListView(generics.ListAPIView):
    serializer_class = StationDataSerializer

    def get_queryset(self):
        device_id = self.kwargs.get('device_id')
        month = self.request.GET.get('month')

        qs = StationData.objects.filter(device__device_id=device_id)
        if month:
            try:
                month = int(month)
                qs = qs.filter(datetime__month=month)
            except ValueError:
                pass
        return qs.order_by('-datetime')

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

        # Для station_month_avg день не поддерживается
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
        temp_col = self.DATA_VAL_TEMP_COLUMN_MAP[data_val]

        params = [year, month]

        query = f"""
            SELECT
                {source_col} AS hex_id,
                AVG({temp_col}) AS avg_temp,
                COUNT(*) AS cnt
            FROM station_month_avg
            WHERE year_num = %s
              AND month_num = %s
              AND {source_col} IS NOT NULL
              AND {temp_col} IS NOT NULL
            GROUP BY {source_col}
        """

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        if source_res == target_res:
            aggregated = [(hex_id, avg_temp, cnt) for hex_id, avg_temp, cnt in rows]
        else:
            groups = defaultdict(lambda: {"sum_temp": 0.0, "total_cnt": 0})
            for hex_id, avg_temp, cnt in rows:
                if hex_id is None:
                    continue

                parent = h3.cell_to_parent(hex_id, target_res)
                groups[parent]["sum_temp"] += float(avg_temp) * int(cnt)
                groups[parent]["total_cnt"] += int(cnt)

            aggregated = [
                (
                    parent,
                    groups[parent]["sum_temp"] / groups[parent]["total_cnt"],
                    groups[parent]["total_cnt"]
                )
                for parent in groups
            ]

        features = []
        for hex_id, avg_temp, cnt in aggregated:
            if hex_id is None:
                continue

            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "avg_temperature": round(float(avg_temp), 2),
                    "count": int(cnt)
                }
            })

        return Response({
            "type": "FeatureCollection",
            "features": features
        })

class SensorCommunityStationsView(APIView):
    MIN_LAT = 55.45
    MIN_LON = 36.80
    MAX_LAT = 56.05
    MAX_LON = 38.10

    URL = f"https://data.sensor.community/airrohr/v1/filter/box={MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON}"

    def get(self, request):
        try:
            response = requests.get(self.URL, timeout=60)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            return Response(
                {"error": f"Failed to fetch Sensor.Community: {e}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        features = []

        for item in data:
            try:
                lat = float(item["location"]["latitude"])
                lon = float(item["location"]["longitude"])
                sensor_id = item["sensor"]["id"]
                location_id = item["location"]["id"]
            except (KeyError, TypeError, ValueError):
                continue

            temperature = None
            for v in item.get("sensordatavalues", []):
                if v.get("value_type") == "temperature":
                    try:
                        temperature = float(v.get("value"))
                    except (TypeError, ValueError):
                        temperature = None
                    break

            if temperature is None:
                continue

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "properties": {
                    "sensor_id": sensor_id,
                    "location_id": location_id,
                    "temperature": temperature
                }
            })

        geojson = {
            "type": "FeatureCollection",
            "features": features
        }

        return Response(geojson)

class SensorCommunityH3AggregationView(APIView):
    MIN_LAT = 55.45
    MIN_LON = 36.80
    MAX_LAT = 56.05
    MAX_LON = 38.10

    URL = f"https://data.sensor.community/airrohr/v1/filter/box={MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON}"

    SOURCE_RESOLUTIONS = {
        3: 5,
        4: 5,
        5: 5,
        6: 7,
        7: 7,
        8: 9,
        9: 9,
    }

    def post(self, request):
        try:
            target_res = int(request.data.get("resolution"))
        except (TypeError, ValueError):
            return Response({"error": "resolution must be integer"}, status=status.HTTP_400_BAD_REQUEST)

        if target_res not in self.SOURCE_RESOLUTIONS:
            return Response({"error": "Resolution must be between 3 and 9"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            limit = int(request.data.get("limit", 500))
        except (TypeError, ValueError):
            return Response({"error": "limit must be integer"}, status=status.HTTP_400_BAD_REQUEST)

        source_res = self.SOURCE_RESOLUTIONS[target_res]
        try:
            r = requests.get(self.URL, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return Response({"error": f"Failed to fetch Sensor.Community: {e}"}, status=status.HTTP_502_BAD_GATEWAY)

        point_bins = []
        for item in data:
            if len(point_bins) >= limit:
                break

            try:
                lat = float(item["location"]["latitude"])
                lon = float(item["location"]["longitude"])
            except (KeyError, ValueError, TypeError):
                continue

            temp = None
            for v in item.get("sensordatavalues", []):
                if v.get("value_type") == "temperature":
                    try:
                        temp = float(v.get("value"))
                    except (TypeError, ValueError):
                        temp = None
                    break

            if temp is None:
                continue

            try:
                source_hex = h3.latlng_to_cell(lat, lon, source_res)
            except Exception:
                continue

            point_bins.append((source_hex, temp))

        if not point_bins:
            return Response({"type": "FeatureCollection", "features": []})

        if source_res == target_res:
            agg = defaultdict(lambda: {"sum": 0.0, "cnt": 0})
            for hx, t in point_bins:
                agg[hx]["sum"] += t
                agg[hx]["cnt"] += 1

            aggregated = [(hx, agg[hx]["sum"] / agg[hx]["cnt"], agg[hx]["cnt"]) for hx in agg]
        else:
            agg = defaultdict(lambda: {"sum": 0.0, "cnt": 0})
            for hx, t in point_bins:
                try:
                    parent = h3.cell_to_parent(hx, target_res)
                except Exception:
                    continue
                agg[parent]["sum"] += t
                agg[parent]["cnt"] += 1

            aggregated = [(hx, agg[hx]["sum"] / agg[hx]["cnt"], agg[hx]["cnt"]) for hx in agg]

        features = []
        for hex_id, avg_temp, cnt in aggregated:
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "avg_temperature": round(avg_temp, 2),
                    "count": cnt
                }
            })

        return Response({"type": "FeatureCollection", "features": features})

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
                    AVG(sd.temperature) AS avg_temp,
                    COUNT(*) AS cnt
                FROM stations_data sd
                WHERE sd.datetime = %s
                  AND sd.temperature IS NOT NULL
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
                        AVG(sd.temperature) AS station_avg_temp
                    FROM stations_data sd
                    WHERE sd.datetime >= %s
                      AND sd.datetime < %s
                      AND sd.temperature IS NOT NULL
                      AND sd.{source_col} IS NOT NULL
                      {data_val_filter}
                    GROUP BY sd.id, sd.{source_col}
                )
                SELECT
                    hex_id,
                    AVG(station_avg_temp) AS avg_temp,
                    COUNT(*) AS cnt
                FROM station_day_avg
                GROUP BY hex_id
            """

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        if source_res == target_res:
            aggregated = [(hex_id, avg_temp, cnt) for hex_id, avg_temp, cnt in rows]
        else:
            groups = defaultdict(lambda: {"sum_temp": 0.0, "total_cnt": 0})
            for hex_id, avg_temp, cnt in rows:
                if hex_id is None:
                    continue

                parent = h3.cell_to_parent(hex_id, target_res)
                groups[parent]["sum_temp"] += float(avg_temp) * int(cnt)
                groups[parent]["total_cnt"] += int(cnt)

            aggregated = [
                (
                    parent,
                    groups[parent]["sum_temp"] / groups[parent]["total_cnt"],
                    groups[parent]["total_cnt"]
                )
                for parent in groups
            ]

        features = []
        for hex_id, avg_temp, cnt in aggregated:
            if hex_id is None:
                continue

            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "avg_temperature": round(float(avg_temp), 2),
                    "count": int(cnt)
                }
            })

        return Response({
            "type": "FeatureCollection",
            "features": features
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

        query = f"""
            WITH station_period_avg AS (
                SELECT
                    ssd.id AS station_id,
                    ssd.{source_col} AS hex_id,
                    AVG(ssd.temperature) AS station_avg_temp
                FROM sensor_stations_data ssd
                WHERE ssd.datetime >= %s
                  AND ssd.datetime < %s
                  AND ssd.temperature IS NOT NULL
                  AND ssd.{source_col} IS NOT NULL
                GROUP BY ssd.id, ssd.{source_col}
            )
            SELECT
                hex_id,
                AVG(station_avg_temp) AS avg_temp,
                COUNT(*) AS cnt
            FROM station_period_avg
            GROUP BY hex_id
        """

        params = [period_start, period_end]

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        if source_res == target_res:
            aggregated = [
                (hex_id, avg_temp, cnt)
                for hex_id, avg_temp, cnt in rows
                if hex_id is not None
            ]
        else:
            groups = defaultdict(lambda: {"sum_temp": 0.0, "total_cnt": 0})

            for hex_id, avg_temp, cnt in rows:
                if hex_id is None:
                    continue

                try:
                    parent = h3.cell_to_parent(str(hex_id), target_res)
                except Exception:
                    continue

                groups[parent]["sum_temp"] += float(avg_temp) * int(cnt)
                groups[parent]["total_cnt"] += int(cnt)

            aggregated = [
                (
                    parent,
                    groups[parent]["sum_temp"] / groups[parent]["total_cnt"],
                    groups[parent]["total_cnt"]
                )
                for parent in groups
                if groups[parent]["total_cnt"] > 0
            ]

        features = []

        for hex_id, avg_temp, cnt in aggregated:
            features.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "hex_id": str(hex_id),
                    "avg_temperature": round(float(avg_temp), 2),
                    "count": int(cnt)
                }
            })

        return Response({
            "type": "FeatureCollection",
            "features": features
        })