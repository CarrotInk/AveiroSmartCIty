import math
import re
import unicodedata
from tkinter import messagebox


EARTH_RADIUS_M = 6_371_000
DEFAULT_ALERT_RADIUS_M = 1_000
PRIORITY_TERMS = ("ambulance", "ambulancia", "police", "policia", "emergency")
VEHICLE_TYPE_FIELDS = ("vehicleType", "vehicleCategory", "category", "entityType", "type")
LOCATION_FIELDS = ("location", "position", "coordinates")


def _unwrap(value):
    """Extrai valores de atributos NGSI e números Extended JSON."""
    while isinstance(value, dict):
        if "value" in value:
            value = value["value"]
        elif "$numberDouble" in value:
            value = value["$numberDouble"]
        elif "$numberInt" in value:
            value = value["$numberInt"]
        elif "$numberLong" in value:
            value = value["$numberLong"]
        else:
            break
    return value


def _normalise(value):
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _vehicle_type(vehicle):
    for field in VEHICLE_TYPE_FIELDS:
        value = _unwrap(vehicle.get(field))
        if value is None:
            continue
        text = str(value)
        normalised = _normalise(text)
        if any(term in normalised for term in PRIORITY_TERMS):
            return text
    return None


def _coordinates(value):
    value = _unwrap(value)
    if isinstance(value, dict):
        value = value.get("coordinates", value)
        value = _unwrap(value)
        if isinstance(value, dict):
            if "longitude" in value and "latitude" in value:
                return float(_unwrap(value["latitude"])), float(_unwrap(value["longitude"]))
            if "lon" in value and "lat" in value:
                return float(_unwrap(value["lat"])), float(_unwrap(value["lon"]))
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(_unwrap(value[1])), float(_unwrap(value[0]))
    return None


def _vehicle_location(vehicle):
    for field in LOCATION_FIELDS:
        if field in vehicle:
            coordinates = _coordinates(vehicle[field])
            if coordinates is not None:
                return coordinates
    return None


def _distance_m(latitude_a, longitude_a, latitude_b, longitude_b):
    latitude_a, latitude_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = latitude_b - latitude_a
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(latitude_a) * math.cos(latitude_b) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, haversine)))


class PriorityVehicleMonitor:
    """Notifica quando um veículo prioritário entra no raio configurado.

    Cada veículo só gera um aviso enquanto permanecer no raio; poderá gerar
    outro depois de sair e voltar a entrar. As localizações podem ser GeoJSON
    (longitude, latitude), NGSI-LD ou ``lat``/``lon``.
    """

    def __init__(self, alert_radius_m=DEFAULT_ALERT_RADIUS_M, notifier=None):
        alert_radius_m = float(alert_radius_m)
        if not math.isfinite(alert_radius_m) or alert_radius_m <= 0:
            raise ValueError("O raio de alerta tem de ser superior a zero.")
        self.alert_radius_m = alert_radius_m
        self.notifier = notifier or self._show_notification
        self._nearby_vehicle_ids = set()

    def has_nearby_vehicle(self, vehicle_id=None):
        """Indica se existe algum veículo prioritário no raio, ou um ID específico."""
        if vehicle_id is None:
            return bool(self._nearby_vehicle_ids)
        return str(vehicle_id) in self._nearby_vehicle_ids

    @staticmethod
    def _show_notification(message):
        messagebox.showwarning("Veículo prioritário a aproximar-se", message)

    def update(self, vehicles, user_latitude, user_longitude):
        """Processa uma atualização e devolve os novos alertas como dicionários."""
        user_latitude, user_longitude = float(user_latitude), float(user_longitude)
        if not -90 <= user_latitude <= 90 or not -180 <= user_longitude <= 180:
            raise ValueError("As coordenadas do utilizador são inválidas.")

        nearby_ids = set()
        alerts = []
        for vehicle in vehicles:
            if not isinstance(vehicle, dict):
                continue
            kind = _vehicle_type(vehicle)
            location = _vehicle_location(vehicle)
            if kind is None or location is None:
                continue

            latitude, longitude = location
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue
            distance = _distance_m(user_latitude, user_longitude, latitude, longitude)
            if distance > self.alert_radius_m:
                continue

            vehicle_id = str(vehicle.get("id") or vehicle.get("entityId") or f"{kind}:unknown")
            nearby_ids.add(vehicle_id)
            if vehicle_id in self._nearby_vehicle_ids:
                continue

            alert = {
                "vehicle_id": vehicle_id,
                "vehicle_type": kind,
                "distance_m": round(distance),
            }
            alerts.append(alert)
            self.notifier(
                f"Um veículo prioritário ({kind}) está a cerca de "
                f"{alert['distance_m']} m de si."
            )

        self._nearby_vehicle_ids = nearby_ids
        return alerts
