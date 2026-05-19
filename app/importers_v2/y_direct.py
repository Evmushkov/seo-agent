"""Importer for Yandex.Direct query-level CSV exports."""

import logging
from pathlib import Path

import duckdb
import pandas as pd

from .base import parse_folder_metadata, read_csv_text, rebuild_query_unified

logger = logging.getLogger(__name__)

_SKIP_COLS = frozenset({"Упоминания брендов в запросах", "Подобранная фраза"})

# Columns divided by 100 before storage (percentages → fractions)
_PCT_COLS = frozenset({"CR, %", "CTR, %", "Отказы, %", "ДРР, %"})

_COL_MAP = {
    "Клики":               "clicks",
    "Показы":              "impressions",
    "Расход, ₽":           "cost",
    "Конверсии":           "conversions",
    "CR, %":               "conversion_rate",
    "CPA, ₽":              "cpa",
    "CTR, %":              "ctr",
    "Ср. позиция показа":  "position_show",
    "Ср. позиция клика":   "position_click",
    "Ср. объём трафика":   "traffic_volume",
    "Отказы, %":           "bounce_rate",
    "Глубина просмотра":   "page_depth",
    "Доход, ₽":            "revenue",
    "ДРР, %":              "drr",
}


def import_folder(folder: Path, con: duckdb.DuckDBPyConnection,
                  file_hash: str | None = None) -> int:
    """Import one Yandex.Direct export folder into the DB.

    Returns the number of rows inserted into query_facts.
    """
    folder = Path(folder)
    meta = parse_folder_metadata(folder)
    file_hash = file_hash or folder_hash(folder)

    row = con.execute(
        "INSERT INTO imports "
        "(project, domain, source, region, platform, date_from, date_to, folder_path, file_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
        [meta["project"], meta["domain"], meta["source"],
         meta["region"], meta["platform"], meta["date_from"], meta["date_to"],
         meta["folder_path"], file_hash],
    ).fetchone()
    import_id = row[0]
    logger.info("import_id=%d  folder=%s", import_id, folder.name)

    csv_files = sorted(folder.glob("*.csv"))
    total_facts = 0

    for path in csv_files:
        df = read_csv_text(path)

        # Drop skip columns that exist
        df = df.drop(columns=[c for c in _SKIP_COLS if c in df.columns])

        query_col = "Поисковый запрос"
        # Drop "Итого" row and rows with no query
        df = df[df[query_col].notna()]
        df = df[df[query_col].astype(str).str.strip() != "Итого"]

        metric_cols = [c for c in df.columns if c != query_col and c in _COL_MAP]

        facts = []
        for _, row_data in df.iterrows():
            query = str(row_data[query_col]).strip()
            if not query:
                continue
            for col in metric_cols:
                raw = row_data[col]
                if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                    continue
                s = str(raw).strip()
                if s in ("-", ""):
                    continue
                try:
                    val = float(s)
                except ValueError:
                    continue
                if col in _PCT_COLS:
                    val /= 100
                facts.append((import_id, query, None, None, None, _COL_MAP[col], val))

        if facts:
            con.executemany(
                "INSERT INTO query_facts "
                "(import_id, query, url, date, traffic_source, metric, value) "
                "VALUES (?,?,?,?,?,?,?)",
                facts,
            )
        logger.info("  %s → %d fact rows", path.name, len(facts))
        total_facts += len(facts)

    con.execute("UPDATE imports SET row_count=? WHERE id=?", [total_facts, import_id])
    rebuild_query_unified(con)
    return total_facts
