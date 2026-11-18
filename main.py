import re
import pandas as pd
from datetime import datetime, timezone
import json
import statistics as st

smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv', usecols=['entityId', 'eventTimestamp'])

#-------------------------------------------------------------------------------------------------------------

def crossWalkInfo(crosswalk_number):
        print("\nCrosswalk {:04}: ".format(crosswalk_number))
        for i in range(10):
            raw_value = smart_crosswalk['eventTimestamp'][i]
            data = json.loads(raw_value)
            milliseconds = int(data["$date"]["$numberLong"])
            dt = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
            print(dt)

def allCrosswalkInfo():
    for i in range(24):
        id_iterator = "entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(i)
        print("\nCrosswalk {}: ".format(i))
        print(smart_crosswalk.query(id_iterator)
              ["eventTimestamp"][1:10])


#-------------------------------------------------------------------------------------------------------------
#df[df['Value']==df['Value'].max()]
while True:
    option = int(input("crossWalkInfo (1)   |   allCrosswalkInfo (2)   |   Exit (3)\n"))
    match option:
        case 1: crossWalkInfo(int(input("Which Crosswalk? ")))
        case 2: allCrosswalkInfo()
        case 3: break

smart_crosswalk.close()
