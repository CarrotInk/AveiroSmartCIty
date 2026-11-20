import requests
import folium
import datetime

def locationCoordinates():
    response = requests.get("https://ipinfo.io", timeout=10)
    response.raise_for_status()
    data = response.json()

    try:
        latitude, longitude = map(float, data["loc"].split(","))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Location service returned invalid coordinates.") from error

    city = data.get("city", "Unknown")
    state = data.get("region", "Unknown")
    return latitude, longitude, city, state


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