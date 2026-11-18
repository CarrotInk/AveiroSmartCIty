from datetime import datetime, timezone
import json
import pandas as pd

smart_crosswalk = pd.read_csv('data/smartcrosswalk.csv', usecols=['_id', 'entityId', 'eventTimestamp'])

raw_value = smart_crosswalk['eventTimestamp'][1]
data = json.loads(raw_value)
milliseconds = int(data["$date"]["$numberLong"])
dt = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
print(dt)