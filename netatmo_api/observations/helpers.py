import h3
from collections import defaultdict


def extract_h3_rows_and_station_ids(rows, source):
    h3_rows = []
    station_ids = set()

    for hex_id, avg_value, cnt, ids in rows:
        h3_rows.append((hex_id, avg_value, cnt))
        if ids:
            station_ids.update(ids)
    if source == "netatmo":
        station_ids = sorted(str(sid) for sid in station_ids)
    elif source == "sensor":
        station_ids = sorted(int(sid) for sid in station_ids)
    return h3_rows, station_ids


def aggregate_to_target_resolution(rows, source_res, target_res):
    if source_res == target_res:
        return [
            (hex_id, avg_value, cnt)
            for hex_id, avg_value, cnt in rows
            if hex_id is not None
        ]
    groups = defaultdict(lambda: {"sum_value": 0.0, "total_cnt": 0})
    for hex_id, avg_value, cnt in rows:
        if hex_id is None:
            continue
        try:
            parent = h3.cell_to_parent(hex_id, target_res)
        except Exception:
            continue
        groups[parent]["sum_value"] += float(avg_value) * int(cnt)
        groups[parent]["total_cnt"] += int(cnt)
    return [
        (
            parent,
            groups[parent]["sum_value"] / groups[parent]["total_cnt"],
            groups[parent]["total_cnt"]
        )
        for parent in groups
        if groups[parent]["total_cnt"] > 0
    ]