import pandas as pd
from datetime import datetime, timezone
import statistics as st
import tkinter as tk
import json
from shapely.geometry import shape
from shapely.geometry import Point
from shapely.geometry import LineString

#==Load Data==========================================================================================================
smart_crosswalk = pd.read_csv('C:/Users/rocha/OneDrive/Antigo/Ambiente de Trabalho/Hackhathon/data/smartcrosswalk.csv',nrows=1000)
road_segments = pd.read_csv('C:/Users/rocha/OneDrive/Antigo/Ambiente de Trabalho/Hackhathon/data/roadsegments_stats.csv', nrows=100000)
smart_parking = pd.read_csv('C:/Users/rocha/OneDrive/Antigo/Ambiente de Trabalho/Hackhathon/data/smartparking.csv',nrows=1000)
road_segments_reference = pd.read_csv('C:/Users/rocha/OneDrive/Antigo/Ambiente de Trabalho/Hackhathon/data/RoadSegment.csv')
#=====================================================================================================================

def convertDate(json_to_convert):
    data = json.loads(json_to_convert)
    milliseconds = int(data["$date"]["$numberLong"])
    dt = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
    return dt


def get_value(data_dict, field):
    val = data_dict.get(field)
    if isinstance(val, list) and len(val) > 0:
        val = val[0]
    if isinstance(val, dict):
        return float(val.get("$numberDouble", 0.0))
    return val


def where(cord):

    road_segements_im_in= []

    for i in range(len(road_segments_reference)):
        segment = shape(json.loads(road_segments_reference.loc[i, 'location']))
        if segment.intersects(cord):
            road_segements_im_in.append(road_segments_reference.loc[i,"entityId"])

    return road_segements_im_in


def roadSegmentInfo(new_road_segment):
    
    temp = road_segments.query("entityId=='"+str(new_road_segment)+"'")
    temp = temp.reset_index()

    if temp.empty:
        print("No data found", "\n")
        return

    max_allowed_velocity = 0

    document_jsons = temp["documentJson"].apply(json.loads)
    for row_idx, row in enumerate(temp.itertuples()):
        print("Index: ", road_segments.query("_id=='{}'".format(temp["_id"][row_idx]))["_id"].index.start + 2)
        print(convertDate(row.eventTimestamp))

        data_dict = document_jsons[row.Index]

        max_allowed_velocity = get_value(data_dict, "maxAllowedVelocity")
        print("Max Velocity:", get_value(data_dict, "maxVelocity"))
        print("Average Velocity Deviations:",get_value(data_dict, "averageVelocityStdDeviation"),"\n")

    #print(f"\n===Road 1{road_number:04}===")
    print(f"- Rua: {document_jsons.iloc[0].get("name")}")
    print(f"- Max Allowed Velocity: {round((max_allowed_velocity * 3.6), 2)}\n")

#roadSegmentInfo("urn:ngsi-ld:RoadSegment:11168")

cord = Point(-8.6524009,40.6360984)

for road_segment in where(cord):
    roadSegmentInfo(road_segment)
 
