"""
fastest_route - caminho mais rápido DE CARRO entre duas ruas (ATCLL, Aveiro).

O resultado é o tempo que um carro demora (nada de tempos a pé). O tempo de cada segmento de estrada é:
    tempo = comprimento / velocidade média (a essa hora)  +  espera média das passadeiras ligadas ao segmento

- Velocidade: averageVelocity do roadsegments_stats.csv (média por segmento e hora), tirada da cache que o
  roadXcross já guarda (road_speeds_cache.pkl): o ficheiro grande só é lido se essa cache não existir. Se o segmento não tiver
  dados nessa hora usa a média de todas as horas; se não tiver nada, o averageVelocity do RoadSegment.csv;
  em último caso, uma fração do limite de velocidade (maxAllowedVelocity).
- Espera: média por passadeira e hora do crosswalk_hourly_profile.csv do roadXcross (já calculada), ligada
  às ruas pelas ligações do roadXcross (crosswalk_road_links.csv).
- O RoadSegment.csv tem um segmento por sentido (oneway=False aparece duplicado ao contrário), por isso o
  grafo é dirigido: cada segmento só se percorre de startPoint para endPoint.

Uso como módulo:
    import fastest_route
    r = fastest_route.fastest_route("Rua de Ovar", "Avenida Europa", hour=8)
    print(fastest_route.format_route(r))

Uso na linha de comandos (sem argumentos abre o menu):
    python fastest_route.py "Rua de Ovar" "Avenida Europa" --hour 8
    python fastest_route.py "Rua de Ovar" "Avenida Europa" --no-crosswalks
    python fastest_route.py --streets                      # lista as ruas conhecidas

A origem/destino também pode ser o id de um segmento (ex: 10956 ou urn:ngsi-ld:RoadSegment:10956).
As horas são UTC (== hora de Aveiro em novembro). Só precisa de pandas e numpy.
"""
import argparse
import difflib
import hashlib
import heapq
import json
import pickle
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import roadXcross as rx

# ----------------------------- configuração ---------------------------------
BASE_DIR = Path(__file__).resolve().parent
ROAD_REF_CSV = rx.REF_DIR / "RoadSegment.csv"
CACHE_FILE = BASE_DIR / "route_cache.pkl"        # velocidades/esperas já agregadas (só pequenas tabelas)

MIN_SPEED_MPS = 1.5          # piso de velocidade: 0 m/s daria tempo infinito (1.5 m/s = 5.4 km/h)
IGNORE_ZERO_SPEED = True     # leituras com velocidade 0 = nenhum veículo em movimento, não "estrada cortada"
FALLBACK_LIMIT_RATIO = 0.6   # sem nenhum dado: velocidade = 60% do limite
CROSSWALK_DELAY_FACTOR = 1.0 # fração da espera (dos peões) que conta como atraso para quem segue na estrada
MIN_SAMPLES = rx.MIN_SAMPLES # mínimo de leituras por hora para confiar na média dessa hora


# ------------------------------ nomes ---------------------------------------
def _norm(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


# ------------------------------ 1) grafo ------------------------------------
def _point(geojson):
    c = json.loads(geojson)["coordinates"]
    return (round(c[0], 6), round(c[1], 6))


def load_segments(path=ROAD_REF_CSV):
    """Uma linha por segmento: ids, nós inicial/final, comprimento (m), nomes e velocidades de referência."""
    rs = rx.read_ref_csv(path)
    rows = []
    for r in rs.itertuples(index=False):
        try:
            xy = rx._xy(json.loads(r.location)["coordinates"])
            length = float(np.sqrt((np.diff(xy, axis=0) ** 2).sum(1)).sum())
            start, end = _point(r.startPoint), _point(r.endPoint)
        except (ValueError, KeyError, TypeError):
            continue                                  # geometria inválida: ignora o segmento
        limits = [x for x in (rx._num(v) for v in json.loads(r.maxAllowedVelocity)) if x] \
            if isinstance(r.maxAllowedVelocity, str) else []
        snap = r.averageVelocity if pd.notna(r.averageVelocity) and 0 < r.averageVelocity <= rx.MAX_SPEED_MPS else np.nan
        rows.append({"segment_id": r.entityId, "start": start, "end": end, "length_m": max(length, 1.0),
                     "names": rx._segment_names(r.name), "limit_mps": float(np.mean(limits)) if limits else np.nan,
                     "snapshot_mps": snap})
    return pd.DataFrame(rows).set_index("segment_id", drop=False)


# --------------------- 2) velocidades e esperas por hora --------------------
def _stat(path):
    path = Path(path)
    return (str(path), path.stat().st_size, int(path.stat().st_mtime)) if path.exists() else (str(path), None)


def _cache_key(roads_path):
    """Muda quando muda o ficheiro grande ou algum dos resultados do roadXcross que aqui se reaproveitam."""
    parts = [_stat(roads_path), _stat(rx.SPEEDS_CACHE), _stat(rx.PROFILE_CSV),
             rx.CAMERA_IN_KMH, rx.MAX_SPEED_MPS, IGNORE_ZERO_SPEED]
    return hashlib.md5(repr(parts).encode()).hexdigest()


def _profile_waits(roads_path):
    """Esperas médias por passadeira e hora: vêm do crosswalk_hourly_profile.csv que o roadXcross já calcula
    (só o constrói se ainda não existir)."""
    try:
        if not rx.PROFILE_CSV.exists():
            rx.ensure_ready(roads_path if Path(roads_path).exists() else None)
        prof = pd.read_csv(rx.PROFILE_CSV, usecols=["smartcrosswalk", "hour", "avg_wait_s", "n_wait"])
        return prof.dropna(subset=["avg_wait_s"])
    except Exception as error:                       # sem smartcrosswalk.csv, etc.: a rota segue sem esperas
        print(f"Aviso: sem esperas das passadeiras ({error}).")
        return pd.DataFrame(columns=["smartcrosswalk", "hour", "avg_wait_s", "n_wait"])


def load_speeds_and_waits(segment_ids, roads_path=None):
    """Devolve (velocidades, esperas) sem reler o roadsegments_stats.csv se o roadXcross já o leu:
    velocidades: segment_id, hour, speed_mps, n   (agregadas a partir da road_speeds_cache.pkl do roadXcross)
    esperas:     smartcrosswalk, hour, avg_wait_s, n_wait   (do crosswalk_hourly_profile.csv do roadXcross)"""
    roads_path = Path(roads_path or rx.ROADS_CSV)
    if CACHE_FILE.exists():
        try:
            cached = pickle.loads(CACHE_FILE.read_bytes())
            if cached["key"] == _cache_key(roads_path):
                return cached["speeds"], cached["waits"]
        except Exception:
            pass

    if roads_path.exists():
        raw = rx._road_speeds(list(segment_ids), roads_path)      # cache partilhada: só lê o CSV se faltar algo
        if IGNORE_ZERO_SPEED:
            raw = raw[raw["speed_mps"] > 0]
        speeds = raw.groupby(["segment_id", "hour"])["speed_mps"].agg(speed_mps="mean", n="size").reset_index()
    else:
        print(f"Aviso: {roads_path} não existe; a usar só as velocidades do RoadSegment.csv e os limites.")
        speeds = pd.DataFrame(columns=["segment_id", "hour", "speed_mps", "n"])

    waits = _profile_waits(roads_path)                            # depois das velocidades: o perfil já apanha a cache
    CACHE_FILE.write_bytes(pickle.dumps({"key": _cache_key(roads_path), "speeds": speeds, "waits": waits}))
    return speeds, waits


def crosswalk_segments(segments):
    """smartcrosswalk -> segmentos onde a espera conta: o mais próximo e o seu gémeo no outro sentido
    (assim uma passadeira nunca é contada duas vezes na mesma viagem)."""
    if not rx.LINKS_CSV.exists():
        rx.build_links()
    links = pd.read_csv(rx.LINKS_CSV)
    links = links[links["use_for_alert"] & links["segment_id"].isin(segments.index)]
    ends = {sid: (r.start, r.end) for sid, r in segments.iterrows()}
    out = {}
    for cw, g in links.groupby("smartcrosswalk"):
        best = g.sort_values("distance_m").iloc[0]["segment_id"]
        s, e = ends[best]
        out[cw] = {best} | {sid for sid, (s2, e2) in ends.items() if (s2, e2) == (e, s)}
    return out


def build_edges(segments, speeds, waits, hour, use_crosswalks=True):
    """Calcula o custo (s) de cada segmento à hora `hour`. Devolve um DataFrame indexado por segment_id."""
    hourly = speeds[speeds["hour"] == hour].set_index("segment_id")
    hourly = hourly[hourly["n"] >= MIN_SAMPLES]["speed_mps"]
    overall = (speeds.assign(w=speeds["speed_mps"] * speeds["n"]).groupby("segment_id")[["w", "n"]].sum()
               .pipe(lambda d: d["w"] / d["n"]) if len(speeds) else pd.Series(dtype=float))

    e = segments.copy()
    e["speed_mps"] = e["segment_id"].map(hourly)
    e["speed_src"] = np.where(e["speed_mps"].notna(), "hora", "")
    for col, label in ((e["segment_id"].map(overall), "média"), (e["snapshot_mps"], "snapshot"),
                       (e["limit_mps"] * FALLBACK_LIMIT_RATIO, "limite")):
        fill = e["speed_mps"].isna() & col.notna()
        e.loc[fill, "speed_mps"] = col[fill]
        e.loc[fill, "speed_src"] = label
    e["speed_mps"] = e["speed_mps"].fillna(rx.MAX_SPEED_MPS / 4).clip(lower=MIN_SPEED_MPS)
    e["drive_s"] = e["length_m"] / e["speed_mps"]

    e["wait_s"] = 0.0
    if use_crosswalks and len(waits):
        w = waits[(waits["hour"] == hour) & (waits["n_wait"] >= MIN_SAMPLES)].set_index("smartcrosswalk")["avg_wait_s"]
        w_all = waits.groupby("smartcrosswalk")["avg_wait_s"].mean()
        for cw, seg_ids in crosswalk_segments(segments).items():
            value = w.get(cw, w_all.get(cw, np.nan))
            if pd.notna(value):
                e.loc[list(seg_ids), "wait_s"] += float(value) * CROSSWALK_DELAY_FACTOR
    e["cost_s"] = e["drive_s"] + e["wait_s"]
    return e


# ------------------------------ 3) ruas -------------------------------------
class StreetNotFound(ValueError):
    pass


class AmbiguousStreet(ValueError):
    def __init__(self, query, options):
        super().__init__(f"'{query}' pode ser: " + "; ".join(options))
        self.options = options


def list_streets(segments):
    return sorted({n for names in segments["names"] for n in names})


def find_street_segments(query, segments):
    """Segmentos de uma rua. Aceita o nome (sem acentos/maiúsculas) ou o id de um segmento."""
    q = str(query).strip()
    if re.fullmatch(r"(urn:ngsi-ld:RoadSegment:)?\d+", q):
        sid = q if q.startswith("urn:") else f"urn:ngsi-ld:RoadSegment:{int(q):05d}"
        if sid in segments.index:
            return [sid], segments.at[sid, "names"][0] if segments.at[sid, "names"] else sid
        raise StreetNotFound(f"Segmento {sid} não existe.")

    nq = _norm(q)
    all_names = list_streets(segments)
    exact = [n for n in all_names if _norm(n) == nq]
    if not exact:
        exact = [n for n in all_names if nq and nq in _norm(n)]       # "ovar" -> "Rua de Ovar"
    if not exact:
        close = difflib.get_close_matches(nq, [_norm(n) for n in all_names], n=3, cutoff=0.6)
        hints = [n for n in all_names if _norm(n) in close]
        raise StreetNotFound(f"Rua '{query}' não encontrada." + (f" Querias dizer: {'; '.join(hints)}?" if hints else ""))
    if len(exact) > 1 and not any(_norm(n) == nq for n in exact):
        raise AmbiguousStreet(query, exact)
    name = next((n for n in exact if _norm(n) == nq), exact[0])
    ids = [sid for sid, names in segments["names"].items() if name in names]
    return ids, name


# ------------------------------ 4) rota -------------------------------------
def _moves(edges, ignore_direction):
    """Lista de movimentos (segmento, nó de partida, nó de chegada, custo, ao contrário?).
    Normalmente só no sentido do segmento. Com ignore_direction junta também o sentido contrário dos
    segmentos que não têm gémeo (as vias de sentido único), que é o que costuma impedir a ligação."""
    moves = [(r.segment_id, r.start, r.end, r.cost_s, False) for r in edges.itertuples()]
    if ignore_direction:
        have = {(r.start, r.end) for r in edges.itertuples()}
        moves += [(r.segment_id, r.end, r.start, r.cost_s, True)
                  for r in edges.itertuples() if (r.end, r.start) not in have]
    return moves


def _search(edges, src_ids, dst_ids, ignore_direction):
    """Dijkstra com várias origens: começa ao percorrer um segmento da rua de origem e acaba ao percorrer
    um da rua de destino. Devolve (custo, lista de movimentos) ou None se não houver caminho."""
    moves = _moves(edges, ignore_direction)
    out = defaultdict(list)
    for m in moves:
        out[m[1]].append(m)
    src, dst = set(src_ids), set(dst_ids)

    dist, came, heap = {}, {}, []
    for m in moves:
        if m[0] in src and m[3] < dist.get(m[2], np.inf):
            dist[m[2]], came[m[2]] = m[3], [m]            # nó de chegada -> movimentos feitos até lá
            heapq.heappush(heap, (m[3], m[2]))
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, np.inf):
            continue
        for m in out[n]:
            nd = d + m[3]
            if nd < dist.get(m[2], np.inf):
                dist[m[2]], came[m[2]] = nd, came[n] + [m]
                heapq.heappush(heap, (nd, m[2]))

    best = None
    for m in moves:                                         # último segmento: um da rua de destino
        if m[0] in dst and m[1] in dist and (best is None or dist[m[1]] + m[3] < best[0]):
            best = (dist[m[1]] + m[3], came[m[1]] + [m])
    for m in moves:                                         # um segmento que já é das duas ruas
        if m[0] in src and m[0] in dst and (best is None or m[3] < best[0]):
            best = (m[3], [m])
    return best


def fastest_route(origin, destination, hour=None, use_crosswalks=True, roads_path=None, segments=None):
    """Caminho mais rápido de carro de uma rua (ou segmento) a outra: começa num segmento da origem e acaba num do
    destino, escolhendo o par que dá o menor tempo. Respeita os sentidos; se assim não houver caminho
    (o mapa tem muitas vias de sentido único sem continuação) repete ignorando-os e marca 'ignores_direction'.
    Devolve um dict: origin, destination, hour, total_s, drive_s, wait_s, distance_m, ignores_direction, steps."""
    hour = rx.hour_now() if hour is None else int(hour)
    segments = load_segments() if segments is None else segments
    src_ids, src_name = find_street_segments(origin, segments)
    dst_ids, dst_name = find_street_segments(destination, segments)

    speeds, waits = load_speeds_and_waits(segments.index, roads_path)
    edges = build_edges(segments, speeds, waits, hour, use_crosswalks)

    ignored = False
    best = _search(edges, src_ids, dst_ids, ignore_direction=False)
    if best is None:
        ignored, best = True, _search(edges, src_ids, dst_ids, ignore_direction=True)
    if best is None:
        raise StreetNotFound(f"Não há caminho entre '{src_name}' e '{dst_name}' (estão em partes não ligadas do mapa).")

    steps = []
    for sid, _, _, _, reverse in best[1]:
        r = edges.loc[sid]
        steps.append({"segment_id": sid, "street": r.names[0] if r.names else "(sem nome)",
                      "length_m": float(r.length_m), "speed_kmh": float(r.speed_mps * 3.6), "speed_src": r.speed_src,
                      "drive_s": float(r.drive_s), "wait_s": float(r.wait_s), "against_direction": reverse})
    return {"origin": src_name, "destination": dst_name, "hour": hour, "total_s": float(best[0]),
            "drive_s": sum(x["drive_s"] for x in steps), "wait_s": sum(x["wait_s"] for x in steps),
            "distance_m": sum(x["length_m"] for x in steps), "ignores_direction": ignored, "steps": steps}


# ------------------------------ 5) texto ------------------------------------
_NOTES = {
    "pt": {"dir": "Aviso: sem caminho a respeitar os sentidos únicos do mapa; esta rota vai contra o sentido em "
                  "{m:.0f} m. Serve como estimativa de tempo, não como percurso legal.",
           "est": "Nota: {p:.0%} do percurso usa velocidades estimadas (sem dados suficientes a esta hora)."},
    "en": {"dir": "Warning: no path respects the map's one-way streets; this route goes against the direction for "
                  "{m:.0f} m. Use it as a time estimate, not as a legal route.",
           "est": "Note: {p:.0%} of the route uses estimated speeds (not enough data at this hour)."},
}


def format_duration(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def average_speed_kmh(r):
    """Velocidade média do carro no percurso """
    return r["distance_m"] / r["total_s"] * 3.6 if r["total_s"] > 0 else 0.0


def group_steps(steps):
    """Junta segmentos seguidos da mesma rua: lista de dicts street, length_m, time_s."""
    groups = []
    for s in steps:
        if groups and groups[-1]["street"] == s["street"]:
            groups[-1]["length_m"] += s["length_m"]
            groups[-1]["time_s"] += s["drive_s"] + s["wait_s"]
        else:
            groups.append({"street": s["street"], "length_m": s["length_m"], "time_s": s["drive_s"] + s["wait_s"]})
    return groups


def route_notes(r, lang="pt"):
    """Avisos sobre a fiabilidade da rota (vai contra sentido / velocidades estimadas)."""
    t, notes = _NOTES[lang], []
    if r["ignores_direction"]:
        notes.append(t["dir"].format(m=sum(x["length_m"] for x in r["steps"] if x["against_direction"])))
    estimated = sum(x["length_m"] for x in r["steps"] if x["speed_src"] in ("snapshot", "limite"))
    if estimated:
        notes.append(t["est"].format(p=estimated / r["distance_m"]))
    return notes


def format_route(r):
    """Texto com o resumo e o percurso agrupado por rua."""
    lines = [f"{r['origin']} -> {r['destination']} (de carro, às {r['hour']}h UTC)",
             f"Tempo de carro: {format_duration(r['total_s'])}  |  distância: {r['distance_m'] / 1000:.2f} km  |  "
             f"velocidade média: {average_speed_kmh(r):.0f} km/h",
             f"atraso nas passadeiras: {format_duration(r['wait_s'])}",
             "", "Percurso:"]
    for i, g in enumerate(group_steps(r["steps"]), 1):
        lines.append(f"  {i}. {g['street']}: {g['length_m']:.0f} m, {format_duration(g['time_s'])}")
    return "\n".join(lines + [""] * bool(route_notes(r)) + route_notes(r))


# ------------------------------- CLI ----------------------------------------
def _choose(query, segments):
    """Resolve uma rua; se for ambígua, pergunta qual (só na linha de comandos)."""
    try:
        return find_street_segments(query, segments)
    except AmbiguousStreet as error:
        for i, name in enumerate(error.options, 1):
            print(f"  {i}. {name}")
        pick = input("Qual? (número): ").strip()
        return find_street_segments(error.options[int(pick) - 1], segments)


def _run(origin, destination, hour, use_crosswalks):
    segments = load_segments()
    o = _choose(origin, segments)[1]
    d = _choose(destination, segments)[1]
    print(format_route(fastest_route(o, d, hour, use_crosswalks, segments=segments)))


def _menu():
    segments = load_segments()
    while True:
        op = input("\nCalcular rota (1)  |  Listar ruas (2)  |  Exit (3)\n").strip()
        if op == "1":
            origin = input("Rua de origem: ").strip()
            destination = input("Rua de destino: ").strip()
            hour = input("Hora (0-23 UTC, Enter = agora): ").strip()
            try:
                o, d = _choose(origin, segments)[1], _choose(destination, segments)[1]
                print(format_route(fastest_route(o, d, int(hour) if hour else None, segments=segments)))
            except (ValueError, IndexError) as error:
                print(f"Erro: {error}")
        elif op == "2":
            print("\n".join(list_streets(segments)))
        elif op == "3":
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Caminho mais rápido de carro entre duas ruas")
    ap.add_argument("origin", nargs="?", help="rua (ou id de segmento) de origem")
    ap.add_argument("destination", nargs="?", help="rua (ou id de segmento) de destino")
    ap.add_argument("--hour", type=int, help="0-23 (UTC); por defeito a hora atual")
    ap.add_argument("--no-crosswalks", action="store_true", help="ignora as esperas das passadeiras")
    ap.add_argument("--streets", action="store_true", help="lista as ruas conhecidas")
    a = ap.parse_args()

    if a.streets:
        print("\n".join(list_streets(load_segments())))
    elif a.origin and a.destination:
        try:
            _run(a.origin, a.destination, a.hour, not a.no_crosswalks)
        except ValueError as error:
            print(f"Erro: {error}")
    else:
        _menu()