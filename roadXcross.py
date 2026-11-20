"""
roadXcross - ligação ruas <-> passadeiras e aviso de trânsito (ATCLL, Aveiro).

Uso como módulo (no main):
    import roadXcross

    roadXcross.ensure_ready()                       # só constrói o que ainda não existir
    avisos = roadXcross.get_warnings(segment_id="urn:ngsi-ld:RoadSegment:10956")
    avisos = roadXcross.get_warnings(street="Avenida 25 de Abril", hour=8)
    for a in avisos:
        print(roadXcross.format_warning(a, lang="en"))

Cada aviso é um dicionário:
    crosswalk, smartcrosswalk, road_name, hour, level ("Livre"/"Moderado"/"Intenso"/"Sem dados"),
    avg_wait_s, speed_ratio, avg_speed_kmh  (None quando não há dados fiáveis)

Uso na linha de comandos (ou sem argumentos para abrir o menu):
    python roadXcross.py links
    python roadXcross.py profile --roads data/roadsegments_stats.csv
    python roadXcross.py warn --street "Avenida 25 de Abril" --hour 8

As horas são UTC (== hora de Aveiro em novembro). Só precisa de pandas e numpy.
"""
import argparse
import difflib
import json
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------- configuração ---------------------------------
BASE_DIR = Path(__file__).resolve().parent        # funciona seja qual for a pasta de onde se corre
EVENTS_DIR = BASE_DIR / "data"                    # smartcrosswalk.csv, roadsegments_stats.csv
REF_DIR = EVENTS_DIR / "reference-entities"       # Crosswalk.csv, SmartCrosswalk.csv, RoadSegment.csv
ROADS_CSV = EVENTS_DIR / "roadsegments_stats.csv"
LINKS_CSV = BASE_DIR / "crosswalk_road_links.csv"
PROFILE_CSV = BASE_DIR / "crosswalk_hourly_profile.csv"

MAX_DIST_M = 30        # raio de procura de segmentos à volta da passadeira
FALLBACK_DIST_M = 15   # se nenhum segmento tiver o nome certo, usa só os que estão colados
WAIT_CAP_S = 120       # esperas acima disto são tratadas como outliers (máx. nos dados: 343 s)
MAX_SPEED_MPS = 60     # acima disto é lixo (o V2X devolve 16383 quando não há velocidade)
MIN_SAMPLES = 5        # mínimo de registos por hora para confiar na média
CAMERA_IN_KMH = False  # o PDF diz km/h, mas nos dados as câmaras estão na mesma escala do radar (m/s).
                       # build_profile imprime uma verificação: se a câmara aparecer muito abaixo dos
                       # outros sensores, confirma isto com o ficheiro completo.

LEVELS = ["Livre", "Moderado", "Intenso"]


# ------------------- leitura do Extended JSON (MongoDB) ----------------------
def _flat(v):
    """Desembrulha $date, $numberDouble, $numberInt, $numberLong e $oid."""
    if isinstance(v, dict):
        if "$date" in v:
            d = v["$date"]
            if isinstance(d, dict):
                d = int(d["$numberLong"])
            return pd.to_datetime(d, unit="ms", utc=True) if isinstance(d, int) else pd.to_datetime(d, utc=True)
        if "$numberDouble" in v:
            return float(v["$numberDouble"])
        if "$numberInt" in v:
            return int(v["$numberInt"])
        if "$numberLong" in v:
            return int(v["$numberLong"])
        if "$oid" in v:
            return v["$oid"]
        return {k: _flat(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_flat(x) for x in v]
    return v


def load(path, keep=None):
    """Carrega um CSV do ATCLL e expande o documentJson em colunas normais."""
    raw = pd.read_csv(path, usecols=["documentJson"])
    df = pd.DataFrame([_flat(json.loads(s)) for s in raw["documentJson"]])
    return df[keep] if keep else df


# ------------------------------ geometria -----------------------------------
LAT0 = 40.64
KX = 111320 * np.cos(np.radians(LAT0))   # metros por grau de longitude
KY = 110540                              # metros por grau de latitude


def _xy(coords):
    c = np.asarray(coords, float)
    return np.c_[(c[:, 0] + 8.65) * KX, (c[:, 1] - LAT0) * KY]


def _poly_centroid(geojson):
    ring = np.asarray(json.loads(geojson)["coordinates"][0])
    return _xy(ring[:-1]).mean(axis=0)


def _dist_point_polyline(p, line):
    a, b = line[:-1], line[1:]
    ab = b - a
    t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab ** 2).sum(1), 1e-12), 0, 1)
    return float(np.sqrt((((a + t[:, None] * ab) - p) ** 2).sum(1)).min())


# --------------------------- nomes de ruas ----------------------------------
_STOP = {"de", "da", "do", "dos", "das", "e", "rua", "avenida", "av", "doutor", "dr",
         "dom", "d", "cap", "capitao", "eng", "engenheiro", "praca", "largo", "travessa"}


def _tokens(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z0-9]+", s) if t not in _STOP}


def _segment_names(raw):
    """O campo name dos segmentos pode ser texto simples ou uma lista JSON em texto."""
    if pd.isna(raw):
        return []
    raw = str(raw)
    if raw.startswith("["):
        try:
            return [str(x) for x in json.loads(raw)]
        except ValueError:
            pass
    return [raw]


def _names_match(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta <= tb or tb <= ta:
        return True
    return difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio() >= 0.85


# ------------------------------ 1) links ------------------------------------
def build_links():
    """Liga cada passadeira aos segmentos de estrada à volta e grava crosswalk_road_links.csv."""
    sc = pd.read_csv(REF_DIR / "SmartCrosswalk.csv")
    cw = pd.read_csv(REF_DIR / "Crosswalk.csv").set_index("entityId")
    rs = pd.read_csv(REF_DIR / "RoadSegment.csv")
    lines = [_xy(json.loads(loc)["coordinates"]) for loc in rs["location"]]
    seg_names = [_segment_names(n) for n in rs["name"]]

    rows = []
    for _, c in sc.iterrows():
        p = _poly_centroid(cw.loc[c["refCrosswalk"], "location"])
        dist = np.array([_dist_point_polyline(p, L) for L in lines])
        cand = np.where(dist <= MAX_DIST_M)[0]
        road = c["roadName"] if pd.notna(c["roadName"]) else None
        match = np.array([road is not None and any(_names_match(road, n) for n in seg_names[i])
                          for i in cand], dtype=bool)
        if match.any():
            used, kind = set(cand[match]), "nome+geo"
        else:
            used, kind = {i for i in cand if dist[i] <= FALLBACK_DIST_M}, "so-geo"
            if not used and len(cand):
                used = {cand[dist[cand].argmin()]}
        for i, m in zip(cand, match):
            rows.append({
                "smartcrosswalk": c["entityId"],
                "crosswalk": c["refCrosswalk"],
                "crosswalk_road_name": road,
                "segment_id": rs.at[i, "entityId"],
                "segment_name": " / ".join(seg_names[i]) or None,
                "distance_m": round(dist[i], 1),
                "name_match": bool(m),
                "use_for_alert": i in used,
                "match_type": kind if i in used else "",
            })
    links = pd.DataFrame(rows).sort_values(["smartcrosswalk", "distance_m"])
    links.to_csv(LINKS_CSV, index=False)
    reload()
    return links


# ----------------------------- 2) perfil ------------------------------------
def _crosswalk_waits():
    cw = load(EVENTS_DIR / "smartcrosswalk.csv", keep=["entityId", "wait_time", "eventTimestamp"])
    cw = cw.dropna(subset=["wait_time"])
    cw = cw[cw["wait_time"] <= WAIT_CAP_S].copy()
    cw["hour"] = cw["eventTimestamp"].dt.hour       # UTC == hora de Aveiro em novembro
    return cw.groupby(["entityId", "hour"])["wait_time"].agg(avg_wait_s="mean", n_wait="size").reset_index()


def _road_speeds(segment_ids, path, chunksize=100_000):
    """Lê o ficheiro grande aos bocados e só faz parse JSON dos segmentos que interessam."""
    keep = set(segment_ids)
    out, bad = [], 0
    for chunk in pd.read_csv(path, usecols=["entityId", "documentJson"], chunksize=chunksize):
        chunk = chunk[chunk["entityId"].isin(keep)]
        for s in chunk["documentJson"]:
            try:
                d = _flat(json.loads(s))
            except ValueError:           # linha cortada/corrompida: ignora
                bad += 1
                continue
            limit = d.get("maxAllowedVelocity")
            limit = float(np.mean(limit)) if isinstance(limit, list) and limit else np.nan
            v = d.get("averageVelocity")
            if v is None:
                continue
            sensor = d.get("sensorType")
            if CAMERA_IN_KMH and sensor == "Camera":
                v = v / 3.6
            if not (0 <= v <= MAX_SPEED_MPS):
                continue
            out.append((d["entityId"], d["eventTimestamp"].hour, v, v / limit if limit > 0 else np.nan, sensor))
    df = pd.DataFrame(out, columns=["segment_id", "hour", "speed_mps", "speed_ratio", "sensor"])
    if bad:
        print(f"Aviso: {bad} linha(s) com JSON inválido ignorada(s).")
    if len(df):
        med = df.groupby("sensor")["speed_ratio"].median().round(2).to_dict()
        print(f"Verificação de unidades - mediana velocidade/limite por sensor: {med}")
    return df


def build_profile(roads_path=None):
    """Perfil por passadeira e hora (espera dos peões + velocidade dos carros) -> crosswalk_hourly_profile.csv."""
    links = pd.read_csv(LINKS_CSV)
    used = links[links["use_for_alert"]]

    waits = _crosswalk_waits()
    waits["smartcrosswalk"] = waits["entityId"]

    if roads_path:
        sp = _road_speeds(used["segment_id"].unique(), roads_path)
        sp = used[["smartcrosswalk", "segment_id"]].drop_duplicates().merge(sp, on="segment_id")
        speeds = (sp.groupby(["smartcrosswalk", "hour"])
                    .agg(avg_speed_mps=("speed_mps", "mean"), speed_ratio=("speed_ratio", "mean"),
                         n_vehicles=("speed_mps", "size")).reset_index())
        prof = waits.merge(speeds, on=["smartcrosswalk", "hour"], how="outer")
    else:
        prof = waits.copy()
        prof["avg_speed_mps"] = prof["speed_ratio"] = np.nan
        prof["n_vehicles"] = 0

    # horas com poucos registos não são fiáveis: ficam sem valor (nem aparecem no aviso)
    prof.loc[prof["n_wait"].fillna(0) < MIN_SAMPLES, "avg_wait_s"] = np.nan
    prof.loc[prof["n_vehicles"].fillna(0) < MIN_SAMPLES, ["avg_speed_mps", "speed_ratio"]] = np.nan

    # níveis (0 = livre, 1 = moderado, 2 = intenso)
    ok = prof["n_wait"].fillna(0) >= MIN_SAMPLES
    q50, q85 = prof.loc[ok, "avg_wait_s"].quantile([0.50, 0.85])
    prof["wait_level"] = np.where(~ok, np.nan, np.select(
        [prof["avg_wait_s"] > q85, prof["avg_wait_s"] > q50], [2, 1], 0))
    okv = prof["n_vehicles"].fillna(0) >= MIN_SAMPLES
    prof["traffic_level"] = np.where(~okv, np.nan, np.select(
        [prof["speed_ratio"] < 0.35, prof["speed_ratio"] < 0.65], [2, 1], 0))
    worst = prof[["wait_level", "traffic_level"]].max(axis=1, skipna=True)   # o pior dos dois
    prof["level"] = worst.map(lambda x: LEVELS[int(x)] if pd.notna(x) else "Sem dados")

    cols = ["smartcrosswalk", "hour", "avg_wait_s", "n_wait", "avg_speed_mps", "speed_ratio",
            "n_vehicles", "wait_level", "traffic_level", "level"]
    prof = prof[cols].sort_values(["smartcrosswalk", "hour"]).round(3)
    prof.to_csv(PROFILE_CSV, index=False)
    reload()
    print(f"Limiares de espera: moderado > {q50:.1f}s, intenso > {q85:.1f}s")
    return prof


# ------------------------- 3) API para o main -------------------------------
def is_ready():
    """True se os dois CSV (links e perfil) já existem."""
    return LINKS_CSV.exists() and PROFILE_CSV.exists()


def ensure_ready(roads_path=None, rebuild=False):
    """Constrói só o que falta. O perfil demora (segundos sem estradas, minutos com o ficheiro grande),
    por isso convém correr isto uma vez antes de abrir a interface."""
    if rebuild or not LINKS_CSV.exists():
        build_links()
    if rebuild or not PROFILE_CSV.exists():
        if roads_path is None and ROADS_CSV.exists():
            roads_path = ROADS_CSV
        build_profile(roads_path)


def reload():
    """Esquece as tabelas em memória (chamado automaticamente depois de reconstruir)."""
    _tables.cache_clear()


def hour_now():
    """Hora atual em UTC (a mesma referência do perfil)."""
    return datetime.now(timezone.utc).hour


@lru_cache(maxsize=1)
def _tables():
    if not is_ready():
        raise FileNotFoundError(
            "Faltam crosswalk_road_links.csv / crosswalk_hourly_profile.csv. Corre roadXcross.ensure_ready().")
    links = pd.read_csv(LINKS_CSV)
    used = links[links["use_for_alert"]].copy()
    prof = pd.read_csv(PROFILE_CSV).set_index(["smartcrosswalk", "hour"])
    # todos os nomes associados a cada passadeira (rua da passadeira + nomes dos segmentos usados)
    names = {}
    for cw, g in used.groupby("smartcrosswalk"):
        s = set(g["crosswalk_road_name"].dropna())
        for n in g["segment_name"].dropna():
            s.update(_segment_names(n))
        names[cw] = s
    return used, prof, names


def _warning(cw, hour, prof, road_name):
    r = prof.loc[(cw, hour)] if (cw, hour) in prof.index else None
    w = {"crosswalk": cw.split(":")[-1], "smartcrosswalk": cw, "road_name": road_name, "hour": hour,
         "level": "Sem dados", "avg_wait_s": None, "speed_ratio": None, "avg_speed_kmh": None}
    if r is not None:
        w["level"] = r["level"]
        if pd.notna(r["avg_wait_s"]):
            w["avg_wait_s"] = float(r["avg_wait_s"])
        if pd.notna(r["speed_ratio"]):
            w["speed_ratio"] = float(r["speed_ratio"])
            w["avg_speed_kmh"] = float(r["avg_speed_mps"]) * 3.6
    return w


def get_warnings(segment_id=None, street=None, hour=None):
    """Avisos das passadeiras ligadas a um segmento (segment_id) e/ou a uma rua (street).
    Se o segmento não tiver passadeiras, tenta pelo nome da rua. `hour` por defeito é a hora atual (UTC).
    Devolve uma lista de dicionários (vazia se não houver passadeiras monitorizadas)."""
    used, prof, names = _tables()
    hour = hour_now() if hour is None else int(hour)

    cws = set()
    if segment_id:
        cws |= set(used.loc[used["segment_id"] == segment_id, "smartcrosswalk"])
    if street and not cws:
        cws |= {cw for cw, ns in names.items() if any(_names_match(street, n) for n in ns)}

    road = {cw: next(iter(sorted(names[cw])), None) for cw in cws}
    return [_warning(cw, hour, prof, road[cw]) for cw in sorted(cws)]


_TEXT = {
    "pt": {"cw": "Passadeira", "traffic": "trânsito", "at": "às", "none": "sem dados suficientes",
           "cars": "carros a {r:.0%} do limite ({k:.0f} km/h)", "wait": "espera média de peões {w:.1f}s",
           "levels": {"Livre": "LIVRE", "Moderado": "MODERADO", "Intenso": "INTENSO"}},
    "en": {"cw": "Crosswalk", "traffic": "traffic", "at": "at", "none": "not enough data",
           "cars": "cars at {r:.0%} of the limit ({k:.0f} km/h)", "wait": "average pedestrian wait {w:.1f}s",
           "levels": {"Livre": "LIGHT", "Moderado": "MODERATE", "Intenso": "HEAVY"}},
}


def format_warning(w, lang="pt"):
    """Texto de uma linha para um aviso devolvido por get_warnings (lang = 'pt' ou 'en')."""
    t = _TEXT[lang]
    head = f"{t['cw']} {w['crosswalk']}"
    if w["level"] == "Sem dados":
        return f"{head}: {t['none']} {t['at']} {w['hour']}h."
    parts = []
    if w["speed_ratio"] is not None:
        parts.append(t["cars"].format(r=w["speed_ratio"], k=w["avg_speed_kmh"]))
    if w["avg_wait_s"] is not None:
        parts.append(t["wait"].format(w=w["avg_wait_s"]))
    return f"{head}: {t['traffic']} {t['levels'][w['level']]} {t['at']} {w['hour']}h - " + ", ".join(parts)


def warn(street, hour):
    """Compatibilidade com a versão antiga: lista de textos para uma rua e hora."""
    ws = get_warnings(street=street, hour=hour)
    return [format_warning(w) for w in ws] or [f"Sem passadeiras monitorizadas em '{street}'."]


# ------------------------------- CLI ----------------------------------------
def _print_links():
    l = build_links()
    print(l[l["use_for_alert"]].groupby("smartcrosswalk")["match_type"].agg(["first", "size"]))


def _menu():
    while True:
        op = input("\nlinks (1)  |  profile (2)  |  warn (3)  |  Exit (4)\n").strip()
        if op == "1":
            _print_links()
        elif op == "2":
            roads = input(f"Ficheiro das estradas (Enter = {ROADS_CSV}, 'n' = sem velocidades): ").strip()
            path = None if roads.lower() == "n" else (roads or ROADS_CSV)
            print(build_profile(path).head(24))
        elif op == "3":
            street = input("Rua: ").strip()
            hour = int(input("Hora (0-23): "))
            print("\n".join(warn(street, hour)))
        elif op == "4":
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")          # sem comando -> abre o menu
    sub.add_parser("links")
    p = sub.add_parser("profile"); p.add_argument("--roads", help="roadsegments_stats.csv")
    w = sub.add_parser("warn"); w.add_argument("--street", required=True); w.add_argument("--hour", type=int, required=True)
    a = ap.parse_args()

    if a.cmd is None:
        _menu()
    elif a.cmd == "links":
        _print_links()
    elif a.cmd == "profile":
        print(build_profile(a.roads).head(24))
    else:
        print("\n".join(warn(a.street, a.hour)))