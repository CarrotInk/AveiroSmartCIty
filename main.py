import json
import statistics as st
import threading
import tkinter as tk
from functools import lru_cache
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import pandas as pd
from tkintermapview import TkinterMapView

import crosswalk_wait
import fastest_route
import parking_availabiality as parking_availability
import roadXcross
from location_time import locationCoordinates
from road_match import find_nearest_road as lookup_nearest_road

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

DATA_DIR = Path(__file__).resolve().parent / "data"
REFERENCE_DIR = DATA_DIR / "reference-entities"


# ===Dados========================================================================================================
@lru_cache(maxsize=1)
def _load_map_sources():
    crosswalks = roadXcross.read_ref_csv(REFERENCE_DIR / "SmartCrosswalk.csv").set_index("entityId")
    roads = roadXcross.read_ref_csv(REFERENCE_DIR / "RoadSegment.csv").set_index("entityId")
    return crosswalks[~crosswalks.index.duplicated()], roads[~roads.index.duplicated()]


@lru_cache(maxsize=None)
def _events_table(filename):
    return roadXcross.read_csv_safe(DATA_DIR / filename, usecols=["entityId", "documentJson"])


@lru_cache(maxsize=1)
def _speed_table():
    segments = fastest_route.load_segments()
    speeds, _ = fastest_route.load_speeds_and_waits(segments.index)
    return speeds


def get_value(data_dict, field):
    value = data_dict.get(field)
    if isinstance(value, list) and value:
        value = value[0]
    return value


def _short_parking_id(entity_id):
    return str(entity_id).rsplit(":", 1)[-1]


def get_rows(frame, entity_id):
    rows = frame[frame["entityId"] == entity_id].reset_index(drop=True)
    if rows.empty:
        raise ValueError(f"Não existem registos para {entity_id}")
    return rows


def get_documents(rows):
    return [roadXcross._flat(json.loads(s)) for s in rows["documentJson"]]


def latest_document(documents):
    floor = pd.Timestamp("1970-01-01", tz="UTC")

    def key(doc):
        ts = doc.get("eventTimestamp")
        return ts if isinstance(ts, pd.Timestamp) else floor

    return max(reversed(documents), key=key)


def _numbers(documents, field):
    values = []
    for doc in documents:
        value = doc.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _polygon_center(location_json):
    coordinates = json.loads(location_json)["coordinates"][0]
    if len(coordinates) > 1 and coordinates[0] == coordinates[-1]:
        coordinates = coordinates[:-1]

    longitude = sum(point[0] for point in coordinates) / len(coordinates)
    latitude = sum(point[1] for point in coordinates) / len(coordinates)
    return latitude, longitude


def _road_paths(road_id):
    _, roads = _load_map_sources()
    if road_id not in roads.index:
        return []
    geometry = json.loads(roads.at[road_id, "location"])
    coordinates = geometry.get("coordinates") or []
    lines = coordinates if geometry.get("type") == "MultiLineString" else [coordinates]
    return [[(point[1], point[0]) for point in line] for line in lines if len(line) >= 2]


def _map_features(crosswalk_ids):
    crosswalks, _ = _load_map_sources()
    links = pd.read_csv(roadXcross.LINKS_CSV)

    crosswalk_ids = set(crosswalk_ids)
    features = []

    for crosswalk_id in sorted(crosswalk_ids):
        if crosswalk_id not in crosswalks.index:
            continue

        lat, lon = _polygon_center(crosswalks.at[crosswalk_id, "location"])
        features.append({
            "kind": "crosswalk",
            "position": (lat, lon),
            "label": f"Passadeira {crosswalk_id.rsplit(':', 1)[-1]}",
        })

    linked_roads = links[
        links["smartcrosswalk"].isin(crosswalk_ids)
        & links["use_for_alert"].astype(str).str.lower().eq("true")
    ]

    for road_id in linked_roads["segment_id"].dropna().unique():
        for path in _road_paths(road_id):
            features.append({
                "kind": "road",
                "position": path,
                "label": f"Rua {road_id.rsplit(':', 1)[-1]}",
            })

    return features


def road_speed_text(segment_id, hour):
    try:
        speeds = _speed_table()
        rows = speeds[(speeds["segment_id"] == segment_id) & (speeds["hour"] == hour)]
        if len(rows) and rows["n"].sum() > 0:
            average = float((rows["speed_mps"] * rows["n"]).sum() / rows["n"].sum())
            return f"{average * 3.6:.2f} km/h (média às {hour}h UTC)"
    except Exception as error:
        print(f"Sem tabela de velocidades: {error}")

    _, roads = _load_map_sources()
    if segment_id in roads.index:
        snapshot = roads.at[segment_id, "averageVelocity"]
        if pd.notna(snapshot) and 0 < snapshot <= roadXcross.MAX_SPEED_MPS:
            return f"{snapshot * 3.6:.2f} km/h (último valor registado)"
    return "Sem registos para esta hora"


# ===Consultas por ID (correm numa thread, por isso podem demorar)=================================================
def crosswalk_fields(number):
    entity_id = f"urn:ngsi-ld:SmartCrosswalk:{number:04}"
    documents = get_documents(get_rows(_events_table("smartcrosswalk.csv"), entity_id))

    wait_times = _numbers(documents, "wait_time")
    traverse_times = _numbers(documents, "traverse_time")
    latest = latest_document(documents)

    average_wait = f"{st.mean(wait_times):.2f} s" if wait_times else "Sem dados"
    average_traverse = f"{st.mean(traverse_times):.2f} s" if traverse_times else "Sem dados"

    return "Passadeira inteligente", [
        ("ID da passadeira", f"{number:04}"),
        ("Observado por", get_value(latest, "observedBy") or "Indisponível"),
        ("Tempo médio de espera", average_wait),
        ("Tempo médio do percurso", average_traverse),
        ("Registos analisados", len(documents)),
    ]


def road_segment_fields(number):
    entity_id = f"urn:ngsi-ld:RoadSegment:{number:05}"
    _, roads = _load_map_sources()
    if entity_id not in roads.index:
        raise ValueError(f"Não existem registos para {entity_id}")
    row = roads.loc[entity_id]

    name = roadXcross._segment_names(row["name"])
    try:
        limits = [roadXcross._num(v) for v in json.loads(row["maxAllowedVelocity"])]
        limits = [v for v in limits if v]
        speed_text = f"{st.mean(limits) * 3.6:.2f} km/h"
    except (TypeError, ValueError, st.StatisticsError):
        speed_text = "Indisponível"

    return "Segmento de estrada", [
        ("ID da rua", f"{number:05}"),
        ("Rua", name[0] if name else "Indisponível"),
        ("Velocidade máxima permitida", speed_text),
    ]


def parking_fields(number):
    entity_id = f"urn:ngsi-ld:ParkingSpot:{number:04}"
    latest = latest_document(get_documents(get_rows(_events_table("smartparking.csv"), entity_id)))

    location = get_value(latest, "location")
    if isinstance(location, dict) and isinstance(location.get("coordinates"), list):
        lon, lat = location["coordinates"][:2]
        location = f"{lat:.6f}, {lon:.6f}"

    return "Lugar de estacionamento", [
        ("ID do lugar", f"{number:04}"),
        ("Estado", get_value(latest, "status") or "Indisponível"),
        ("Localização", location or "Indisponível"),
    ]


# ================================================================================================================
class SmartCityApp:
    THEMES = {
        "dark": {
            "bg": "#10151D",
            "card": "#1A222D",
            "text": "#E6EDF7",
            "blue": "#4D8DFF",
            "muted": "#9AA8BA",
            "border": "#303B49",
            "secondary": "#25364F",
            "secondary_hover": "#304766",
            "secondary_text": "#A9C7FF",
            "result": "#222D3A",
        },
        "light": {
            "bg": "#F4F7FB",
            "card": "#FFFFFF",
            "text": "#172B4D",
            "blue": "#2463EB",
            "muted": "#667085",
            "border": "#DDE5EF",
            "secondary": "#EAF0FF",
            "secondary_hover": "#DCE6FF",
            "secondary_text": "#2463EB",
            "result": "#F7F9FC",
        },
    }

    def __init__(self, root):
        self.root = root
        self.root.title("Aveiro Smart City")
        self.root.geometry("1320x720")
        self.root.minsize(1000, 600)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.is_dark = True
        self._apply_theme_palette()
        self.last_result = None
        self.current_map_features = None
        self.current_map_fit_bounds = False
        self.route_busy = False
        self.content_column_weights = [100, 140, 140]
        self._column_resize_drag = None
        self.wait_data = None
        self.parking_data = None
        try:
            self.segments = fastest_route.load_segments()
            self.streets = fastest_route.list_streets(self.segments)
        except Exception as error:
            print(f"Could not load the road map: {error}")
            self.segments, self.streets = None, []

        self.build_ui()

    def _apply_theme_palette(self):
        palette = self.THEMES["dark" if self.is_dark else "light"]
        self.BG = palette["bg"]
        self.CARD = palette["card"]
        self.NAVY = palette["text"]
        self.BLUE = palette["blue"]
        self.MUTED = palette["muted"]
        self.BORDER = palette["border"]
        self.root.configure(fg_color=self.BG)

    def toggle_theme(self):
        form_values = {
            "category": self.category.get(),
            "id": self.id_entry.get(),
            "wait_time": self.wait_time_entry.get(),
            "street": self.street_entry.get(),
            "route_from": self.route_from.get(),
            "route_to": self.route_to.get(),
            "route_hour": self.route_hour.get(),
        }
        status = self.status.get()
        button_states = {
            name: getattr(self, name).cget("state")
            for name in (
                "search_button",
                "nearest_road_button",
                "nearest_parking_button",
                "wait_button",
                "warning_button",
                "route_button",
            )
        }
        self.is_dark = bool(self.theme_switch.get())
        ctk.set_appearance_mode("dark" if self.is_dark else "light")
        self._apply_theme_palette()

        self.page.destroy()
        self.build_ui()
        for name, state in button_states.items():
            getattr(self, name).configure(state=state)
        self.category.set(form_values["category"])
        for entry, value in (
            (self.id_entry, form_values["id"]),
            (self.wait_time_entry, form_values["wait_time"]),
            (self.street_entry, form_values["street"]),
            (self.route_from, form_values["route_from"]),
            (self.route_to, form_values["route_to"]),
            (self.route_hour, form_values["route_hour"]),
        ):
            entry.insert(0, value)
        if self.last_result is not None:
            self.show_results(*self.last_result)
        if self.current_map_features is not None:
            self.show_map_features(
                self.current_map_features,
                fit_bounds=self.current_map_fit_bounds,
            )
        self.status.set(status)

    def _font(self, size, weight="normal"):
        return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)

    def _caption(self, parent, text):
        ctk.CTkLabel(parent, text=text, text_color=self.MUTED,
                     font=self._font(10, "bold")).pack(anchor="w", padx=20, pady=(0, 7))

    def _entry(self, parent, placeholder, height=38, pady=(0, 12)):
        entry = ctk.CTkEntry(parent, height=height, corner_radius=11, border_color=self.BORDER,
                             placeholder_text=placeholder, font=self._font(12))
        entry.pack(fill="x", padx=20, pady=pady)
        return entry

    def _button(self, parent, text, command, primary=True, pady=(0, 10)):
        kwargs = {} if primary else {
            "fg_color": self.THEMES["dark" if self.is_dark else "light"]["secondary"],
            "hover_color": self.THEMES["dark" if self.is_dark else "light"]["secondary_hover"],
            "text_color": self.THEMES["dark" if self.is_dark else "light"]["secondary_text"],
        }
        button = ctk.CTkButton(parent, text=text, command=command, height=40, corner_radius=12,
                               font=self._font(12, "bold"), **kwargs)
        button.pack(fill="x", padx=20, pady=pady)
        return button

    def _separator(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=self.BORDER).pack(fill="x", padx=20, pady=10)

    def build_ui(self):
        page = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        self.page = page
        page.grid(row=0, column=0, sticky="nsew", padx=28, pady=24)
        page.grid_columnconfigure((0, 1), weight=1)
        page.grid_rowconfigure(1, weight=1)

        title_header = ctk.CTkFrame(page, fg_color="transparent")
        title_header.grid(row=0, column=0, sticky="ew", pady=(0, 20))

        ctk.CTkLabel(title_header, text="Aveiro Smart City", text_color=self.NAVY,
                     font=self._font(27, "bold")).pack(anchor="w")
        ctk.CTkLabel(title_header, text="Visualização de dados sobre mobilidade", text_color=self.MUTED,
                     font=self._font(12)).pack(anchor="w", pady=(4, 0))

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=1, sticky="ne", pady=(0, 20))
        self.theme_switch = ctk.CTkSwitch(
            header,
            text="Dark mode",
            command=self.toggle_theme,
            onvalue=1,
            offvalue=0,
            font=self._font(11),
            text_color=self.MUTED,
        )
        self.theme_switch.pack(anchor="w", pady=(0, 10))
        if self.is_dark:
            self.theme_switch.select()

        content = ctk.CTkFrame(page, fg_color="transparent")
        content.grid(row=1, column=0, columnspan=2, sticky="nsew")
        pane_columns = (0, 2, 4)
        for column, weight, minsize in zip(
            pane_columns, self.content_column_weights, (230, 250, 280)
        ):
            content.grid_columnconfigure(column, weight=weight, minsize=minsize)
        content.grid_columnconfigure(1, weight=0, minsize=10)
        content.grid_columnconfigure(3, weight=0, minsize=10)
        content.grid_rowconfigure(0, weight=1)

        controls = ctk.CTkScrollableFrame(content, fg_color=self.CARD, corner_radius=20,
                                          border_width=1, border_color=self.BORDER, width=250)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._add_column_resize_handle(content, 1, 0, 1)

        # --- pesquisa por ID ---
        ctk.CTkLabel(controls, text="Pesquisa um registo", text_color=self.NAVY,
                     font=self._font(16, "bold")).pack(anchor="w", padx=20, pady=(14, 0))
        ctk.CTkLabel(controls, text="Escolha uma categoria e insira o ID do elemento",
                     text_color=self.MUTED, font=self._font(11), wraplength=210,
                     justify="left").pack(anchor="w", padx=20, pady=(6, 16))

        self._caption(controls, "CATEGORIA")
        self.category = ctk.CTkComboBox(
            controls, values=("Smart crosswalk", "Road segment", "Parking spot"), state="readonly",
            height=40, corner_radius=11, border_color=self.BORDER, button_color=self.BLUE,
            font=self._font(12))
        self.category.set("Smart crosswalk")
        self.category.pack(fill="x", padx=20, pady=(0, 14))

        self._caption(controls, "ID DO REGISTO")
        self.id_entry = self._entry(controls, "Insira o ID numérico", height=42, pady=(0, 14))
        self.id_entry.bind("<Return>", lambda _event: self.search())

        self.search_button = self._button(controls, "Procurar", self.search)

        self._separator(controls)

        self.nearest_road_button = self._button(
            controls, "Rua mais próxima", self.find_nearest_road, primary=False, pady=(4, 0))
        self.nearest_parking_button = self._button(
            controls, "Estacionamento livre mais próximo", self.find_nearest_parking,
            primary=False, pady=(4, 0))

        self._separator(controls)

        self.wait_time_entry = self._entry(controls, "Hora UTC (HH:MM)", pady=(4, 6))
        self.wait_button = self._button(controls, "Analisar espera da passadeira", self.analyze_wait)

        self._separator(controls)

        self.street_entry = self._entry(controls, "Nome da rua", pady=(4, 6))
        self.street_entry.bind("<Return>", lambda _event: self.show_street_warnings())
        self.warning_button = self._button(controls, "Avisos da rua", self.show_street_warnings)

        self._separator(controls)

        ctk.CTkLabel(controls, text="Rota mais rápida (de carro)", text_color=self.NAVY,
                     font=self._font(14, "bold")).pack(anchor="w", padx=20, pady=(0, 8))

        self._caption(controls, "RUA DE ORIGEM")
        self.route_from = self._entry(controls, "Ex.: Rua de Ovar")
        self._caption(controls, "RUA DE DESTINO")
        self.route_to = self._entry(controls, "Ex.: Avenida Europa")
        self._caption(controls, "HORA (UTC, VAZIO = AGORA)")
        self.route_hour = self._entry(controls, "0-23")
        self.route_hour.bind("<Return>", lambda _event: self.find_route())

        self.route_button = self._button(controls, "Calcular rota mais rápida", self.find_route)

        self.status = tk.StringVar(value="Pronto")
        ctk.CTkLabel(controls, textvariable=self.status, text_color=self.MUTED, font=self._font(10),
                     wraplength=210, justify="left").pack(anchor="w", padx=20, pady=(8, 14))
        result_card = ctk.CTkFrame(content, fg_color=self.CARD, corner_radius=20,
                                   border_width=1, border_color=self.BORDER)
        result_card.grid(row=0, column=2, sticky="nsew", padx=8)
        result_card.grid_columnconfigure(0, weight=1)
        result_card.grid_rowconfigure(1, weight=1)
        self._add_column_resize_handle(content, 3, 1, 2)

        ctk.CTkLabel(result_card, text="Resultado", text_color=self.NAVY,
                     font=self._font(16, "bold")).grid(row=0, column=0, sticky="w", padx=24, pady=(22, 12))

        self.result_body = ctk.CTkScrollableFrame(result_card, fg_color="transparent", corner_radius=12)
        self.result_body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        map_card = ctk.CTkFrame(content, fg_color=self.CARD, corner_radius=20,
                                border_width=1, border_color=self.BORDER)
        map_card.grid(row=0, column=4, sticky="nsew", padx=(8, 0))
        map_card.grid_rowconfigure(1, weight=1)
        map_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(map_card, text="Mapa Interativo", text_color=self.NAVY,
                     font=self._font(16, "bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))

        self.map_widget = TkinterMapView(map_card, corner_radius=12)
        self.map_widget.set_tile_server("https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.map_widget.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.map_widget.set_position(40.64, -8.65)
        self.map_widget.set_zoom(14)
        ctk.CTkLabel(
            map_card,
            text="© OpenStreetMap contributors",
            text_color=self.MUTED,
            font=self._font(9),
        ).grid(row=2, column=0, sticky="e", padx=14, pady=(0, 8))

        self.show_empty_state()
        self.id_entry.focus_set()

    def _add_column_resize_handle(self, parent, grid_column, left_pane, right_pane):
        handle = ctk.CTkFrame(
            parent,
            width=4,
            corner_radius=2,
            fg_color=self.BORDER,
            cursor="sb_h_double_arrow",
        )
        handle.grid(row=0, column=grid_column, sticky="ns", padx=3)
        handle.bind(
            "<Button-1>",
            lambda event: self._start_column_resize(
                event, parent, left_pane, right_pane
            ),
        )
        handle.bind("<B1-Motion>", self._resize_content_columns)
        handle.bind("<ButtonRelease-1>", lambda _event: self._stop_column_resize())
        handle.bind("<Enter>", lambda _event: handle.configure(fg_color=self.BLUE))
        handle.bind("<Leave>", lambda _event: handle.configure(fg_color=self.BORDER))

    def _start_column_resize(self, event, content, left_pane, right_pane):
        pane_columns = (0, 2, 4)
        left_column = pane_columns[left_pane]
        right_column = pane_columns[right_pane]
        self._column_resize_drag = {
            "content": content,
            "left_pane": left_pane,
            "right_pane": right_pane,
            "start_x": event.x_root,
            "left_width": content.grid_bbox(left_column, 0)[2],
            "right_width": content.grid_bbox(right_column, 0)[2],
        }

    def _resize_content_columns(self, event):
        drag = self._column_resize_drag
        if drag is None:
            return

        left_pane = drag["left_pane"]
        right_pane = drag["right_pane"]
        min_widths = (230, 250, 280)
        total_width = drag["left_width"] + drag["right_width"]
        left_width = drag["left_width"] + event.x_root - drag["start_x"]
        left_width = max(min_widths[left_pane], left_width)
        left_width = min(total_width - min_widths[right_pane], left_width)
        right_width = total_width - left_width

        self.content_column_weights[left_pane] = left_width
        self.content_column_weights[right_pane] = right_width
        pane_columns = (0, 2, 4)
        drag["content"].grid_columnconfigure(
            pane_columns[left_pane], weight=left_width
        )
        drag["content"].grid_columnconfigure(
            pane_columns[right_pane], weight=right_width
        )

    def _stop_column_resize(self):
        self._column_resize_drag = None

    def clear_results(self):
        for widget in self.result_body.winfo_children():
            widget.destroy()

    def show_empty_state(self):
        self.clear_results()

        ctk.CTkLabel(self.result_body, text="O resultado aparecerá aqui", text_color=self.NAVY,
                     font=self._font(15, "bold")).pack(anchor="w", padx=10, pady=(18, 5))
        ctk.CTkLabel(self.result_body, text="Escolha um tipo de dados e insira o seu ID",
                     text_color=self.MUTED, font=self._font(12), wraplength=480,
                     justify="left").pack(anchor="w", padx=10)

    def show_results(self, title, fields):
        self.last_result = (title, fields)
        self.clear_results()

        ctk.CTkLabel(self.result_body, text=title, text_color=self.NAVY,
                     font=self._font(16, "bold")).pack(anchor="w", padx=10, pady=(8, 16))

        for name, value in fields:
            row = ctk.CTkFrame(
                self.result_body,
                fg_color=self.THEMES["dark" if self.is_dark else "light"]["result"],
                corner_radius=12,
            )
            row.pack(fill="x", padx=6, pady=5)

            ctk.CTkLabel(row, text=name.upper(), text_color=self.MUTED,
                         font=self._font(9, "bold")).pack(anchor="w", padx=14, pady=(11, 0))
            ctk.CTkLabel(row, text=str(value), text_color=self.NAVY, font=self._font(12),
                         wraplength=500, justify="left").pack(anchor="w", padx=14, pady=(3, 11))

        self.status.set("Informação carregada com sucesso")

    def search(self):
        raw_id = self.id_entry.get().strip()

        if not raw_id.isdigit():
            messagebox.showwarning("ID inválido", "Insira um ID numérico.", parent=self.root)
            self.id_entry.focus_set()
            return

        self.search_button.configure(state="disabled")
        self.status.set("A carregar dados... (a primeira pesquisa pode demorar)")
        threading.Thread(target=self._search_worker, args=(self.category.get(), int(raw_id)),
                         daemon=True).start()

    def _search_worker(self, category, number):
        try:
            if category == "Smart crosswalk":
                title, fields = crosswalk_fields(number)
            elif category == "Road segment":
                title, fields = road_segment_fields(number)
            else:
                title, fields = parking_fields(number)
            self.root.after(0, self._show_search_result, title, fields, None)
        except Exception as error:
            self.root.after(0, self._show_search_result, None, None, error)

    def _show_search_result(self, title, fields, error):
        self.search_button.configure(state="normal")
        if error is not None:
            self.status.set("Sem registos correspondentes")
            messagebox.showerror("Falha a carregar registo", str(error), parent=self.root)
            return
        self.show_results(title, fields)

    def find_route(self):
        if self.route_busy:
            return

        origin = self.route_from.get().strip()
        destination = self.route_to.get().strip()
        hour_text = self.route_hour.get().strip()

        if not origin or not destination:
            messagebox.showwarning("Rua em falta", "Indique a rua de origem e a de destino.", parent=self.root)
            return
        if hour_text and (not hour_text.isdigit() or int(hour_text) > 23):
            messagebox.showwarning("Hora inválida", "Indique uma hora de 0 a 23, ou deixe vazio.", parent=self.root)
            return
        hour = int(hour_text) if hour_text else None

        self.route_busy = True
        self.route_button.configure(state="disabled")
        self.status.set("A calcular a rota... a primeira vez lê os ficheiros grandes e pode demorar alguns minutos.")

        job = {}

        def work():                                # corre noutra thread para a janela não bloquear
            try:
                if self.segments is None:
                    self.segments = fastest_route.load_segments()
                job["result"] = fastest_route.fastest_route(origin, destination, hour, segments=self.segments)
            except Exception as error:
                job["error"] = error
            finally:
                job["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self.root.after(200, lambda: self.poll_route(job))

    def poll_route(self, job):
        if not job.get("done"):
            self.root.after(200, lambda: self.poll_route(job))
            return

        self.route_busy = False
        self.route_button.configure(state="normal")

        error = job.get("error")
        if error is not None:
            self.status.set("Falha a calcular a rota")
            if isinstance(error, fastest_route.AmbiguousStreet):
                options = error.options
                text = "Seja mais específico. Queria dizer:\n- " + "\n- ".join(options[:8])
                if len(options) > 8:
                    text += f"\n... e mais {len(options) - 8}"
            else:
                text = str(error)
            messagebox.showerror("Não foi possível calcular a rota", text, parent=self.root)
            return

        route = job["result"]
        steps = [
            f"{i}. {g['street']}: {g['length_m']:.0f} m, {fastest_route.format_duration(g['time_s'])}"
            for i, g in enumerate(fastest_route.group_steps(route["steps"]), 1)
        ]
        fields = [
            ("Rota", f"{route['origin']}  →  {route['destination']}"),
            ("Hora (UTC)", f"{route['hour']}h"),
            ("Tempo de carro", fastest_route.format_duration(route["total_s"])),
            ("Distância", f"{route['distance_m'] / 1000:.2f} km"),
            ("Velocidade média", f"{fastest_route.average_speed_kmh(route):.0f} km/h"),
            ("Tempo em movimento", fastest_route.format_duration(route["drive_s"])),
            ("Atraso nas passadeiras", fastest_route.format_duration(route["wait_s"])),
            ("Percurso", "\n".join(steps)),
        ]
        notes = fastest_route.route_notes(route, lang="pt")
        if notes:
            fields.append(("Notas", "\n".join(notes)))

        features = [
            {"kind": "road", "position": path, "label": step["street"]}
            for step in route["steps"]
            for path in _road_paths(step["segment_id"])
        ]
        self.show_map_features(features, fit_bounds=True)
        self.show_results("Rota mais rápida de carro", fields)

    # --- rua mais próxima ---
    def find_nearest_road(self):
        self.nearest_road_button.configure(state="disabled")
        self.status.set("A procurar a rua mais próxima...")
        threading.Thread(target=self._lookup_nearest_road, daemon=True).start()

    def _lookup_nearest_road(self):
        try:
            latitude, longitude, *_ = locationCoordinates()
            match = lookup_nearest_road(latitude, longitude)
            if not match:
                self.root.after(0, self._show_nearest_road_result, None, None, (latitude, longitude), None)
                return

            average_velocity = road_speed_text(match["entityId"], roadXcross.hour_now())
            self.root.after(0, self._show_nearest_road_result, match, average_velocity,
                            (latitude, longitude), None)
        except BaseException as error:             # locationCoordinates() pode terminar com SystemExit sem rede
            self.root.after(0, self._show_nearest_road_result, None, None, None, error)

    def _show_nearest_road_result(self, match, average_velocity, position, error):
        self.nearest_road_button.configure(state="normal")
        if error is not None:
            self.status.set("Falha a procurar a rua mais próxima")
            messagebox.showerror("Falha a procurar a rua mais próxima",
                                 str(error) or "Sem ligação à internet?", parent=self.root)
            return

        features = [{"kind": "marker", "position": position, "label": "A sua localização"}]
        if match:
            features += [{"kind": "road", "position": path, "label": match["name"] or ""}
                         for path in _road_paths(match["entityId"])]
        self.show_map_features(features)

        if not match:
            self.show_results("Rua mais próxima", [("Resultado", "Não foram encontradas ruas na proximidade")])
            return

        self.show_results(
            "Rua mais próxima",
            [
                ("Nome", match["name"] or "Sem nome"),
                ("ID da rua", match["entityId"]),
                ("Distância", f"{match['distance_m']:.1f} m"),
                ("Velocidade média", average_velocity),
            ],
        )

    # --- estacionamento livre mais próximo ---
    def find_nearest_parking(self):
        self.nearest_parking_button.configure(state="disabled")
        self.status.set("A procurar estacionamento livre e a calcular a rota...")
        threading.Thread(target=self._nearest_parking_worker, daemon=True).start()

    def _nearest_parking_worker(self):
        parking_data = self.parking_data
        try:
            latitude, longitude, *_ = locationCoordinates()
            if parking_data is None:
                parking_data = parking_availability.load_parking_data()
            result = parking_availability.nearest_available_route(
                parking_data,
                latitude,
                longitude,
                max_age_h=24,
                hour=roadXcross.hour_now(),
                segments=self.segments,
            )
            self.root.after(
                0, self._show_nearest_parking_result, result,
                (latitude, longitude), parking_data, None,
            )
        except BaseException as error:  # locationCoordinates() termina com SystemExit quando não há rede
            self.root.after(0, self._show_nearest_parking_result, None, None, parking_data, error)

    def _show_nearest_parking_result(self, result, position, parking_data, error):
        self.nearest_parking_button.configure(state="normal")
        if parking_data is not None:
            self.parking_data = parking_data
        if error is not None:
            self.status.set("Falha ao procurar estacionamento")
            messagebox.showerror("Falha ao procurar estacionamento", str(error), parent=self.root)
            return

        spot = result["spot"]
        route = result["route"]
        features = [
            {"kind": "marker", "position": position, "label": "A sua localização"},
            {
                "kind": "marker",
                "position": (float(spot["lat"]), float(spot["lon"])),
                "label": f"Estacionamento livre {_short_parking_id(spot['entityId'])}",
            },
        ]
        for step in route["steps"]:
            features.extend(
                {"kind": "road", "position": path, "label": step["street"]}
                for path in _road_paths(step["segment_id"])
            )
        self.show_map_features(features)

        steps = [
            f"{index}. {step['street']}: {step['length_m']:.0f} m, "
            f"{fastest_route.format_duration(step['time_s'])}"
            for index, step in enumerate(fastest_route.group_steps(route["steps"]), 1)
        ]
        fields = [
            ("Lugar livre", _short_parking_id(spot["entityId"])),
            ("Estado dos dados", f"Última leitura geral: {result['moment']:%Y-%m-%d %H:%M} UTC"),
            ("Última leitura deste lugar", f"há {spot['age_h']:.1f} h (máximo aceite: 24 h)"),
            ("Distância em linha reta", f"{spot['distance_m']:.0f} m"),
            ("Rua de origem", result["origin_road"]["name"] or "Sem nome"),
            ("Rua do estacionamento", result["parking_road"]["name"] or "Sem nome"),
            ("Tempo estimado de carro", fastest_route.format_duration(route["total_s"])),
            ("Distância da rota", f"{route['distance_m'] / 1000:.2f} km"),
            ("Percurso", "\n".join(steps) if steps else "Sem instruções disponíveis"),
        ]
        notes = fastest_route.route_notes(route, lang="pt")
        if notes:
            fields.append(("Notas", "\n".join(notes)))
        self.show_results("Estacionamento livre mais próximo", fields)

    # --- espera nas passadeiras ---
    def analyze_wait(self):
        raw_id = self.id_entry.get().strip()
        time_text = self.wait_time_entry.get().strip()

        if not raw_id.isdigit():
            messagebox.showwarning("ID inválido", "Insira o ID numérico da passadeira.", parent=self.root)
            return
        if self.category.get() != "Smart crosswalk":
            messagebox.showwarning(
                "Categoria inválida",
                "Selecione Smart crosswalk e insira o ID da passadeira.",
                parent=self.root,
            )
            return

        self.wait_button.configure(state="disabled")
        self.status.set("A analisar tempo de espera...")
        threading.Thread(target=self._wait_worker, args=(int(raw_id), time_text), daemon=True).start()

    def _wait_worker(self, crosswalk_number, time_text):
        try:
            if self.wait_data is None:
                self.wait_data = crosswalk_wait.load_crosswalk_data(str(DATA_DIR / "smartcrosswalk.csv"))

            if not roadXcross.LINKS_CSV.exists():
                roadXcross.build_links()

            crosswalk_id = f"urn:ngsi-ld:SmartCrosswalk:{crosswalk_number:04d}"
            result = crosswalk_wait.summarize_wait_time_by_time(self.wait_data, crosswalk_number, time_text)
            features = _map_features([crosswalk_id])

            self.root.after(0, self._show_wait_result, result, features, None)
        except Exception as error:
            self.root.after(0, self._show_wait_result, None, [], error)

    def _show_wait_result(self, result, features, error):
        self.wait_button.configure(state="normal")

        if error is not None:
            self.status.set("Falha a analisar tempo de espera")
            messagebox.showerror("Falha na análise", str(error), parent=self.root)
            return

        self.show_map_features(features)

        if result["status"] == "no_crosswalk":
            self.status.set("Sem registos para esta passadeira")
            messagebox.showinfo("Sem dados", "Não há registos para esta passadeira.", parent=self.root)
            return
        if result["status"] == "no_data":
            self.status.set("Sem dados nesse intervalo")
            messagebox.showinfo("Sem dados", "Não há tempos de espera válidos nesse intervalo.", parent=self.root)
            return

        fields = [
            ("Intervalo UTC", f"{result['start']}–{result['end']}"),
            ("Espera média", f"{result['average']:.2f} s"),
            ("Mediana", f"{result['median']:.2f} s"),
            ("Registos", result["records"]),
            ("Dias distintos", result["days"]),
        ]
        warnings = []
        if result["low_data"]:
            warnings.append("Poucos dados: o valor é pouco fiável.")
        if result["skewed"]:
            warnings.append("A média é muito maior que a mediana: há esperas muito longas a puxá-la para cima.")
        if warnings:
            fields.append(("Aviso", "\n".join(warnings)))

        self.show_results("Tempo de espera", fields)

    # --- avisos da rua ---
    def show_street_warnings(self):
        street = self.street_entry.get().strip()
        if not street:
            messagebox.showwarning("Rua em falta", "Insira o nome da rua.", parent=self.root)
            return

        self.warning_button.configure(state="disabled")
        self.status.set("A preparar avisos da rua...")
        threading.Thread(target=self._warnings_worker, args=(street,), daemon=True).start()

    def _warnings_worker(self, street):
        try:
            roadXcross.ensure_ready()
            warnings = roadXcross.get_warnings(street=street)
            crosswalk_ids = [warning["smartcrosswalk"] for warning in warnings]
            features = _map_features(crosswalk_ids)

            self.root.after(0, self._show_warnings, warnings, features, None)
        except Exception as error:
            self.root.after(0, self._show_warnings, None, [], error)

    def _show_warnings(self, warnings, features, error):
        self.warning_button.configure(state="normal")

        if error is not None:
            self.status.set("Falha ao carregar avisos")
            messagebox.showerror("Falha ao carregar avisos", str(error), parent=self.root)
            return

        self.show_map_features(features)

        if not warnings:
            self.show_results("Avisos da rua", [("Resultado", "Sem passadeiras monitorizadas para esta rua.")])
            return

        self.show_results(
            "Avisos da rua",
            [
                (f"Passadeira {warning['crosswalk']}", roadXcross.format_warning(warning, lang="pt"))
                for warning in warnings
            ],
        )

    # --- mapa ---
    def show_map_features(self, features, fit_bounds=False):
        self.current_map_features = features
        self.current_map_fit_bounds = fit_bounds
        self.map_widget.delete_all_marker()
        self.map_widget.delete_all_path()

        points = []

        for feature in features:
            if feature["kind"] in ("crosswalk", "marker"):
                lat, lon = feature["position"]
                self.map_widget.set_marker(lat, lon, text=feature["label"])
                points.append((lat, lon))
            else:
                path = feature["position"]
                if len(path) >= 2:
                    self.map_widget.set_path(path, width=3)
                    points.extend(path)

        if points and fit_bounds:
            latitudes, longitudes = zip(*points)
            self.map_widget.fit_bounding_box(
                (max(latitudes), min(longitudes)),
                (min(latitudes), max(longitudes)),
            )
        elif points:
            center_lat = sum(point[0] for point in points) / len(points)
            center_lon = sum(point[1] for point in points) / len(points)
            self.map_widget.set_position(center_lat, center_lon)
            self.map_widget.set_zoom(16)
        else:
            self.map_widget.set_position(40.64, -8.65)
            self.map_widget.set_zoom(14)
# ================================================================================================================


def main():
    root = ctk.CTk()
    SmartCityApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()