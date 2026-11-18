from tkinter import font

import pandas as pd
from datetime import datetime, timezone
import statistics as st
import tkinter as tk
import json

#==Load Data==========================================================================================================
smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv', nrows = 1000)
road_segments = pd.read_csv('data/roadsegments_stats.csv', nrows=1000)
#=====================================================================================================================

def convertDate(json_to_convert):
    data = json.loads(json_to_convert)
    milliseconds = int(data["$date"]["$numberLong"])
    dt = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
    return dt

#===Road Segments====================================================================================================
def get_value(data_dict, field):
    val = data_dict.get(field)
    if isinstance(val, list) and len(val) > 0:
        val = val[0]
    if isinstance(val, dict):
        return float(val.get("$numberDouble", 0.0))
    return val


def roadSegmentInfo(road_number, label):
    temp = road_segments.query("entityId=='urn:ngsi-ld:RoadSegment:1{:04}'".format(road_number))
    temp = temp.reset_index()

    max_allowed_velocity = 0

    document_jsons = temp["documentJson"].apply(json.loads)

    for row_idx, row in enumerate(temp.itertuples()):
        print("Index: ", road_segments.query("_id=='{}'".format(temp["_id"][row_idx]))["_id"].index.start + 2)
        print(convertDate(row.eventTimestamp))

        data_dict = document_jsons[row.Index]

        max_allowed_velocity = get_value(data_dict, "maxAllowedVelocity")
        print("Max Velocity:", get_value(data_dict, "maxVelocity"))
        print("Average Velocity Deviations:",get_value(data_dict, "averageVelocityStdDeviation"),"\n")

    print(f"\nRoad 1{road_number:04}")
    print("Rua:", document_jsons.iloc[0].get("name"))
    print("Max Allowed Velocity:", round((max_allowed_velocity * 3.6), 2), "\n")

#=====================================================================================================================

#===CrossWalks========================================================================================================
def crossWalkInfo(crosswalk_number, label):
    temp = smart_crosswalk.query("entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(crosswalk_number))
    temp = temp.reset_index()

    wait_times = []
    traverse_times = []

    document_jsons = temp["documentJson"].apply(json.loads)

    for row_idx, row in enumerate(temp.itertuples()):
        print("Index: ", smart_crosswalk.query("_id=='{}'".format(temp["_id"][row_idx]))["_id"].index.start + 2)
        print(convertDate(row.eventTimestamp))

        data_dict = document_jsons[row.Index]

        traverse_time = data_dict.get("traverse_time")
        if traverse_time and ("$numberDouble" in traverse_time):
            print("Traverse Time:", float(traverse_time["$numberDouble"]), "")
            traverse_times.append(float(traverse_time["$numberDouble"]))
        elif traverse_time and ("$intDouble" in traverse_time):
            print("Traverse Time:", int(traverse_time["$intDouble"]), "")

        wait_time = data_dict.get("wait_time")
        if wait_time and ("$numberDouble" in wait_time):
            print("Wait Time:", float(wait_time["$numberDouble"]), "\n")
            wait_times.append(float(wait_time["$numberDouble"]))
        elif wait_time and ("$intDouble" in wait_time):
            print("Wait Time:", int(wait_time["$intDouble"]), "")

    print(f"\nCrosswalk {crosswalk_number:04}")
    print(f"Observed By:{document_jsons.iloc[0].get("observedBy")}")
    print(f"Average Wait Time: {round(st.mean(wait_times), 2)}")
    print(f"Average Traverse Time: {round(st.mean(traverse_times), 2)}\n")

    label.configure(text = f"\n===Crosswalk {crosswalk_number:04}===\n"
                           f"- Observed By:{document_jsons.iloc[0].get("observedBy")}\n"
                           f"- Average Wait Time: {round(st.mean(wait_times), 2)}\n"
                           f"- Average Traverse Time: {round(st.mean(traverse_times), 2)}")

#=====================================================================================================================
root = tk.Tk()
root.title("Welcome to GeekForGeeks")
root.geometry('800x600')

txt = tk.Entry(root, width=25)
txt.pack(side='top', anchor='center', pady=5)
txt.config(font=("Arial", 15))

lbl = tk.Label(root, text = "", justify="left")


def crosswalk_clicked():
    crossWalkInfo(int(txt.get()), lbl)

def roadsegment_clicked():
    roadSegmentInfo(int(txt.get()), lbl)

crosswalk_btn = tk.Button(root, text = "Crosswalks", command= crosswalk_clicked, justify="center")
crosswalk_btn.pack(side='left', anchor='ne', expand=True, pady=False)
crosswalk_btn.config(font=("Arial", 15))
roadsegment_btn = tk.Button(root, text = "Road Segments", command = roadsegment_clicked, justify="center")
roadsegment_btn.pack(side='right', anchor='nw', expand=True, pady=False)
roadsegment_btn.config(font=("Arial", 15))

lbl.place(relx=0.64, rely=0.15, anchor="ne")
lbl.config(font=("Arial", 15))

root.mainloop()