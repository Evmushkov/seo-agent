"""Importer for Yandex.Webmaster exports — two formats:

Format A (xlsx): query.xlsx, wide table with {date}_{metric} columns.
Format B (csv):  clicks.csv, impressions.csv, CTR.csv, average_position.csv.
"""

import re
import logging
from pathlib import Path

import duckdb
import pandas as pd

from .base import parse_folder_metadata, read_csv_text, rebuild_query_unified

logger = logging.getLogger(__name__)

_DATE_METRIC_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})_(shows|position|demand|ctr|clicks)$')

_METRIC_MAP_A = {
    "shows":    "impressions",
    "clicks":   "clicks",
    "position": "position",
    "demand":   "demand",
    "ctr":      "ctr",
}

_FORMAT_B_FILES = {
    "clicks.csv":           ("clicks",      False),
    "impressions.csv":      ("impressions", False),
    "CTR.csv":              ("ctr",         True),   # % → fraction
    "average_position.csv": ("position",    False),
}

_BATCH = 50_000


def _normalise_url(path_val: str, base: str) -> str:
    s = str(path_val).strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return base.rstrip("/") + ("" if s.startswith("/") else "/") + s


def _import_format_a(folder: Path, import_id: int, domain: str,
                     con: duckdb.DuckDBPyConnection) -> int:
    xlsx_files = sorted(folder.glob("*.xlsx"))
    total = 0
    for path in xlsx_files:
        logger.info("  format A: %s", path.name)
        df = pd.read_excel(path, engine="openpyxl")

        id_cols = ["Query", "Url"]
        value_cols = [c for c in df.columns if _DATE_METRIC_RE.match(str(c))]
        if not value_cols:
            logger.warning("  no date-metric columns found in %s, skip", path.name)
            continue

        melted = (
            df[id_cols + value_cols]
            .melt(id_vars=id_cols, var_name="col", value_name="value")
            .dropna(subset=["value"])
        )

        parsed = melted["col"].str.extract(_DATE_METRIC_RE)
        melted = melted.assign(date=parsed[0], raw_metric=parsed[1])
        melted["metric"] = melted["raw_metric"].map(_METRIC_MAP_A)
        melted = melted.dropna(subset=["metric"])

        # Keep only (Query, Url, date) where shows > 0 — zero means no data.
        shows = (
            melted[melted["raw_metric"] == "shows"][["Query", "Url", "date", "value"]]
            .rename(columns={"value": "_shows"})
        )
        melted = melted.merge(shows, on=["Query", "Url", "date"], how="left")
        melted = melted[melted["_shows"] > 0].drop(columns=["_shows"])

        # position == 0 is invalid even when impressions > 0
        melted = melted[~((melted["raw_metric"] == "position") & (melted["value"] == 0))]

        # ctr is stored as percentage in xlsx → convert to fraction
        ctr_mask = melted["metric"] == "ctr"
        melted.loc[ctr_mask, "value"] = melted.loc[ctr_mask, "value"] / 100

        base = domain.rstrip("/")
        melted["url"] = melted["Url"].fillna("").apply(lambda p: _normalise_url(p, base))
        melted["import_id"] = import_id
        melted["traffic_source"] = None

        fact_df = melted[["import_id", "Query", "url", "date", "traffic_source", "metric", "value"]].copy()
        fact_df.columns = ["import_id", "query", "url", "date", "traffic_source", "metric", "value"]

        for start in range(0, len(fact_df), _BATCH):
            batch = fact_df.iloc[start:start + _BATCH]
            con.register("_wb_batch", batch)
            con.execute("""
                INSERT INTO query_facts
                    (import_id, query, url, date, traffic_source, metric, value)
                SELECT import_id, query, url,
                       TRY_CAST(date AS DATE),
                       traffic_source, metric, value
                FROM _wb_batch
            """)
            con.unregister("_wb_batch")

        logger.info("  %s → %d fact rows", path.name, len(fact_df))
        total += len(fact_df)
    return total


def _build_valid_pairs_b(folder: Path, base: str) -> set:
    """Return set of (url, date_str) where impressions > 0."""
    imp_path = folder / "impressions.csv"
    if not imp_path.exists():
        return set()
    df = read_csv_text(imp_path)
    path_col = df.columns[0]
    date_cols = [c for c in df.columns[1:] if re.match(r'\d{4}-\d{2}-\d{2}', str(c))]
    valid = set()
    for _, row in df.iterrows():
        url = _normalise_url(str(row[path_col]), base)
        for dc in date_cols:
            val = row[dc]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            try:
                if float(val) > 0:
                    valid.add((url, dc))
            except (ValueError, TypeError):
                pass
    return valid


def _import_format_b(folder: Path, import_id: int, domain: str,
                     con: duckdb.DuckDBPyConnection) -> int:
    base = domain.rstrip("/")

    # Pre-compute (url, date) pairs that have impressions > 0
    valid_pairs = _build_valid_pairs_b(folder, base)
    logger.info("  format B: %d valid (url, date) pairs with impressions>0", len(valid_pairs))

    total = 0
    for fname, (metric, is_pct) in _FORMAT_B_FILES.items():
        path = folder / fname
        if not path.exists():
            logger.info("  skip: missing %s", fname)
            continue

        df = read_csv_text(path)
        path_col = df.columns[0]  # "Path"
        date_cols = [c for c in df.columns[1:] if re.match(r'\d{4}-\d{2}-\d{2}', str(c))]

        facts = []
        for _, row in df.iterrows():
            url = _normalise_url(str(row[path_col]), base)
            for dc in date_cols:
                # Skip days with no impressions
                if (url, dc) not in valid_pairs:
                    continue
                val = row[dc]
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                try:
                    v = float(val)
                except (ValueError, TypeError):
                    continue
                # position == 0 is invalid
                if metric == "position" and v == 0:
                    continue
                if is_pct:
                    v /= 100
                facts.append((import_id, None, url, dc, None, metric, v))

        if facts:
            con.executemany(
                "INSERT INTO query_facts "
                "(import_id, query, url, date, traffic_source, metric, value) "
                "VALUES (?,?,?,?,?,?,?)",
                facts,
            )
        logger.info("  %s → %d fact rows", fname, len(facts))
        total += len(facts)
    return total


def import_folder(folder: Path, con: duckdb.DuckDBPyConnection) -> int:
    """Import one Yandex.Webmaster export folder.

    Returns the number of rows inserted into query_facts.
    """
    folder = Path(folder)
    meta = parse_folder_metadata(folder)
    domain = meta["domain"]

    has_xlsx = bool(list(folder.glob("*.xlsx")))
    has_csv  = any((folder / f).exists() for f in _FORMAT_B_FILES)

    if has_xlsx and has_csv:
        raise ValueError(f"mixed formats in folder: {folder}")
    if not has_xlsx and not has_csv:
        logger.warning("no importable files found in %s", folder)
        return 0

    row = con.execute(
        "INSERT INTO imports "
        "(project, domain, source, region, platform, date_from, date_to, folder_path) "
        "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        [meta["project"], meta["domain"], meta["source"],
         meta["region"], meta["platform"], meta["date_from"], meta["date_to"],
         meta["folder_path"]],
    ).fetchone()
    import_id = row[0]
    fmt = "A (xlsx)" if has_xlsx else "B (csv)"
    logger.info("import_id=%d  format=%s  folder=%s", import_id, fmt, folder.name)

    if has_xlsx:
        total = _import_format_a(folder, import_id, domain, con)
    else:
        total = _import_format_b(folder, import_id, domain, con)

    con.execute("UPDATE imports SET row_count=? WHERE id=?", [total, import_id])
    rebuild_query_unified(con)
    return total
