from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


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


def get_database_url() -> str:
    load_env_from_file(ENV_PATH)
    return os.environ.get("DATABASE_URL", "postgresql://postgres:2026@localhost:5432/postgres").strip()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def split_multi_values(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for part in str(raw_value).split("|"):
        val = part.strip()
        if not val:
            continue
        key = val.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(val)
    return values


def format_answer_values(values: list[str]) -> str:
    if not values:
        return "Informação não consta nos manuais ou tabelas disponíveis."
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} e {values[1]}"
    return ", ".join(values[:-1]) + f" e {values[-1]}"


def upsert_qa(cur: psycopg.Cursor, question: str, answer: str, admin_user_id: int = 1) -> None:
    q_raw = question.strip()
    q_norm = normalize_text(q_raw)
    if not q_norm or not answer.strip():
        return
    cur.execute(
        """
        INSERT INTO qa_overrides (question_norm, question_raw, answer, admin_user_id, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (question_norm) DO UPDATE
        SET question_raw = EXCLUDED.question_raw,
            answer = EXCLUDED.answer,
            admin_user_id = EXCLUDED.admin_user_id,
            updated_at = NOW()
        """,
        (q_norm, q_raw, answer.strip(), admin_user_id),
    )


def generate_route_qas(cur: psycopg.Cursor) -> int:
    cur.execute(
        """
        SELECT route_code_norm, route_code, partner_name, driver_name, route_time_days, departure_days, region
        FROM route_facts
        ORDER BY route_code_norm
        """
    )
    rows = cur.fetchall()

    inserted = 0
    for row in rows:
        route = row["route_code"] or row["route_code_norm"]
        partner = format_answer_values(split_multi_values(row.get("partner_name")))
        driver = format_answer_values(split_multi_values(row.get("driver_name")))
        route_time = format_answer_values(split_multi_values(row.get("route_time_days")))
        departure = format_answer_values(split_multi_values(row.get("departure_days")))
        region = format_answer_values(split_multi_values(row.get("region")))

        qa_pairs = [
            (f"quem é o motorista da rota {route}?", driver),
            (f"quem é o motorista da subrota {route}?", driver),
            (f"motorista da rota {route}", driver),
            (f"qual o motorista da {route}?", driver),
            (f"quem é o parceiro da rota {route}?", partner),
            (f"quem é o parceiro da subrota {route}?", partner),
            (f"parceiro da rota {route}", partner),
            (f"qual o parceiro da {route}?", partner),
            (f"quais os dias de largada da rota {route}?", departure),
            (f"dias de largada da {route}", departure),
            (f"qual o tempo em rota da subrota {route}?", route_time),
            (f"qual o tempo em rota da rota {route}?", route_time),
            (f"subrota {route} qual tempo em rota?", route_time),
            (f"tempo de rota da {route}", route_time),
            (f"quantos dias em rota da {route}?", route_time),
            (f"qual a região da rota {route}?", region),
            (f"região da {route}", region),
        ]

        for question, answer in qa_pairs:
            upsert_qa(cur, question, answer)
            inserted += 1

    return inserted


def generate_manual_focus_qas(cur: psycopg.Cursor) -> int:
    # Resposta curada com base no trecho "1. OBJETIVO" do MNOP03
    sentence = (
        "Segundo o MNOP03, o papel do Gestor de Operações é estabelecer e executar "
        "a super rotina da unidade, definindo ordem, periodicidade, responsabilidade "
        "e forma de execução das atividades operacionais, administrativas e estratégicas, "
        "garantindo padronização da gestão, previsibilidade operacional e execução "
        "sistemática das atividades críticas."
    )

    questions = [
        "qual o papel do gestor?",
        "qual é o papel do gestor?",
        "qual o papel do gestor no mnop03?",
        "qual é o papel do gestor no manual mnop03?",
        "no mnop03, qual é o papel do gestor?",
        "o que o gestor deve fazer segundo o mnop03?",
        "qual a função do gestor na super rotina?",
        "função do gestor no mnop03",
        "responsabilidade do gestor no mnop03",
        "o que diz o mnop03 sobre o gestor?",
        "qual o papel do gestor de operações?",
        "qual a função do gestor de operações?",
        "qual o papel do gestor que consta no mnop03?",
        "qual o papel do gestor no manual super rotina?",
        "papel do gestor no manual mnop03",
        "o que o manual mnop03 fala sobre o gestor?",
    ]

    for q in questions:
        upsert_qa(cur, q, sentence)
    return len(questions)


def main() -> None:
    db_url = get_database_url()
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            routes_count = generate_route_qas(cur)
            manual_count = generate_manual_focus_qas(cur)
        conn.commit()
    print({"route_qas_upserted": routes_count, "manual_qas_upserted": manual_count, "total": routes_count + manual_count})


if __name__ == "__main__":
    main()
