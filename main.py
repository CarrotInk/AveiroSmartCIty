import pandas as pd
from datetime import datetime, timezone
import json

#==Load Data==========================================================================================================
smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv')
road_segments = pd.read_csv('data/roadsegments_stats.csv', nrows=1000)
#=====================================================================================================================

def convertDate(json_to_convert):
    data = json.loads(json_to_convert)
    milliseconds = int(data["$date"]["$numberLong"])
    dt = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
    return dt

#===Road Segments====================================================================================================
def roadSegmentInfo(road_number):
        temp = road_segments.query("entityId=='urn:ngsi-ld:RoadSegment:1{:04}'".format(road_number))
        temp = temp.reset_index()

        for i in range(len(temp['documentJson'])):
            data_dict = json.loads(temp['documentJson'][i])

            def get_value(field):
                val = data_dict.get(field)
                if isinstance(val, list) and len(val) > 0: val = val[0]
                if isinstance(val, dict): return float(val.get("$numberDouble", 0.0))
                return val

            print(convertDate(temp["eventTimestamp"][i]))
            print("Max Allowed Velocity:", get_value("maxAllowedVelocity"))
            print("Max Velocity:", get_value("maxVelocity"), "\n")

        print("\nRoad 1{:04}".format(road_number))
        print("Rua:", json.loads(temp['documentJson'][0])["name"], "\n")

#=====================================================================================================================

#===CrossWalks========================================================================================================
def crossWalkInfo(crosswalk_number):
        temp = smart_crosswalk.query("entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(crosswalk_number))
        temp = temp.reset_index()

        for i in range(10):
            data_dict = json.loads(temp['documentJson'][i])

            print(convertDate(temp["eventTimestamp"][i]))
            print("Traverse Time:", float(data_dict["traverse_time"]["$numberDouble"]))
            try: print("Wait Time:", float(data_dict["wait_time"]["$numberDouble"]), "\n")
            except: print("Wait Time: 0.0\n")

        print("\nCrosswalk {:04}".format(crosswalk_number))
        print("Observed By:", json.loads(temp['documentJson'][0])["observedBy"], "\n")

#=====================================================================================================================
while True:
    option = int(input("Crosswalk Info (1)   |   Road Segments (2)   |   Exit (3)\n"))
    match option:
        case 1: crossWalkInfo(int(input("Which Crosswalk? ")))
        case 2: roadSegmentInfo(int(input("Which Road? ")))
        case 3: break

