import json
import statistics as st
import tkinter as tk
import threading
from datetime import datetime, timezone
from tkinter import messagebox

import customtkinter as ctk

import pandas as pd

from location_time import locationCoordinates
from road_match import find_nearest_road as lookup_nearest_road

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

#===Load data=================================================================================================
smart_crosswalk = pd.read_csv("data/smartcrosswalk.csv")
road_segments = pd.read_csv("data/roadsegments_stats.csv", nrows=1000)
smart_parking = pd.read_csv("data/smartparking.csv")
#=============================================================================================================

def convertDate(json_to_convert):
    data = json.loads(json_to_convert)
    milliseconds = int(data["$date"]["$numberLong"])
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


def get_value(data_dict, field):
    value = data_dict.get(field)
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        return float(value.get("$numberDouble", 0.0))
    return value


def get_rows(frame, entity_id):
    rows = frame.query("entityId == @entity_id").reset_index(drop=True)
    if rows.empty:
        raise ValueError(f"No records found for {entity_id}")
    return rows


def get_documents(rows):
    return rows["documentJson"].apply(json.loads).tolist()


#=============================================================================================================
class SmartCityApp:
    BG = "#F4F7FB"
    CARD = "#FFFFFF"
    NAVY = "#172B4D"
    BLUE = "#2463EB"
    MUTED = "#667085"
    BORDER = "#DDE5EF"

    def __init__(self, root):
        self.root = root
        self.root.title("Aveiro Smart City")
        self.root.geometry("1000x680")
        self.root.minsize(760, 540)
        self.root.configure(fg_color=self.BG)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.build_ui()

    def build_ui(self):
        page = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew", padx=28, pady=24)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 20))

        ctk.CTkLabel(
            header,
            text="Aveiro Smart City",
            text_color=self.NAVY,
            font=ctk.CTkFont(family="Segoe UI", size=27, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Visualização de dados sobre mobilidade",
            text_color=self.MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        ).pack(anchor="w", pady=(4, 0))

        content = ctk.CTkFrame(page, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=0, minsize=260)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        controls = ctk.CTkFrame(
            content,
            fg_color=self.CARD,
            corner_radius=20,
            border_width=1,
            border_color=self.BORDER,
        )
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 18))

        ctk.CTkLabel(
            controls,
            text="Pesquisa um registo",
            text_color=self.NAVY,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(22, 0))

        ctk.CTkLabel(
            controls,
            text="Escolha uma categoria e insira o id do elemento",
            text_color=self.MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            wraplength=210,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(6, 22))

        ctk.CTkLabel(
            controls,
            text="CATEGORIA",
            text_color=self.MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(0, 7))

        category_box = ctk.CTkComboBox(
            controls,
            values=("Smart crosswalk", "Road segment", "Parking spot"),
            state="readonly",
            height=40,
            corner_radius=11,
            border_color=self.BORDER,
            button_color=self.BLUE,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.category = category_box
        category_box.pack(fill="x", padx=20, pady=(0, 18))

        ctk.CTkLabel(
            controls,
            text="RECORD ID",
            text_color=self.MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(0, 7))

        self.record_id = tk.StringVar()
        self.id_entry = ctk.CTkEntry(
            controls,
            textvariable=self.record_id,
            height=42,
            corner_radius=11,
            border_color=self.BORDER,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            placeholder_text="Enter numeric ID",
        )
        self.id_entry.pack(fill="x", padx=20, pady=(0, 16))
        self.id_entry.bind("<Return>", lambda _event: self.search())

        ctk.CTkButton(
            controls,
            text="Procurar",
            command=self.search,
            height=42,
            corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
        ).pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkFrame(controls, height=1, fg_color=self.BORDER).pack(
            fill="x", padx=20, pady=8
        )

        self.nearest_road_button = ctk.CTkButton(
            controls,
            text="Rua mais próxima",
            command=self.find_nearest_road,
            height=42,
            corner_radius=12,
            fg_color="#EAF0FF",
            hover_color="#DCE6FF",
            text_color=self.BLUE,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
        )
        self.nearest_road_button.pack(fill="x", padx=20, pady=(8, 0))

        self.status = tk.StringVar(value="Ready")
        ctk.CTkLabel(
            controls,
            textvariable=self.status,
            text_color=self.MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            wraplength=210,
            justify="left",
        ).pack(anchor="w", side="bottom", padx=20, pady=20)

        result_card = ctk.CTkFrame(
            content,
            fg_color=self.CARD,
            corner_radius=20,
            border_width=1,
            border_color=self.BORDER,
        )
        result_card.grid(row=0, column=1, sticky="nsew")
        result_card.grid_columnconfigure(0, weight=1)
        result_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            result_card,
            text="Resultado",
            text_color=self.NAVY,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(22, 12))

        self.result_body = ctk.CTkScrollableFrame(
            result_card,
            fg_color="transparent",
            corner_radius=12,
        )
        self.result_body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.show_empty_state()

        self.id_entry.focus_set()

    def clear_results(self):
        for widget in self.result_body.winfo_children():
            widget.destroy()

    def show_empty_state(self):
        self.clear_results()

        ctk.CTkLabel(
            self.result_body,
            text="O resultado aparecerá aqui",
            text_color=self.NAVY,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(18, 5))

        ctk.CTkLabel(
            self.result_body,
            text="Escolha um tipo de dados e insira o seu ID",
            text_color=self.MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            wraplength=480,
            justify="left",
        ).pack(anchor="w", padx=10)

    def show_results(self, title, fields):
        self.clear_results()

        ctk.CTkLabel(
            self.result_body,
            text=title,
            text_color=self.NAVY,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(8, 16))

        for name, value in fields:
            row = ctk.CTkFrame(
                self.result_body,
                fg_color="#F7F9FC",
                corner_radius=12,
            )
            row.pack(fill="x", padx=6, pady=5)

            ctk.CTkLabel(
                row,
                text=name.upper(),
                text_color=self.MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            ).pack(anchor="w", padx=14, pady=(11, 0))

            ctk.CTkLabel(
                row,
                text=str(value),
                text_color=self.NAVY,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                wraplength=500,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(3, 11))

        self.status.set("Informação carregada com sucesso")

    def search(self):
        raw_id = self.record_id.get().strip()

        if not raw_id.isdigit():
            messagebox.showwarning(
                "ID Invalido",
                "Insira um ID n númerico",
                parent=self.root,
            )
            self.id_entry.focus_set()
            return

        number = int(raw_id)

        try:
            if self.category.get() == "Smart crosswalk":
                self.show_crosswalk(number)
            elif self.category.get() == "Road segment":
                self.show_road_segment(number)
            else:
                self.show_parking(number)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.status.set("Sem registos correspondentes")
            messagebox.showerror(
                "Falha a carregar registo",
                str(error),
                parent=self.root,
            )

    def show_crosswalk(self, number):
        entity_id = f"urn:ngsi-ld:SmartCrosswalk:{number:04}"
        rows = get_rows(smart_crosswalk, entity_id)
        documents = get_documents(rows)

        wait_times = []
        traverse_times = []

        for document in documents:
            wait = document.get("wait_time")
            if isinstance(wait, dict):
                if "$numberDouble" in wait:
                    wait_times.append(float(wait["$numberDouble"]))
                elif "$intDouble" in wait:
                    wait_times.append(float(wait["$intDouble"]))

            traverse = document.get("traverse_time")
            if isinstance(traverse, dict):
                if "$numberDouble" in traverse:
                    traverse_times.append(float(traverse["$numberDouble"]))
                elif "$intDouble" in traverse:
                    traverse_times.append(float(traverse["$intDouble"]))

        latest = documents[-1]
        average_wait = round(st.mean(wait_times), 2) if wait_times else "No data"
        average_traverse = round(st.mean(traverse_times), 2) if traverse_times else "No data"

        self.show_results(
            "Smart crosswalk",
            [
                ("ID da passadeira", f"{number:04}"),
                ("Observado por", get_value(latest, "observedBy") or "Not available"),
                ("Tempo médio de espera", f"{average_wait} seconds"),
                ("Tempo médio do precurso", f"{average_traverse} seconds"),
                ("Registos analizados", len(documents)),
            ],
        )

    def show_road_segment(self, number):
        entity_id = f"urn:ngsi-ld:RoadSegment:{number:05}"
        rows = get_rows(road_segments, entity_id)
        latest = get_documents(rows)[-1]

        speed = get_value(latest, "maxAllowedVelocity")
        try:
            speed_text = f"{round(float(speed) * 3.6, 2)} km/h"
        except (TypeError, ValueError):
            speed_text = "Indisponivel"

        self.show_results(
            "Road segment",
            [
                ("ID da rua", f"{number:05}"),
                ("Rua", get_value(latest, "name") or "Indisponivel"),
                ("Velocidade máxima permitida", speed_text),
            ],
        )

    def show_parking(self, number):
        entity_id = f"urn:ngsi-ld:ParkingSpot:{number:04}"
        rows = get_rows(smart_parking, entity_id)
        latest = get_documents(rows)[-1]

        self.show_results(
            "Parking spot",
            [
                ("ID do espaço", f"{number:04}"),
                ("Estado", get_value(latest, "status") or "Indisponivel"),
                ("Localização", get_value(latest, "location") or "Indisponivel"),
            ],
        )

    def find_nearest_road(self):
        self.nearest_road_button.configure(state="disabled")
        self.status.set("Á procura da rua mais próxima")
        threading.Thread(target=self._lookup_nearest_road, daemon=True).start()

    def _lookup_nearest_road(self):
        try:
            latitude, longitude, *_ = locationCoordinates()
            match = lookup_nearest_road(latitude, longitude)
            if not match:
                self.root.after(0, self._show_nearest_road_result, None, None, None)
                return

            now = datetime.now().astimezone()
            velocities = []
            rows = road_segments[road_segments["entityId"] == match["entityId"]]
            for row in rows.itertuples(index=False):
                observed_at = convertDate(row.eventTimestamp).astimezone(now.tzinfo)
                if observed_at.hour == now.hour:
                    velocity = get_value(
                        json.loads(row.documentJson), "averageVelocity"
                    )
                    if velocity is not None:
                        velocities.append(float(velocity))

            average_velocity = (
                f"{st.mean(velocities) * 3.6:.2f} km/h"
                if velocities
                else "Sem registos para esta hora"
            )
            self.root.after(
                0, self._show_nearest_road_result, match, average_velocity, None
            )
        except Exception as error:
            self.root.after(
                0, self._show_nearest_road_result, None, None, error
            )

    def _show_nearest_road_result(self, match, average_velocity, error):
        self.nearest_road_button.configure(state="normal")
        if error is not None:
            self.status.set("Falha a procurar a rua mais próxima")
            messagebox.showerror(
                "Falha a procurar a rua mais próxima",
                str(error),
                parent=self.root,
            )
            return

        if not match:
            self.show_results("Rua mais próxima", [("Resultado", "Não foram encontradas ruas na próximidade")])
            return

        self.show_results(
            "Rua mais próxima",
            [
                ("Nome", match["name"]),
                ("ID da rua", match["entityId"]),
                ("Distância", f"{match['distance_m']:.1f} m"),
                ("Velocidade média", average_velocity),
            ],
        )
#=============================================================================================================

def main():
    root = ctk.CTk()
    SmartCityApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()