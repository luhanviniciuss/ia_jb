from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.ingest import ingest_docx_file, ingest_pdf_file, ingest_routes_xlsx, ingest_text_content

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


def init_schema(conn: psycopg.Connection) -> None:
    sql = """
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
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def run(args: argparse.Namespace) -> None:
    database_url = get_database_url()
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        init_schema(conn)
        print("Schema OK.")

        if args.xlsx:
            xlsx = (ROOT / args.xlsx).resolve()
            print(f"Ingerindo XLSX: {xlsx}")
            out = ingest_routes_xlsx(conn, xlsx)
            print(out)

        if args.text:
            for text_path in args.text:
                path = (ROOT / text_path).resolve()
                print(f"Ingerindo TXT: {path}")
                content = path.read_text(encoding="utf-8", errors="ignore")
                count = ingest_text_content(
                    conn,
                    source_name=path.name,
                    source_type="manual_text",
                    text=content,
                    section_title=path.stem,
                )
                print({"source": path.name, "chunks_inserted": count})

        if args.pdf:
            for pdf_path in args.pdf:
                path = (ROOT / pdf_path).resolve()
                print(f"Ingerindo PDF: {path}")
                out = ingest_pdf_file(conn, path)
                print(out)

        if args.docx:
            for docx_path in args.docx:
                path = (ROOT / docx_path).resolve()
                print(f"Ingerindo DOCX: {path}")
                out = ingest_docx_file(conn, path)
                print(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Treina base PostgreSQL com tabelas e documentos.")
    parser.add_argument("--xlsx", help="Arquivo XLSX de rotas, ex: D23V7.xlsx")
    parser.add_argument("--text", nargs="*", help="Arquivos texto (ex: mnop02.txt mnop03.txt)")
    parser.add_argument("--pdf", nargs="*", help="Arquivos PDF para ingestÃ£o direta")
    parser.add_argument("--docx", nargs="*", help="Arquivos DOCX para ingestÃ£o direta")
    run(parser.parse_args())

