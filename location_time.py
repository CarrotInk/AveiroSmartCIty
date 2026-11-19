from pprint import pprint

import requests
import folium
import datetime

def locationCoordinates():
    try:
        response = requests.get('https://ipinfo.io')
        data = response.json()
        pprint(data)
        loc = data['loc'].split(',')
        lat, long = float(loc[0]), float(loc[1])
        city = data.get('city', 'Unknown')
        state = data.get('region', 'Unknown')
        return lat, long, city, state
    except:
        print("Internet Not avialable")
        exit()


def gps_locator():
    obj = folium.Map(location=[0, 0], zoom_start=2)
    now = datetime.datetime.now()

    try:
        lat, long, city, state = locationCoordinates()
        print("You Are in {},{}".format(city, state))
        print("Your latitude = {} and longitude = {}".format(lat, long))
        print(f"Current time: {now.time()}")
        folium.Marker([lat, long], popup='Current Location').add_to(obj)
        fileName = "C:/screengfg/Location" + str(datetime.date.today()) + ".html"

        obj.save(fileName)
        return fileName

    except:
        return False