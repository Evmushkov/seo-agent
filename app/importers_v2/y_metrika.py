"""Importer for Yandex.Metrika exports (landing_pages.csv only)."""

import logging
from pathlib import Path

import duckdb
import pandas as pd

from .base import parse_folder_metadata, read_csv_text, rebuild_query_unified

logger = logging.getLogger(__name__)

_SKIP_COLS = frozenset({
    "Страница входа, ур. 1", "Страница входа, ур. 2",
    "Страница входа, ур. 3", "Страница входа, ур. 4",
    "Поисковая фраза",
})

_COL_MAP = {
    "Визиты":                                        "visits",
    "Посетители":                                    "users",
    "Отказы":                                        "bounce_rate",
    "Глубина просмотра":                             "page_depth",
    "Время на сайте":                                "time_on_site",
    "Конверсия (Ecommerce: покупка)":                "ecommerce_purchase_rate",
    "Конверсия (Ecommerce: добавление в корзину)":   "ecommerce_add_to_cart_rate",
}

_URL_COL    = "Страница входа"
_SOURCE_COL = "Источник трафика (детально)"


def _parse_time(val) -> float | None:
    """Parse 'hh:mm:ss' string → total seconds."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    parts = s.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        return float(h * 3600 + m * 60 + sec)
    except ValueError:
        return None


def import_folder(folder: Path, con: duckdb.DuckDBPyConnection,
                  file_hash: str | None = None) -> int:
    """Import one Yandex.Metrika export folder (landing_pages.csv only).

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

    total_facts = 0

    for path in sorted(folder.glob("*.csv")):
        if path.name != "landing_pages.csv":
            logger.info("  skip: not in import plan  %s", path.name)
            continue

        df = read_csv_text(path)
        df = df.drop(columns=[c for c in _SKIP_COLS if c in df.columns])

        # Drop "Итого и средние" — identified by NaN in url column
        df = df[df[_URL_COL].notna()]
        df = df[df[_URL_COL].astype(str).str.strip() != ""]

        metric_cols = [c for c in df.columns if c in _COL_MAP]

        facts = []
        for _, row_data in df.iterrows():
            url = str(row_data[_URL_COL]).strip()
            if not url:
                continue
            traffic_source = row_data.get(_SOURCE_COL)
            if traffic_source is None or (isinstance(traffic_source, float) and pd.isna(traffic_source)):
                traffic_source = None
            else:
                traffic_source = str(traffic_source).strip() or None

            for col in metric_cols:
                raw = row_data[col]
                if col == "Время на сайте":
                    val = _parse_time(raw)
                else:
                    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                        continue
                    try:
                        val = float(raw)
                    except (ValueError, TypeError):
                        continue
                if val is None:
                    continue
                facts.append((import_id, None, url, None, traffic_source, _COL_MAP[col], val))

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
