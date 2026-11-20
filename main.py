import json
from datetime import datetime, timezone
import pandas as pd


MIN_RECORDS = 30   # abaixo disto avisa que o valor é pouco fiável
MIN_DAYS = 3


def parse_timestamp_utc(raw_value):
    #Converte o campo eventTimestamp (Extended JSON ou número/string) para datetime UTC.
    if pd.isna(raw_value):
        return None
    try:
        if isinstance(raw_value, str):
            data = json.loads(raw_value)
            if isinstance(data, dict) and "$date" in data:
                date_val = data["$date"]
                ms = int(date_val["$numberLong"]) if isinstance(date_val, dict) else int(date_val)
                return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        elif isinstance(raw_value, (int, float)):
            return datetime.fromtimestamp(float(raw_value) / 1000.0, tz=timezone.utc)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return None


def parse_numeric_field(raw_value):
    #Extrai o valor float de campos Extended JSON (ex: {'$numberDouble': '8.005'}) ou numéricos.
    if pd.isna(raw_value):
        return None
    if isinstance(raw_value, dict):          # ex: {"$numberDouble": "8.005"} já vindo de json.loads
        for key in ("$numberDouble", "$numberInt", "$numberLong"):
            if key in raw_value:
                return float(raw_value[key])
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        try:
            data = json.loads(raw_value)
            if isinstance(data, dict):
                for key in ("$numberDouble", "$numberInt", "$numberLong"):
                    if key in data:
                        return float(data[key])
            return float(data)
        except (ValueError, TypeError, json.JSONDecodeError):
            try:
                return float(raw_value)
            except ValueError:
                return None
    return None


def extract_wait_time(row):
    #Obtém wait_time diretamente da coluna ou dentro de documentJson.
    if "wait_time" in row and pd.notna(row["wait_time"]):
        val = parse_numeric_field(row["wait_time"])
        if val is not None:
            return val

    if "documentJson" in row and pd.notna(row["documentJson"]):
        try:
            doc = json.loads(row["documentJson"])
            if isinstance(doc, dict) and "wait_time" in doc and doc["wait_time"] is not None:
                return parse_numeric_field(doc["wait_time"])
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    return None


def load_crosswalk_data(csv_path="data/smartcrosswalk.csv"):
    #Carrega apenas as colunas necessárias de smartcrosswalk.csv e prepara timestamps e wait_time.
    header_cols = pd.read_csv(csv_path, nrows=0).columns.tolist()
    needed_cols = [c for c in ["entityId", "eventTimestamp", "wait_time", "documentJson"] if c in header_cols]

    df = pd.read_csv(csv_path, usecols=needed_cols)
    df["dt_utc"] = df["eventTimestamp"].apply(parse_timestamp_utc)
    df = df.dropna(subset=["dt_utc"]).copy()

    df["wait_time_sec"] = df.apply(extract_wait_time, axis=1)
    # Converter para minutos desde a meia-noite (0 a 1439) para comparar horários independentemente do dia
    df["minutes_of_day"] = df["dt_utc"].dt.hour * 60 + df["dt_utc"].dt.minute + df["dt_utc"].dt.second / 60.0
    return df


def format_minutes_hhmm(total_minutes):
    #Formata minutos desde a meia-noite (com wrap-around de 24h) como HH:MM.
    wrapped = int(round(total_minutes)) % (24 * 60)
    hours = wrapped // 60
    minutes = wrapped % 60
    return f"{hours:02d}:{minutes:02d}"


def is_in_time_window(series_minutes, target_minutes, window_half_width=15):
    #Verifica se cada timestamp (em minutos do dia) está dentro de [target - 15 min; target + 15 min], lidando com meia-noite.
    start_min = target_minutes - window_half_width
    end_min = target_minutes + window_half_width

    if start_min < 0:
        # Ex: 00:05 -> [23:50; 00:20]
        return (series_minutes >= (24 * 60 + start_min)) | (series_minutes <= end_min)
    elif end_min >= 24 * 60:
        # Ex: 23:55 -> [23:40; 00:10]
        return (series_minutes >= start_min) | (series_minutes <= (end_min - 24 * 60))
    else:
        return (series_minutes >= start_min) & (series_minutes <= end_min)


def summarize_wait_time_by_time(df, crosswalk_number, time_str):
    """Calcula a espera média/mediana de uma passadeira em [hora - 15 min; hora + 15 min] (UTC).
    Devolve um dict (usado pelo main) com "status" = "ok" | "no_crosswalk" | "no_data".
    Levanta ValueError se a hora não estiver no formato HH:MM."""
    try:
        parsed_time = datetime.strptime(time_str.strip(), "%H:%M")
    except ValueError:
        raise ValueError("Formato de hora inválido. Usa HH:MM (ex: 14:30).")

    entity_id = f"urn:ngsi-ld:SmartCrosswalk:{crosswalk_number:04d}"
    target_minutes = parsed_time.hour * 60 + parsed_time.minute

    result = {
        "status": "ok",
        "start": format_minutes_hhmm(target_minutes - 15),
        "end": format_minutes_hhmm(target_minutes + 15),
        "average": None, "median": None, "records": 0, "days": 0,
        "low_data": False, "skewed": False,
        "available_ids": [],
    }

    subset = df[df["entityId"] == entity_id]
    if subset.empty:
        result["status"] = "no_crosswalk"
        result["available_ids"] = sorted(df["entityId"].str[-4:].unique())
        return result

    mask = is_in_time_window(subset["minutes_of_day"], target_minutes, window_half_width=15)
    filtered = subset[mask].dropna(subset=["wait_time_sec"])
    if filtered.empty:
        result["status"] = "no_data"
        return result

    waits = filtered["wait_time_sec"]
    result["average"] = float(waits.mean())
    result["median"] = float(waits.median())
    result["records"] = len(waits)
    result["days"] = int(filtered["dt_utc"].dt.date.nunique())
    result["low_data"] = result["records"] < MIN_RECORDS or result["days"] < MIN_DAYS
    result["skewed"] = result["average"] > 2 * result["median"]
    return result


def average_wait_time_by_time(df, crosswalk_number, time_str):
    """Versão de consola: imprime o resultado de summarize_wait_time_by_time."""
    try:
        r = summarize_wait_time_by_time(df, crosswalk_number, time_str)
    except ValueError as error:
        print(error)
        return

    if r["status"] == "no_crosswalk":
        print(f"Não há registos para a crosswalk {crosswalk_number:04d}. Existentes: {', '.join(r['available_ids'])}")
        return
    if r["status"] == "no_data":
        print(
            f"na crosswalk {crosswalk_number:04d} no intervalo [{r['start']}; {r['end']}] "
            f"não existem registos válidos de wait_time (a passadeira pode não ter dados a essa hora)."
        )
        return

    print(
        f"Crosswalk {crosswalk_number:04d}, intervalo [{r['start']}; {r['end']}] UTC: "
        f"espera média = {r['average']:.2f} s | mediana = {r['median']:.2f} s "
        f"({r['records']} registos, {r['days']} dia(s) distinto(s))."
    )
    if r["low_data"]:
        print(f"  Aviso: poucos dados (menos de {MIN_RECORDS} registos ou {MIN_DAYS} dias), o valor é pouco fiável.")
    if r["skewed"]:
        print("  Aviso: a média é muito maior que a mediana, há esperas muito longas a puxá-la para cima.")


def main():
    print("A carregar dados de data/smartcrosswalk.csv...")
    df = load_crosswalk_data("data/smartcrosswalk.csv")
    print(f"Dados carregados com sucesso ({len(df)} registos com timestamp válido).\n")

    while True:
        try:
            crosswalk_num = int(input("Número da Crosswalk (ex: 19): ").strip())
            time_input = input("Horário a pesquisar em UTC (HH:MM, ex: 14:30): ").strip()
            average_wait_time_by_time(df, crosswalk_num, time_input)
        except ValueError:
            print("Por favor, insere um número inteiro válido para a crosswalk.")

        again = input("\nQueres fazer outra pesquisa? (s/n): ").strip().lower()
        if again != "s":
            break

if __name__ == "__main__":
    main()