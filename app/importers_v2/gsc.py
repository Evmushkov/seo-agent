"""Importer for Google Search Console exports."""

import hashlib
import json
import logging
from pathlib import Path

import duckdb
import pandas as pd

from .base import parse_folder_metadata, read_csv_text, rebuild_query_unified

logger = logging.getLogger(__name__)

_KNOWN_FILES = frozenset({
    "Queries.csv", "Pages.csv", "Chart.csv",
    "Search appearance.csv", "Filters.csv",
})
_SKIP_FILES = frozenset({"Countries.csv", "Devices.csv"})


def _ctr(val) -> float | None:
    """Convert '69.23%' string or bare float to fraction (0..1)."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100
    return float(s)


def _folder_hash(folder: Path) -> str:
    sha = hashlib.sha256()
    for f in sorted(folder.glob("*.csv")):
        sha.update(f.name.encode())
        sha.update(f.read_bytes())
    return sha.hexdigest()


def _insert_facts(rows: list, con: duckdb.DuckDBPyConnection) -> None:
    if rows:
        con.executemany(
            "INSERT INTO query_facts "
            "(import_id, query, url, date, traffic_source, metric, value) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def _import_queries(path: Path, import_id: int, con: duckdb.DuckDBPyConnection) -> int:
    df = read_csv_text(path)
    key_col = df.columns[0]   # "Top queries"
    rows = []
    for _, row in df.iterrows():
        q = row[key_col]
        if pd.isna(q):
            continue
        q = str(q)
        for metric, col in [("clicks", "Clicks"), ("impressions", "Impressions"),
                             ("ctr", "CTR"), ("position", "Position")]:
            raw = row.get(col)
            if pd.isna(raw):
                continue
            val = _ctr(raw) if metric == "ctr" else float(raw)
            rows.append((import_id, q, None, None, None, metric, val))
    _insert_facts(rows, con)
    return len(rows)


def _import_pages(path: Path, import_id: int, con: duckdb.DuckDBPyConnection) -> int:
    df = read_csv_text(path)
    key_col = df.columns[0]   # "Top pages"
    rows = []
    for _, row in df.iterrows():
        url = row[key_col]
        if pd.isna(url):
            continue
        url = str(url)
        for metric, col in [("clicks", "Clicks"), ("impressions", "Impressions"),
                             ("ctr", "CTR"), ("position", "Position")]:
            raw = row.get(col)
            if pd.isna(raw):
                continue
            val = _ctr(raw) if metric == "ctr" else float(raw)
            rows.append((import_id, None, url, None, None, metric, val))
    _insert_facts(rows, con)
    return len(rows)


def _import_chart(path: Path, import_id: int, con: duckdb.DuckDBPyConnection) -> None:
    df = read_csv_text(path)
    rows = []
    for _, row in df.iterrows():
        d = row.get("Date")
        if pd.isna(d):
            continue
        for metric, col in [("clicks", "Clicks"), ("impressions", "Impressions"),
                             ("ctr", "CTR"), ("position", "Position")]:
            raw = row.get(col)
            if pd.isna(raw):
                continue
            val = _ctr(raw) if metric == "ctr" else float(raw)
            rows.append((import_id, str(d), metric, val))
    if rows:
        con.executemany(
            "INSERT INTO gsc_daily_totals (import_id, date, metric, value) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            rows,
        )


def _import_appearance(path: Path, import_id: int, con: duckdb.DuckDBPyConnection) -> None:
    df = read_csv_text(path)
    app_col = df.columns[0]   # "Search Appearance"
    rows = []
    for _, row in df.iterrows():
        app = row[app_col]
        if pd.isna(app):
            continue
        for metric, col in [("clicks", "Clicks"), ("impressions", "Impressions"),
                             ("ctr", "CTR"), ("position", "Position")]:
            raw = row.get(col)
            if pd.isna(raw):
                continue
            val = _ctr(raw) if metric == "ctr" else float(raw)
            rows.append((import_id, None, str(app), metric, val))
    if rows:
        con.executemany(
            "INSERT INTO gsc_facts_by_appearance "
            "(import_id, date, appearance_type, metric, value) VALUES (?,?,?,?,?)",
            rows,
        )


def import_folder(folder: Path, con: duckdb.DuckDBPyConnection) -> int:
    """Import one GSC export folder into the DB.

    Returns the number of rows inserted into query_facts.
    """
    folder = Path(folder)
    meta = parse_folder_metadata(folder)

    # Parse Filters.csv → JSON
    filters_json = None
    fp = folder / "Filters.csv"
    if fp.exists():
        df_f = read_csv_text(fp)
        filters_json = json.dumps(
            dict(zip(df_f.iloc[:, 0].astype(str), df_f.iloc[:, 1].astype(str))),
            ensure_ascii=False,
        )

    file_hash = _folder_hash(folder)

    # Idempotency check
    existing = con.execute(
        "SELECT id FROM imports "
        "WHERE project=? AND source=? AND region=? AND platform=? "
        "  AND date_from=? AND date_to=? AND file_hash=?",
        [meta["project"], meta["source"], meta["region"], meta["platform"],
         meta["date_from"], meta["date_to"], file_hash],
    ).fetchone()
    if existing:
        logger.info("skip: already imported (import_id=%d)", existing[0])
        return 0

    row = con.execute(
        "INSERT INTO imports "
        "(project, domain, source, region, platform, date_from, date_to, "
        " folder_path, file_hash, filters_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id",
        [meta["project"], meta["domain"], meta["source"],
         meta["region"], meta["platform"], meta["date_from"], meta["date_to"],
         meta["folder_path"], file_hash, filters_json],
    ).fetchone()
    import_id = row[0]
    logger.info("import_id=%d  folder=%s", import_id, folder.name)

    total_facts = 0

    for f in sorted(folder.iterdir()):
        if f.suffix != ".csv":
            continue
        if f.name in _SKIP_FILES:
            logger.info("  skip: trivial aggregate  %s", f.name)
            continue
        if f.name not in _KNOWN_FILES:
            logger.info("  skip: unknown file  %s", f.name)
            continue

        if f.name == "Queries.csv":
            n = _import_queries(f, import_id, con)
            logger.info("  Queries.csv → %d fact rows", n)
            total_facts += n
        elif f.name == "Pages.csv":
            n = _import_pages(f, import_id, con)
            logger.info("  Pages.csv → %d fact rows", n)
            total_facts += n
        elif f.name == "Chart.csv":
            _import_chart(f, import_id, con)
            logger.info("  Chart.csv → gsc_daily_totals")
        elif f.name == "Search appearance.csv":
            _import_appearance(f, import_id, con)
            logger.info("  Search appearance.csv → gsc_facts_by_appearance")

    con.execute("UPDATE imports SET row_count=? WHERE id=?", [total_facts, import_id])
    rebuild_query_unified(con)
    return total_facts
