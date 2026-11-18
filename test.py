import pandas as pd
from datetime import datetime, timezone
import json

smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv')

temp = smart_crosswalk.query("entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(2))
temp = temp.reset_index()

print(temp["_id"][0], "\n")
print(smart_crosswalk.query("_id=='{}'".format(temp["_id"][0]))["_id"].index.start)