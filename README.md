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
- `location_time.py` — IP-based location lookup and map-generation experiment.
- `data/roadsegments_stats.csv` — road-segment records.
- `data/smartparking.csv` — smart-parking records.
- `data/smartcrosswalk.csv` — smart-crosswalk records.
- `data/touriststats_transitions.csv` and `data/touriststats_visits.csv` — tourism-related datasets.
- `Data available.json` — sample records and fields available in the data.
- `Hackathon_Tech_Week_2026.pdf` — hackathon reference material.

## Data notes

The CSV records contain serialized JSON documents, including NGSI-LD-style entity IDs such as `RoadSegment`, `ParkingSpot`, and `SmartCrosswalk`. The sample file `Data available.json` illustrates fields such as road geometry and speed, parking status, and crosswalk wait/traverse times. Treat the datasets as project inputs, and check their source, licensing, and update frequency before redistributing or using them for operational decisions.

## Contributing

Ideas, fixes, documentation, and improvements to the data exploration are welcome. For hackathon contributions, please include a short description of the change and any setup or data assumptions it introduces.

## Acknowledgements

Created as part of the **Aveiro City Hackathon / Tech Week 2026** by the team:
###H.D.S.
- Henrique Romão
- Diogo Caseiro
- Simão Rocha
