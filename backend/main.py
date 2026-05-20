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
    gemini_api_keys: tuple[str, ...]
    gemini_model: str


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
        gemini_api_keys=parse_gemini_api_keys(
            os.environ.get("GEMINI_API_KEYS", "").strip(),
            os.environ.get("GEMINI_API_KEY", "").strip(),
        ),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip(),
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


def get_conn() -> psycopg.Connection:
    return psycopg.connect(SETTINGS.database_url, row_factory=dict_row)


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

    if any(k in q for k in ["mais", "maior", "maximo", "maxima", "demora mais", "mais dias"]):
        return "max"
    if any(k in q for k in ["menos", "menor", "minimo", "minima", "demora menos"]):
        return "min"
    if any(k in q for k in ["media", "médio", "medio"]):
        return "avg"
    # Follow-up curto: herda direção da pergunta anterior do usuário
    if history:
        for msg in reversed(history[-8:]):
            if msg.get("role") != "user":
                continue
            hq = normalize_text(str(msg.get("content", "")))
            if any(k in hq for k in ["mais", "maior", "maximo", "maxima", "demora mais", "mais dias"]):
                return "max"
            if any(k in hq for k in ["menos", "menor", "minimo", "minima", "demora menos"]):
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
    field = detect_structured_intent(question) or detect_structured_intent_from_history(history)
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
                    SELECT route_code, partner_name, driver_name, route_time_days, departure_days, region
                    FROM route_facts
                    WHERE route_code_norm LIKE %s
                    ORDER BY route_code_norm
                    LIMIT 200
                    """,
                    (f"{route_ref}%",),
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


def lookup_override(question: str) -> str | None:
    question_norm = normalize_text(question)
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
            structured = try_structured_answer(question, history)
            if structured:
                answer = structured.get("answer", "")
                yield f"data: {json.dumps({'text': answer})}\n\n"
                full_response = answer
                return

            override_answer = lookup_override(question)
            if override_answer:
                yield f"data: {json.dumps({'text': override_answer})}\n\n"
                full_response = override_answer
                return

            hits = search_documents(question, limit=5)
            if not hits:
                unknown = "Informação não consta nos manuais ou tabelas disponíveis."
                yield f"data: {json.dumps({'text': unknown})}\n\n"
                full_response = unknown
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

