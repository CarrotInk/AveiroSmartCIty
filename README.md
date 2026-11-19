# Aveiro Smart City — Tech Week Hackathon 2026

A hackathon project exploring how open and simulated smart-city data can help people understand mobility and public-space activity in Aveiro, Portugal.

## Project goals

This repository is a prototype for exploring city data related to:

- **Road segments:** road names, locations, and vehicle-speed information.
- **Smart parking:** parking-spot locations and occupancy status.
- **Smart crosswalks:** pedestrian wait and traversal times.

The project includes a small Tkinter interface for looking up road, parking, and crosswalk records, along with experiments for identifying nearby roads and displaying a device-derived location on a map. The code and data are exploratory and may need adaptation as the hackathon project develops.

## Repository contents

- `main.py` — prototype desktop interface for querying road, parking, and crosswalk data.
- `road_match.py` — experiment for finding the nearest road segment to a location.
- `location.py` — IP-based location lookup and map-generation experiment.
- `data/roadsegments_stats.csv` — road-segment records.
- `data/smartparking.csv` — smart-parking records.
- `data/smartcrosswalk.csv` — smart-crosswalk records.
- `data/touriststats_transitions.csv` and `data/touriststats_visits.csv` — tourism-related datasets.
- `Data available.txt` — sample records and fields available in the data.
- `Hackathon_Tech_Week_2026.pdf` — hackathon reference material.

## Getting started

### Requirements

- Python 3
- `pandas`
- `requests` and `folium` for the location/map experiment
- Tkinter, which is commonly included with desktop Python installations

Install the Python packages with:

```bash
python -m pip install pandas requests folium
```

### Run the prototype

From the repository root, try the desktop interface with:

```bash
python main.py
```

The scripts expect to be run from the repository root so their relative `data/...` paths resolve. The location experiment uses an external IP-geolocation service and requires an internet connection. Review and adjust its output path before using it on a different machine.

> **Prototype note:** This is hackathon-stage code. Some scripts are exploratory and may require fixes or configuration for your environment before they run successfully.

## Data notes

The CSV records contain serialized JSON documents, including NGSI-LD-style entity IDs such as `RoadSegment`, `ParkingSpot`, and `SmartCrosswalk`. The sample file `Data available.txt` illustrates fields such as road geometry and speed, parking status, and crosswalk wait/traverse times. Treat the datasets as project inputs, and check their source, licensing, and update frequency before redistributing or using them for operational decisions.

## Contributing

Ideas, fixes, documentation, and improvements to the data exploration are welcome. For hackathon contributions, please include a short description of the change and any setup or data assumptions it introduces.

## Acknowledgements

Created as part of the **Aveiro City Hackathon / Tech Week 2026**. Add event, team, and data-provider acknowledgements here as they are confirmed.
