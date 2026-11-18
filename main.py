import pandas as pd
import json
from datetime import datetime, timezone

smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv', usecols=['_id', 'entityId', 'eventTimestamp'])

#-------------------------------------------------------------------------------------------------------------

def crossWalkInfo(crosswalk_number):
        print("\nCrosswalk {:04}: ".format(crosswalk_number))
        print(smart_crosswalk.query(
            "entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(crosswalk_number))
              ["eventTimestamp"][1:10])

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
