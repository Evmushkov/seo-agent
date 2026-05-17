"""Importer for Topvisor position exports (xlsx)."""

import logging
from datetime import datetime
from pathlib import Path

import duckdb
import openpyxl

from .base import parse_folder_metadata, rebuild_query_unified

logger = logging.getLogger(__name__)


def _search_engine_from_name(stem: str) -> str:
    """'Yandex' → 'position_yandex', 'Google' → 'position_google'."""
    s = stem.lower()
    if "google" in s:
        return "position_google"
    return "position_yandex"


def _import_xlsx(path: Path, import_id: int, con: duckdb.DuckDBPyConnection) -> int:
    metric_pos = _search_engine_from_name(path.stem)

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return 0

    header = rows[0]
    # col 0: query name; col 1: frequency_exact; col 2: frequency_quoted; col 3+: dates
    date_cols = []
    for i, h in enumerate(header):
        if i < 3:
            continue
        if isinstance(h, datetime):
            date_cols.append((i, h.date()))

    facts = []
    for row in rows[1:]:
        query = row[0]
        if not query or query == "--":
            continue
        query = str(query)

        # frequencies (date=NULL, one row per query per metric)
        freq_exact = row[1] if len(row) > 1 else None
        freq_quoted = row[2] if len(row) > 2 else None
        if freq_exact is not None and freq_exact != "--":
            facts.append((import_id, query, None, None, None, "frequency_exact", float(freq_exact)))
        if freq_quoted is not None and freq_quoted != "--":
            facts.append((import_id, query, None, None, None, "frequency_quoted", float(freq_quoted)))

        # positions by date
        for col_idx, dt in date_cols:
            val = row[col_idx] if col_idx < len(row) else None
            if val is None or str(val).strip() in ("--", ""):
                continue
            facts.append((import_id, query, None, dt, None, metric_pos, float(val)))

    if facts:
        con.executemany(
            "INSERT INTO query_facts "
            "(import_id, query, url, date, traffic_source, metric, value) "
            "VALUES (?,?,?,?,?,?,?)",
            facts,
        )
    logger.info("  %s → %d fact rows", path.name, len(facts))
    return len(facts)


def import_folder(folder: Path, con: duckdb.DuckDBPyConnection) -> int:
    """Import one Topvisor export folder into the DB.

    Returns the total number of rows inserted into query_facts.
    """
    folder = Path(folder)
    meta = parse_folder_metadata(folder)

    row = con.execute(
        "INSERT INTO imports "
        "(project, domain, source, region, platform, date_from, date_to, folder_path) "
        "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        [meta["project"], meta["domain"], meta["source"],
         meta["region"], meta["platform"], meta["date_from"], meta["date_to"],
         meta["folder_path"]],
    ).fetchone()
    import_id = row[0]
    logger.info("import_id=%d  folder=%s", import_id, folder.name)

    total_facts = 0
    for f in sorted(folder.glob("*.xlsx")):
        total_facts += _import_xlsx(f, import_id, con)

    con.execute("UPDATE imports SET row_count=? WHERE id=?", [total_facts, import_id])
    rebuild_query_unified(con)
    return total_facts
