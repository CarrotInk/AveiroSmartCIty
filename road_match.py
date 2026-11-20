import csv
import json
import math
import os
import sys
from functools import lru_cache
from pathlib import Path

EARTH_RADIUS_M = 6_371_000


def build_compact_roads(source_path, output_path):
    """Stream the large source file into a compact one-row-per-road CSV."""
    seen_entity_ids = set()
    output_path = Path(output_path)
    temporary_path = output_path.with_name(output_path.name + ".tmp")

    try:
        with open(source_path, newline="", encoding="utf-8") as source:
            csv.field_size_limit(sys.maxsize)
            reader = csv.DictReader(source)
            with open(temporary_path, "w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(("entityId", "documentJson"))
                for row in reader:
                    entity_id = row.get("entityId")
                    document_json = row.get("documentJson")
                    if entity_id and entity_id not in seen_entity_ids and document_json:
                        seen_entity_ids.add(entity_id)
                        writer.writerow((entity_id, document_json))

        os.replace(temporary_path, output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat = lat2 - lat1
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2+ math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _number(value):
    if isinstance(value, dict):
        for key in ("$numberDouble", "$numberInt", "$numberLong"):
            if key in value:
                return float(value[key])
    return float(value)


def _lon_lat(point):
    if isinstance(point, dict):
        if "coordinates" in point:
            return _lon_lat(point["coordinates"])
        if "longitude" in point and "latitude" in point:
            return _number(point["longitude"]), _number(point["latitude"])
        if "lon" in point and "lat" in point:
            return _number(point["lon"]), _number(point["lat"])
        if "x" in point and "y" in point:
            return _number(point["x"]), _number(point["y"])
        raise ValueError(f"Unrecognized road coordinate format: {point!r}")

    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return _number(point[0]), _number(point[1])

    raise ValueError(f"Unrecognized road coordinate format: {point!r}")


def nearest_point_on_segment_m(latitude, longitude, start, end):
    start_lon, start_lat = _lon_lat(start)
    end_lon, end_lat = _lon_lat(end)

    latitude = float(latitude)
    longitude = float(longitude)
    latitude0 = math.radians(latitude)

    def project(lat, lon):
        x = EARTH_RADIUS_M * math.radians(lon - start_lon) * math.cos(latitude0)
        y = EARTH_RADIUS_M * math.radians(lat - start_lat)
        return x, y

    px, py = project(latitude, longitude)
    ax, ay = project(start_lat, start_lon)
    bx, by = project(end_lat, end_lon)

    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy

    if length_squared == 0:
        closest_x, closest_y = ax, ay
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / length_squared
        t = max(0.0, min(1.0, t))
        closest_x = ax + t * dx
        closest_y = ay + t * dy

    nearest_lon = start_lon + math.degrees(closest_x / (EARTH_RADIUS_M * math.cos(latitude0)))
    nearest_lat = start_lat + math.degrees(closest_y / EARTH_RADIUS_M)

    return haversine_m(latitude, longitude, nearest_lat, nearest_lon)


def road_coordinates(document_json):
    try:
        document = json.loads(document_json)
    except (TypeError, json.JSONDecodeError):
        return None, {}

    if not isinstance(document, dict):
        return None, {}

    location = document.get("location", {})

    if isinstance(location, dict):
        location = location.get("value", location)

    if not isinstance(location, dict):
        return None, document

    geometry_type = location.get("type")
    coordinates = location.get("coordinates")

    if geometry_type == "LineString" and coordinates:
        return [coordinates], document
    if geometry_type == "MultiLineString" and coordinates:
        return coordinates, document

    return None, document


@lru_cache(maxsize=1)
def _load_roads(csv_path):
    roads = []
    csv.field_size_limit(sys.maxsize)
    with open(csv_path, newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            lines, document = road_coordinates(row["documentJson"])
            if lines:
                roads.append((row["entityId"], document, lines))
    return roads


def find_nearest_road(
    latitude,
    longitude,
    csv_path="data/roads_unique.csv",
    max_distance_m=4000,
):
    csv_path = str(Path(csv_path).resolve())
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Compact road data not found: {csv_path}. "
            "Run road_match.py once to build it from data/roadsegments_stats.csv."
        )

    nearest = None

    for entity_id, document, lines in _load_roads(csv_path):
        for line in lines:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            for start, end in zip(line, line[1:]):
                try:
                    distance = nearest_point_on_segment_m(
                        latitude, longitude, start, end
                    )
                except (TypeError, ValueError, KeyError) as error:
                    print(f"Skipping invalid road coordinates: {error}")
                    continue

                if nearest is None or distance < nearest["distance_m"]:
                    nearest = {
                        "entityId": entity_id,
                        "name": document.get("name"),
                        "distance_m": distance,
                    }

    if nearest is None or nearest["distance_m"] > max_distance_m:
        return None
    return nearest


if __name__ == "__main__":
    build_compact_roads(
        "data/roadsegments_stats.csv",
        "data/roads_unique.csv",
    )
