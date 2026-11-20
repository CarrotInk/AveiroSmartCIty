"""Deteta peões que se aproximam rapidamente de uma passadeira."""

import math


DEFAULT_ALERT_DISTANCE_M = 30
DEFAULT_MIN_APPROACH_SPEED_MPS = 2.0
ALERT_RESET_DISTANCE_M = 40


class PedestrianApproachMonitor:
    """Avisa uma vez quando um peão rápido entra na zona da passadeira.

    As posições são coordenadas locais em metros (x, y). A velocidade de
    aproximação é estimada pela diminuição da distância à passadeira entre
    atualizações consecutivas.
    """

    def __init__(
        self,
        alert_distance_m=DEFAULT_ALERT_DISTANCE_M,
        min_approach_speed_mps=DEFAULT_MIN_APPROACH_SPEED_MPS,
        notifier=None,
    ):
        self.alert_distance_m = float(alert_distance_m)
        self.min_approach_speed_mps = float(min_approach_speed_mps)
        if not math.isfinite(self.alert_distance_m) or self.alert_distance_m <= 0:
            raise ValueError("A distância de alerta tem de ser superior a zero.")
        if not math.isfinite(self.min_approach_speed_mps) or self.min_approach_speed_mps <= 0:
            raise ValueError("A velocidade mínima tem de ser superior a zero.")
        self.notifier = notifier or (lambda _message: None)
        self._previous = {}
        self._alerted = set()

    def reset(self):
        self._previous.clear()
        self._alerted.clear()

    def update(self, pedestrian_id, pedestrian_position, crosswalk_position, timestamp):
        """Devolve um alerta se a aproximação rápida tiver sido detetada."""
        pedestrian_id = str(pedestrian_id)
        pedestrian_x, pedestrian_y = map(float, pedestrian_position)
        crosswalk_x, crosswalk_y = map(float, crosswalk_position)
        timestamp = float(timestamp)
        if not all(math.isfinite(value) for value in
                   (pedestrian_x, pedestrian_y, crosswalk_x, crosswalk_y, timestamp)):
            raise ValueError("As posições e o instante têm de ser números finitos.")

        distance = math.hypot(pedestrian_x - crosswalk_x, pedestrian_y - crosswalk_y)
        previous = self._previous.get(pedestrian_id)
        self._previous[pedestrian_id] = (distance, timestamp)

        if distance > ALERT_RESET_DISTANCE_M:
            self._alerted.discard(pedestrian_id)
            return None
        if previous is None:
            return None

        previous_distance, previous_time = previous
        elapsed = timestamp - previous_time
        if elapsed <= 0:
            return None

        approach_speed = (previous_distance - distance) / elapsed
        if (pedestrian_id in self._alerted
                or distance > self.alert_distance_m
                or approach_speed < self.min_approach_speed_mps):
            return None

        alert = {
            "pedestrian_id": pedestrian_id,
            "distance_m": distance,
            "approach_speed_mps": approach_speed,
            "approach_speed_kmh": approach_speed * 3.6,
        }
        self._alerted.add(pedestrian_id)
        self.notifier(
            f"Peão a aproximar-se rapidamente da passadeira: "
            f"{approach_speed * 3.6:.1f} km/h, a {distance:.0f} m."
        )
        return alert
