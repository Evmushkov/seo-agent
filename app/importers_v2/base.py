"""Shared utilities for all importers."""

import re
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

VALID_SOURCES  = frozenset({"y.direct", "g.search.console", "y.metrika", "topvisor", "y.webmaster"})
VALID_REGIONS  = frozenset({"moscow", "moscow.district", "russia"})
VALID_PLATFORMS = frozenset({"desktop", "mobile", "tablet"})

_DATE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_folder_metadata(path: Path) -> dict:
    """Extract and validate import metadata from a folder path.

    Expected hierarchy (4 metadata levels):
        .../{project}/{source}/{region}_{platform}/{date_from}_{date_to}/[files]

    Accepts either the dates folder itself or any file inside it.

    Returns:
        {project, domain, source, region, platform,
         date_from (date), date_to (date), folder_path (str)}

    Raises:
        ValueError describing which field is invalid + allowed values.
    """
    path = Path(path)

    # Find the dates folder: try path itself, then parent (file-inside-folder).
    candidate = None
    dates: list[str] = []
    for probe in (path, path.parent):
        found = _DATE_ISO_RE.findall(probe.name)
        if len(found) >= 2:
            candidate = probe
            dates = found
            break

    if candidate is None:
        raise ValueError(
            f"Не найдены две даты ISO (YYYY-MM-DD) в имени папки '{path.name}'. "
            f"Ожидаемый формат папки дат: {{date_from}}_{{date_to}} "
            f"(например 2026-04-01_2026-04-30)."
        )

    date_from_str, date_to_str = dates[0], dates[1]

    try:
        date_from = date.fromisoformat(date_from_str)
    except ValueError:
        raise ValueError(
            f"Некорректный date_from '{date_from_str}'. Ожидается ISO YYYY-MM-DD."
        )
    try:
        date_to = date.fromisoformat(date_to_str)
    except ValueError:
        raise ValueError(
            f"Некорректный date_to '{date_to_str}'. Ожидается ISO YYYY-MM-DD."
        )

    if date_from > date_to:
        raise ValueError(
            f"date_from ({date_from}) > date_to ({date_to}): период некорректен."
        )

    # Navigate up: dates_folder → region_platform → source → project
    rp_dir      = candidate.parent
    source_dir  = rp_dir.parent
    project_dir = source_dir.parent

    if not rp_dir.name or not source_dir.name or not project_dir.name:
        raise ValueError(
            f"Путь слишком короткий: ожидается "
            f".../{{project}}/{{source}}/{{region}}_{{platform}}/{{dates}}/, "
            f"но уровней недостаточно выше '{candidate.name}'."
        )

    # Parse region and platform from the region_platform folder
    rp = rp_dir.name
    us = rp.rfind("_")
    if us == -1:
        raise ValueError(
            f"Не удалось разобрать регион и платформу из '{rp}': нет разделителя '_'. "
            f"Ожидается {{region}}_{{platform}} (например moscow_desktop)."
        )
    region   = rp[:us]
    platform = rp[us + 1:]

    if region not in VALID_REGIONS:
        raise ValueError(
            f"Недопустимый регион '{region}'. "
            f"Допустимые значения: {sorted(VALID_REGIONS)}."
        )
    if platform not in VALID_PLATFORMS:
        raise ValueError(
            f"Недопустимая платформа '{platform}'. "
            f"Допустимые значения: {sorted(VALID_PLATFORMS)}."
        )

    source  = source_dir.name
    project = project_dir.name

    if source not in VALID_SOURCES:
        raise ValueError(
            f"Недопустимый источник '{source}'. "
            f"Допустимые значения: {sorted(VALID_SOURCES)}."
        )

    return {
        "project":     project,
        "domain":      f"https://{project}",
        "source":      source,
        "region":      region,
        "platform":    platform,
        "date_from":   date_from,
        "date_to":     date_to,
        "folder_path": str(candidate.resolve() if candidate.exists() else candidate),
    }


# ── File readers ───────────────────────────────────────────────────────────────

def read_csv_text(path: Path, **kwargs) -> pd.DataFrame:
    """Read CSV trying UTF-8-sig → UTF-8 → cp1251 → errors=replace."""
    path = Path(path)
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="cp1251", encoding_errors="replace", **kwargs)


def read_xlsx(path: Path, sheet_name=0) -> pd.DataFrame:
    """Read Excel file via openpyxl (data_only=True semantics via pandas).

    When sheet_name=None, returns a dict of DataFrames.
    """
    path = Path(path)
    result = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", header=0)
    return result


# ── Shared rebuild ─────────────────────────────────────────────────────────────

def rebuild_query_unified(con: duckdb.DuckDBPyConnection) -> None:
    """Full rebuild of query_unified from query_facts + imports."""
    con.execute("DELETE FROM query_unified")
    con.execute("""
        INSERT INTO query_unified (
            project, query, url, period_from, period_to, region, platform, traffic_source,
            direct_clicks, direct_impressions, direct_cost, direct_ctr,
            direct_position_show, direct_conversions, direct_revenue,
            gsc_clicks, gsc_impressions, gsc_position, gsc_ctr,
            ymetrika_visits, ymetrika_users, ymetrika_bounce_rate,
            ymetrika_page_depth, ymetrika_time_on_site,
            topvisor_position_yandex, topvisor_position_google,
            topvisor_freq_exact, topvisor_freq_quoted,
            ywebmaster_clicks, ywebmaster_impressions, ywebmaster_position,
            ywebmaster_ctr, ywebmaster_demand
        )
        SELECT
            i.project, f.query, f.url,
            i.date_from, i.date_to, i.region, i.platform, f.traffic_source,
            SUM(CASE WHEN i.source='y.direct'         AND f.metric='clicks'           THEN f.value END),
            SUM(CASE WHEN i.source='y.direct'         AND f.metric='impressions'      THEN f.value END),
            SUM(CASE WHEN i.source='y.direct'         AND f.metric='cost'             THEN f.value END),
            AVG(CASE WHEN i.source='y.direct'         AND f.metric='ctr'              THEN f.value END),
            AVG(CASE WHEN i.source='y.direct'         AND f.metric='position_show'    THEN f.value END),
            SUM(CASE WHEN i.source='y.direct'         AND f.metric='conversions'      THEN f.value END),
            SUM(CASE WHEN i.source='y.direct'         AND f.metric='revenue'          THEN f.value END),
            SUM(CASE WHEN i.source='g.search.console' AND f.metric='clicks'           THEN f.value END),
            SUM(CASE WHEN i.source='g.search.console' AND f.metric='impressions'      THEN f.value END),
            AVG(CASE WHEN i.source='g.search.console' AND f.metric='position'         THEN f.value END),
            AVG(CASE WHEN i.source='g.search.console' AND f.metric='ctr'              THEN f.value END),
            SUM(CASE WHEN i.source='y.metrika'        AND f.metric='visits'           THEN f.value END),
            SUM(CASE WHEN i.source='y.metrika'        AND f.metric='users'            THEN f.value END),
            AVG(CASE WHEN i.source='y.metrika'        AND f.metric='bounce_rate'      THEN f.value END),
            AVG(CASE WHEN i.source='y.metrika'        AND f.metric='page_depth'       THEN f.value END),
            AVG(CASE WHEN i.source='y.metrika'        AND f.metric='time_on_site'     THEN f.value END),
            AVG(CASE WHEN i.source='topvisor'         AND f.metric='position_yandex'  THEN f.value END),
            AVG(CASE WHEN i.source='topvisor'         AND f.metric='position_google'  THEN f.value END),
            MAX(CASE WHEN i.source='topvisor'         AND f.metric='frequency_exact'  THEN f.value END),
            MAX(CASE WHEN i.source='topvisor'         AND f.metric='frequency_quoted' THEN f.value END),
            SUM(CASE WHEN i.source='y.webmaster'      AND f.metric='clicks'           THEN f.value END),
            SUM(CASE WHEN i.source='y.webmaster'      AND f.metric='impressions'      THEN f.value END),
            AVG(CASE WHEN i.source='y.webmaster'      AND f.metric='position'         THEN f.value END),
            AVG(CASE WHEN i.source='y.webmaster'      AND f.metric='ctr'              THEN f.value END),
            MAX(CASE WHEN i.source='y.webmaster'      AND f.metric='demand'           THEN f.value END)
        FROM query_facts f
        JOIN imports i ON f.import_id = i.id
        GROUP BY
            i.project, f.query, f.url,
            i.date_from, i.date_to, i.region, i.platform, f.traffic_source
    """)
