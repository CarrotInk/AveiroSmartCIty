"""
priority_simulation - simulação em que um condutor é avisado de que uma ambulância se está a aproximar.

Usa o priority_vehicle_alert.py tal como está: a simulação só gera as posições (como faria o sistema de
localização de veículos) e chama PriorityVehicleMonitor.update(...) a cada passo; o aviso aparece quando a
ambulância entra no raio de alerta.

Cenário: a ambulância e o condutor andam pelo mesmo percurso real do mapa de Aveiro (RoadSegment.csv). O
condutor vai à frente a velocidade normal; a ambulância vem atrás, mais rápida. Quando entra no raio, o
condutor é avisado, encosta, a ambulância passa e ele retoma a marcha.

Uso a partir do main (abre numa janela por cima):
    import priority_simulation
    priority_simulation.open_window(self.root)

Uso direto (abre a janela; --headless só escreve a cronologia na consola):
    python priority_simulation.py
    python priority_simulation.py --from "Avenida Vasco Branco" --to "Rua Pedro Álvares Cabral" --radius 500
    python priority_simulation.py --headless
Só precisa de pandas e numpy (mais o tkinter para a janela).
"""
import argparse
import json
import math
import time

import numpy as np
import pandas as pd

import fastest_route
from priority_vehicle_alert import DEFAULT_ALERT_RADIUS_M, PriorityVehicleMonitor

# ----------------------------- configuração ---------------------------------
DEFAULT_FROM = "Avenida Doutor Lourenço Peixinho"
DEFAULT_TO = "Rua Pedro Álvares Cabral"
AMBULANCE_KMH = 70          # a ambulância vai acima do limite urbano (50 km/h), como em emergência
DRIVER_KMH = 35             # velocidade normal do condutor
BUS_KMH = 25                # autocarro à frente: veículo NÃO prioritário, nunca gera aviso
START_GAP_M = 1500          # distância inicial entre a ambulância (atrás) e o condutor (à frente)
REACTION_S = 2.0            # tempo que o condutor demora a reagir ao aviso
BRAKE_MPS2 = 3.0            # travagem ao encostar
ACCEL_MPS2 = 1.5            # aceleração ao retomar a marcha
CLEAR_M = 25                # o condutor só arranca quando a ambulância já vai 25 m à sua frente
AFTER_PASS_S = 40         # a simulação acaba 40 s depois de a ambulância passar (ou quando sai do raio)
PEDESTRIAN_ALERT_RADIUS_M = 500
PEDESTRIAN_LIGHT_INTERVAL_S = 5.0
BUS_OVERTAKE_WAIT_S = 5
BUS_CLEARANCE_M = 8
AMBULANCE_ID = "urn:ngsi-ld:Vehicle:ambulance-01"
BUS_ID = "urn:ngsi-ld:Vehicle:bus-12"


def _kmh(value):
    return value / 3.6


def _mmss(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


# ------------------------------ percurso ------------------------------------
class Route:
    """Linha poligonal (lon, lat) com distâncias acumuladas em metros."""

    def __init__(self, lonlat):
        pts = [p for i, p in enumerate(lonlat) if i == 0 or p != lonlat[i - 1]]
        arr = np.asarray(pts, float)
        self.lon0, self.lat0 = arr[:, 0].mean(), arr[:, 1].mean()
        self.kx = 111320 * math.cos(math.radians(self.lat0))
        self.ky = 110540
        self.xy = np.c_[(arr[:, 0] - self.lon0) * self.kx, (arr[:, 1] - self.lat0) * self.ky]
        self.cum = np.r_[0.0, np.cumsum(np.hypot(*np.diff(self.xy, axis=0).T))]
        self.length = float(self.cum[-1])

    def to_xy(self, lon, lat):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def xy_at(self, s):
        s = min(max(s, 0.0), self.length)
        i = min(int(np.searchsorted(self.cum, s, side="right")) - 1, len(self.cum) - 2)
        span = self.cum[i + 1] - self.cum[i]
        t = (s - self.cum[i]) / span if span > 0 else 0.0
        return tuple(self.xy[i] + t * (self.xy[i + 1] - self.xy[i]))

    def lonlat_at(self, s):
        x, y = self.xy_at(s)
        return self.lon0 + x / self.kx, self.lat0 + y / self.ky

    def direction_at(self, s):
        i = min(int(np.searchsorted(self.cum, min(max(s, 0.0), self.length), side="right")) - 1,
                len(self.cum) - 2)
        direction = self.xy[i + 1] - self.xy[i]
        length = np.hypot(*direction)
        return direction / length if length else np.array([1.0, 0.0])


def load_geometry(path=fastest_route.ROAD_REF_CSV):
    """segment_id -> lista de (lon, lat) com a geometria de cada segmento."""
    rs = pd.read_csv(path, usecols=["entityId", "location"])
    out = {}
    for sid, loc in zip(rs["entityId"], rs["location"]):
        try:
            out[sid] = [tuple(c[:2]) for c in json.loads(loc)["coordinates"]]
        except (ValueError, KeyError, TypeError):
            continue
    return out


def build_route(origin, destination, segments=None, geometry=None):
    """Percurso (Route) mais rápido pelo limite de velocidade entre duas ruas, a respeitar os sentidos;
    se não houver, ignora-os. Devolve (Route, nome_origem, nome_destino)."""
    segments = fastest_route.load_segments() if segments is None else segments
    geometry = load_geometry() if geometry is None else geometry
    src, src_name = fastest_route.find_street_segments(origin, segments)
    dst, dst_name = fastest_route.find_street_segments(destination, segments)

    edges = segments.copy()
    edges["cost_s"] = edges["length_m"] / edges["limit_mps"].fillna(_kmh(50))
    best = fastest_route._search(edges, src, dst, ignore_direction=False) \
        or fastest_route._search(edges, src, dst, ignore_direction=True)
    if best is None:
        raise fastest_route.StreetNotFound(f"Não há caminho entre '{src_name}' e '{dst_name}'.")

    points = []
    for sid, _, _, _, reverse in best[1]:
        line = geometry[sid][::-1] if reverse else geometry[sid]
        points.extend(line if not points else line[1:])
    return Route(points), src_name, dst_name


# ------------------------------ simulação -----------------------------------
class Simulation:
    """Simula a ambulância, um condutor e um autocarro com reação opcional aos avisos."""

    def __init__(self, route, radius_m=DEFAULT_ALERT_RADIUS_M, driver_reacts=True,
                 start_gap_m=START_GAP_M, bus_reacts=False):
        self.route = route
        self.radius_m = float(radius_m)
        self.driver_reacts = driver_reacts
        self.bus_reacts = bus_reacts
        self.gap_m = self._initial_gap(start_gap_m)
        self.on_event = None                      # função(t, texto) opcional, usada pela janela
        self.events = []
        self.reset()

    def _initial_gap(self, wanted):
        """Afasta o condutor até a distância em linha reta (que é a que o alerta usa) ficar bem acima do raio:
        nas curvas, 1500 m de estrada podem ser menos de 1000 m a direito e o aviso saltava logo no início.
        Se nenhum ponto chegar lá, usa aquele onde a distância a direito é maior."""
        limit = self.route.length * 0.85
        ax, ay = self.route.xy_at(0)
        best_gap, best_dist = min(wanted, limit), -1.0
        gap = wanted
        while gap <= limit:
            dx, dy = self.route.xy_at(gap)
            dist = math.hypot(ax - dx, ay - dy)
            if dist >= self.radius_m * 1.3:
                return gap
            if dist > best_dist:
                best_gap, best_dist = gap, dist
            gap += 100
        return best_gap

    def reset(self):
        self.t = 0.0
        self.pass_t = None
        self.amb_s = 0.0
        self.crosswalk_s = self.route.length / 2
        self.pedestrian_signal = "verde"
        self.pedestrian_alerts = []
        self.bus_wait_remaining = 0.0
        self.bus_wait_count = 0
        self.bus_wait_done = False
        self.driver_s = min(self.gap_m, self.crosswalk_s)
        self.bus_s = min(self.gap_m + 250, self.crosswalk_s)
        self.bus_v = _kmh(BUS_KMH)
        self.bus_state = "a andar"
        self.bus_state_t = 0.0
        self.driver_v = _kmh(DRIVER_KMH)
        self.state = "a andar"            # a andar -> a reagir -> a travar -> parado -> a retomar
        self.state_t = 0.0
        self.status = "livre"             # livre -> aproxima-se -> passou
        self.alerts = []
        self.finished = False
        self.monitor = PriorityVehicleMonitor(self.radius_m, notifier=self._notify)   # sem memória de alertas
        self.bus_monitor = PriorityVehicleMonitor(self.radius_m, notifier=self._notify_bus)
        self.events.clear()
        self._log(f"Início: condutor a {_kmh_text(self.driver_v)}, ambulância {self.gap_m:.0f} m atrás.")

    # --- eventos ---
    def _log(self, text):
        self.events.append((self.t, text))
        if self.on_event:
            self.on_event(self.t, text)

    def _notify(self, message):                   # chamado pelo PriorityVehicleMonitor
        self._log(f"AVISO ao condutor: {message}")

    def _notify_bus(self, message):
        if self.bus_reacts and self.bus_state == "a andar":
            self._log(f"AVISO ao condutor do autocarro: {message}")

    # --- dados que o sistema de localização enviaria ---
    def vehicles(self):
        def point(s):
            lon, lat = self.route.lonlat_at(s)
            return {"type": "Point", "coordinates": [lon, lat]}
        return [
            {"id": AMBULANCE_ID, "vehicleType": "Ambulância", "location": point(self.amb_s)},
            {"id": BUS_ID, "vehicleType": "Autocarro", "location": point(self.bus_s)},
        ]

    def driver_position(self):
        lon, lat = self.route.lonlat_at(self.driver_s)
        return lat, lon

    def distance_m(self):
        ax, ay = self.route.xy_at(self.amb_s)
        dx, dy = self.route.xy_at(self.driver_s)
        return math.hypot(ax - dx, ay - dy)

    def crosswalk_distance_m(self):
        ax, ay = self.route.xy_at(self.amb_s)
        cx, cy = self.route.xy_at(self.crosswalk_s)
        return math.hypot(ax - cx, ay - cy)

    def update_pedestrian_signal(self, pedestrian_distance):
        """Mantém alternância normal e força vermelho a menos de 500 m."""
        if self.amb_s >= self.crosswalk_s:
            if self.pedestrian_signal != "verde":
                self.pedestrian_signal = "verde"
                self._log("A ambulância passou a passadeira. Semáforo para peões volta a VERDE.")
            return

        if pedestrian_distance <= PEDESTRIAN_ALERT_RADIUS_M:
            if self.pedestrian_signal != "vermelho":
                self.pedestrian_signal = "vermelho"
                self.pedestrian_alerts.append((self.t, pedestrian_distance))
                self._log(
                    f"AVISO aos peões: ambulância a {pedestrian_distance:.0f} m. "
                    "Semáforo para peões fica VERMELHO; aguarde antes de atravessar."
                )
            return

        alternating_signal = "vermelho" if int(self.t // PEDESTRIAN_LIGHT_INTERVAL_S) % 2 else "verde"
        if self.pedestrian_signal != alternating_signal:
            self.pedestrian_signal = alternating_signal

    # --- um passo ---
    def step(self, dt):
        if self.finished:
            return
        self.t += dt
        pedestrian_distance = self.crosswalk_distance_m()
        self.update_pedestrian_signal(pedestrian_distance)

        if self.bus_wait_remaining > 0:
            self.bus_wait_remaining = max(0.0, self.bus_wait_remaining - dt)
            self.amb_s = max(0.0, self.bus_s - BUS_CLEARANCE_M)
            if self.bus_wait_remaining == 0:
                self.bus_wait_done = True
                self._log("A ambulância pode agora ultrapassar o autocarro.")
        else:
            next_amb_s = self._advance_road_vehicle(self.amb_s, _kmh(AMBULANCE_KMH), dt)
            bus_following_limit = max(0.0, self.bus_s - BUS_CLEARANCE_M)
            if (not self.bus_wait_done
                    and next_amb_s >= bus_following_limit
                    and self.amb_s < bus_following_limit):
                self.bus_wait_remaining = BUS_OVERTAKE_WAIT_S
                self.bus_wait_count += 1
                self.amb_s = bus_following_limit
                self._log(
                    f"A ambulância alcançou o autocarro e espera "
                    f"{BUS_OVERTAKE_WAIT_S} segundos antes de o ultrapassar."
                )
            else:
                self.amb_s = next_amb_s
        self.update_pedestrian_signal(self.crosswalk_distance_m())

        self._drive_driver(dt)
        bus_lat, bus_lon = self.route.lonlat_at(self.bus_s)[::-1]
        for alert in self.bus_monitor.update(self.vehicles(), bus_lat, bus_lon):
            if self.bus_reacts and self.bus_state == "a andar":
                self.bus_state, self.bus_state_t = "a reagir", 0.0
                self._log("O condutor do autocarro recebe o aviso e prepara-se para encostar.")
        self._drive_bus(dt)

        lat, lon = self.driver_position()
        for alert in self.monitor.update(self.vehicles(), lat, lon):
            self.alerts.append((self.t, alert))
            self.status = "aproxima-se"
            if self.driver_reacts and self.state == "a andar":
                self._set_state("a reagir")

        if not self.monitor.has_nearby_vehicle(AMBULANCE_ID) and self.status == "aproxima-se":
            self.status = "livre"

        ahead = self.amb_s > self.driver_s
        if self.status == "aproxima-se" and ahead:
            self.status, self.pass_t = "passou", self.t
            self._log("A ambulância passou pelo condutor.")
        far_enough = self.status == "passou" and (self.distance_m() > self.radius_m or self.t - self.pass_t > AFTER_PASS_S)
        if far_enough or self.amb_s >= self.route.length:
            self.finished = True
            self._log("Simulação concluída.")

    def _set_state(self, state):
        self.state, self.state_t = state, 0.0
        text = {"a reagir": f"O condutor vê o aviso e prepara-se para encostar ({REACTION_S:.0f} s de reação).",
                "a travar": "O condutor encosta e trava.",
                "parado": "O condutor está parado; a ambulância pode passar.",
                "a retomar": "A ambulância já passou: o condutor retoma a marcha."}.get(state)
        if text:
            self._log(text)

    def _drive_driver(self, dt):
        cruise = _kmh(DRIVER_KMH)
        self.state_t += dt
        if self.state == "a reagir" and self.state_t >= REACTION_S:
            self._set_state("a travar")
        elif self.state == "a travar":
            self.driver_v = max(0.0, self.driver_v - BRAKE_MPS2 * dt)
            if self.driver_v == 0:
                self._set_state("parado")
        elif self.state == "parado" and self.amb_s > self.driver_s + CLEAR_M:
            self._set_state("a retomar")
        elif self.state == "a retomar":
            self.driver_v = min(cruise, self.driver_v + ACCEL_MPS2 * dt)
            if self.driver_v >= cruise:
                self.state = "a andar"
        self.driver_s = self._advance_road_vehicle(self.driver_s, self.driver_v, dt)

    def _drive_bus(self, dt):
        self.bus_state_t += dt
        if self.bus_state == "a reagir" and self.bus_state_t >= REACTION_S:
            self.bus_state, self.bus_state_t = "a travar", 0.0
            self._log("O autocarro começa a encostar para deixar a ambulância passar.")
        elif self.bus_state == "a travar":
            self.bus_v = max(0.0, self.bus_v - BRAKE_MPS2 * dt)
            if self.bus_v == 0:
                self.bus_state = "encostado"
                self.bus_state_t = 0.0
                self._log("O autocarro está encostado.")
        elif self.bus_state == "encostado" and self.amb_s > self.bus_s + CLEAR_M:
            self.bus_state, self.bus_state_t = "a retomar", 0.0
            self._log("A ambulância passou; o autocarro retoma a marcha.")
        elif self.bus_state == "a retomar":
            self.bus_v = min(_kmh(BUS_KMH), self.bus_v + ACCEL_MPS2 * dt)
            if self.bus_v >= _kmh(BUS_KMH):
                self.bus_state = "a andar"

        if self.bus_state != "encostado":
            self.bus_s = self._advance_road_vehicle(self.bus_s, self.bus_v, dt)

    def _advance_road_vehicle(self, position, speed, dt):
        next_position = min(position + speed * dt, self.route.length)
        if (self.pedestrian_signal == "verde"
                and position <= self.crosswalk_s
                and next_position > self.crosswalk_s):
            return self.crosswalk_s
        return next_position


def _kmh_text(mps):
    return f"{mps * 3.6:.0f} km/h"


# ------------------------------ janela --------------------------------------
class SimulationWindow:
    BG, CARD, NAVY, BLUE, MUTED = "#F4F7FB", "#FFFFFF", "#172B4D", "#2463EB", "#667085"
    GREEN, RED, AMBER = "#16A34A", "#DC2626", "#F59E0B"

    def __init__(self, parent=None, origin=DEFAULT_FROM, destination=DEFAULT_TO, radius_m=DEFAULT_ALERT_RADIUS_M):
        import tkinter as tk
        from tkinter import messagebox, ttk
        self.tk, self.ttk, self.messagebox = tk, ttk, messagebox
        self.own_root = parent is None
        self.win = tk.Tk() if parent is None else tk.Toplevel(parent)
        self.win.title("Simulação - veículo prioritário")
        self.win.geometry("1100x680")
        self.win.minsize(900, 600)
        self.win.configure(bg=self.BG)
        self.alive, self.running, self.sim = True, False, None
        self.win.bind("<Destroy>", lambda e: e.widget is self.win and setattr(self, "alive", False))

        self.segments = fastest_route.load_segments()
        self.geometry = load_geometry()
        self.streets = fastest_route.list_streets(self.segments)
        self.radius_var = tk.StringVar(value=str(int(radius_m)))
        self.speed_var = tk.StringVar(value="5x")
        self.reacts_var = tk.BooleanVar(value=True)
        self.bus_reacts_var = tk.BooleanVar(value=False)
        self.from_var, self.to_var = tk.StringVar(value=origin), tk.StringVar(value=destination)
        self.build_ui()
        self.load_scenario(origin, destination)
        self.tick()

    # --- interface ---
    def build_ui(self):
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.win)
        style.theme_use("clam")
        style.configure("Sim.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 8))

        left = tk.Frame(self.win, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(18, 8), pady=18)
        self.banner = tk.Label(left, text="", font=("Segoe UI", 14, "bold"), fg="white", bg=self.GREEN,
                               anchor="w", padx=16, pady=12)
        self.banner.pack(fill="x", pady=(0, 10))
        self.canvas = tk.Canvas(left, bg=self.CARD, highlightthickness=1, highlightbackground="#DDE5EF")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw_static())

        right = tk.Frame(self.win, bg=self.CARD, padx=16, pady=16, width=300)
        right.pack(side="right", fill="y", padx=(8, 18), pady=18)
        right.pack_propagate(False)

        def label(text, **kw):
            return tk.Label(right, text=text, bg=self.CARD, fg=kw.pop("fg", self.MUTED),
                            font=kw.pop("font", ("Segoe UI", 9, "bold")), anchor="w", **kw)

        label("Simulação", fg=self.NAVY, font=("Segoe UI", 14, "bold")).pack(fill="x")
        self.info = tk.StringVar()
        tk.Label(right, textvariable=self.info, bg=self.CARD, fg=self.NAVY, font=("Consolas", 10),
                 justify="left", anchor="w").pack(fill="x", pady=(6, 10))

        for text, var, values in (("RAIO DE ALERTA (m)", self.radius_var, ("300", "500", "1000", "1500")),
                                  ("VELOCIDADE DA SIMULAÇÃO", self.speed_var, ("1x", "2x", "5x", "10x", "20x"))):
            label(text).pack(fill="x")
            box = ttk.Combobox(right, textvariable=var, values=values, state="readonly")
            box.pack(fill="x", pady=(2, 8))
            if var is self.radius_var:
                box.bind("<<ComboboxSelected>>", lambda e: self.apply_settings())
        ttk.Checkbutton(right, text="O condutor encosta quando avisado", variable=self.reacts_var,
                        command=self.apply_settings).pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            right,
            text="O condutor do autocarro recebe avisos e encosta",
            variable=self.bus_reacts_var,
            command=self.apply_settings,
        ).pack(fill="x", pady=(0, 8))

        for text, var in (("ORIGEM DA AMBULÂNCIA", self.from_var), ("DESTINO", self.to_var)):
            label(text).pack(fill="x")
            ttk.Combobox(right, textvariable=var, values=self.streets).pack(fill="x", pady=(2, 8))
        ttk.Button(right, text="Novo percurso", style="Sim.TButton", command=self.new_route).pack(fill="x", pady=(0, 8))

        self.play_button = ttk.Button(right, text="Iniciar", style="Sim.TButton", command=self.toggle)
        self.play_button.pack(fill="x", pady=(4, 6))
        ttk.Button(right, text="Reiniciar", style="Sim.TButton", command=self.apply_settings).pack(fill="x")

        label("REGISTO").pack(fill="x", pady=(12, 2))
        self.log = tk.Listbox(right, font=("Segoe UI", 9), height=8, bd=0, highlightthickness=1,
                              highlightbackground="#DDE5EF")
        self.log.pack(fill="both", expand=True)

    # --- cenário ---
    def load_scenario(self, origin, destination):
        try:
            self.route, self.origin_name, self.destination_name = build_route(
                origin, destination, self.segments, self.geometry)
        except (ValueError, KeyError) as error:
            self.messagebox.showerror("Percurso", str(error), parent=self.win)
            return False
        self.network = [(np.array([self.route.to_xy(*p) for p in line])) for line in self.geometry.values()
                        if len(line) > 1]
        self.apply_settings()
        return True

    def new_route(self):
        self.load_scenario(self.from_var.get().strip(), self.to_var.get().strip())

    def apply_settings(self):
        self.running = False
        self.play_button.config(text="Iniciar")
        self.sim = Simulation(
            self.route,
            float(self.radius_var.get()),
            self.reacts_var.get(),
            bus_reacts=self.bus_reacts_var.get(),
        )
        self.log.delete(0, "end")
        self.sim.on_event = self.add_log
        self.log.insert("end", f"[{_mmss(0)}] {self.sim.events[0][1]}")
        self.redraw_static()
        self.refresh()

    def toggle(self):
        if self.sim.finished:
            self.apply_settings()
        self.running = not self.running
        self.play_button.config(text="Pausa" if self.running else "Continuar")

    def add_log(self, t, text):
        self.log.insert("end", f"[{_mmss(t)}] {text}")
        self.log.see("end")

    # --- animação ---
    def tick(self):
        if not self.alive:
            return
        if self.running and not self.sim.finished:
            budget = 0.04 * float(self.speed_var.get().rstrip("x"))
            while budget > 0 and not self.sim.finished:
                dt = min(0.25, budget)
                self.sim.step(dt)
                budget -= dt
            if self.sim.finished:
                self.running = False
                self.play_button.config(text="Repetir")
        self.refresh()
        self.win.after(40, self.tick)

    # --- desenho ---
    def view(self):
        """Transformação mundo (m) -> píxeis, ajustada ao percurso."""
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        xy = self.route.xy
        (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
        pad = 0.08 * max(x1 - x0, y1 - y0, 200)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        scale = min(w / (x1 - x0), h / (y1 - y0))
        ox, oy = (w - (x1 - x0) * scale) / 2, (h - (y1 - y0) * scale) / 2
        self._scale = scale
        return lambda x, y: (ox + (x - x0) * scale, h - (oy + (y - y0) * scale)), (x0, y0, x1, y1)

    def redraw_static(self):
        if not hasattr(self, "network"):
            return
        c = self.canvas
        c.delete("static")
        to_px, (x0, y0, x1, y1) = self.view()
        for line in self.network:
            if line[:, 0].max() < x0 or line[:, 0].min() > x1 or line[:, 1].max() < y0 or line[:, 1].min() > y1:
                continue
            c.create_line(*[v for p in line for v in to_px(*p)], fill="#D5DCE6", width=1, tags="static")
        c.create_line(*[v for p in self.route.xy for v in to_px(*p)], fill="#9DB8F2", width=5,
                      capstyle="round", joinstyle="round", tags="static")
        crosswalk_x, crosswalk_y = self.route.xy_at(self.sim.crosswalk_s) if self.sim else (0, 0)
        direction = self.route.direction_at(self.sim.crosswalk_s) if self.sim else np.array([1.0, 0.0])
        perpendicular = np.array([-direction[1], direction[0]])
        for offset in np.linspace(-12, 12, 5):
            stripe = np.array([crosswalk_x, crosswalk_y]) + offset * direction
            start = stripe - 9 * perpendicular
            end = stripe + 9 * perpendicular
            c.create_line(*to_px(*start), *to_px(*end), fill="white", width=3, tags="static")
        self.to_px = to_px
        c.tag_lower("static")

    def dot(self, s, color, text, r=9, below=False, lateral_offset=0):
        x, y = self.route.xy_at(s)
        if lateral_offset:
            direction = self.route.direction_at(s)
            x -= direction[1] * lateral_offset
            y += direction[0] * lateral_offset
        px, py = self.to_px(x, y)
        self.canvas.create_oval(px - r, py - r, px + r, py + r, fill=color, outline="white", width=2, tags="dyn")
        self.canvas.create_text(px, py + (r + 10 if below else -r - 10), text=text, fill=self.NAVY,
                                font=("Segoe UI", 9, "bold"), tags="dyn")

    def refresh(self):
        sim = self.sim
        if sim is None or not hasattr(self, "to_px"):
            return
        c = self.canvas
        c.delete("dyn")
        px, py = self.to_px(*self.route.xy_at(sim.driver_s))
        r = sim.radius_m * self._scale
        c.create_oval(px - r, py - r, px + r, py + r, outline=self.RED if sim.status == "aproxima-se" else "#9AA7BA",
                      dash=(6, 4), width=2, tags="dyn")
        bus_pulled_over = sim.bus_state == "encostado"
        self.dot(
            sim.bus_s,
            "#F59E0B" if bus_pulled_over else "#8A94A6",
            "Autocarro (encostado)" if bus_pulled_over else "Autocarro",
            r=6,
            lateral_offset=12 if bus_pulled_over else 0,
        )
        self.dot(sim.driver_s, self.BLUE, "Condutor", below=True)
        self.dot(sim.amb_s, self.RED, "Ambulância")
        crosswalk_x, crosswalk_y = self.route.xy_at(sim.crosswalk_s)
        perpendicular = np.array([-self.route.direction_at(sim.crosswalk_s)[1],
                                  self.route.direction_at(sim.crosswalk_s)[0]])
        for offset in (-7, 0, 7):
            pedestrian_x, pedestrian_y = self.to_px(
                *(np.array([crosswalk_x, crosswalk_y]) + offset * perpendicular))
            c.create_oval(pedestrian_x - 3, pedestrian_y - 3, pedestrian_x + 3, pedestrian_y + 3,
                          fill=self.NAVY, outline="white", tags="dyn")
            c.create_line(pedestrian_x, pedestrian_y + 3, pedestrian_x, pedestrian_y + 9,
                          fill=self.NAVY, width=2, tags="dyn")
        signal_x, signal_y = self.to_px(crosswalk_x + 18 * perpendicular[0],
                                        crosswalk_y + 18 * perpendicular[1])
        signal_color = self.RED if sim.pedestrian_signal == "vermelho" else self.GREEN
        c.create_oval(signal_x - 7, signal_y - 7, signal_x + 7, signal_y + 7,
                      fill=signal_color, outline="white", width=2, tags="dyn")
        c.create_text(signal_x, signal_y - 14, text="Peões", fill=self.NAVY,
                      font=("Segoe UI", 9, "bold"), tags="dyn")

        gap = sim.distance_m()
        pedestrian_distance = sim.crosswalk_distance_m()
        pedestrian_status = sim.pedestrian_signal.upper()
        bus_status = (f"espera {sim.bus_wait_remaining:.1f} s"
                      if sim.bus_wait_remaining > 0 else sim.bus_state)
        self.info.set(f"Tempo:        {_mmss(sim.t)}\nDistância:    {gap:6.0f} m\n"
                      f"Ambulância:   {AMBULANCE_KMH:6.0f} km/h\nCondutor:     {sim.driver_v * 3.6:6.0f} km/h\n"
                      f"Condutor:     {sim.state}\nAutocarro:    {bus_status}\n"
                      f"Passadeira:   {pedestrian_distance:6.0f} m\n"
                      f"Semáforo peões: {pedestrian_status}\nPercurso:     {sim.route.length / 1000:.1f} km")
        if sim.pedestrian_signal == "vermelho":
            blink = int(time.time() * 2) % 2 == 0
            self.banner.config(
                bg=self.RED if blink else "#991B1B",
                text=f"⚠  Peões avisados: ambulância a {pedestrian_distance:.0f} m. "
                     "Semáforo VERMELHO para peões.",
            )
        elif sim.status == "aproxima-se":
            blink = int(time.time() * 2) % 2 == 0
            self.banner.config(bg=self.RED if blink else "#991B1B",
                               text=f"⚠  Ambulância a aproximar-se: a {gap:.0f} m de si")
        elif sim.status == "passou":
            self.banner.config(bg=self.AMBER, text="A ambulância já passou. Pode retomar a marcha.")
        else:
            self.banner.config(bg=self.GREEN, text=f"Sem veículos prioritários no raio de {sim.radius_m:.0f} m")


def open_window(parent=None, **kwargs):
    """Abre a janela da simulação (por cima do main se passares a janela principal)."""
    window = SimulationWindow(parent, **kwargs)
    if window.own_root:
        window.win.mainloop()
    return window


# ------------------------------- consola ------------------------------------
def run_headless(origin, destination, radius_m, report_every_s=10, max_s=3600):
    route, o, d = build_route(origin, destination)
    sim = Simulation(route, radius_m)
    sim.on_event = lambda t, text: print(f"[{_mmss(t)}] {text}")
    print(f"Percurso da ambulância: {o} -> {d} ({route.length / 1000:.1f} km), raio de alerta {radius_m:.0f} m\n")
    last = -report_every_s
    while not sim.finished and sim.t < max_s:
        sim.step(0.25)
        if sim.t - last >= report_every_s:
            last = sim.t
            print(f"[{_mmss(sim.t)}]   distância {sim.distance_m():5.0f} m | condutor {sim.driver_v * 3.6:3.0f} km/h ({sim.state})")
    print(f"\nAvisos ao condutor: {len(sim.alerts)}")
    print(f"Avisos aos peões: {len(sim.pedestrian_alerts)}")
    return sim


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Simulação de aviso de veículo prioritário")
    ap.add_argument("--from", dest="origin", default=DEFAULT_FROM, help="rua de origem da ambulância")
    ap.add_argument("--to", dest="destination", default=DEFAULT_TO, help="rua de destino da ambulância")
    ap.add_argument("--radius", type=float, default=DEFAULT_ALERT_RADIUS_M, help="raio de alerta em metros")
    ap.add_argument("--headless", action="store_true", help="só escreve a cronologia na consola")
    a = ap.parse_args()
    if a.headless:
        run_headless(a.origin, a.destination, a.radius)
    else:
        open_window(None, origin=a.origin, destination=a.destination, radius_m=a.radius)
