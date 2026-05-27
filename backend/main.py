from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg import sql
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.rows import dict_row

app = FastAPI(title="JB Intelligence API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_schema: str
    gemini_api_keys: tuple[str, ...]
    gemini_model: str
    pedidos_api_base_url: str


def parse_gemini_api_keys(raw_multi: str, raw_single: str) -> tuple[str, ...]:
    combined = raw_multi or raw_single or ""
    if not combined.strip():
        return tuple()

    parts = re.split(r"[\n,; ]+", combined.strip())
    keys: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return tuple(keys)


def load_env_from_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_settings() -> Settings:
    load_env_from_file(ENV_PATH)
    default_local_pg = "postgresql://postgres:2026@localhost:5432/postgres"
    return Settings(
        database_url=os.environ.get("DATABASE_URL", default_local_pg).strip(),
        database_schema=os.environ.get("DATABASE_SCHEMA", "public").strip() or "public",
        gemini_api_keys=parse_gemini_api_keys(
            os.environ.get("GEMINI_API_KEYS", "").strip(),
            os.environ.get("GEMINI_API_KEY", "").strip(),
        ),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        pedidos_api_base_url=os.environ.get("PEDIDOS_API_BASE_URL", "http://192.168.10.10:5010").strip(),
    )


SETTINGS = get_settings()
SEARCH_STOPWORDS = {
    "qual",
    "quais",
    "como",
    "onde",
    "quando",
    "quem",
    "que",
    "o",
    "a",
    "os",
    "as",
    "de",
    "do",
    "da",
    "dos",
    "das",
    "um",
    "uma",
    "por",
    "para",
    "com",
    "no",
    "na",
    "nos",
    "nas",
    "e",
}

FILIAL_ALIASES: dict[str, list[str]] = {
    "FORTALEZA": ["fortaleza", "for"],
    "IMPERATRIZ": ["imperatriz", "imp"],
    "JUAZEIRO": ["juazeiro", "jua"],
    "SÃO LUÍS": ["sao luis", "são luís", "sao luis", "slz", "sao luis"],
    "SOBRAL": ["sobral", "sob"],
    "TERESINA": ["teresina", "the", "ter"],
}


def get_conn() -> psycopg.Connection:
    conn = psycopg.connect(SETTINGS.database_url, row_factory=dict_row)
    schema = SETTINGS.database_schema
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", schema):
        schema = "public"
    with conn.cursor() as cur:
        if schema.lower() == "public":
            cur.execute("SET search_path TO public")
        else:
            cur.execute(
                sql.SQL("SET search_path TO {}, public").format(
                    sql.Identifier(schema)
                )
            )
    return conn


def _normalize_sequence_regclass(default_expr: str) -> str | None:
    m = re.search(r"nextval\('([^']+)'::regclass\)", default_expr or "", flags=re.IGNORECASE)
    if not m:
        return None
    seq_ref = m.group(1).strip()
    if "." in seq_ref:
        return seq_ref
    schema = SETTINGS.database_schema if SETTINGS.database_schema else "public"
    return f"{schema}.{seq_ref}"


def sync_serial_sequences(cur: psycopg.Cursor) -> None:
    schema = SETTINGS.database_schema if SETTINGS.database_schema else "public"
    cur.execute(
        """
        SELECT table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = %s
          AND column_default LIKE 'nextval(%%'
        ORDER BY table_name, ordinal_position
        """,
        (schema,),
    )
    for row in cur.fetchall():
        table_name = row["table_name"]
        column_name = row["column_name"]
        seq_regclass = _normalize_sequence_regclass(str(row.get("column_default") or ""))
        if not seq_regclass:
            continue
        cur.execute(
            sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {}.{}").format(
                sql.Identifier(column_name),
                sql.Identifier(schema),
                sql.Identifier(table_name),
            )
        )
        max_id = int(cur.fetchone()["coalesce"] or 0)
        next_id = max_id + 1
        cur.execute("SELECT setval(%s::regclass, %s, false)", (seq_regclass, next_id))


def get_ordered_gemini_api_keys() -> list[str]:
    keys = list(SETTINGS.gemini_api_keys)
    if not keys:
        return []

    today = date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gemini_key_state (id, current_index, last_reset_date, updated_at)
                VALUES (1, 0, CURRENT_DATE, NOW())
                ON CONFLICT (id) DO NOTHING
                """
            )
            cur.execute(
                "SELECT current_index, last_reset_date FROM gemini_key_state WHERE id = 1"
            )
            row = cur.fetchone()
            current_index = int(row["current_index"]) if row else 0
            last_reset = row["last_reset_date"] if row else today

            if last_reset < today:
                current_index = 0
                cur.execute(
                    """
                    UPDATE gemini_key_state
                    SET current_index = 0, last_reset_date = CURRENT_DATE, updated_at = NOW()
                    WHERE id = 1
                    """
                )
            conn.commit()

    if not keys:
        return []
    start = current_index % len(keys)
    return keys[start:] + keys[:start]


def advance_gemini_key_pointer(used_key: str) -> None:
    keys = list(SETTINGS.gemini_api_keys)
    if not keys:
        return
    try:
        used_idx = keys.index(used_key)
    except ValueError:
        return
    next_idx = (used_idx + 1) % len(keys)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gemini_key_state (id, current_index, last_reset_date, updated_at)
                VALUES (1, %s, CURRENT_DATE, NOW())
                ON CONFLICT (id) DO UPDATE
                SET current_index = EXCLUDED.current_index,
                    last_reset_date = EXCLUDED.last_reset_date,
                    updated_at = NOW()
                """,
                (next_idx,),
            )
        conn.commit()


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def tokenize_for_matching(text: str) -> list[str]:
    tokens = [t for t in normalize_text(text).split() if len(t) >= 3]
    filtered = [t for t in tokens if t not in SEARCH_STOPWORDS]
    dedup: list[str] = []
    seen: set[str] = set()
    for tok in filtered:
        if tok in seen:
            continue
        seen.add(tok)
        dedup.append(tok)
    return dedup


def to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(str(value).strip())
    except Exception:
        return 0


def normalize_filial_name(value: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip().upper()


def detect_filial_in_text(text: str) -> str | None:
    norm = normalize_text(text)
    for filial, aliases in FILIAL_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_text(alias)
            if not alias_norm:
                continue
            if re.search(rf"\b{re.escape(alias_norm)}\b", norm):
                return filial
    return None


def detect_filiais_in_text(text: str) -> list[str]:
    norm = normalize_text(text)
    hits: list[tuple[int, str]] = []
    for filial, aliases in FILIAL_ALIASES.items():
        first_pos: int | None = None
        for alias in aliases:
            alias_norm = normalize_text(alias)
            if not alias_norm:
                continue
            m = re.search(rf"\b{re.escape(alias_norm)}\b", norm)
            if not m:
                continue
            pos = int(m.start())
            if first_pos is None or pos < first_pos:
                first_pos = pos
        if first_pos is not None:
            hits.append((first_pos, filial))

    hits.sort(key=lambda x: x[0])
    ordered: list[str] = []
    seen: set[str] = set()
    for _, filial in hits:
        if filial in seen:
            continue
        seen.add(filial)
        ordered.append(filial)
    return ordered


def normalize_route_code(text: str) -> str:
    only = re.sub(r"[^a-zA-Z0-9]", "", text or "")
    return only.upper()


def find_exact_route_codes(text: str) -> list[str]:
    found: list[str] = []
    patterns = [
        r"\b([A-Za-z]{2,4})\s*[-_/]?\s*(\d{1,4})\b",
        r"\b([A-Za-z]{2,4}\d{1,4})\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text or ""):
            route = normalize_route_code("".join(match.groups()) if match.groups() else match.group(0))
            if len(route) < 5:
                continue
            if route not in found:
                found.append(route)
    return found


def find_prefix_route_token(text: str) -> str | None:
    if not text:
        return None
    ignore = {
        "quem",
        "qual",
        "quais",
        "motorista",
        "motoristas",
        "parceiro",
        "parceiros",
        "da",
        "de",
        "do",
        "e",
        "rota",
        "subrota",
        "o",
        "a",
        "em",
        "tem",
        "tinha",
        "ha",
    }
    # Pega o último token alfabético curto da pergunta, ex.: "PTC"
    candidates = re.findall(r"\b([A-Za-z]{2,4})\b", text.upper())
    for token in reversed(candidates):
        low = token.lower()
        if low in ignore:
            continue
        return token
    return None


def extract_route_selector(question: str, history: list[dict[str, Any]] | None = None) -> dict[str, str] | None:
    # 1) prioridade absoluta: rota presente na pergunta atual
    exact_from_question = find_exact_route_codes(question)
    if exact_from_question:
        return {"mode": "exact", "value": exact_from_question[0]}

    prefix_from_question = find_prefix_route_token(question)
    if prefix_from_question:
        return {"mode": "prefix", "value": prefix_from_question}

    # 2) fallback de histórico somente quando pergunta atual não trouxe rota
    if history:
        history_text = " ".join(str(m.get("content", "")) for m in history[-8:])
        exact_from_history = find_exact_route_codes(history_text)
        if exact_from_history:
            return {"mode": "exact", "value": exact_from_history[0]}

    return None


def detect_structured_intent(question: str) -> str | None:
    q = normalize_text(question)
    if any(k in q for k in ["parceiro", "parceria"]):
        return "partner_name"
    if any(k in q for k in ["motorista", "condutor", "motoristas", "motorist"]):
        return "driver_name"
    if any(k in q for k in ["tempo em rota", "tempo de rota", "dias em rota", "quantos dias", "tempo rota"]):
        return "route_time_days"
    if "largada" in q or "dias" in q:
        return "departure_days"
    if "regiao" in q or "regional" in q:
        return "region"
    return None


def detect_structured_intent_from_history(history: list[dict[str, Any]] | None) -> str | None:
    if not history:
        return None
    # Busca de trás para frente para manter o último campo pedido
    for msg in reversed(history[-8:]):
        if msg.get("role") != "user":
            continue
        intent = detect_structured_intent(str(msg.get("content", "")))
        if intent:
            return intent
    return None


def is_short_followup_question(question: str) -> bool:
    q = normalize_text(question)
    tokens = [t for t in q.split() if t]
    if len(tokens) <= 4:
        return True
    followup_starts = (
        "e ",
        "e da ",
        "e do ",
        "e de ",
        "e o ",
        "e a ",
        "e as ",
        "e os ",
    )
    if q.startswith(followup_starts):
        return True
    return q in {"e da", "e do", "e de", "e o", "e a", "e as", "e os"}


def extract_search_terms(question: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", normalize_text(question))
    terms: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in SEARCH_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms[:8]


def split_multi_values(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    parts = [p.strip() for p in str(raw_value).split("|")]
    values: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(part)
    return values


def format_answer_values(values: list[str]) -> str:
    if not values:
        return "Informação não consta nos manuais ou tabelas disponíveis."
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} e {values[1]}"
    return ", ".join(values[:-1]) + f" e {values[-1]}"


def parse_first_number(raw_value: str | None) -> float | None:
    if not raw_value:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(raw_value))
    if not match:
        return None
    numeric = match.group(0).replace(",", ".")
    try:
        return float(numeric)
    except ValueError:
        return None


def format_days_value(value: float) -> str:
    if value.is_integer():
        days = int(value)
        return f"{days} dia" if days == 1 else f"{days} dias"
    return f"{value:.2f}".rstrip("0").rstrip(".") + " dias"


def format_route_list_compact(routes: list[str], max_items: int = 12) -> str:
    if len(routes) <= max_items:
        return format_answer_values(routes)
    visible = routes[:max_items]
    hidden = len(routes) - max_items
    return f"{format_answer_values(visible)} e mais {hidden} subrotas"


def longest_common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        i = 0
        max_i = min(len(prefix), len(value))
        while i < max_i and prefix[i] == value[i]:
            i += 1
        prefix = prefix[:i]
        if not prefix:
            break
    return prefix


def normalize_prefix_hint(prefix: str) -> str:
    # Evita sugestões com dígitos no final, ex.: "SPM0" -> "SPM"
    cleaned = re.sub(r"\d+$", "", (prefix or "").strip())
    return cleaned or prefix


def detect_route_time_aggregate_mode(question: str, history: list[dict[str, Any]] | None = None) -> str | None:
    q = normalize_text(question)
    history_text = " ".join(str(m.get("content", "")) for m in (history or [])[-8:])
    qh = normalize_text(history_text)

    has_route_scope = any(k in q for k in ["rota", "subrota"]) or any(k in qh for k in ["rota", "subrota"])
    has_time_topic = any(k in q for k in ["tempo", "dias", "demora"]) or any(
        k in qh for k in ["tempo", "dias", "demora"]
    )
    if not (has_route_scope and has_time_topic):
        return None

    max_markers = [
        "demora mais",
        "mais dias",
        "maior tempo",
        "maximo",
        "maxima",
        "subrota demora mais",
        "rota demora mais",
    ]
    min_markers = [
        "demora menos",
        "menos dias",
        "menor tempo",
        "minimo",
        "minima",
        "subrota demora menos",
        "rota demora menos",
    ]

    if any(k in q for k in max_markers):
        return "max"
    if any(k in q for k in min_markers):
        return "min"
    if any(k in q for k in ["media", "médio", "medio"]):
        return "avg"
    # Follow-up curto: herda direção da pergunta anterior do usuário
    if history:
        for msg in reversed(history[-8:]):
            if msg.get("role") != "user":
                continue
            hq = normalize_text(str(msg.get("content", "")))
            if any(k in hq for k in max_markers):
                return "max"
            if any(k in hq for k in min_markers):
                return "min"
            if any(k in hq for k in ["media", "médio", "medio"]):
                return "avg"
    return None


def try_route_time_aggregate_answer(question: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    mode = detect_route_time_aggregate_mode(question, history)
    if not mode:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT route_code, route_code_norm, route_time_days
                FROM route_facts
                WHERE COALESCE(route_time_days, '') <> ''
                """
            )
            rows = cur.fetchall()

    samples: list[tuple[str, float]] = []
    for row in rows:
        route = row.get("route_code") or row.get("route_code_norm") or ""
        route = str(route).strip()
        if not route:
            continue
        for raw in split_multi_values(row.get("route_time_days")):
            number = parse_first_number(raw)
            if number is None:
                continue
            samples.append((route, number))

    if not samples:
        return {
            "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
            "used_mode": "structured_time_aggregate_miss",
        }

    if mode == "avg":
        avg_value = sum(v for _, v in samples) / len(samples)
        return {
            "answer": f"Média de tempo em rota: {format_days_value(avg_value)}.",
            "used_mode": "structured_time_aggregate_avg",
        }

    target = max(v for _, v in samples) if mode == "max" else min(v for _, v in samples)
    routes = sorted({route for route, value in samples if value == target})
    routes_answer = format_route_list_compact(routes)
    verb = "demora mais" if mode == "max" else "demora menos"
    return {
        "answer": f"{routes_answer} ({format_days_value(target)}; {verb}).",
        "used_mode": f"structured_time_aggregate_{mode}",
    }


def is_subrota_count_question(question: str) -> bool:
    q = normalize_text(question)
    if "subrota" not in q and "subrotas" not in q:
        return False
    return any(k in q for k in ["quant", "qtd", "qtde", "numero", "total", "tem"])


def is_subrota_count_question_from_history(history: list[dict[str, Any]] | None) -> bool:
    if not history:
        return False
    # Herda apenas da última pergunta do usuário (contexto imediato).
    for msg in reversed(history[-8:]):
        if msg.get("role") != "user":
            continue
        return is_subrota_count_question(str(msg.get("content", "")))
    return False


def try_subrota_count_answer(question: str, history: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not is_subrota_count_question(question):
        if not (is_short_followup_question(question) and is_subrota_count_question_from_history(history)):
            return None

    filial = detect_pedidos_filial(question, history)
    if not filial:
        return {
            "answer": "Informe a filial para eu contar as subrotas (ex.: FORTALEZA, TERESINA, JUAZEIRO).",
            "used_mode": "structured_subrota_count_need_filial",
        }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT NULLIF(TRIM("SUBROTA"), '')) AS total
                FROM d23_full
                WHERE lower(unaccent(COALESCE("FILIAL", ''))) = lower(unaccent(%s))
                """,
                (filial,),
            )
            row = cur.fetchone()

    total = int(row["total"] or 0) if row else 0
    if total <= 0:
        return {
            "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
            "used_mode": "structured_subrota_count_miss",
        }

    noun = "subrota" if total == 1 else "subrotas"
    return {
        "answer": f"{filial}: {total} {noun}.",
        "used_mode": "structured_subrota_count",
    }


def detect_d23_analytics_metric(question: str) -> str | None:
    q = normalize_text(question)
    if ("tempo" in q or "dias" in q) and any(k in q for k in ["media", "medio", "médio"]):
        return "avg_route_time_days"
    if ("tempo" in q or "dias" in q) and any(k in q for k in ["maior", "maximo", "maxima", "demora mais"]):
        return "max_route_time_days"
    if ("tempo" in q or "dias" in q) and any(k in q for k in ["menor", "minimo", "minima", "demora menos"]):
        return "min_route_time_days"
    if "cidade" in q and any(k in q for k in ["quant", "qtd", "qtde", "numero", "total"]):
        return "count_cities"
    if "parceir" in q and any(k in q for k in ["quant", "qtd", "qtde", "numero", "total"]):
        return "count_partners"
    if "motorist" in q and any(k in q for k in ["quant", "qtd", "qtde", "numero", "total"]):
        return "count_drivers"
    if "parceir" in q and any(k in q for k in ["quais", "listar", "liste", "mostre"]):
        return "list_partners"
    if "motorist" in q and any(k in q for k in ["quais", "listar", "liste", "mostre"]):
        return "list_drivers"
    return None


def build_d23_filter_sql(
    question: str, history: list[dict[str, Any]]
) -> tuple[str, list[Any], str | None, dict[str, str] | None, str | None]:
    filters: list[str] = []
    params: list[Any] = []

    filial = detect_pedidos_filial(question, history)
    if filial:
        filters.append("lower(unaccent(COALESCE(row_data->>'FILIAL', ''))) = lower(unaccent(%s))")
        params.append(filial)

    qn = normalize_text(question)
    has_route_scope = "rota" in qn or "subrota" in qn
    exact_codes_in_question = find_exact_route_codes(question)
    selector = None
    if exact_codes_in_question:
        selector = {"mode": "exact", "value": exact_codes_in_question[0]}
    elif has_route_scope:
        scoped_match = re.search(r"\b(?:subrota|rota)\s+([A-Za-z0-9]{2,8})\b", question, flags=re.IGNORECASE)
        if scoped_match:
            scoped_token = normalize_route_code(scoped_match.group(1))
            if scoped_token and scoped_token not in {"EM", "NA", "NO", "DA", "DO", "DE"}:
                if re.search(r"\d", scoped_token):
                    selector = {"mode": "exact", "value": scoped_token}
                elif len(scoped_token) >= 3:
                    selector = {"mode": "prefix", "value": scoped_token}
        # Para analytics em linguagem natural, evitamos herdar prefixo genérico do histórico
        # quando não houver código/subrota explícitos na pergunta atual.

    if selector:
        if selector["mode"] == "exact":
            filters.append("route_code_norm = %s")
            params.append(selector["value"])
        else:
            filters.append("route_code_norm LIKE %s")
            params.append(f"{selector['value']}%")

    city = extract_city_filter(question)
    if city:
        filters.append(
            """
            (
              lower(unaccent(COALESCE(row_data->>'LOCALIDADE', ''))) LIKE ('%%' || lower(unaccent(%s)) || '%%')
              OR lower(unaccent(COALESCE(row_data->>'LOCALIDADE SEM ACENTO', ''))) LIKE ('%%' || lower(unaccent(%s)) || '%%')
            )
            """
        )
        params.extend([city, city])

    where_sql = " AND ".join(["1=1"] + filters)
    return where_sql, params, filial, selector, city


def query_d23_distinct_values(where_sql: str, params: list[Any], key: str, limit: int = 300) -> list[str]:
    sql = f"""
        SELECT DISTINCT NULLIF(TRIM(COALESCE(row_data->>'{key}', '')), '') AS value
        FROM d23_rows
        WHERE {where_sql}
        ORDER BY value
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            rows = cur.fetchall()
    values = [str(r.get("value") or "").strip() for r in rows if str(r.get("value") or "").strip()]
    return values


def query_d23_time_samples(where_sql: str, params: list[Any], limit: int = 50000) -> list[float]:
    sql = f"""
        SELECT row_data->>'Tempo de Rota (Dias)' AS days
        FROM d23_rows
        WHERE {where_sql}
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            rows = cur.fetchall()

    numbers: list[float] = []
    for row in rows:
        raw = str(row.get("days") or "").strip()
        if not raw:
            continue
        for part in split_multi_values(raw):
            parsed = parse_first_number(part)
            if parsed is None:
                continue
            numbers.append(parsed)
    return numbers


def try_d23_analytics_answer(question: str, history: list[dict[str, Any]]) -> dict[str, Any] | None:
    metric = detect_d23_analytics_metric(question)
    if not metric:
        return None

    where_sql, params, filial, selector, city = build_d23_filter_sql(question, history)
    scope_parts: list[str] = []
    if filial:
        scope_parts.append(f"filial {filial}")
    if selector and selector["mode"] == "exact":
        scope_parts.append(f"subrota {selector['value']}")
    if city:
        scope_parts.append(f"cidade {city}")
    scope_text = " | ".join(scope_parts)

    if metric == "count_partners":
        values = query_d23_distinct_values(where_sql, params, "Parceiro")
        prefix = f"No filtro ({scope_text}), " if scope_text else ""
        return {"answer": f"{prefix}existem {len(values)} parceiros distintos.", "used_mode": "d23_count_partners"}

    if metric == "count_drivers":
        values = query_d23_distinct_values(where_sql, params, "Motorista")
        prefix = f"No filtro ({scope_text}), " if scope_text else ""
        return {"answer": f"{prefix}existem {len(values)} motoristas distintos.", "used_mode": "d23_count_drivers"}

    if metric == "count_cities":
        values = query_d23_distinct_values(where_sql, params, "LOCALIDADE")
        prefix = f"No filtro ({scope_text}), " if scope_text else ""
        return {"answer": f"{prefix}existem {len(values)} cidades distintas.", "used_mode": "d23_count_cities"}

    if metric == "list_partners":
        values = query_d23_distinct_values(where_sql, params, "Parceiro")
        if not values:
            return {
                "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
                "used_mode": "d23_list_partners_miss",
            }
        listed = format_route_list_compact(values, max_items=20)
        prefix = f"Parceiros ({scope_text}): " if scope_text else "Parceiros: "
        return {"answer": f"{prefix}{listed}.", "used_mode": "d23_list_partners"}

    if metric == "list_drivers":
        values = query_d23_distinct_values(where_sql, params, "Motorista")
        if not values:
            return {
                "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
                "used_mode": "d23_list_drivers_miss",
            }
        listed = format_route_list_compact(values, max_items=20)
        prefix = f"Motoristas ({scope_text}): " if scope_text else "Motoristas: "
        return {"answer": f"{prefix}{listed}.", "used_mode": "d23_list_drivers"}

    samples = query_d23_time_samples(where_sql, params)
    if not samples:
        return {
            "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
            "used_mode": "d23_time_miss",
        }

    if metric == "avg_route_time_days":
        avg_value = sum(samples) / len(samples)
        prefix = f"Média de tempo em rota ({scope_text}): " if scope_text else "Média de tempo em rota: "
        return {"answer": f"{prefix}{format_days_value(avg_value)}.", "used_mode": "d23_avg_time"}
    if metric == "max_route_time_days":
        max_value = max(samples)
        prefix = f"Maior tempo em rota ({scope_text}): " if scope_text else "Maior tempo em rota: "
        return {"answer": f"{prefix}{format_days_value(max_value)}.", "used_mode": "d23_max_time"}
    if metric == "min_route_time_days":
        min_value = min(samples)
        prefix = f"Menor tempo em rota ({scope_text}): " if scope_text else "Menor tempo em rota: "
        return {"answer": f"{prefix}{format_days_value(min_value)}.", "used_mode": "d23_min_time"}

    return None


def init_db() -> None:
    schema_sql = """
    CREATE EXTENSION IF NOT EXISTS unaccent;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;

    CREATE TABLE IF NOT EXISTS app_users (
      id BIGSERIAL PRIMARY KEY,
      username TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'consultor',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS conversations (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL REFERENCES app_users(id),
      title TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS messages (
      id BIGSERIAL PRIMARY KEY,
      conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS qa_overrides (
      id BIGSERIAL PRIMARY KEY,
      question_norm TEXT NOT NULL UNIQUE,
      question_raw TEXT NOT NULL,
      answer TEXT NOT NULL,
      admin_user_id BIGINT REFERENCES app_users(id),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS qa_cache (
      question_norm TEXT PRIMARY KEY,
      question_raw TEXT NOT NULL,
      answer TEXT NOT NULL,
      source_mode TEXT NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS route_facts (
      id BIGSERIAL PRIMARY KEY,
      route_code TEXT NOT NULL,
      route_code_norm TEXT NOT NULL UNIQUE,
      partner_name TEXT,
      driver_name TEXT,
      route_time_days TEXT,
      departure_days TEXT,
      region TEXT,
      source_name TEXT,
      extra JSONB NOT NULL DEFAULT '{}'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_route_facts_route_norm ON route_facts(route_code_norm);
    ALTER TABLE route_facts ADD COLUMN IF NOT EXISTS route_time_days TEXT;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_app_users_username ON app_users(username);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_qa_overrides_question_norm ON qa_overrides(question_norm);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_qa_cache_question_norm ON qa_cache(question_norm);

    CREATE TABLE IF NOT EXISTS d23_rows (
      id BIGSERIAL PRIMARY KEY,
      source_name TEXT NOT NULL,
      row_number INT NOT NULL,
      route_code TEXT,
      route_code_norm TEXT,
      row_data JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(source_name, row_number)
    );

    CREATE INDEX IF NOT EXISTS idx_d23_rows_route_norm ON d23_rows(route_code_norm);
    CREATE INDEX IF NOT EXISTS idx_d23_rows_jsonb ON d23_rows USING GIN(row_data);

    CREATE TABLE IF NOT EXISTS d23_full (
      id BIGSERIAL PRIMARY KEY,
      source_name TEXT NOT NULL,
      row_number INT NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(source_name, row_number)
    );

    CREATE TABLE IF NOT EXISTS doc_sources (
      id BIGSERIAL PRIMARY KEY,
      source_name TEXT NOT NULL UNIQUE,
      source_type TEXT NOT NULL,
      version_tag TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS doc_chunks (
      id BIGSERIAL PRIMARY KEY,
      source_id BIGINT NOT NULL REFERENCES doc_sources(id) ON DELETE CASCADE,
      chunk_index INT NOT NULL,
      section_title TEXT,
      page_start INT,
      page_end INT,
      content TEXT NOT NULL,
      content_norm TEXT NOT NULL,
      search_tsv tsvector NOT NULL,
      metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
      UNIQUE(source_id, chunk_index)
    );

    CREATE INDEX IF NOT EXISTS idx_doc_chunks_tsv ON doc_chunks USING GIN(search_tsv);
    CREATE INDEX IF NOT EXISTS idx_doc_chunks_norm_trgm ON doc_chunks USING GIN(content_norm gin_trgm_ops);

    CREATE TABLE IF NOT EXISTS gemini_key_state (
      id SMALLINT PRIMARY KEY CHECK (id = 1),
      current_index INT NOT NULL DEFAULT 0,
      last_reset_date DATE NOT NULL DEFAULT CURRENT_DATE,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
            cur.execute(
                """
                INSERT INTO app_users (username, password_hash, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT (username) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    role = EXCLUDED.role
                """,
                ("admin", hash_password("admin123")),
            )
            cur.execute(
                """
                INSERT INTO app_users (username, password_hash, role)
                VALUES (%s, %s, 'consultor')
                ON CONFLICT (username) DO NOTHING
                """,
                ("consultor", hash_password("jb123")),
            )
            cur.execute(
                """
                INSERT INTO gemini_key_state (id, current_index, last_reset_date, updated_at)
                VALUES (1, 0, CURRENT_DATE, NOW())
                ON CONFLICT (id) DO NOTHING
                """
            )
            sync_serial_sequences(cur)
        conn.commit()


@app.on_event("startup")
async def on_startup() -> None:
    init_db()


@app.get("/health")
@app.get("/api/health")
async def health() -> JSONResponse:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return JSONResponse(
            {
                "status": "ok",
                "model": SETTINGS.gemini_model,
                "database": "postgresql",
            }
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


@app.post("/api/login")
async def login(request: Request) -> JSONResponse:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role FROM app_users WHERE username = %s AND password_hash = %s",
                (username, hash_password(password)),
            )
            user = cur.fetchone()

    if not user:
        return JSONResponse({"status": "error", "message": "Usuário ou senha inválidos"}, status_code=401)

    return JSONResponse(
        {
            "status": "success",
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            },
        }
    )


@app.api_route("/api/conversations", methods=["GET", "POST"])
async def conversations(request: Request):
    if request.method == "POST":
        body = await request.json()
        user_id = int(body.get("user_id"))
        title = (body.get("titulo") or "Nova Conversa").strip() or "Nova Conversa"

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id",
                    (user_id, title),
                )
                row = cur.fetchone()
            conn.commit()

        return JSONResponse({"id": row["id"], "titulo": title})

    user_id = int(request.query_params.get("user_id"))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, created_at FROM conversations WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cur.fetchall()

    payload = [
        {
            "id": r["id"],
            "titulo": r["title"],
            "data": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]
    return JSONResponse(payload)


@app.get("/api/messages/{conversation_id}")
async def get_messages(conversation_id: int) -> JSONResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content FROM messages WHERE conversation_id = %s ORDER BY created_at ASC, id ASC",
                (conversation_id,),
            )
            rows = cur.fetchall()
    payload = [{"role": r["role"], "content": r["content"]} for r in rows]
    return JSONResponse(payload)


@app.post("/api/learn")
async def learn(request: Request) -> JSONResponse:
    data = await request.json()
    question = (data.get("pergunta") or "").strip()
    answer = (data.get("resposta") or "").strip()
    admin_id = data.get("admin_id")

    if not question or not answer:
        raise HTTPException(status_code=400, detail="Pergunta e resposta são obrigatórias")

    question_norm = normalize_text(question)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qa_overrides (question_norm, question_raw, answer, admin_user_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (question_norm) DO UPDATE
                SET question_raw = EXCLUDED.question_raw,
                    answer = EXCLUDED.answer,
                    admin_user_id = EXCLUDED.admin_user_id,
                    updated_at = NOW()
                """,
                (question_norm, question, answer, admin_id),
            )
        conn.commit()

    return JSONResponse({"status": "success", "message": "IA aprendeu com sucesso"})


def try_structured_answer(question: str, history: list[dict[str, Any]]) -> dict[str, Any] | None:
    subrota_count = try_subrota_count_answer(question, history)
    if subrota_count:
        return subrota_count

    d23_analytics = try_d23_analytics_answer(question, history)
    if d23_analytics:
        return d23_analytics

    field = detect_structured_intent(question)
    if not field and is_short_followup_question(question):
        field = detect_structured_intent_from_history(history)
    aggregate_answer = try_route_time_aggregate_answer(question, history)
    if aggregate_answer:
        return aggregate_answer
    selector = extract_route_selector(question, history)
    if not field or not selector:
        return None

    route_ref = selector["value"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            if selector["mode"] == "exact":
                cur.execute(
                    """
                    SELECT route_code, partner_name, driver_name, route_time_days, departure_days, region
                    FROM route_facts
                    WHERE route_code_norm = %s
                    """,
                    (route_ref,),
                )
            else:
                cur.execute(
                    """
                    SELECT route_code_norm
                    FROM route_facts
                    WHERE route_code_norm LIKE %s
                    ORDER BY route_code_norm
                    LIMIT 300
                    """,
                    (f"{route_ref}%",),
                )
                candidates = [str(r["route_code_norm"]) for r in cur.fetchall() if r.get("route_code_norm")]

                if not candidates:
                    cur.execute(
                        """
                        SELECT route_code_norm
                        FROM route_facts
                        WHERE similarity(route_code_norm, %s) > 0.45
                        ORDER BY similarity(route_code_norm, %s) DESC
                        LIMIT 1
                        """,
                        (route_ref, route_ref),
                    )
                    suggestion = cur.fetchone()
                    if suggestion:
                        suggested = suggestion["route_code_norm"]
                        return {
                            "answer": (
                                f"Não encontrei subrota '{route_ref}'. "
                                f"Você quis dizer '{suggested}'?"
                            ),
                            "used_mode": "structured_route_suggestion",
                        }
                    return {
                        "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
                        "used_mode": "structured_miss",
                        "route_code": route_ref,
                    }

                if len(candidates) > 1:
                    common = normalize_prefix_hint(longest_common_prefix(candidates))
                    listed = format_route_list_compact(candidates, max_items=10)
                    if common and len(common) >= len(route_ref) + 1:
                        answer = (
                            f"Encontrei várias subrotas para '{route_ref}': {listed}. "
                            f"Você quis dizer o prefixo '{common}'? "
                            f"Me diga a subrota exata (ex.: {candidates[0]})."
                        )
                    else:
                        answer = (
                            f"Encontrei várias subrotas para '{route_ref}': {listed}. "
                            "Sobre qual você quer saber? "
                            f"Informe a subrota exata (ex.: {candidates[0]})."
                        )
                    return {
                        "answer": answer,
                        "used_mode": "structured_disambiguation",
                    }

                route_ref = candidates[0]
                cur.execute(
                    """
                    SELECT route_code, partner_name, driver_name, route_time_days, departure_days, region
                    FROM route_facts
                    WHERE route_code_norm = %s
                    """,
                    (route_ref,),
                )
            rows = cur.fetchall()

    if not rows:
        return {
            "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
            "used_mode": "structured_miss",
            "route_code": route_ref,
        }

    aggregated_values: list[str] = []
    seen_values: set[str] = set()
    for row in rows:
        for value in split_multi_values(row.get(field)):
            key = value.lower()
            if key in seen_values:
                continue
            seen_values.add(key)
            aggregated_values.append(value)

    if not aggregated_values:
        return {
            "answer": "Informação não consta nos manuais ou tabelas disponíveis.",
            "used_mode": "structured_missing_field",
            "route_code": route_ref,
        }

    answer = format_answer_values(aggregated_values)
    return {
        "answer": answer,
        "used_mode": "structured_sql",
        "route_code": rows[0].get("route_code") or route_ref,
    }


def is_material_inventory_question(question: str) -> bool:
    q = normalize_text(question)
    patterns = [
        "sobre quais materiais",
        "quais materiais",
        "quais manuais",
        "quais tabelas",
        "material disponivel",
        "materiais disponiveis",
        "base de conhecimento",
        "fontes de dados",
        "que documentos voce tem",
        "autonomia de responder",
    ]
    return any(p in q for p in patterns)


def build_material_inventory_answer() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_name, source_type
                FROM doc_sources
                ORDER BY source_type, source_name
                """
            )
            docs = cur.fetchall()

            cur.execute(
                """
                SELECT ds.source_name, dc.chunk_index, dc.content
                FROM doc_chunks dc
                JOIN doc_sources ds ON ds.id = dc.source_id
                WHERE lower(ds.source_name) = lower(%s)
                ORDER BY dc.chunk_index ASC
                """,
                ("Compliado Processos.docx",),
            )
            compilado_chunks = cur.fetchall()

            cur.execute(
                """
                SELECT source_name, COUNT(*) AS total_rotas
                FROM route_facts
                GROUP BY source_name
                ORDER BY source_name
                """
            )
            route_tables = cur.fetchall()

            cur.execute(
                """
                SELECT source_name, COUNT(*) AS total_linhas
                FROM d23_full
                GROUP BY source_name
                ORDER BY source_name
                """
            )
            d23_full_tables = cur.fetchall()

    lines: list[str] = []
    lines.append("Materiais carregados atualmente:")

    if docs:
        lines.append("Manuais e documentos:")
        for d in docs:
            lines.append(f"- {d['source_name']} ({d['source_type']})")

    if route_tables:
        lines.append("Tabelas estruturadas de rota:")
        for t in route_tables:
            src = t.get("source_name") or "fonte_desconhecida"
            lines.append(f"- {src} -> route_facts ({t['total_rotas']} subrotas consolidadas)")

    if d23_full_tables:
        lines.append("Tabelas completas importadas:")
        for t in d23_full_tables:
            src = t.get("source_name") or "fonte_desconhecida"
            lines.append(f"- {src} -> d23_full ({t['total_linhas']} linhas)")

    if compilado_chunks:
        joined = "\n".join((row.get("content") or "") for row in compilado_chunks)
        pattern = r"\b([A-Z]{4}\d{2}-\d{2})\s*[–-]\s*([^\n\r]{3,140})"
        refs_by_code: dict[str, str] = {}
        order: list[str] = []

        for match in re.finditer(pattern, joined):
            code = match.group(1).strip()
            raw_title = re.sub(r"\s+", " ", match.group(2)).strip()
            if not raw_title:
                continue

            # Corta qualquer continuação além do título.
            raw_title = re.split(r"\s+[A-Z]{4}\d{2}-\d{2}\s*[–-]\s*", raw_title)[0]
            for delimiter in [
                " Informações Gerais",
                " Finalidade",
                " 1. ",
                " Código:",
                " Responsável",
                " Responsáveis",
                " Setor ",
                " Setores ",
            ]:
                if delimiter in raw_title:
                    raw_title = raw_title.split(delimiter, 1)[0].strip()

            clean_title = raw_title.rstrip(".,;:- ").strip()
            if not clean_title:
                continue

            if code not in refs_by_code:
                refs_by_code[code] = clean_title
                order.append(code)
            else:
                # Mantém o título mais curto/objetivo quando houver variações.
                if len(clean_title) < len(refs_by_code[code]):
                    refs_by_code[code] = clean_title

        if refs_by_code:
            canonical_refs = {
                "PCOP07-00": "Procedimento de Armazenamento e Transporte de Produtos",
                "PCOP08-00": "Procedimento de Recebimento de Carga HUB (Andreani)",
                "PCOP09-00": "Procedimento de Transferência de Carga Jequiti",
                "PCOP01-00": "Procedimento de Descarregamento de Veículo",
                "PCOP06-00": "Procedimento Baixa Online de Entregas",
                "PCOP03-00": "Procedimento de Tratativas de Avarias",
                "MNGR03-00": "Manual de Equipamentos de Segurança Patrimonial",
                "MNCP02-00": "Manual Sistema de Compras - Solicitante",
            }

            lines.append("Referências internas do Compliado Processos.docx:")
            for code in order:
                title = canonical_refs.get(code, refs_by_code[code])
                lines.append(f"- {code} - {title}")

    if len(lines) == 1:
        return "Informação não consta nos manuais ou tabelas disponíveis."

    lines.append(
        "Posso responder com base nesses materiais; quando a informação não estiver neles, eu aviso explicitamente."
    )
    return "\n".join(lines)


def extract_procedure_code(question: str) -> str | None:
    m = re.search(r"\b(PCOP|MNOP|MNGR|MNCP)\s*[-]?\s*(\d{2})\b", question or "", flags=re.IGNORECASE)
    if not m:
        return None
    return f"{m.group(1).upper()}{m.group(2)}"


def fetch_compilado_chunks_by_keyword(keyword: str, limit: int = 6) -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dc.content
                FROM doc_chunks dc
                JOIN doc_sources ds ON ds.id = dc.source_id
                WHERE lower(ds.source_name) = lower(%s)
                  AND dc.content_norm LIKE %s
                ORDER BY dc.chunk_index
                LIMIT %s
                """,
                ("Compliado Processos.docx", f"%{keyword}%", limit),
            )
            rows = cur.fetchall()
    return [str(r.get("content") or "") for r in rows if str(r.get("content") or "").strip()]


def find_established_date_for_code(code: str) -> str | None:
    code_lower = code.lower()
    chunks = fetch_compilado_chunks_by_keyword(code_lower, limit=12)
    if not chunks:
        return None

    for content in chunks:
        pattern = rf"{re.escape(code)}\s*[-–]?\s*00?.{{0,380}}?Estabelecido em:\s*(\d{{2}}/\d{{2}}/\d{{4}})"
        m = re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)

    for content in chunks:
        m = re.search(r"Estabelecido em:\s*(\d{2}/\d{2}/\d{4})", content, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def try_compilado_specific_answer(question: str) -> dict[str, Any] | None:
    qn = normalize_text(question)

    # Regra direta para pergunta recorrente de item obrigatório faltante no veículo.
    # Mantém precisão mesmo quando há variações de escrita na pergunta.
    if (
        ("triangulo" in qn or "sinalizacao" in qn)
        and "macaco" in qn
        and "estepe" in qn
        and any(k in qn for k in ["item", "falt", "faltando", "falta"])
    ):
        return {
            "answer": "Extintor de incêndio.",
            "used_mode": "compilado_item_obrigatorio_veiculo",
        }

    if (
        "pre alerta" in qn
        and any(k in qn for k in ["onde", "comunic", "inform", "avis"])
        and "falta" in qn
    ):
        chunks = fetch_compilado_chunks_by_keyword("falta do pre alerta", limit=4)
        if chunks:
            return {
                "answer": "A falta de pré-alerta é comunicada à Andreani no grupo 'Transferência x Recebimento' (registro via WhatsApp).",
                "used_mode": "compilado_pre_alerta",
            }

    if "recebimento dinam" in qn and "sistema" in qn:
        chunks = fetch_compilado_chunks_by_keyword("modulo de recebimento dinamico no mytracking", limit=4)
        if chunks:
            return {
                "answer": "No recebimento dinâmico, o sistema usado é o módulo de recebimento dinâmico no MyTracking.",
                "used_mode": "compilado_recebimento_dinamico_sistema",
            }

    if ("sistema de compras" in qn or "mncp02" in qn or "manual sistema de compras" in qn) and (
        "suporte" in qn or "duvida" in qn or "dúvida" in qn
    ):
        chunks = fetch_compilado_chunks_by_keyword("suporte e duvidas", limit=4)
        if chunks:
            if "quem" in qn:
                return {
                    "answer": "Setor de Compras.",
                    "used_mode": "compilado_mncp02_suporte_quem",
                }
            if "possui suporte" in qn or "tem suporte" in qn:
                return {
                    "answer": "Sim.",
                    "used_mode": "compilado_mncp02_suporte_sim",
                }
            return {
                "answer": "Sim. No MNCP02-00, o solicitante deve acionar o Setor de Compras para suporte e orientação.",
                "used_mode": "compilado_mncp02_suporte",
            }

    if "pcop07" in qn and ("objetivo" in qn or "qual e o objetivo" in qn):
        return {
            "answer": "Garantir a integridade da carga, a segurança dos colaboradores e a conformidade operacional.",
            "used_mode": "compilado_pcop07_objetivo",
        }

    if "pcop07" in qn and ("responsavel pelo processo" in qn or "quem e responsavel" in qn):
        return {
            "answer": "Segurança do Trabalho e Logística.",
            "used_mode": "compilado_pcop07_responsavel",
        }

    if "pcop07" in qn and ("setores envolvidos" in qn or "quais setores" in qn):
        return {
            "answer": "Operação, Transporte e GRIS.",
            "used_mode": "compilado_pcop07_setores",
        }

    if "estabelecido" in qn and "data" in qn:
        code = extract_procedure_code(question)
        if code:
            dt = find_established_date_for_code(code)
            if dt:
                return {
                    "answer": f"O procedimento {code} foi estabelecido em {dt}.",
                    "used_mode": "compilado_estabelecido_data",
                }
    return None


def detect_pedidos_scope(question: str, history: list[dict[str, Any]]) -> str | None:
    def is_off_topic_short_question(text_norm: str) -> bool:
        off_topic_markers = [
            "que dia",
            "qual dia",
            "que horas",
            "qual a hora",
            "hora agora",
            "data de hoje",
            "dia de hoje",
            "hoje e",
            "hj",
            "bom dia",
            "boa tarde",
            "boa noite",
            "obrigado",
            "valeu",
        ]
        return any(marker in text_norm for marker in off_topic_markers)

    def is_pedidos_followup_candidate(text_norm: str) -> bool:
        # Follow-up curto de pedidos precisa carregar algum sinal de continuidade útil.
        if detect_filial_in_text(text_norm):
            return True
        if any(
            k in text_norm
            for k in [
                "detalhe",
                "detalhes",
                "cidade",
                "cidades",
                "em rota",
                "na filial",
                "inserido",
                "retorno",
                "retornos",
                "aberto",
                "vencido",
                "vence hoje",
                "hoje",
                "pedidos",
                "pedido",
                "quais",
                "quantos",
                "qtd",
            ]
        ):
            return True
        # Formato clássico de continuação: "e ...?"
        return text_norm.startswith(("e ", "e?"))

    def has_pedidos_signal(text_norm: str) -> bool:
        return any(k in text_norm for k in ["pedido", "pedidos", "aberto", "vencido", "vence hoje", "hoje"])

    def has_route_signal(text_norm: str) -> bool:
        return any(k in text_norm for k in ["subrota", "subrotas", "rota", "motorista", "parceiro"])

    def scope_from_text(text_norm: str) -> str | None:
        if any(k in text_norm for k in ["aberto", "vencido", "vencidos", "atrasado", "atrasados"]):
            return "vencidos"
        if "hoje" in text_norm or "vence hoje" in text_norm:
            return "hoje"
        return None

    q = normalize_text(question)
    if is_off_topic_short_question(q):
        return None

    if has_pedidos_signal(q):
        return scope_from_text(q)

    if not is_short_followup_question(question):
        return None

    if not is_pedidos_followup_candidate(q):
        return None

    # Follow-up curto: usa o sinal mais recente da conversa (user/assistant).
    # Se o sinal mais recente for rota/subrota, não herda escopo de pedidos antigo.
    for msg in reversed(history[-10:]):
        txt = normalize_text(str(msg.get("content", "")))
        if not txt:
            continue
        if has_route_signal(txt) and not has_pedidos_signal(txt):
            return None
        if has_pedidos_signal(txt):
            return scope_from_text(txt)
    return None


def is_live_pedidos_query(question: str) -> bool:
    q = normalize_text(question)
    has_pedidos_topic = any(k in q for k in ["pedido", "pedidos", "em aberto", "vencido", "vence hoje", "hoje"])
    if not has_pedidos_topic:
        return False

    # Termos de consulta operacional em tempo real (API de monitoramento)
    has_live_intent = any(
        k in q
        for k in [
            "quant",
            "qtd",
            "total",
            "detalhe",
            "detalhes",
            "listar",
            "lista",
            "cidade",
            "cidades",
            "na filial",
            "em rota",
            "inserido",
            "retorno",
        ]
    )
    if not has_live_intent:
        return False

    # Perguntas conceituais/manuais não devem cair na API de pedidos
    has_manual_style = any(
        k in q
        for k in [
            "atividade",
            "objetivo",
            "finalidade",
            "classificacao",
            "classificacao",
            "range",
            "o que",
            "quem assume",
            "como",
            "quando",
            "procedimento",
            "manual",
        ]
    )
    return not has_manual_style


def detect_pedidos_filial(question: str, history: list[dict[str, Any]]) -> str | None:
    # 1) Prioridade absoluta: filial explícita na pergunta atual
    current_filial = detect_filial_in_text(question)
    if current_filial:
        return current_filial

    # 2) Só usa histórico quando for follow-up curto e sem filial explícita
    if is_short_followup_question(question):
        # Pega a filial mais recente mencionada pelo usuário no histórico
        for msg in reversed(history[-10:]):
            if msg.get("role") != "user":
                continue
            filial = detect_filial_in_text(str(msg.get("content", "")))
            if filial:
                return filial

        # Fallback: varre histórico agregado se não houver menção clara recente
        merged = question + " " + " ".join(str(m.get("content", "")) for m in history[-6:])
        fallback_filial = detect_filial_in_text(merged)
        if fallback_filial:
            return fallback_filial
    return None


def detect_pedidos_filiais(question: str, history: list[dict[str, Any]]) -> list[str]:
    explicit = detect_filiais_in_text(question)
    if explicit:
        return explicit
    inherited = detect_pedidos_filial(question, history)
    return [inherited] if inherited else []


def detect_metric_in_text(text: str) -> str | None:
    q = normalize_text(text)
    if "em rota" in q:
        return "em_rota"
    if "inserido" in q or "inseridos" in q:
        return "inserido"
    if "na filial" in q:
        return "na_filial"
    if "retorno" in q:
        return "retornos"
    return None


def detect_pedidos_metric(question: str, scope: str, history: list[dict[str, Any]]) -> str:
    direct = detect_metric_in_text(question)
    if direct:
        return direct

    # Follow-up de detalhes/herança de contexto de métrica
    if is_short_followup_question(question) or wants_pedidos_details(question):
        for msg in reversed(history[-10:]):
            if msg.get("role") != "user":
                continue
            hist_metric = detect_metric_in_text(str(msg.get("content", "")))
            if hist_metric:
                return hist_metric

    if scope == "vencidos":
        return "total"
    if scope == "hoje":
        return "total"
    return "total"


def wants_pedidos_details(question: str) -> bool:
    q = normalize_text(question)
    return any(k in q for k in ["detalhe", "detalhes", "listar", "lista", "quais pedidos", "mostre pedidos"])


def wants_city_breakdown(question: str) -> bool:
    q = normalize_text(question)
    if "cidade" not in q and "cidades" not in q:
        return False
    return any(k in q for k in ["quais", "qual", "por cidade", "desses pedidos", "desses pedidos", "listar cidades"])


def extract_city_filter(question: str) -> str | None:
    q = normalize_text(question)
    if "cidade" not in q:
        return None
    # Filtro de cidade só quando estiver no singular (ex.: "cidade de russas")
    m = re.search(r"(?:na|no|da|do|de)?\s*cidade\s+(?:de|do|da|em)?\s*([a-z0-9 ]{3,50})", q)
    if not m:
        return None
    city = m.group(1).strip()
    city = re.split(r"\b(hoje|aberto|abertos|vencido|vencidos|pedido|pedidos|detalhe|detalhes)\b", city)[0].strip()
    if len(city) < 3:
        return None
    invalid_tokens = {
        "desses",
        "desse",
        "dessas",
        "dessa",
        "deste",
        "desta",
        "esses",
        "essas",
        "pedidos",
        "pedido",
        "todos",
        "todas",
        "qual",
        "quais",
    }
    if city in invalid_tokens:
        return None

    # Remove sufixo de filial, ex.: "russas em fortaleza" -> "russas"
    for aliases in FILIAL_ALIASES.values():
        for alias in aliases:
            alias_norm = normalize_text(alias)
            suffix = f" em {alias_norm}"
            if city.endswith(suffix):
                city = city[: -len(suffix)].strip()
                break

    if len(city) < 3 or city in invalid_tokens:
        return None
    return city


def normalize_city(value: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip().upper()


async def fetch_pedidos_api(path: str, params: dict[str, Any] | None = None) -> Any:
    base = SETTINGS.pedidos_api_base_url.rstrip("/")
    url = f"{base}{path}"
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"API monitoramento retornou {resp.status_code} em {path}")
        return resp.json()


def build_detail_params(filial: str | None, metric: str) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": 10000}
    if filial:
        params["cd"] = filial
    if metric != "total":
        params["tipo"] = metric
    return params


def pick_resumo_row(resumo: list[dict[str, Any]], filial: str | None) -> dict[str, Any] | None:
    if not filial:
        return None
    target = normalize_filial_name(filial)
    for row in resumo:
        cd = normalize_filial_name(str(row.get("cd") or row.get("filial") or ""))
        if cd == target:
            return row
    return None


def format_pedidos_resumo_answer(scope: str, metric: str, filial: str | None, resumo: list[dict[str, Any]]) -> str:
    metric_labels = {
        "total": "pedidos em aberto" if scope == "vencidos" else "pedidos que vencem hoje",
        "em_rota": "pedidos em rota",
        "inserido": "pedidos inseridos",
        "na_filial": "pedidos na filial",
        "retornos": "retornos",
    }
    label = metric_labels.get(metric, "pedidos")

    if filial:
        row = pick_resumo_row(resumo, filial)
        if not row:
            cds = sorted({str(r.get("cd", "")).strip() for r in resumo if str(r.get("cd", "")).strip()})
            listed = format_answer_values(cds) if cds else "sem filiais disponíveis"
            return f"Não encontrei filial '{filial}'. Filiais disponíveis: {listed}."
        value = to_int(row.get(metric))
        cd = str(row.get("cd") or filial).strip()
        when = "hoje" if scope == "hoje" else "em aberto"
        if metric == "total" and scope == "vencidos":
            return f"{cd}: {value} pedidos em aberto."
        if metric == "total" and scope == "hoje":
            return f"{cd}: {value} pedidos para vencer hoje."
        return f"{cd}: {value} {label} ({when})."

    # Sem filial: devolve resumo por filial
    parts: list[str] = []
    for row in resumo:
        cd = str(row.get("cd") or "").strip()
        if not cd:
            continue
        value = to_int(row.get(metric))
        parts.append(f"{cd}: {value}")
    if not parts:
        return "Informação não consta nos manuais ou tabelas disponíveis."

    prefix = "Resumo de pedidos em aberto por filial" if scope == "vencidos" else "Resumo de pedidos de hoje por filial"
    return f"{prefix} ({label}): " + "; ".join(parts) + "."


def format_pedidos_resumo_answer_for_filiais(
    scope: str, metric: str, filiais: list[str], resumo: list[dict[str, Any]]
) -> str:
    metric_labels = {
        "total": "pedidos em aberto" if scope == "vencidos" else "pedidos para vencer hoje",
        "em_rota": "pedidos em rota",
        "inserido": "pedidos inseridos",
        "na_filial": "pedidos na filial",
        "retornos": "retornos",
    }
    label = metric_labels.get(metric, "pedidos")

    lines: list[str] = []
    missing: list[str] = []
    for filial in filiais:
        row = pick_resumo_row(resumo, filial)
        if not row:
            missing.append(filial)
            continue
        value = to_int(row.get(metric))
        cd = str(row.get("cd") or filial).strip()
        if metric == "total" and scope == "vencidos":
            lines.append(f"{cd}: {value} pedidos em aberto")
        elif metric == "total" and scope == "hoje":
            lines.append(f"{cd}: {value} pedidos para vencer hoje")
        else:
            when = "em aberto" if scope == "vencidos" else "hoje"
            lines.append(f"{cd}: {value} {label} ({when})")

    if not lines:
        if missing:
            return "Não encontrei as filiais informadas."
        return "Informação não consta nos manuais ou tabelas disponíveis."

    answer = "; ".join(lines) + "."
    if missing:
        answer += " Não encontrei: " + ", ".join(missing) + "."
    return answer


def format_pedidos_detail_answer(scope: str, filial: str | None, detail_rows: list[dict[str, Any]]) -> str:
    if not detail_rows:
        return "Não encontrei pedidos para esse filtro."

    sample = detail_rows[:8]
    ids = [str(r.get("pedido") or r.get("numero_nfe") or r.get("id") or "").strip() for r in sample]
    ids = [x for x in ids if x]
    if not ids:
        return "Detalhes encontrados, mas sem identificador de pedido disponível."

    title = "pedidos em aberto" if scope == "vencidos" else "pedidos que vencem hoje"
    if filial:
        return (
            f"Exemplos de {title} em {filial}: {', '.join(ids[:8])}. "
            f"Total retornado no detalhe: {len(detail_rows)}."
        )
    return f"Exemplos de {title}: {', '.join(ids[:8])}. Total retornado no detalhe: {len(detail_rows)}."


def format_pedidos_city_breakdown_answer(scope: str, detail_rows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in detail_rows:
        city = normalize_city(str(row.get("cidades") or ""))
        if not city:
            city = "SEM_CIDADE"
        counts[city] = counts.get(city, 0) + 1
    if not counts:
        return "Não encontrei cidades para esse filtro."

    ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    parts = [f"{city}: {total}" for city, total in ordered]
    label = "em aberto" if scope == "vencidos" else "que vencem hoje"
    return f"Cidades dos pedidos {label}: " + "; ".join(parts) + "."


def filter_rows_by_city(detail_rows: list[dict[str, Any]], city_filter: str) -> list[dict[str, Any]]:
    target = normalize_city(city_filter)
    filtered: list[dict[str, Any]] = []
    for row in detail_rows:
        city = normalize_city(str(row.get("cidades") or ""))
        if target and target in city:
            filtered.append(row)
    return filtered


async def try_pedidos_monitoring_answer(question: str, history: list[dict[str, Any]]) -> dict[str, Any] | None:
    scope = detect_pedidos_scope(question, history)
    if not scope:
        return None

    if scope == "vencidos":
        resumo_path = "/api/pedidos/vencidos/resumo"
        detalhe_path = "/api/pedidos/vencidos/detalhe"
    else:
        resumo_path = "/api/pedidos/hoje/resumo"
        detalhe_path = "/api/pedidos/hoje/detalhe"

    filiais = detect_pedidos_filiais(question, history)
    filial = filiais[0] if filiais else None
    metric = detect_pedidos_metric(question, scope, history)
    city_filter = extract_city_filter(question)

    try:
        if wants_city_breakdown(question):
            detail_params = build_detail_params(filial, metric)
            detalhe = await fetch_pedidos_api(detalhe_path, params=detail_params or None)
            if isinstance(detalhe, list):
                rows = detalhe
                if city_filter:
                    rows = filter_rows_by_city(rows, city_filter)
                answer = format_pedidos_city_breakdown_answer(scope, rows)
                return {"answer": answer, "used_mode": "pedidos_api_city_breakdown"}

        if city_filter and not wants_pedidos_details(question):
            detail_params = build_detail_params(filial, metric)
            detalhe = await fetch_pedidos_api(detalhe_path, params=detail_params or None)
            if isinstance(detalhe, list):
                rows = filter_rows_by_city(detalhe, city_filter)
                total = len(rows)
                label = "pedidos em aberto" if scope == "vencidos" else "pedidos que vencem hoje"
                if metric == "em_rota":
                    label = "pedidos em rota"
                elif metric == "inserido":
                    label = "pedidos inseridos"
                elif metric == "na_filial":
                    label = "pedidos na filial"
                elif metric == "retornos":
                    label = "retornos"
                if filial:
                    return {
                        "answer": f"{filial} / {city_filter}: {total} {label}.",
                        "used_mode": "pedidos_api_city_count",
                    }
                return {
                    "answer": f"{city_filter}: {total} {label}.",
                    "used_mode": "pedidos_api_city_count",
                }

        if wants_pedidos_details(question):
            if len(filiais) > 1:
                return {
                    "answer": "Para detalhes, informe uma filial por vez.",
                    "used_mode": "pedidos_api_detail_multi_filial_disambiguation",
                }
            detail_params = build_detail_params(filial, metric)
            detalhe = await fetch_pedidos_api(detalhe_path, params=detail_params or None)
            if isinstance(detalhe, list):
                rows = detalhe
                if city_filter:
                    rows = filter_rows_by_city(rows, city_filter)
                    if not rows:
                        return {
                            "answer": f"Não encontrei pedidos para a cidade '{city_filter}' nesse filtro.",
                            "used_mode": "pedidos_api_detail_city_empty",
                        }
                if wants_city_breakdown(question):
                    answer = format_pedidos_city_breakdown_answer(scope, rows)
                    return {"answer": answer, "used_mode": "pedidos_api_detail_city_breakdown"}

                answer = format_pedidos_detail_answer(scope, filial, rows)
                return {"answer": answer, "used_mode": "pedidos_api_detail"}

        resumo = await fetch_pedidos_api(resumo_path)
        if isinstance(resumo, list):
            if len(filiais) > 1:
                answer = format_pedidos_resumo_answer_for_filiais(scope, metric, filiais, resumo)
                return {"answer": answer, "used_mode": "pedidos_api_resumo_multi_filial"}
            answer = format_pedidos_resumo_answer(scope, metric, filial, resumo)
            return {"answer": answer, "used_mode": "pedidos_api_resumo"}
    except Exception as exc:
        return {
            "answer": f"Falha ao consultar API de pedidos: {str(exc)}",
            "used_mode": "pedidos_api_error",
        }

    return None


def lookup_override(question: str) -> str | None:
    question_norm = normalize_text(question)
    question_tokens = tokenize_for_matching(question)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT answer
                FROM qa_overrides
                WHERE question_norm = %s
                LIMIT 1
                """,
                (question_norm,),
            )
            row = cur.fetchone()
            if row:
                return row["answer"]

            cur.execute(
                """
                SELECT answer
                FROM qa_overrides
                WHERE STRPOS(question_norm, %s) > 0
                   OR STRPOS(%s, question_norm) > 0
                ORDER BY LENGTH(question_norm) DESC
                LIMIT 1
                """,
                (question_norm, question_norm),
            )
            row = cur.fetchone()
            if row:
                return row["answer"]

            if question_tokens:
                cur.execute(
                    """
                    WITH q AS (
                      SELECT %s::text AS qnorm, %s::text[] AS qtoks
                    ),
                    scored AS (
                      SELECT
                        o.answer,
                        o.question_norm,
                        similarity(o.question_norm, q.qnorm) AS sim,
                        (
                          SELECT COUNT(*)
                          FROM unnest(q.qtoks) t
                          WHERE POSITION(t IN o.question_norm) > 0
                        ) AS overlap
                      FROM qa_overrides o
                      CROSS JOIN q
                      WHERE similarity(o.question_norm, q.qnorm) > 0.62
                    )
                    SELECT answer
                    FROM scored
                    WHERE overlap >= 2 OR sim >= 0.82
                    ORDER BY overlap DESC, sim DESC, LENGTH(question_norm) DESC
                    LIMIT 1
                    """,
                    (question_norm, question_tokens),
                )
                row = cur.fetchone()
                if row:
                    return row["answer"]

            cur.execute(
                """
                SELECT answer
                FROM qa_overrides
                WHERE similarity(question_norm, %s) > 0.86
                ORDER BY similarity(question_norm, %s) DESC
                LIMIT 1
                """,
                (question_norm, question_norm),
            )
            row = cur.fetchone()
            return row["answer"] if row else None


def lookup_cached_answer(question: str) -> tuple[str, str] | None:
    question_norm = normalize_text(question)
    if not question_norm:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT answer, source_mode FROM qa_cache WHERE question_norm = %s LIMIT 1", (question_norm,))
            row = cur.fetchone()
            return (row["answer"], row.get("source_mode") or "") if row else None


def save_cached_answer(question: str, answer: str, source_mode: str) -> None:
    question_norm = normalize_text(question)
    if not question_norm or not answer.strip():
        return
    if source_mode == "unknown":
        return
    # Evita poluir cache com follow-up contextual curto (ex.: "e em fortaleza?", "e quais as cidades?").
    if is_short_followup_question(question):
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qa_cache (question_norm, question_raw, answer, source_mode, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (question_norm) DO UPDATE
                SET question_raw = EXCLUDED.question_raw,
                    answer = EXCLUDED.answer,
                    source_mode = EXCLUDED.source_mode,
                    updated_at = NOW()
                """,
                (question_norm, question, answer.strip(), source_mode),
            )
        conn.commit()


def search_documents(question: str, limit: int = 5) -> list[dict[str, Any]]:
    normalized = normalize_text(question)

    # Busca principal (FTS + trigram)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH q AS (
                  SELECT websearch_to_tsquery('simple', unaccent(%s)) AS query,
                         unaccent(%s) AS norm_q
                )
                SELECT
                  ds.source_name,
                  dc.section_title,
                  dc.page_start,
                  dc.page_end,
                  dc.content,
                  ts_rank(dc.search_tsv, q.query) + GREATEST(similarity(dc.content_norm, q.norm_q), 0) AS score
                FROM doc_chunks dc
                JOIN doc_sources ds ON ds.id = dc.source_id
                CROSS JOIN q
                WHERE dc.search_tsv @@ q.query
                   OR dc.content_norm %% q.norm_q
                ORDER BY score DESC
                LIMIT %s
                """,
                (question, normalized, limit),
            )
            rows = cur.fetchall()

    if rows:
        return rows

    # Fallback robusto: busca por termos individuais com score acumulado
    terms = extract_search_terms(question)
    if not terms:
        return []

    score_expr = " + ".join(["CASE WHEN dc.content_norm LIKE %s THEN 1 ELSE 0 END" for _ in terms])
    where_expr = " OR ".join(["dc.content_norm LIKE %s" for _ in terms])
    score_params = [f"%{t}%" for t in terms]
    where_params = [f"%{t}%" for t in terms]

    fallback_sql = f"""
        SELECT
          ds.source_name,
          dc.section_title,
          dc.page_start,
          dc.page_end,
          dc.content,
          ({score_expr})::float AS score
        FROM doc_chunks dc
        JOIN doc_sources ds ON ds.id = dc.source_id
        WHERE {where_expr}
        ORDER BY score DESC, dc.id ASC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(fallback_sql, score_params + where_params + [max(limit * 2, 12)])
            fallback_rows = cur.fetchall()

    return fallback_rows or []


def build_context_snippets(hits: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for i, hit in enumerate(hits, start=1):
        source = hit.get("source_name") or "fonte_desconhecida"
        section = hit.get("section_title") or "sem seção"
        page_start = hit.get("page_start")
        page_end = hit.get("page_end")
        page = ""
        if page_start and page_end:
            page = f"páginas {page_start}-{page_end}"
        elif page_start:
            page = f"página {page_start}"
        citation = f"[{i}] {source} | {section}"
        if page:
            citation += f" | {page}"
        blocks.append(f"{citation}\n{hit.get('content', '')}")
    return "\n\n".join(blocks)


def split_sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []
    raw_parts = re.split(r"(?<=[\.\?\!;:])\s+", clean)
    return [p.strip() for p in raw_parts if len(p.strip()) >= 12]


def is_noisy_ocr_sentence(sentence: str) -> bool:
    if not sentence:
        return True
    up = sentence.upper()
    if "## PAGINA" in up:
        return True
    if re.search(r"\b[A-Z]\s+[A-Z]\s+[A-Z]\s+[A-Z]\b", up):
        return True
    if re.search(r"\b(MNOP|PCOP|MNCP|MNGR)\s*\d{2}\s*[-]?\s*0{0,2}\b", up) and len(sentence) < 80:
        return True
    letters = re.findall(r"[A-Za-zÀ-ÿ]", sentence)
    digits = re.findall(r"\d", sentence)
    if letters and len(digits) > len(letters):
        return True
    if re.search(r"\?filecite\?turn\d+file\d+\?", sentence, flags=re.IGNORECASE):
        return True
    return False


def is_critical_orders_identification_question(question: str) -> bool:
    q = normalize_text(question)
    if "pedido" not in q or "critic" not in q:
        return False
    return any(
        k in q
        for k in [
            "como",
            "identific",
            "criter",
            "range",
            "vencimento",
            "classific",
            "o que e",
            "defin",
            "significa",
        ]
    )


def is_critical_definition_question(question: str) -> bool:
    q = normalize_text(question)
    if "pedido" not in q or "critic" not in q:
        return False
    return any(k in q for k in ["o que e", "defin", "significa", "conceito", "afinal"])


def fetch_mnop02_critical_contents() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dc.content
                FROM doc_chunks dc
                JOIN doc_sources ds ON ds.id = dc.source_id
                WHERE lower(ds.source_name) LIKE '%mnop02%'
                  AND (
                    dc.content_norm LIKE '%pedido%critic%'
                    OR dc.content_norm LIKE '%criticidade%'
                    OR dc.content_norm LIKE '%range de vencimento%'
                  )
                ORDER BY dc.chunk_index
                LIMIT 16
                """
            )
            rows = cur.fetchall()
    return [str(r.get("content") or "").strip() for r in rows if str(r.get("content") or "").strip()]


def build_critical_orders_answer(question: str, contents: list[str]) -> str | None:
    if not contents:
        return None

    merged_norm = normalize_text(" ".join(contents))
    has_venc_dia = "vencimento no dia da analise" in merged_norm or "vencimento no dia" in merged_norm
    has_vencido = "vencimento ja ocorrido" in merged_norm or "vencidos ha" in merged_norm or "pedido vencido" in merged_norm
    has_status_risco = "status sistemico" in merged_norm and "risco operacional" in merged_norm
    has_fora_rota = "fora de rota" in merged_norm
    has_falta_volume = "falta de volume" in merged_norm
    has_sem_mov_5d = "sem movimentacao sistemica por mais de 5 dias" in merged_norm

    if not (has_venc_dia or has_vencido or has_status_risco):
        return None

    if is_critical_definition_question(question):
        return (
            "Pedido crítico é todo pedido com vencimento no dia da análise, "
            "vencimento já ocorrido ou status sistêmico que indique risco operacional, "
            "exigindo acompanhamento prioritário e análise imediata."
        )

    criteria: list[str] = []
    if has_venc_dia:
        criteria.append("vencimento no dia da análise")
    if has_vencido:
        criteria.append("vencimento já ocorrido")
    if has_status_risco:
        criteria.append("status sistêmico com risco operacional")
    examples: list[str] = []
    if has_fora_rota:
        examples.append("fora de rota")
    if has_falta_volume:
        examples.append("falta de volume")
    if has_sem_mov_5d:
        examples.append("sem movimentação sistêmica por mais de 5 dias (sem ocorrência finalizadora)")

    answer = "Para identificar pedidos críticos, considere: " + "; ".join(criteria) + "."
    if examples:
        answer += " Exemplos de status críticos: " + "; ".join(examples) + "."
    return answer


def try_critical_orders_identification_answer(question: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not is_critical_orders_identification_question(question):
        return None

    contents: list[str] = []
    for hit in hits[:8]:
        text = str(hit.get("content") or "").strip()
        source = normalize_text(str(hit.get("source_name") or ""))
        if text and ("mnop02" in source or "pedido" in normalize_text(text)):
            contents.append(text)

    contents.extend(fetch_mnop02_critical_contents())
    answer = build_critical_orders_answer(question, contents)
    if not answer:
        return None
    return {"answer": answer, "used_mode": "critical_orders_extractive"}


def try_extractive_document_answer(question: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    terms = extract_search_terms(question)
    if not hits or not terms:
        return None

    question_norm = normalize_text(question)
    best_score = -1
    best_sentence = ""
    best_source = ""

    for hit in hits[:4]:
        content = hit.get("content") or ""
        source_name = hit.get("source_name") or "fonte_desconhecida"
        sentences = split_sentences(content)
        for idx, sentence in enumerate(sentences):
            if is_noisy_ocr_sentence(sentence):
                continue
            sentence_norm = normalize_text(sentence)
            if not sentence_norm:
                continue
            overlap = sum(1 for t in terms if t in sentence_norm)
            if overlap == 0:
                continue

            bonus = 0
            if any(k in question_norm for k in ["papel", "funcao", "objetivo"]) and any(
                k in sentence_norm for k in ["papel", "funcao", "objetivo", "finalidade", "responsabilidade"]
            ):
                bonus += 2
            if any(k in question_norm for k in ["como", "procedimento", "rotina"]) and any(
                k in sentence_norm for k in ["procedimento", "rotina", "execucao", "passo"]
            ):
                bonus += 1

            score = overlap + bonus
            if score > best_score:
                best_score = score
                candidate = sentence
                if sentence.rstrip().endswith(":") and idx + 1 < len(sentences):
                    candidate = f"{sentence} {sentences[idx + 1]}".strip()
                best_sentence = candidate
                best_source = source_name

    if best_score < 2 or not best_sentence:
        return None

    answer = best_sentence
    if len(answer) > 420:
        answer = answer[:420].rsplit(" ", 1)[0] + "..."
    return {"answer": answer, "source": best_source, "score": best_score}


def try_steps_document_answer(question: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    qn = normalize_text(question)
    if "passos" not in qn:
        return None

    terms = extract_search_terms(question)
    best: dict[str, Any] | None = None

    for hit in hits[:6]:
        content = re.sub(r"\s+", " ", hit.get("content") or "").strip()
        if not content:
            continue
        steps_pattern = (
            r"([A-Za-zÀ-ÿ0-9\s]{4,110})\s*Passos:\s*"
            r"(.{20,260}?)"
            r"(?=\s+\d+\.\s+[A-Za-zÀ-ÿ]|"
            r"\s+[A-Za-zÀ-ÿ0-9\s]{4,110}\s+Passos:|$)"
        )
        for m in re.finditer(steps_pattern, content, flags=re.IGNORECASE):
            topic = m.group(1).strip(" -:;,.")
            raw_steps = m.group(2).strip()
            raw_steps = re.split(
                r"\s+(Informações|Campos|Regras|Cadastro|Acompanhamento|Status|Finalidade|Objetivo|Abrangência)\b",
                raw_steps,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            items = [x.strip(" .") for x in raw_steps.split(";") if x.strip()]
            if not items:
                continue

            if len(items) > 8:
                items = items[:8]
            answer = f"{topic} - Passos: " + "; ".join(items) + "."

            joined_norm = normalize_text(f"{topic} {' '.join(items)}")
            overlap = sum(1 for t in terms if t in joined_norm)
            score = overlap * 3 + len(items)
            if "acompanhamento" in qn and "acompanhamento" in joined_norm:
                score += 4
            if "requisic" in qn and "requisic" in joined_norm:
                score += 4

            candidate = {
                "answer": answer,
                "source": hit.get("source_name") or "fonte_desconhecida",
                "score": score,
            }
            if not best or score > best["score"]:
                best = candidate

    return best if best and best["score"] >= 4 else None


def extract_manual_hint(text: str) -> str | None:
    norm = normalize_text(text)
    hints = {
        "mncp02": ["mncp02", "sistema de compras", "compras solicitante"],
        "mngr03": ["mngr03", "seguranca patrimonial"],
        "mnop02": ["mnop02", "pedidos criticos"],
        "mnop03": ["mnop03", "super rotina", "gestor de operacao"],
        "pcop01": ["pcop01", "descarregamento de veiculo"],
        "pcop03": ["pcop03", "tratativas de avarias"],
        "pcop06": ["pcop06", "baixa online de entregas"],
        "pcop07": ["pcop07", "armazenamento e transporte de produtos"],
        "pcop08": ["pcop08", "recebimento de carga hub"],
        "pcop09": ["pcop09", "transferencia de carga jequiti"],
    }
    for key, variants in hints.items():
        if any(v in norm for v in variants):
            return key
    return None


def is_manual_purpose_question(question: str) -> bool:
    qn = normalize_text(question)
    return "finalidade" in qn and "manual" in qn


def _is_valid_purpose_sentence(text: str) -> bool:
    sentence = re.sub(r"\s+", " ", text or "").strip(" .;:-")
    if len(sentence) < 20:
        return False
    low = sentence.lower()
    if low.startswith("finalidade do manual"):
        return False
    if sentence.count(".") > 8:
        return False
    if re.search(r"\.{3,}", sentence):
        return False
    strong_terms = ["orientar", "estabelecer", "definir", "descrever", "padronizar", "garantir"]
    return any(t in low for t in strong_terms)


def extract_purpose_sentence_from_content(content: str) -> str | None:
    text = re.sub(r"\s+", " ", content or "").strip()
    if not text:
        return None

    patterns = [
        r"Finalidade do Manual\s+(.{20,260}?)(?=\s+\d+\.\s+|Como Utilizar Este Manual|Diretrizes:|$)",
        r"\bFinalidade\s+(.{20,260}?)(?=\s+(O que|Equipamentos|2\.\s+|3\.\s+|4\.\s+|Como Utilizar|Diretrizes:|$))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        sentence = re.sub(r"\s+", " ", m.group(1)).strip(" .;:-")
        sentence = re.sub(r"^[A-Z]{4}\d{2}-\d{2}\s*[–-]\s*", "", sentence)
        if not sentence.endswith("."):
            sentence += "."
        if _is_valid_purpose_sentence(sentence):
            return sentence
    return None


def try_manual_purpose_answer(question: str, history: list[dict[str, Any]], hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not is_manual_purpose_question(question):
        return None

    history_text = " ".join(str(m.get("content", "")) for m in history[-8:] if m.get("role") == "user")
    hint = extract_manual_hint(f"{question} {history_text}")
    candidates: list[tuple[int, str]] = []

    for hit in hits[:8]:
        content = re.sub(r"\s+", " ", hit.get("content") or "").strip()
        if not content:
            continue
        source = str(hit.get("source_name") or "")
        content_norm = normalize_text(content)
        score = 0
        if hint and hint in content_norm:
            score += 8
        if hint and hint in normalize_text(source):
            score += 8
        if "finalidade do manual" in content_norm:
            score += 4
        sentence = extract_purpose_sentence_from_content(content)
        if sentence:
            candidates.append((score + 10, sentence))

    # fallback dirigido ao manual quando não houver candidatos em hits gerais
    if not candidates and hint:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dc.content
                    FROM doc_chunks dc
                    JOIN doc_sources ds ON ds.id = dc.source_id
                    WHERE dc.content_norm LIKE %s
                    ORDER BY dc.chunk_index
                    LIMIT 12
                    """,
                    (f"%{hint}%",),
                )
                for row in cur.fetchall():
                    sentence = extract_purpose_sentence_from_content(str(row.get("content") or ""))
                    if sentence:
                        candidates.append((12, sentence))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_sentence = candidates[0][1]
    return {"answer": best_sentence, "source": "manual_purpose", "score": candidates[0][0]}


def extract_gemini_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                chunks.append(text)
    return "".join(chunks)


def save_user_message(conversation_id: int, content: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (%s, 'user', %s)",
                (conversation_id, content),
            )
        conn.commit()


def save_assistant_message(conversation_id: int, content: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (%s, 'assistant', %s)",
                (conversation_id, content),
            )
        conn.commit()


def safe_save_user_message(conversation_id: int, content: str) -> None:
    try:
        save_user_message(conversation_id, content)
    except Exception as exc:
        print(f"[warn] failed to save user message: {exc}")


def safe_save_assistant_message(conversation_id: int, content: str) -> None:
    try:
        save_assistant_message(conversation_id, content)
    except Exception as exc:
        print(f"[warn] failed to save assistant message: {exc}")


async def ask_impl(data: dict[str, Any]) -> StreamingResponse:
    question = (data.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Pergunta não fornecida")

    history = data.get("history") or []
    conversation_id = data.get("conversa_id")

    if conversation_id:
        safe_save_user_message(conversation_id, question)

    async def event_generator():
        done_sent = False
        full_response = ""

        async def send_text_chunk(text: str) -> None:
            nonlocal full_response
            full_response += text
            yield_str = f"data: {json.dumps({'text': text})}\n\n"
            return yield_str

        try:
            if not is_live_pedidos_query(question):
                override_answer = lookup_override(question)
                if override_answer:
                    yield f"data: {json.dumps({'text': override_answer})}\n\n"
                    full_response = override_answer
                    save_cached_answer(question, override_answer, "override")
                    return

            pedidos_answer = await try_pedidos_monitoring_answer(question, history)
            if pedidos_answer:
                answer = pedidos_answer.get("answer", "")
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                return

            if is_material_inventory_question(question):
                inventory_answer = build_material_inventory_answer()
                yield f"data: {json.dumps({'text': inventory_answer})}\n\n"
                full_response = inventory_answer
                save_cached_answer(question, inventory_answer, "materials_inventory")
                return

            compilado_specific = try_compilado_specific_answer(question)
            if compilado_specific:
                answer = compilado_specific.get("answer", "")
                used_mode = str(compilado_specific.get("used_mode") or "compilado_specific")
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                if answer:
                    save_cached_answer(question, answer, used_mode)
                return

            structured = try_structured_answer(question, history)
            if structured:
                answer = structured.get("answer", "")
                used_mode = structured.get("used_mode", "structured")
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                if answer != "Informação não consta nos manuais ou tabelas disponíveis." and str(used_mode) not in {
                    "structured_disambiguation",
                    "structured_route_suggestion",
                    "structured_miss",
                    "structured_missing_field",
                }:
                    save_cached_answer(question, answer, str(used_mode))
                return

            if not is_short_followup_question(question) and not is_critical_orders_identification_question(question):
                cached = lookup_cached_answer(question)
                if cached:
                    cached_answer, cached_mode = cached
                    if cached_mode != "unknown":
                        yield f"data: {json.dumps({'text': cached_answer})}\n\n"
                        full_response = cached_answer
                        return

            hits = search_documents(question, limit=5)
            if not hits:
                unknown = "Informação não consta nos manuais ou tabelas disponíveis."
                yield f"data: {json.dumps({'text': unknown})}\n\n"
                full_response = unknown
                return

            critical_answer = try_critical_orders_identification_answer(question, hits)
            if critical_answer:
                answer = critical_answer["answer"]
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                save_cached_answer(question, answer, "critical_orders_extractive")
                return

            purpose_answer = try_manual_purpose_answer(question, history, hits)
            if purpose_answer:
                answer = purpose_answer["answer"]
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                save_cached_answer(question, answer, "manual_purpose")
                return

            steps_answer = try_steps_document_answer(question, hits)
            if steps_answer:
                answer = steps_answer["answer"]
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                save_cached_answer(question, answer, "steps_extractive")
                return

            extractive = try_extractive_document_answer(question, hits)
            if extractive:
                answer = extractive["answer"]
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                save_cached_answer(question, answer, "extractive")
                return

            api_keys = get_ordered_gemini_api_keys()
            if not api_keys:
                msg = "GEMINI_API_KEY(S) não configurada(s) no .env."
                yield f"data: {json.dumps({'text': msg})}\n\n"
                full_response = msg
                return

            context = build_context_snippets(hits)

            history_text = ""
            if history:
                lines = []
                for msg in history[-6:]:
                    role = "Gestor" if msg.get("role") == "user" else "IA"
                    lines.append(f"{role}: {msg.get('content', '')}")
                history_text = "\n".join(lines)

            prompt = (
                "Você é o Especialista em Logística e Processos do Grupo JB.\n"
                "Sua prioridade é precisão factual e resposta curta.\n"
                "Regras obrigatórias:\n"
                "1) Responda APENAS o que foi perguntado.\n"
                "2) Não invente dados.\n"
                "3) Se o contexto não contiver a resposta exata, responda literalmente: Informação não consta nos manuais ou tabelas disponíveis.\n"
                "4) Se a pergunta for de rota (parceiro/motorista/dias/região), devolva somente o campo pedido.\n"
                "5) Não inclua introduções.\n\n"
                f"HISTÓRICO RECENTE:\n{history_text}\n\n"
                f"CONTEXTO RECUPERADO:\n{context}\n\n"
                f"PERGUNTA:\n{question}\n"
            )

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0},
            }

            candidate_models = [SETTINGS.gemini_model, "gemini-2.0-flash"]
            last_error = ""
            successful_key: str | None = None

            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                for api_key in api_keys:
                    streamed_with_key = False
                    for model_name in candidate_models:
                        url = (
                            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:"
                            f"streamGenerateContent?key={api_key}&alt=sse"
                        )

                        async with client.stream("POST", url, json=payload) as response:
                            if response.status_code != 200:
                                error_text = await response.aread()
                                msg = error_text.decode("utf-8", errors="ignore")
                                last_error = f"Erro Gemini ({response.status_code}) no modelo {model_name}: {msg[:180]}"
                                if response.status_code in (429, 500, 503):
                                    continue
                                if response.status_code in (400, 401, 403):
                                    # chave inválida/bloqueada: tenta próxima chave
                                    break
                                continue

                            streamed_any = False
                            async for raw_line in response.aiter_lines():
                                if not raw_line or not raw_line.startswith("data: "):
                                    continue
                                data_line = raw_line[6:].strip()
                                if not data_line:
                                    continue
                                try:
                                    parsed = json.loads(data_line)
                                except json.JSONDecodeError:
                                    continue

                                text = extract_gemini_text(parsed)
                                if text:
                                    streamed_any = True
                                    full_response += text
                                    yield f"data: {json.dumps({'text': text})}\n\n"

                            if streamed_any:
                                streamed_with_key = True
                                successful_key = api_key
                                break

                    if streamed_with_key:
                        break

            if successful_key:
                advance_gemini_key_pointer(successful_key)

            if full_response.strip() and not full_response.startswith(
                ("Erro Gemini", "Falha temporária", "Falha interna")
            ):
                save_cached_answer(question, full_response.strip(), "gemini")

            if not full_response.strip():
                fallback_error = last_error or "Falha temporária ao consultar modelo de IA. Tente novamente em alguns segundos."
                full_response = fallback_error
                yield f"data: {json.dumps({'text': fallback_error})}\n\n"

        except Exception as exc:
            error_msg = f"Falha interna no stream: {str(exc)}"
            full_response = full_response or error_msg
            yield f"data: {json.dumps({'text': error_msg})}\n\n"
        finally:
            if conversation_id and full_response.strip():
                safe_save_assistant_message(conversation_id, full_response.strip())
            if not done_sent:
                done_sent = True
                yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/ask")
async def ask_root(request: Request):
    data = await request.json()
    return await ask_impl(data)


@app.post("/api/ask")
async def ask_api(request: Request):
    data = await request.json()
    return await ask_impl(data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8899)

