from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pypdf import PdfReader


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def normalize_route_code(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def split_text_chunks(text: str, chunk_size: int = 1400, overlap: int = 220) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        piece = clean[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return chunks


def _upsert_source(cur: psycopg.Cursor, source_name: str, source_type: str, version_tag: str | None = None) -> int:
    cur.execute(
        """
        INSERT INTO doc_sources (source_name, source_type, version_tag, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (source_name) DO UPDATE
        SET source_type = EXCLUDED.source_type,
            version_tag = EXCLUDED.version_tag,
            updated_at = NOW()
        RETURNING id
        """,
        (source_name, source_type, version_tag),
    )
    row = cur.fetchone()
    return int(row["id"])


def ingest_text_content(
    conn: psycopg.Connection,
    source_name: str,
    source_type: str,
    text: str,
    section_title: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    version_tag: str | None = None,
) -> int:
    chunks = split_text_chunks(text)
    if not chunks:
        return 0

    with conn.cursor() as cur:
        source_id = _upsert_source(cur, source_name=source_name, source_type=source_type, version_tag=version_tag)
        cur.execute("DELETE FROM doc_chunks WHERE source_id = %s", (source_id,))

        for idx, chunk in enumerate(chunks):
            chunk_norm = normalize_text(chunk)
            cur.execute(
                """
                INSERT INTO doc_chunks (
                  source_id, chunk_index, section_title, page_start, page_end,
                  content, content_norm, search_tsv, metadata
                )
                VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, to_tsvector('simple', unaccent(%s)), '{}'::jsonb
                )
                """,
                (
                    source_id,
                    idx,
                    section_title,
                    page_start,
                    page_end,
                    chunk,
                    chunk_norm,
                    chunk,
                ),
            )
    conn.commit()
    return len(chunks)


def ingest_pdf_file(conn: psycopg.Connection, pdf_path: Path) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    inserted_chunks = 0
    pages_with_text = 0

    with conn.cursor() as cur:
        source_id = _upsert_source(cur, source_name=pdf_path.name, source_type="pdf", version_tag=None)
        cur.execute("DELETE FROM doc_chunks WHERE source_id = %s", (source_id,))

        chunk_index = 0
        for p_idx, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            text = raw.strip()
            if not text:
                continue
            pages_with_text += 1
            page_chunks = split_text_chunks(text, chunk_size=1200, overlap=160)
            for chunk in page_chunks:
                chunk_norm = normalize_text(chunk)
                cur.execute(
                    """
                    INSERT INTO doc_chunks (
                      source_id, chunk_index, section_title, page_start, page_end,
                      content, content_norm, search_tsv, metadata
                    )
                    VALUES (
                      %s, %s, %s, %s, %s,
                      %s, %s, to_tsvector('simple', unaccent(%s)), %s::jsonb
                    )
                    """,
                    (
                        source_id,
                        chunk_index,
                        f"Página {p_idx}",
                        p_idx,
                        p_idx,
                        chunk,
                        chunk_norm,
                        chunk,
                        '{"kind":"pdf_page_chunk"}',
                    ),
                )
                chunk_index += 1
                inserted_chunks += 1

    conn.commit()
    return {
        "source": pdf_path.name,
        "pages_with_text": pages_with_text,
        "chunks_inserted": inserted_chunks,
    }


def _extract_docx_text(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    paragraphs: list[str] = []
    for p in root.findall(".//w:p", ns):
        texts = [t.text or "" for t in p.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs).strip()


def ingest_docx_file(conn: psycopg.Connection, docx_path: Path) -> dict[str, Any]:
    text = _extract_docx_text(docx_path)
    chunks = ingest_text_content(
        conn,
        source_name=docx_path.name,
        source_type="manual_docx",
        text=text,
        section_title=docx_path.stem,
    )
    return {
        "source": docx_path.name,
        "characters": len(text),
        "chunks_inserted": chunks,
    }


def _find_first_column(columns_norm: dict[str, str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        for original, normalized in columns_norm.items():
            if candidate == normalized:
                return original
    return None


def _to_jsonable(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _ensure_d23_full_table(cur: psycopg.Cursor, columns: list[str]) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS d23_full (
          id BIGSERIAL PRIMARY KEY,
          source_name TEXT NOT NULL,
          row_number INT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE(source_name, row_number)
        )
        """
    )
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'd23_full'
        """
    )
    existing = {r["column_name"] for r in cur.fetchall()}
    base = {"id", "source_name", "row_number", "updated_at"}

    for col in columns:
        if col in existing:
            continue
        if col in base:
            continue
        cur.execute(
            sql.SQL("ALTER TABLE d23_full ADD COLUMN {} TEXT").format(sql.Identifier(col))
        )


def ingest_d23_full(conn: psycopg.Connection, xlsx_path: Path, df: pd.DataFrame) -> dict[str, Any]:
    cols = [str(c) for c in df.columns]
    with conn.cursor() as cur:
        _ensure_d23_full_table(cur, cols)
        cur.execute("DELETE FROM d23_full WHERE source_name = %s", (xlsx_path.name,))

        insert_columns = [sql.Identifier("source_name"), sql.Identifier("row_number")] + [sql.Identifier(c) for c in cols]
        insert_sql = sql.SQL("INSERT INTO d23_full ({}) VALUES ({})").format(
            sql.SQL(", ").join(insert_columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in range(2 + len(cols))),
        )

        params_list: list[list[Any]] = []
        for row_number, (_, row) in enumerate(df.iterrows(), start=1):
            row_values = []
            for col in cols:
                v = _to_jsonable(row.get(col))
                row_values.append(None if v is None else str(v))
            params_list.append([xlsx_path.name, row_number, *row_values])

        cur.executemany(insert_sql, params_list)
    conn.commit()
    return {"source": xlsx_path.name, "rows_inserted": len(df.index), "columns_loaded": len(cols)}


def ingest_routes_xlsx(conn: psycopg.Connection, xlsx_path: Path) -> dict[str, Any]:
    df = pd.read_excel(xlsx_path)
    cols = list(df.columns)
    cols_norm = {c: normalize_text(str(c)).replace(" ", "") for c in cols}

    route_col = _find_first_column(
        cols_norm,
        ["subrota", "rota", "codigorota", "route", "routecode"],
    )
    partner_col = _find_first_column(cols_norm, ["parceiro", "partner"])
    driver_col = _find_first_column(cols_norm, ["motorista", "driver", "condutor"])
    route_time_col = _find_first_column(cols_norm, ["tempoderotadias", "temporotadias", "diasemrota"])
    departure_col = _find_first_column(cols_norm, ["largadadias", "diasdelargada", "largada", "dias"])
    region_col = _find_first_column(cols_norm, ["regiao", "regional"])

    if not route_col:
        raise ValueError("Não encontrei coluna de rota/subrota no XLSX.")

    def add_value(bucket: set[str], raw: Any) -> None:
        val = str(raw or "").strip()
        if val and val.lower() != "nan":
            bucket.add(val)

    grouped: dict[str, dict[str, Any]] = {}

    for _, row in df.iterrows():
        route_raw = str(row.get(route_col, "")).strip()
        route_code_norm = normalize_route_code(route_raw)
        if not route_code_norm:
            continue

        current = grouped.setdefault(
            route_code_norm,
            {
                "route_code": route_raw,
                "partners": set(),
                "drivers": set(),
                "route_times": set(),
                "departures": set(),
                "regions": set(),
            },
        )

        if partner_col:
            add_value(current["partners"], row.get(partner_col))
        if driver_col:
            add_value(current["drivers"], row.get(driver_col))
        if route_time_col:
            add_value(current["route_times"], row.get(route_time_col))
        if departure_col:
            add_value(current["departures"], row.get(departure_col))
        if region_col:
            add_value(current["regions"], row.get(region_col))

    full_insert_info = ingest_d23_full(conn, xlsx_path, df)
    raw_rows_inserted = 0
    upserted = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM d23_rows WHERE source_name = %s", (xlsx_path.name,))

        for row_number, (_, row) in enumerate(df.iterrows(), start=1):
            route_raw = str(row.get(route_col, "")).strip() if route_col else ""
            route_code_norm = normalize_route_code(route_raw) if route_raw else None
            row_data = {str(col): _to_jsonable(row.get(col)) for col in cols}
            cur.execute(
                """
                INSERT INTO d23_rows (source_name, row_number, route_code, route_code_norm, row_data, updated_at)
                VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, NOW())
                """,
                (
                    xlsx_path.name,
                    row_number,
                    route_raw,
                    route_code_norm or "",
                    Jsonb(row_data),
                ),
            )
            raw_rows_inserted += 1

        for route_code_norm, info in grouped.items():
            route_raw = info["route_code"]
            partner = " | ".join(sorted(info["partners"])) if info["partners"] else ""
            driver = " | ".join(sorted(info["drivers"])) if info["drivers"] else ""
            route_time = " | ".join(sorted(info["route_times"])) if info["route_times"] else ""
            departure = " | ".join(sorted(info["departures"])) if info["departures"] else ""
            region = " | ".join(sorted(info["regions"])) if info["regions"] else ""

            cur.execute(
                """
                INSERT INTO route_facts (
                  route_code, route_code_norm, partner_name, driver_name,
                  route_time_days, departure_days, region, source_name, extra, updated_at
                )
                VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), %s, '{}'::jsonb, NOW())
                ON CONFLICT (route_code_norm) DO UPDATE
                SET route_code = EXCLUDED.route_code,
                    partner_name = COALESCE(NULLIF(EXCLUDED.partner_name, ''), route_facts.partner_name),
                    driver_name = COALESCE(NULLIF(EXCLUDED.driver_name, ''), route_facts.driver_name),
                    route_time_days = COALESCE(NULLIF(EXCLUDED.route_time_days, ''), route_facts.route_time_days),
                    departure_days = COALESCE(NULLIF(EXCLUDED.departure_days, ''), route_facts.departure_days),
                    region = COALESCE(NULLIF(EXCLUDED.region, ''), route_facts.region),
                    source_name = EXCLUDED.source_name,
                    updated_at = NOW()
                """,
                (route_raw, route_code_norm, partner, driver, route_time, departure, region, xlsx_path.name),
            )
            upserted += 1
    conn.commit()
    return {
        "source": xlsx_path.name,
        "rows_processed": int(len(df.index)),
        "d23_full_rows_inserted": full_insert_info["rows_inserted"],
        "d23_full_columns_loaded": full_insert_info["columns_loaded"],
        "raw_rows_inserted": raw_rows_inserted,
        "route_upserted": upserted,
        "distinct_routes": len(grouped),
    }
