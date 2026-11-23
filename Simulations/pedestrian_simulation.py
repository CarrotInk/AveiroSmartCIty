import argparse
import math
import time

from pedestrian_alert import (
    DEFAULT_MIN_APPROACH_SPEED_MPS,
    PedestrianApproachMonitor,
)

PEDESTRIAN_START_M = -50.0
CROSSWALK_Y_M = 0.0
CAR_SPEED_MPS = 3.0
CAR_START_X_M = -50.0
CAR_STOP_M = -2.5
PEDESTRIAN_CLEAR_Y_M = 16.0
APP_REACTION_S = 0.7
APP_BRAKE_MPS2 = 2.0
NO_APP_REACTION_S = 0.25
EMERGENCY_BRAKE_MPS2 = 8.0
ACCEL_MPS2 = 2.0
NO_APP_VISIBILITY_M = 2.0
COLLISION_DISTANCE_M = 1.5
APP_ALERT_DISTANCE_M = 30.0
CROSSWALK_MIDPOINT_X_M = 0.0


class Simulation:
    """Peão aproxima-se da passadeira enquanto um carro se dirige para ela."""

    def __init__(self, pedestrian_speed_mps=3.0, driver_has_app=True):
        self.pedestrian_speed_mps = float(pedestrian_speed_mps)
        if not math.isfinite(self.pedestrian_speed_mps) or self.pedestrian_speed_mps <= 0:
            raise ValueError("A velocidade do peão tem de ser superior a zero.")
        self.driver_has_app = bool(driver_has_app)
        self.events = []
        self.on_event = None
        self.reset()

    def reset(self):
        self.time_s = 0.0
        self.pedestrian_y = PEDESTRIAN_START_M
        self.car_x = CAR_START_X_M
        self.car_speed_mps = CAR_SPEED_MPS
        self.car_state = "a circular"
        self.state_time_s = 0.0
        self.alerts = []
        self.pedestrian_alert_active = False
        self.car_passed_crosswalk_midpoint = False
        self.collision = False
        self.finished = False
        self.events.clear()
        self.monitor = PedestrianApproachMonitor(
            alert_distance_m=APP_ALERT_DISTANCE_M,
            min_approach_speed_mps=0.1 if self.driver_has_app else DEFAULT_MIN_APPROACH_SPEED_MPS,
            notifier=self._notify if self.driver_has_app else None,
        )
        self._log(
            f"Início: peão a {self.pedestrian_speed_mps * 3.6:.1f} km/h; "
            f"condutor {'com' if self.driver_has_app else 'sem'} aplicação."
        )

    def _log(self, message):
        self.events.append((self.time_s, message))
        if self.on_event:
            self.on_event(self.time_s, message)

    def _notify(self, message):
        message = message.replace(
            "Peão a aproximar-se rapidamente da passadeira",
            "Peão a aproximar-se da passadeira",
        )
        self._log(f"AVISO AO CONDUTOR: {message}")

    def step(self, dt):
        if self.finished:
            return
        dt = float(dt)
        if dt <= 0:
            raise ValueError("O intervalo de simulação tem de ser superior a zero.")
        self.time_s += dt

        if self.pedestrian_y < PEDESTRIAN_CLEAR_Y_M:
            self.pedestrian_y = min(
                PEDESTRIAN_CLEAR_Y_M,
                self.pedestrian_y + self.pedestrian_speed_mps * dt,
            )

        alert = self.monitor.update(
            "pedestrian-01",
            (0.0, self.pedestrian_y),
            (0.0, CROSSWALK_Y_M),
            self.time_s,
        )
        if alert and self.driver_has_app:
            self.alerts.append(alert)
            self.pedestrian_alert_active = True
            if self.car_state == "a circular" and not self.car_passed_crosswalk_midpoint:
                self.car_state = "a reagir"
                self.state_time_s = 0.0
                self._log("O condutor recebe o aviso e inicia uma travagem suave.")

        self._drive_car(dt)
        if self._is_collision():
            self.collision = True
            self.finished = True
            self.car_state = "colisão simulada"
            self._log("COLISÃO SIMULADA: o carro não conseguiu parar antes da passadeira.")
            return

        if self.pedestrian_y >= PEDESTRIAN_CLEAR_Y_M:
            self.pedestrian_alert_active = False
            if self.car_state == "parado":
                self.car_state = "a retomar"
                self._log("O peão já atravessou; o carro retoma a marcha.")
        if self.car_x >= 20:
            self.finished = True
            self._log("Simulação concluída.")

    def _is_collision(self):
        return (
            abs(self.car_x) <= COLLISION_DISTANCE_M
            and -10.0 <= self.pedestrian_y <= PEDESTRIAN_CLEAR_Y_M - 2
        )

    def _drive_car(self, dt):
        if (self.car_x >= CROSSWALK_MIDPOINT_X_M
                and not self.car_passed_crosswalk_midpoint):
            self.car_passed_crosswalk_midpoint = True
            self.pedestrian_alert_active = False
            if self.car_state in ("a reagir", "a travar", "parado"):
                self.car_state = "a retomar"
                self.state_time_s = 0.0
            self._log("O carro já passou metade da passadeira e continua, ignorando o aviso.")

        if (not self.driver_has_app
                and self.car_state == "a circular"
                and self.car_x < 0
                and abs(self.pedestrian_y) <= NO_APP_VISIBILITY_M):
            self.car_state = "a reagir"
            self.state_time_s = 0.0
            self._log("O condutor vê o peão tarde e inicia uma travagem de emergência.")

        self.state_time_s += dt
        if (self.car_state == "a reagir"
                and self.state_time_s >= (APP_REACTION_S if self.driver_has_app else NO_APP_REACTION_S)):
            self.car_state = "a travar"
            if self.driver_has_app:
                self._log("O carro continua a travar suavemente antes da passadeira.")
            else:
                self._log("O carro trava bruscamente para evitar o peão.")
        elif self.car_state == "a travar":
            deceleration = APP_BRAKE_MPS2 if self.driver_has_app else EMERGENCY_BRAKE_MPS2
            self.car_speed_mps = max(0.0, self.car_speed_mps - deceleration * dt)
            if self.car_speed_mps == 0:
                self.car_state = "parado"
                self._log("O carro parou antes da passadeira para dar passagem ao peão.")
        elif self.car_state == "a retomar":
            self.car_speed_mps = min(CAR_SPEED_MPS, self.car_speed_mps + ACCEL_MPS2 * dt)
            if self.car_speed_mps >= CAR_SPEED_MPS:
                self.car_state = "a circular"

        next_x = self.car_x + self.car_speed_mps * dt
        if (self.car_state == "a circular"
                and self.car_x < CAR_STOP_M
                and self.pedestrian_alert_active
                and self.pedestrian_y < PEDESTRIAN_CLEAR_Y_M):
            next_x = min(next_x, CAR_STOP_M)
            if next_x == CAR_STOP_M:
                self.car_speed_mps = 0.0
                self.car_state = "parado"
                self._log("O carro aguarda o peão junto à passadeira.")
        self.car_x = next_x


class SimulationWindow:
    BG = "#F4F7FB"
    NAVY = "#172B4D"
    RED = "#DC2626"
    GREEN = "#16A34A"
    BLUE = "#2463EB"
    MUTED = "#667085"

    def __init__(self, parent=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.own_root = parent is None
        self.win = tk.Tk() if parent is None else tk.Toplevel(parent)
        self.win.title("Aviso de peão junto à passadeira")
        self.win.geometry("900x600")
        self.win.minsize(720, 500)
        self.win.configure(bg=self.BG)
        self.alive = True
        self.running = False
        self.win.bind("<Destroy>", lambda event: event.widget is self.win and setattr(self, "alive", False))

        self.speed_var = tk.StringVar(value="Rápido (10.8 km/h)")
        self.speed_options = {
            "Lento (4.3 km/h)": 1.2,
            "Moderado (5.4 km/h)": 1.5,
            "Rápido (10.8 km/h)": 3.0,
        }
        self.app_var = tk.BooleanVar(value=True)
        self.build_ui()
        self.reset()
        self.tick()

    def build_ui(self):
        tk, ttk = self.tk, self.ttk
        self.canvas = tk.Canvas(self.win, bg="white", highlightthickness=0, height=380)
        self.canvas.pack(fill="both", expand=True, padx=18, pady=(18, 8))
        self.banner = tk.Label(
            self.win,
            text="Sem aviso: o peão aproxima-se lentamente.",
            bg=self.GREEN,
            fg="white",
            font=("Segoe UI", 13, "bold"),
            padx=12,
            pady=10,
        )
        self.banner.pack(fill="x", padx=18, pady=6)

        controls = tk.Frame(self.win, bg=self.BG)
        controls.pack(fill="x", padx=18, pady=(4, 12))
        tk.Label(controls, text="Velocidade do peão", bg=self.BG, fg=self.NAVY).pack(side="left")
        speed_box = ttk.Combobox(
            controls,
            textvariable=self.speed_var,
            values=tuple(self.speed_options),
            state="readonly",
            width=24,
        )
        speed_box.pack(side="left", padx=8)
        speed_box.bind("<<ComboboxSelected>>", lambda _event: self.reset())
        ttk.Checkbutton(
            controls,
            text="Condutor tem acesso à aplicação",
            variable=self.app_var,
            command=self.reset,
        ).pack(side="left", padx=8)
        ttk.Button(controls, text="Iniciar / continuar", command=self.start).pack(side="left", padx=4)
        ttk.Button(controls, text="Reiniciar", command=self.reset).pack(side="left", padx=4)
        self.info = tk.StringVar()
        tk.Label(controls, textvariable=self.info, bg=self.BG, fg=self.MUTED).pack(side="right")

    def reset(self):
        self.running = False
        self.sim = Simulation(
            self.speed_options[self.speed_var.get()],
            driver_has_app=self.app_var.get(),
        )
        self.sim.on_event = self.add_log
        self.banner.config(
            text=f"Peão a {self.sim.pedestrian_speed_mps * 3.6:.1f} km/h — "
                 + ("condutor recebe aviso antecipado."
                    if self.sim.driver_has_app else "condutor sem aviso antecipado."),
            bg=self.GREEN,
        )
        self.draw()

    def start(self):
        selected_speed = self.speed_options[self.speed_var.get()]
        if (selected_speed != self.sim.pedestrian_speed_mps
                or self.app_var.get() != self.sim.driver_has_app):
            self.reset()
        elif self.sim.finished:
            self.reset()
        self.running = True

    def add_log(self, _time_s, message):
        if message.startswith("AVISO AO CONDUTOR"):
            self.banner.config(text=message, bg=self.RED)
        elif "COLISÃO SIMULADA" in message:
            self.banner.config(text=message, bg=self.RED)
        elif "carro parou" in message.lower():
            self.banner.config(text=message, bg=self.RED)
        elif "emergência" in message:
            self.banner.config(text=message, bg="#B45309")
        elif "retoma" in message:
            self.banner.config(text=message, bg=self.GREEN)

    def tick(self):
        if not self.alive:
            return
        if self.running and not self.sim.finished:
            for _ in range(4):
                self.sim.step(0.05)
                if self.sim.finished:
                    self.running = False
                    break
            self.draw()
        self.win.after(50, self.tick)

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 500)
        height = max(canvas.winfo_height(), 300)
        horizontal_span = max(140, abs(self.sim.car_x) + 35)
        scale = min((width - 100) / horizontal_span, (height - 60) / 100)
        center_x, center_y = width * 0.53, height * 0.5

        def point(x, y):
            return center_x + x * scale, center_y - y * scale

        road_top = center_y + 18 * scale
        road_bottom = center_y - 18 * scale
        canvas.create_rectangle(25, road_bottom, width - 25, road_top, fill="#454B54", outline="")
        canvas.create_line(30, center_y, width - 30, center_y, fill="white", dash=(12, 9), width=2)

        crossing_x, crossing_y = point(0, 0)
        for offset in (-12, -6, 0, 6, 12):
            x1, y1 = point(offset, -16)
            x2, y2 = point(offset, 16)
            canvas.create_line(x1, y1, x2, y2, fill="white", width=4)
        canvas.create_line(crossing_x, center_y + 18 * scale, crossing_x, 30,
                           fill="#AAB4C3", width=3)
        canvas.create_text(crossing_x + 45, 28, text="PASSADEIRA",
                           fill=self.NAVY, font=("Segoe UI", 10, "bold"))

        pedestrian_x, pedestrian_y = point(0, self.sim.pedestrian_y)
        canvas.create_oval(pedestrian_x - 8, pedestrian_y - 8, pedestrian_x + 8, pedestrian_y + 8,
                           fill=self.NAVY, outline="white", width=2)
        canvas.create_line(pedestrian_x, pedestrian_y + 8, pedestrian_x, pedestrian_y + 20,
                           fill=self.NAVY, width=3)
        canvas.create_text(pedestrian_x + 35, pedestrian_y, text="Peão",
                           fill=self.NAVY, font=("Segoe UI", 10, "bold"))

        car_x, car_y = point(self.sim.car_x, 0)
        canvas.create_rectangle(car_x - 18, car_y - 10, car_x + 18, car_y + 10,
                                fill=self.RED if self.sim.collision else self.BLUE,
                                outline="white", width=2)
        canvas.create_text(car_x, car_y - 22, text="Carro",
                           fill=self.NAVY, font=("Segoe UI", 10, "bold"))
        if self.sim.collision:
            canvas.create_text(crossing_x, crossing_y - 35, text="COLISÃO SIMULADA",
                               fill=self.RED, font=("Segoe UI", 12, "bold"))
        self.info.set(
            f"Peão: {self.sim.pedestrian_speed_mps * 3.6:.1f} km/h   |   "
            f"Aplicação: {'sim' if self.sim.driver_has_app else 'não'}   |   "
            f"Carro: {self.sim.car_state}"
        )

    def open(self):
        if self.own_root:
            self.win.mainloop()
        return self


def run_headless(pedestrian_speed_mps, driver_has_app=True, max_s=60):
    sim = Simulation(pedestrian_speed_mps, driver_has_app=driver_has_app)
    sim.on_event = lambda timestamp, message: print(f"[{timestamp:05.1f}s] {message}")
    while not sim.finished and sim.time_s < max_s:
        sim.step(0.05)
    print(f"Avisos enviados: {len(sim.alerts)}")
    print(f"Resultado: {'colisão simulada' if sim.collision else sim.car_state}")
    return sim


def open_window(parent=None):
    return SimulationWindow(parent).open()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulação de alerta de peão junto à passadeira")
    parser.add_argument("--headless", action="store_true", help="executa sem abrir a janela")
    parser.add_argument(
        "--speed",
        type=float,
        default=3.0,
        help="velocidade do peão em m/s",
    )
    parser.add_argument("--no-app", action="store_true", help="simula um condutor sem acesso à aplicação")
    args = parser.parse_args()
    if args.headless:
        run_headless(args.speed, driver_has_app=not args.no_app)
    else:
        open_window()
