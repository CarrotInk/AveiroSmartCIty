import json
import statistics as st
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk

import pandas as pd

from road_match import nearest_road

#===Load data=================================================================================================
smart_crosswalk = pd.read_csv("data/smartcrosswalk.csv", nrows=1000)
road_segments = pd.read_csv("data/roadsegments_stats.csv", nrows=1000)
smart_parking = pd.read_csv("data/smartparking.csv", nrows=1000)
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
        self.root.geometry("900x600")
        self.root.minsize(720, 500)
        self.root.configure(bg=self.BG)

        self.configure_styles()
        self.build_ui()

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("App.TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure(
            "Title.TLabel",
            background=self.BG,
            foreground=self.NAVY,
            font=("Segoe UI", 23, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=self.BG,
            foreground=self.MUTED,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Section.TLabel",
            background=self.CARD,
            foreground=self.NAVY,
            font=("Segoe UI", 13, "bold"),
        )
        style.configure(
            "Field.TLabel",
            background=self.CARD,
            foreground=self.MUTED,
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Value.TLabel",
            background=self.CARD,
            foreground=self.NAVY,
            font=("Segoe UI", 11),
        )
        style.configure(
            "Action.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(12, 10),
        )

    def build_ui(self):
        page = ttk.Frame(self.root, style="App.TFrame", padding=(30, 24))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        header = ttk.Frame(page, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 22))

        ttk.Label(
            header,
            text="Aveiro Smart City",
            style="Title.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            header,
            text="Mobility and infrastructure data viewer",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        content = ttk.Frame(page, style="App.TFrame")
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=0, minsize=250)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        controls = ttk.Frame(content, style="Card.TFrame", padding=20)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 18))

        ttk.Label(
            controls,
            text="Look up a record",
            style="Section.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            controls,
            text="Choose a category and enter its numeric ID.",
            style="Subtitle.TLabel",
            wraplength=210,
        ).pack(anchor="w", pady=(6, 18))

        ttk.Label(
            controls,
            text="CATEGORY",
            style="Field.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        self.category = tk.StringVar(value="Smart crosswalk")
        category_box = ttk.Combobox(
            controls,
            textvariable=self.category,
            state="readonly",
            values=("Smart crosswalk", "Road segment", "Parking spot"),
            font=("Segoe UI", 10),
        )
        category_box.pack(fill="x", pady=(0, 14), ipady=4)

        ttk.Label(
            controls,
            text="RECORD ID",
            style="Field.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        self.record_id = tk.StringVar()
        self.id_entry = ttk.Entry(
            controls,
            textvariable=self.record_id,
            font=("Segoe UI", 12),
        )
        self.id_entry.pack(fill="x", pady=(0, 14), ipady=5)
        self.id_entry.bind("<Return>", lambda _event: self.search())

        ttk.Button(
            controls,
            text="Search",
            style="Action.TButton",
            command=self.search,
        ).pack(fill="x", pady=(0, 8))

        ttk.Separator(controls).pack(fill="x", pady=10)

        ttk.Button(
            controls,
            text="Find nearest road",
            style="Action.TButton",
            command=self.find_nearest_road,
        ).pack(fill="x")

        self.status = tk.StringVar(value="Ready")
        ttk.Label(
            controls,
            textvariable=self.status,
            style="Subtitle.TLabel",
            wraplength=210,
        ).pack(anchor="w", side="bottom", pady=(18, 0))

        result_card = ttk.Frame(content, style="Card.TFrame", padding=24)
        result_card.grid(row=0, column=1, sticky="nsew")
        result_card.columnconfigure(0, weight=1)

        ttk.Label(
            result_card,
            text="Results",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 16))

        self.result_body = ttk.Frame(result_card, style="Card.TFrame")
        self.result_body.grid(row=1, column=0, sticky="new")
        self.show_empty_state()

        self.id_entry.focus_set()

    def clear_results(self):
        for widget in self.result_body.winfo_children():
            widget.destroy()

    def show_empty_state(self):
        self.clear_results()

        ttk.Label(
            self.result_body,
            text="Your results will appear here",
            style="Value.TLabel",
        ).pack(anchor="w", pady=(12, 4))

        ttk.Label(
            self.result_body,
            text="Choose a data type and enter its ID to view the latest information.",
            style="Subtitle.TLabel",
            wraplength=480,
            justify="left",
        ).pack(anchor="w")

    def show_results(self, title, fields):
        self.clear_results()

        ttk.Label(
            self.result_body,
            text=title,
            style="Section.TLabel",
        ).pack(anchor="w", pady=(0, 14))

        for name, value in fields:
            row = ttk.Frame(self.result_body, style="Card.TFrame")
            row.pack(fill="x", pady=7)

            ttk.Label(
                row,
                text=name.upper(),
                style="Field.TLabel",
            ).pack(anchor="w")

            ttk.Label(
                row,
                text=str(value),
                style="Value.TLabel",
                wraplength=500,
                justify="left",
            ).pack(anchor="w", pady=(3, 0))

        self.status.set("Information loaded successfully")

    def search(self):
        raw_id = self.record_id.get().strip()

        if not raw_id.isdigit():
            messagebox.showwarning(
                "Invalid ID",
                "Enter a numeric record ID.",
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
            self.status.set("No matching record")
            messagebox.showerror(
                "Unable to load record",
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
                ("Crosswalk ID", f"{number:04}"),
                ("Observed by", get_value(latest, "observedBy") or "Not available"),
                ("Average wait time", f"{average_wait} seconds"),
                ("Average traverse time", f"{average_traverse} seconds"),
                ("Records analyzed", len(documents)),
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
            speed_text = "Not available"

        self.show_results(
            "Road segment",
            [
                ("Road ID", f"{number:05}"),
                ("Street", get_value(latest, "name") or "Not available"),
                ("Maximum allowed speed", speed_text),
            ],
        )

    def show_parking(self, number):
        entity_id = f"urn:ngsi-ld:ParkingSpot:{number:04}"
        rows = get_rows(smart_parking, entity_id)
        latest = get_documents(rows)[-1]

        self.show_results(
            "Parking spot",
            [
                ("Spot ID", f"{number:04}"),
                ("Status", get_value(latest, "status") or "Not available"),
                ("Location", get_value(latest, "location") or "Not available"),
            ],
        )

    def find_nearest_road(self):
        try:
            temporary_label = tk.Label(self.root)
            match = nearest_road(temporary_label)

            if not isinstance(match, dict) or "entityId" not in match:
                raise ValueError("No nearest-road result was returned.")

            self.show_results(
                "Nearest road",
                [("Road Name", match["name"]), ("Road entity ID", match["entityId"])],
            )
        except Exception as error:
            self.status.set("Nearest-road lookup failed")
            messagebox.showerror(
                "Unable to find nearest road",
                str(error),
                parent=self.root,
            )
#=============================================================================================================

def main():
    root = tk.Tk()
    SmartCityApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()