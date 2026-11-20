"""
parking_availability - lugares de estacionamento livres a uma hora definida pelo utilizador (ATCLL, Aveiro).

Lê o data/smartparking.csv (um registo por mudança/leitura de estado de cada lugar) e responde a:
    "Que lugares estavam livres às 14:30?"

Duas formas de responder:
  - estado nesse instante (por defeito): para cada lugar usa a ÚLTIMA leitura até essa hora.
    Sem data usa o último dia que existe nos dados.
  - típico (--typical): repete o cálculo em todos os dias dos dados e diz, para cada lugar, em que
    percentagem dos dias estava livre a essa hora.

Uso como módulo (no main):
    import parking_availability as pa
    df = pa.load_parking_data()
    r = pa.availability_at(df, "14:30")                    # ou date="2026-11-10"
    print(pa.format_availability(r))
    r["free_spots"]            # DataFrame: entityId, lat, lon, age_h
    t = pa.typical_availability(df, "14:30")

Uso na linha de comandos (sem argumentos abre o menu):
    python parking_availability.py 14:30
    python parking_availability.py 14:30 --date 2026-11-10 --max-age 6
    python parking_availability.py 14:30 --typical
    python parking_availabiality.py --nearest
    python parking_availabiality.py --nearest --lat 40.64 --lon -8.65

As horas são UTC (== hora de Aveiro em novembro), como no resto do projeto. Só precisa de pandas.
"""
import argparse
import json
import math
from pathlib import Path

import pandas as pd

import fastest_route
import roadXcross as rx

# ----------------------------- configuração ---------------------------------
PARKING_CSV = rx.EVENTS_DIR / "smartparking.csv"

# Valores do campo "status" (em minúsculas). Se os teus dados usarem outros, acrescenta-os aqui:
# ao carregar, o programa imprime os valores que encontrou e quais não reconheceu.
FREE_VALUES = {"free", "libre", "livre", "available", "vacant", "empty", "0", "false"}
OCCUPIED_VALUES = {"occupied", "ocupado", "taken", "busy", "full", "1", "true"}

FREE, OCCUPIED, UNKNOWN = "livre", "ocupado", "desconhecido"


# ------------------------------ leitura -------------------------------------
def _unwrap(value):
    """Valores NGSI-LD vêm como {"type": "Property", "value": ...}."""
    while isinstance(value, dict) and "value" in value:
        value = value["value"]
    return value


def _to_timestamp(value):
    """Converte vários formatos (Timestamp, segundos/milissegundos, texto ISO, Extended JSON) para UTC."""
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            value = rx._flat(json.loads(value))
        except ValueError:
            return None
    value = _unwrap(value)
    try:
        if isinstance(value, pd.Timestamp):
            ts = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            ts = pd.to_datetime(value, unit="ms" if value > 1e11 else "s", utc=True)
        elif isinstance(value, str):
            ts = pd.to_datetime(value, utc=True)
        else:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    if pd.isna(ts):
        return None
    return ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")


def _lat_lon(location):
    """(lat, lon) de um GeoJSON Point (coordenadas [lon, lat]) ou de um dict lat/lon; None se não houver."""
    location = _unwrap(location)
    if isinstance(location, str):
        try:
            location = rx._flat(json.loads(location))
        except ValueError:
            return None
    if not isinstance(location, dict):
        return None
    coords = _unwrap(location.get("coordinates"))
    try:
        if isinstance(coords, list) and len(coords) >= 2 and not isinstance(coords[0], list):
            return float(coords[1]), float(coords[0])
        lat = location.get("latitude", location.get("lat"))
        lon = location.get("longitude", location.get("lon"))
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    except (TypeError, ValueError):
        pass
    return None


def _state(raw_status):
    text = str(raw_status).strip().lower() if raw_status is not None else ""
    if text in FREE_VALUES:
        return FREE
    if text in OCCUPIED_VALUES:
        return OCCUPIED
    return UNKNOWN


def load_parking_data(csv_path=PARKING_CSV):
    """Devolve um DataFrame ordenado no tempo: entityId, ts (UTC), status, state, lat, lon."""
    csv_path = Path(csv_path)
    header = pd.read_csv(csv_path, nrows=0, encoding="utf-8-sig").columns
    wanted = [c for c in ("entityId", "eventTimestamp", "documentJson") if c in header]
    if "entityId" not in wanted or "documentJson" not in wanted:
        raise KeyError(f"{csv_path.name}: preciso das colunas entityId e documentJson (encontrei {list(header)[:8]}).")
    raw = rx.read_csv_safe(csv_path, usecols=wanted)
    column_ts = raw["eventTimestamp"] if "eventTimestamp" in raw.columns else [None] * len(raw)

    rows, bad = [], 0
    for entity_id, col_ts, doc_json in zip(raw["entityId"], column_ts, raw["documentJson"]):
        try:
            doc = rx._flat(json.loads(doc_json))
        except (ValueError, TypeError):
            bad += 1
            continue
        if not isinstance(doc, dict):
            bad += 1
            continue
        ts = _to_timestamp(doc.get("eventTimestamp")) or _to_timestamp(col_ts)
        if ts is None:
            bad += 1
            continue
        status = _unwrap(doc.get("status"))
        pos = _lat_lon(doc.get("location"))
        rows.append((entity_id, ts, status, _state(status), pos[0] if pos else None, pos[1] if pos else None))

    df = pd.DataFrame(rows, columns=["entityId", "ts", "status", "state", "lat", "lon"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)

    if bad:
        print(f"Aviso: {bad} registo(s) sem timestamp válido ou com JSON inválido ignorado(s).")
    if len(df):
        counts = df["status"].astype(str).value_counts().to_dict()
        print(f"Valores de 'status' encontrados: {counts}")
        unknown = sorted(set(df.loc[df["state"] == UNKNOWN, "status"].astype(str)))
        if unknown:
            print(f"Aviso: não reconheço estes valores de status: {unknown}. "
                  f"Acrescenta-os a FREE_VALUES ou OCCUPIED_VALUES em parking_availability.py.")
        print(f"{len(df):,} registos de {df['entityId'].nunique()} lugares, de {df['ts'].min():%Y-%m-%d %H:%M} "
              f"a {df['ts'].max():%Y-%m-%d %H:%M} UTC.")
    return df


# ------------------------------ cálculo -------------------------------------
def parse_moment(df, time_str, date_str=None):
    """Instante (UTC) a partir de 'HH:MM' e, opcionalmente, 'AAAA-MM-DD' (por defeito, o último dia dos dados)."""
    try:
        parsed = pd.to_datetime(time_str.strip(), format="%H:%M")
    except ValueError:
        raise ValueError("Formato de hora inválido. Usa HH:MM (ex: 14:30).")
    if date_str:
        try:
            day = pd.to_datetime(date_str.strip(), format="%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Formato de data inválido. Usa AAAA-MM-DD (ex: 2026-11-10).")
    else:
        if df.empty:
            raise ValueError("Não há dados de estacionamento.")
        day = df["ts"].max().date()
    return pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=parsed.hour, minute=parsed.minute, tz="UTC")


def _spot_positions(df):
    last = df.dropna(subset=["lat", "lon"]).groupby("entityId").tail(1)
    return last[["entityId", "lat", "lon"]]


def state_at(df, moment, max_age_h=None):
    """Estado de cada lugar em `moment`: a última leitura até esse instante.
    Sem leitura anterior (ou mais antiga que max_age_h horas) fica 'desconhecido'."""
    last = df[df["ts"] <= moment].groupby("entityId").tail(1)[["entityId", "ts", "state"]]
    spots = pd.DataFrame({"entityId": sorted(df["entityId"].unique())})
    spots = spots.merge(last, on="entityId", how="left").merge(_spot_positions(df), on="entityId", how="left")
    spots["age_h"] = (moment - spots["ts"]).dt.total_seconds() / 3600
    spots["state"] = spots["state"].fillna(UNKNOWN)
    if max_age_h is not None:
        spots.loc[spots["age_h"] > max_age_h, "state"] = UNKNOWN
    return spots


def availability_at(df, time_str, date_str=None, max_age_h=None):
    """Resumo dos lugares livres/ocupados à hora indicada (dict; ver format_availability)."""
    moment = parse_moment(df, time_str, date_str)
    spots = state_at(df, moment, max_age_h)
    free = spots[spots["state"] == FREE].sort_values("entityId").reset_index(drop=True)
    return {
        "moment": moment,
        "total": len(spots),
        "free": len(free),
        "occupied": int((spots["state"] == OCCUPIED).sum()),
        "unknown": int((spots["state"] == UNKNOWN).sum()),
        "free_spots": free[["entityId", "lat", "lon", "age_h"]],
        "outside_data": bool(moment < df["ts"].min() or moment > df["ts"].max()),
        "spots": spots,
    }


def latest_availability(df, max_age_h=24):
    """Estado dos lugares à hora da leitura mais recente existente no dataset."""
    if df.empty:
        raise ValueError("Não há dados de estacionamento.")
    moment = df["ts"].max()
    spots = state_at(df, moment, max_age_h)
    free = spots[spots["state"] == FREE].copy()
    return {
        "moment": moment,
        "total": len(spots),
        "free": len(free),
        "free_spots": free[["entityId", "lat", "lon", "age_h"]],
    }


def _haversine_m(lat1, lon1, lat2, lon2):
    radius_m = 6_371_000
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat = lat2 - lat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(min(1.0, a)))


def nearest_available_spot(df, latitude, longitude, max_age_h=24):
    """Devolve o lugar livre mais próximo em linha reta e a distância em metros."""
    latitude, longitude = float(latitude), float(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("As coordenadas da localização são inválidas.")

    availability = latest_availability(df, max_age_h)
    free_spots = availability["free_spots"].dropna(subset=["lat", "lon"]).copy()
    if free_spots.empty:
        raise ValueError("Não há lugares livres com localização e estado recentes.")

    free_spots["distance_m"] = [
        _haversine_m(latitude, longitude, float(row.lat), float(row.lon))
        for row in free_spots.itertuples(index=False)
    ]
    spot = free_spots.sort_values(["distance_m", "entityId"]).iloc[0].to_dict()
    return {"moment": availability["moment"], "spot": spot}


def nearest_available_route(df, latitude, longitude, max_age_h=24, hour=None, segments=None):
    """Encontra o lugar livre mais próximo e calcula a rota e o tempo de carro."""
    nearest = nearest_available_spot(df, latitude, longitude, max_age_h)

    import road_match

    origin_road = road_match.find_nearest_road(float(latitude), float(longitude))
    parking_road = road_match.find_nearest_road(
        float(nearest["spot"]["lat"]), float(nearest["spot"]["lon"])
    )
    if not origin_road or not origin_road.get("entityId"):
        raise ValueError("Não foi encontrada uma estrada perto da localização atual.")
    if not parking_road or not parking_road.get("entityId"):
        raise ValueError("Não foi encontrada uma estrada perto do estacionamento.")

    route = fastest_route.fastest_route(
        origin_road["entityId"],
        parking_road["entityId"],
        hour=hour,
        segments=segments,
    )
    return {
        **nearest,
        "origin_road": origin_road,
        "parking_road": parking_road,
        "route": route,
    }


def format_nearest_route(result):
    """Formata o estacionamento selecionado e o percurso de carro até lá."""
    spot = result["spot"]
    route = result["route"]
    lines = [
        f"Lugar livre mais próximo: {_short(spot['entityId'])}",
        f"Estado dos lugares consultado em {result['moment']:%Y-%m-%d %H:%M} UTC "
        "(última leitura do dataset; não é disponibilidade em tempo real).",
        f"Distância em linha reta: {spot['distance_m']:.0f} m",
        f"Última leitura deste lugar: {spot['age_h']:.1f} h atrás",
        "",
        fastest_route.format_route(route),
    ]
    return "\n".join(lines)


def typical_availability(df, time_str):
    """Em todos os dias dos dados, estado de cada lugar a essa hora. Devolve dict com
    'spots' (entityId, days_observed, days_free, pct_free), 'avg_free' (lugares livres em média) e 'days'."""
    days = sorted(df["ts"].dt.date.unique())
    frames = []
    for day in days:
        moment = parse_moment(df, time_str, day.isoformat())
        s = state_at(df, moment)[["entityId", "state"]]
        s["day"] = day
        frames.append(s)
    all_days = pd.concat(frames, ignore_index=True)
    known = all_days[all_days["state"] != UNKNOWN]

    per_spot = pd.DataFrame({"entityId": sorted(df["entityId"].unique())})
    per_spot["days_observed"] = per_spot["entityId"].map(known.groupby("entityId").size()).fillna(0).astype(int)
    per_spot["days_free"] = per_spot["entityId"].map(
        known[known["state"] == FREE].groupby("entityId").size()).fillna(0).astype(int)
    per_spot["pct_free"] = (per_spot["days_free"] / per_spot["days_observed"].where(per_spot["days_observed"] > 0) * 100)
    per_spot = per_spot.merge(_spot_positions(df), on="entityId", how="left")

    free_per_day = known[known["state"] == FREE].groupby("day").size().reindex(days, fill_value=0)
    return {"time": time_str.strip(), "days": len(days), "spots": per_spot,
            "avg_free": float(free_per_day.mean()) if len(days) else 0.0}


# ------------------------------ texto ---------------------------------------
def _short(entity_id):
    return str(entity_id).rsplit(":", 1)[-1]


def format_availability(r, max_list=40):
    lines = [f"Estacionamento às {r['moment']:%H:%M} UTC de {r['moment']:%Y-%m-%d}",
             f"Livres: {r['free']} de {r['total']}  |  ocupados: {r['occupied']}  |  sem informação: {r['unknown']}"]
    if r["outside_data"]:
        lines.append("Aviso: esta hora está fora do período coberto pelos dados; o resultado pode não ser fiável.")
    if r["free"]:
        lines += ["", "Lugares livres:"]
        for row in r["free_spots"].head(max_list).itertuples(index=False):
            where = f" ({row.lat:.5f}, {row.lon:.5f})" if pd.notna(row.lat) else ""
            age = f", última leitura há {row.age_h:.1f} h" if pd.notna(row.age_h) else ""
            lines.append(f"  - {_short(row.entityId)}{where}{age}")
        if r["free"] > max_list:
            lines.append(f"  ... e mais {r['free'] - max_list}")
    else:
        lines.append("Não há lugares livres a essa hora.")
    return "\n".join(lines)


def format_typical(t, max_list=40):
    lines = [f"Estacionamento às {t['time']} UTC, em {t['days']} dia(s) de dados",
             f"Em média, {t['avg_free']:.1f} lugares livres a essa hora", "", "Percentagem de dias em que estava livre:"]
    spots = t["spots"].sort_values(["pct_free", "entityId"], ascending=[False, True], na_position="last")
    for row in spots.head(max_list).itertuples(index=False):
        pct = f"{row.pct_free:.0f}%" if pd.notna(row.pct_free) else "sem dados"
        lines.append(f"  - {_short(row.entityId)}: {pct} ({row.days_free}/{row.days_observed} dias)")
    if len(spots) > max_list:
        lines.append(f"  ... e mais {len(spots) - max_list}")
    return "\n".join(lines)


# ------------------------------- CLI ----------------------------------------
def _menu(csv_path):
    df = load_parking_data(csv_path)
    while True:
        time_str = input("\nHora a pesquisar em UTC (HH:MM, ex: 14:30) [Enter para sair]: ").strip()
        if not time_str:
            break
        date_str = input("Data (AAAA-MM-DD, Enter = último dia dos dados): ").strip() or None
        typical = input("Ver o típico de todos os dias? (s/n): ").strip().lower() == "s"
        try:
            print("\n" + (format_typical(typical_availability(df, time_str)) if typical
                          else format_availability(availability_at(df, time_str, date_str))))
        except ValueError as error:
            print(error)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Lugares de estacionamento livres a uma hora definida")
    ap.add_argument("time", nargs="?", help="hora em UTC, HH:MM (sem argumentos abre o menu)")
    ap.add_argument("--date", help="data AAAA-MM-DD (por defeito, o último dia dos dados)")
    ap.add_argument("--max-age", type=float, help="ignora leituras com mais de N horas (fica 'sem informação')")
    ap.add_argument("--typical", action="store_true", help="média de todos os dias em vez de um dia")
    ap.add_argument("--nearest", action="store_true", help="procura estacionamento livre perto da localização atual e calcula a rota")
    ap.add_argument("--lat", type=float, help="latitude (sem --lat/--lon usa location_time.py)")
    ap.add_argument("--lon", type=float, help="longitude (sem --lat/--lon usa location_time.py)")
    ap.add_argument("--csv", default=str(PARKING_CSV), help="caminho do smartparking.csv")
    a = ap.parse_args()

    if a.nearest:
        if (a.lat is None) != (a.lon is None):
            ap.error("--lat e --lon têm de ser usados em conjunto.")
        try:
            if a.lat is None:
                from location_time import locationCoordinates

                a.lat, a.lon, *_ = locationCoordinates()
            data = load_parking_data(a.csv)
            route_result = nearest_available_route(
                data,
                a.lat,
                a.lon,
                a.max_age if a.max_age is not None else 24,
            )
            print(format_nearest_route(route_result))
        except (ValueError, KeyError, FileNotFoundError, OSError) as error:
            ap.exit(1, f"Erro: {error}\n")
    elif not a.time:
        _menu(a.csv)
    else:
        try:
            data = load_parking_data(a.csv)
            if a.typical:
                print(format_typical(typical_availability(data, a.time)))
            else:
                print(format_availability(availability_at(data, a.time, a.date, a.max_age)))
        except (ValueError, KeyError, FileNotFoundError) as error:
            print(f"Erro: {error}")