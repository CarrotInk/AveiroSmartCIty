from unittest import case

import pandas as pd
from datetime import datetime, timezone
import json

#==Load Data==========================================================================================================
smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv',nrows=1000)
road_segments = pd.read_csv('data/roadsegments_stats.csv', nrows=100000)
smart_parking = pd.read_csv('data/smartparking.csv',nrows=1000)
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


def roadSegmentInfo(road_number):
  temp = road_segments.query(
      "entityId=='urn:ngsi-ld:RoadSegment:1{:04}'".format(road_number)
  )
  temp = temp.reset_index()

  if temp.empty:
    print(f"\nRoad 1{road_number:04} não encontrada.")
    return

  max_allowed_velocity = 0

  # 1. Fazer o parse de todos os JSONs de uma vez
  document_jsons = temp["documentJson"].apply(json.loads)

  print(f"\nRoad 1{road_number:04}")
  print("Rua:", document_jsons.iloc[0].get("name"))

  # 2. Iterar de forma eficiente com itertuples
  for row_idx, row in enumerate(temp.itertuples()):
    print("Index:", row_idx + 2)
    print(convertDate(row.eventTimestamp))

    data_dict = document_jsons[row.Index]

    max_allowed_velocity = get_value(data_dict, "maxAllowedVelocity")
    print("Max Velocity:", get_value(data_dict, "maxVelocity"))
    print(
        "Average Velocity Deviations:",
        get_value(data_dict, "averageVelocityStdDeviation"),
        "\n",
    )

  print("Max Allowed Velocity:", max_allowed_velocity * 3.6, "\n")

#===Parking==================================================================================================================
def parkingInfo(parking_number):

    temp = smart_parking.query("entityId == 'urn:ngsi-ld:ParkingSpot:{:04}'".format(parking_number))
    temp = temp.reset_index()

    for row_idx, row in enumerate(temp.itertuples()):

        print("Index: ", smart_parking.query("_id=='{}'".format(temp["_id"][row_idx]))["_id"].index.start + 2)

        data_dict = temp["documentJson"].apply(json.loads)

        print("State Park:", get_value(data_dict, "state"))
        print("Location Park:", get_value(data_dict, "location"))

#===CrossWalks========================================================================================================
def crossWalkInfo(crosswalk_number):
  temp = smart_crosswalk.query("entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(crosswalk_number))
  temp = temp.reset_index()

  if temp.empty:
    print(f"\nCrosswalk {crosswalk_number:04} não encontrada.")
    return

  document_jsons = temp["documentJson"].apply(json.loads)

  print(f"\nCrosswalk {crosswalk_number:04}")
  print("Observed By:", document_jsons.iloc[0].get("observedBy"), "\n")

  for row_idx, row in enumerate(temp.itertuples()):
    print("Index:", row_idx + 2)
    print(convertDate(row.eventTimestamp))

    data_dict = document_jsons[row.Index]

    traverse_time = data_dict.get("traverse_time")
    if traverse_time and ("$numberDouble" in traverse_time):
        print("Traverse Time:", float(traverse_time["$numberDouble"]), "")
    elif traverse_time and ("$intDouble" in traverse_time):
        print("Traverse Time:", int(traverse_time["$intDouble"]), "")

    wait_time = data_dict.get("wait_time")
    if wait_time and ("$numberDouble" in wait_time):
      print("Wait Time:", float(wait_time["$numberDouble"]), "\n")
    elif wait_time and ("$intDouble" in wait_time):
      print("Wait Time:", int(wait_time["$intDouble"]), "")

#=====================================================================================================================
while True:
    option = int(input("Crosswalk Info (1)   |   Road Segments Info (2)   |     Parking Status (3)      |   Exit (4)\n"))
    match option:
        case 1: crossWalkInfo(int(input("Which Crosswalk? ")))
        case 2: roadSegmentInfo(int(input("Which Road? ")))
        case 3: parkingInfo(int(input("Which Parking? ")))
        case 4: break

