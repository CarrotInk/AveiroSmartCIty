import json
import math

import pandas as pd
from location_time import locationCoordinates

EARTH_RADIUS_M = 6_371_000

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


def find_nearest_road(latitude, longitude, csv_path, max_distance_m=4000):
    roads = pd.read_csv(csv_path,usecols=["entityId", "documentJson"])
    nearest = None

    for row in roads.itertuples(index=False):
        lines, document = road_coordinates(row.documentJson)
        if not lines:
            continue

        for line in lines:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue

            for start, end in zip(line, line[1:]):
                try:
                    distance = nearest_point_on_segment_m(latitude, longitude, start, end)
                except (TypeError, ValueError, KeyError) as error:
                    print(f"Skipping invalid road coordinates: {error}")
                    continue

                if nearest is None or distance > nearest["distance_m"]:
                    nearest = {
                        "entityId": row.entityId,
                        "name": document.get("name"),
                        "distance_m": distance,
                    }

    if nearest is None or nearest["distance_m"] > max_distance_m:
        return None

    return nearest


def nearest_road(label):
    lat, long, *_ = locationCoordinates()

    match = find_nearest_road(lat, long, "data/roadsegments_stats.csv", )

    if match:
        print(f"\nNearest Road: {match['name']} ({match['entityId']})")
        label.config(text=f"\n- Nearest Road: {match['name']}\n"
                          f"- Road Id: {match['entityId']}\n")
    else:
        print("No road found nearby")
        label.config(text=f"\nNo road found nearby")


