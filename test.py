from datetime import datetime, timezone
import json
import pandas as pd

smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv', usecols=['_id', 'entityId', 'eventTimestamp'])

raw_value = smart_crosswalk['eventTimestamp'][1]
data = json.loads(raw_value)
milliseconds = int(data["$date"]["$numberLong"])
dt = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
print(dt)

temp = smart_crosswalk.query(
    "entityId=='urn:ngsi-ld:SmartCrosswalk:{:04}'".format(crosswalk_number))
temp = list(temp["eventTimestamp"][0:10])

new_list = []
for i in temp:
    new_list.append(''.join(re.findall(r'\d', i)))

print(new_list)
average = st.mean(milliseconds)
print(''.join(re.findall(r'\d', average)))