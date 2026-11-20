import argparse
import csv
import difflib
import hashlib
import json
import pickle
import re
import sys
import time
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
SPEEDS_CACHE = BASE_DIR / "road_speeds_cache.pkl"   # evita reler o ficheiro grande de cada vez

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


def read_ref_csv(path, **kwargs):
    """Lê um CSV de referência (Crosswalk, SmartCrosswalk, RoadSegment...) mesmo que tenha sido guardado
    pelo Excel: aceita BOM no início (que estraga o nome da 1.ª coluna), separador ';' ou tab e espaços
    nos nomes das colunas. Se faltar a coluna entityId, diz que colunas encontrou."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        first = f.readline()
    sep = max((",", ";", "\t"), key=first.count)
    df = pd.read_csv(path, sep=sep, encoding="utf-8-sig", **kwargs)
    df.columns = [str(c).strip() for c in df.columns]
    if "entityId" not in df.columns:
        raise KeyError(f"{Path(path).name}: não encontrei a coluna 'entityId'. "
                       f"Colunas lidas: {list(df.columns)[:8]} (separador detetado: {sep!r}). "
                       f"Confirma se o ficheiro é o certo e se não está vazio ou danificado.")
    return df


def read_csv_safe(path, usecols=None, **kwargs):
    """pd.read_csv que não rebenta com CSV truncado/corrompido (ex.: "EOF inside string"):
    se o pandas falhar, lê com o módulo csv e fica só com as linhas completas e válidas."""
    try:
        return pd.read_csv(path, usecols=usecols, **kwargs)
    except pd.errors.ParserError as error:
        print(f"Aviso: {Path(path).name} parece truncado ou corrompido ({error}). "
              f"A usar só as linhas válidas; confirma se o ficheiro está completo.")
    csv.field_size_limit(sys.maxsize)
    rows, bad = [], 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader)
        try:
            for row in reader:
                if len(row) == len(header):
                    rows.append(row)
                else:
                    bad += 1
        except csv.Error:
            bad += 1
    print(f"  {len(rows):,} linhas válidas lidas, {bad} descartada(s).")
    df = pd.DataFrame(rows, columns=header)
    return df[list(usecols)] if usecols else df


def load(path, keep=None):
    """Carrega um CSV do ATCLL e expande o documentJson em colunas normais."""
    raw = read_csv_safe(path, usecols=["documentJson"])
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
    sc = read_ref_csv(REF_DIR / "SmartCrosswalk.csv")
    cw = read_ref_csv(REF_DIR / "Crosswalk.csv").set_index("entityId")
    rs = read_ref_csv(REF_DIR / "RoadSegment.csv")
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


def _num(x):
    """Número a partir de um valor Extended JSON ({"$numberDouble": "1.5"}) ou de um número normal."""
    if isinstance(x, dict):
        for k in ("$numberDouble", "$numberInt", "$numberLong"):
            if k in x:
                return float(x[k])
        return None
    return float(x) if isinstance(x, (int, float)) else None


def _hour_utc(ts):
    """Hora (UTC) de um eventTimestamp Extended JSON, sem criar objetos datetime (muito mais rápido)."""
    v = ts["$date"]
    if isinstance(v, dict):
        return int(int(v["$numberLong"]) // 3_600_000 % 24)
    return pd.to_datetime(v, utc=True).hour


def _road_speeds(segment_ids, path, chunksize=100_000, use_cache=True):
    """Lê o ficheiro grande aos bocados e só faz parse JSON dos segmentos que interessam.
    A cache (road_speeds_cache.pkl) é partilhada: guarda os segmentos já lidos e só volta a ler o ficheiro
    se pedirem algum segmento que ainda não lá esteja (nesse caso junta-o aos que já tinha)."""
    path = Path(path).resolve()      # o mesmo ficheiro dá sempre a mesma chave (relativo ou absoluto)
    keep = set(segment_ids)
    st = path.stat()
    key = hashlib.md5(repr((str(path), st.st_size, int(st.st_mtime), CAMERA_IN_KMH, MAX_SPEED_MPS)).encode()).hexdigest()
    have = set()
    if use_cache and SPEEDS_CACHE.exists():
        try:
            cached = pickle.loads(SPEEDS_CACHE.read_bytes())
            if cached["key"] == key and "ids" in cached:
                if keep <= cached["ids"]:
                    print("Velocidades das estradas lidas da cache (apaga road_speeds_cache.pkl para forçar nova leitura).")
                    df = cached["df"]
                    return df if keep == cached["ids"] else df[df["segment_id"].isin(keep)]
                have = cached["ids"]                      # já tem alguns: vai ler também os que faltam
        except Exception:
            pass
    keep_read = keep | have

    print(f"A ler {path.name} ({st.st_size / 1e9:.2f} GB). Pode demorar vários minutos, não está bloqueado...")
    t0 = time.time()
    out, bad, rows_read = [], 0, 0
    for chunk in pd.read_csv(path, usecols=["entityId", "documentJson"], chunksize=chunksize, dtype=str):
        rows_read += len(chunk)
        chunk = chunk[chunk["entityId"].isin(keep_read)]
        for s in chunk["documentJson"]:
            try:
                d = json.loads(s)
                v = _num(d.get("averageVelocity"))
                if v is None:
                    continue
                lim = d.get("maxAllowedVelocity")
                lim = [x for x in map(_num, lim) if x is not None] if isinstance(lim, list) else []
                limit = float(np.mean(lim)) if lim else np.nan
                sensor = d.get("sensorType")
                if CAMERA_IN_KMH and sensor == "Camera":
                    v = v / 3.6
                if not (0 <= v <= MAX_SPEED_MPS):
                    continue
                out.append((d["entityId"], _hour_utc(d["eventTimestamp"]), v,
                            v / limit if limit > 0 else np.nan, sensor))
            except (ValueError, KeyError, TypeError):     # linha cortada/corrompida: ignora
                bad += 1
        print(f"  {rows_read:,} linhas lidas | {len(out):,} úteis | {time.time() - t0:.0f}s")

    df = pd.DataFrame(out, columns=["segment_id", "hour", "speed_mps", "speed_ratio", "sensor"])
    if bad:
        print(f"Aviso: {bad} linha(s) com JSON inválido ignorada(s).")
    if len(df):
        med = df.groupby("sensor")["speed_ratio"].median().round(2).to_dict()
        print(f"Verificação de unidades - mediana velocidade/limite por sensor: {med}")
    if use_cache:
        SPEEDS_CACHE.write_bytes(pickle.dumps({"key": key, "ids": keep_read, "df": df}))
    return df if keep_read == keep else df[df["segment_id"].isin(keep)]


def build_profile(roads_path=None):
    """Perfil por passadeira e hora (espera dos peões + velocidade dos carros) -> crosswalk_hourly_profile.csv."""
    links = pd.read_csv(LINKS_CSV)
    used = links[links["use_for_alert"]]

    print("A ler as esperas das passadeiras (~20-60 s)...")
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


def nearest_road_match(lat=None, lon=None):
    """Rua mais próxima segundo o road_match: dict(entityId, name, distance_m) ou None.
    Sem lat/lon usa a localização atual (location_time.locationCoordinates, como o main)."""
    import road_match            # import tardio: só é preciso aqui (e assim o roadXcross funciona sem ele)
    if lat is not None and lon is not None:
        match = road_match.find_nearest_road(float(lat), float(lon))
    else:
        match = road_match.nearest_road()
    return match if match and match.get("entityId") else None


def get_warnings_nearest(hour=None, lat=None, lon=None, match=None):
    """Avisos para a rua mais próxima. Devolve (match, avisos).
    `match` permite passar o resultado do road_match que o main já tenha (evita procurar outra vez)."""
    match = match if match and match.get("entityId") else nearest_road_match(lat, lon)
    if not match:
        return None, []
    return match, get_warnings(segment_id=match["entityId"], street=match.get("name"), hour=hour)


def warn(street=None, hour=None, nearest=False, lat=None, lon=None, match=None, lang="pt"):
    """Lista de textos de aviso. Com nearest=True (ou sem `street`) usa a rua mais próxima dada
    pelo road_match; caso contrário usa a rua indicada. `hour` por defeito é a hora atual (UTC)."""
    hour = hour_now() if hour is None else int(hour)

    if nearest or match is not None or not street:
        match, ws = get_warnings_nearest(hour, lat, lon, match)
        if not match:
            return ["Não foi encontrada nenhuma rua próxima."]
        dist = match.get("distance_m")
        head = f"Rua mais próxima: {match.get('name') or match['entityId']}" + (f" ({dist:.0f} m)" if dist else "")
        return [head] + ([format_warning(w, lang) for w in ws] or ["Sem passadeiras monitorizadas nesta rua."])

    ws = get_warnings(street=street, hour=hour)
    return [format_warning(w, lang) for w in ws] or [f"Sem passadeiras monitorizadas em '{street}'."]


# ------------------------------- CLI ----------------------------------------
def _print_links():
    l = build_links()
    print(l[l["use_for_alert"]].groupby("smartcrosswalk")["match_type"].agg(["first", "size"]))


def _ask_coords():
    raw = input("Não consegui obter a localização. Escreve 'lat, lon' (Enter para cancelar): ").strip()
    if not raw:
        return None
    lat, lon = (float(x) for x in raw.split(","))
    return lat, lon


def _menu():
    while True:
        op = input("\nlinks (1)  |  profile (2)  |  warn (3)  |  Exit (4)\n").strip()
        if op == "1":
            _print_links()
        elif op == "2":
            roads = input(f"Ficheiro das estradas - carrega Enter para usar {ROADS_CSV} (demora alguns minutos) ou escreve 'n' para ignorar as velocidades: ").strip()
            path = None if roads.lower() == "n" else (roads or ROADS_CSV)
            print(build_profile(path).head(24))
        elif op == "3":
            street = input("Rua (Enter = rua mais próxima da tua localização): ").strip()
            hour = input("Hora (0-23, Enter = agora): ").strip()
            hour = int(hour) if hour else None
            try:
                print("\n".join(warn(street or None, hour)))
            except Exception as error:               # location_time/road_match indisponíveis, sem rede, etc.
                print(f"Erro ao obter a rua mais próxima: {error}")
                coords = _ask_coords() if not street else None
                if coords:
                    print("\n".join(warn(None, hour, nearest=True, lat=coords[0], lon=coords[1])))
        elif op == "4":
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")          # sem comando -> abre o menu
    sub.add_parser("links")
    p = sub.add_parser("profile"); p.add_argument("--roads", help="roadsegments_stats.csv")
    w = sub.add_parser("warn")
    w.add_argument("--street", help="nome da rua (se omitido, usa a rua mais próxima)")
    w.add_argument("--nearest", action="store_true", help="usar a rua mais próxima (road_match)")
    w.add_argument("--lat", type=float); w.add_argument("--lon", type=float)
    w.add_argument("--hour", type=int, help="0-23 (UTC); por defeito a hora atual")
    a = ap.parse_args()

    if a.cmd is None:
        _menu()
    elif a.cmd == "links":
        _print_links()
    elif a.cmd == "profile":
        print(build_profile(a.roads).head(24))
    else:
        print("\n".join(warn(a.street, a.hour, nearest=a.nearest, lat=a.lat, lon=a.lon)))
