"""Idempotent schema creation for v2 (raw long + aggregate wide).

Usage:
    python3 scripts/create_schema_v2.py [db_path]

Default db_path: value of DATABASE_PATH env var, or ./tempus.duckdb.
The legacy 'queries' table is never touched.
"""

import os
import sys
from pathlib import Path

import duckdb

_DDL = """
CREATE SEQUENCE IF NOT EXISTS seq_imports_id;
CREATE SEQUENCE IF NOT EXISTS seq_query_facts_id;
CREATE SEQUENCE IF NOT EXISTS seq_gsc_app_id;
CREATE SEQUENCE IF NOT EXISTS seq_gsc_dt_id;

-- ── imports ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY DEFAULT nextval('seq_imports_id'),
    project      TEXT NOT NULL,
    domain       TEXT NOT NULL,
    source       TEXT NOT NULL,
    region       TEXT NOT NULL,
    platform     TEXT NOT NULL,
    date_from    DATE NOT NULL,
    date_to      DATE NOT NULL,
    folder_path  TEXT NOT NULL,
    file_hash    TEXT,
    imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count    INTEGER,
    filters_json JSON,

    CHECK (source   IN ('y.direct','g.search.console','y.metrika','topvisor','y.webmaster')),
    CHECK (region   IN ('moscow','moscow.district','russia')),
    CHECK (platform IN ('desktop','mobile','tablet')),
    CHECK (date_from <= date_to)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_imports
    ON imports (project, source, region, platform, date_from, date_to, file_hash);

-- ── query_facts ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_facts (
    id             INTEGER PRIMARY KEY DEFAULT nextval('seq_query_facts_id'),
    import_id      INTEGER NOT NULL,
    query          TEXT,
    url            TEXT,
    date           DATE,
    traffic_source TEXT,
    metric         TEXT NOT NULL,
    value          DOUBLE PRECISION,

    CHECK (query IS NOT NULL OR url IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_qf_import ON query_facts(import_id);
CREATE INDEX IF NOT EXISTS idx_qf_query  ON query_facts(query);
CREATE INDEX IF NOT EXISTS idx_qf_url    ON query_facts(url);
CREATE INDEX IF NOT EXISTS idx_qf_date   ON query_facts(date);
CREATE INDEX IF NOT EXISTS idx_qf_metric ON query_facts(metric);

-- ── gsc_facts_by_appearance ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gsc_facts_by_appearance (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_gsc_app_id'),
    import_id       INTEGER NOT NULL,
    date            DATE,
    appearance_type TEXT NOT NULL,
    metric          TEXT NOT NULL,
    value           DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_gsc_app_import ON gsc_facts_by_appearance(import_id);
CREATE INDEX IF NOT EXISTS idx_gsc_app_type   ON gsc_facts_by_appearance(appearance_type);

-- ── gsc_daily_totals ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gsc_daily_totals (
    id        INTEGER PRIMARY KEY DEFAULT nextval('seq_gsc_dt_id'),
    import_id INTEGER NOT NULL,
    date      DATE NOT NULL,
    metric    TEXT NOT NULL,
    value     DOUBLE PRECISION
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gsc_dt
    ON gsc_daily_totals (import_id, date, metric);

CREATE INDEX IF NOT EXISTS idx_gsc_dt_import ON gsc_daily_totals(import_id);
CREATE INDEX IF NOT EXISTS idx_gsc_dt_date   ON gsc_daily_totals(date);

-- ── query_unified ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_unified (
    project                   TEXT NOT NULL,
    query                     TEXT,
    url                       TEXT,
    period_from               DATE NOT NULL,
    period_to                 DATE NOT NULL,
    region                    TEXT NOT NULL,
    platform                  TEXT NOT NULL,
    traffic_source            TEXT,

    -- y.direct
    direct_clicks             DOUBLE PRECISION,
    direct_impressions        DOUBLE PRECISION,
    direct_cost               DOUBLE PRECISION,
    direct_ctr                DOUBLE PRECISION,
    direct_position_show      DOUBLE PRECISION,
    direct_conversions        DOUBLE PRECISION,
    direct_revenue            DOUBLE PRECISION,

    -- g.search.console
    gsc_clicks                DOUBLE PRECISION,
    gsc_impressions           DOUBLE PRECISION,
    gsc_position              DOUBLE PRECISION,
    gsc_ctr                   DOUBLE PRECISION,

    -- y.metrika
    ymetrika_visits           DOUBLE PRECISION,
    ymetrika_users            DOUBLE PRECISION,
    ymetrika_bounce_rate      DOUBLE PRECISION,
    ymetrika_page_depth       DOUBLE PRECISION,
    ymetrika_time_on_site     DOUBLE PRECISION,

    -- topvisor
    topvisor_position_yandex  DOUBLE PRECISION,
    topvisor_position_google  DOUBLE PRECISION,
    topvisor_freq_exact       DOUBLE PRECISION,
    topvisor_freq_quoted      DOUBLE PRECISION,

    -- y.webmaster
    ywebmaster_clicks         DOUBLE PRECISION,
    ywebmaster_impressions    DOUBLE PRECISION,
    ywebmaster_position       DOUBLE PRECISION,
    ywebmaster_ctr            DOUBLE PRECISION,
    ywebmaster_demand         DOUBLE PRECISION,

    -- NULL-safe surrogate key: query/url/traffic_source могут быть NULL,
    -- поэтому уникальность контролируется индексом, а не PK.
    UNIQUE (project, query, url, period_from, period_to, region, platform, traffic_source)
);
"""


def create_schema(db_path: str) -> None:
    print(f"БД: {db_path}")
    con = duckdb.connect(db_path)
    for stmt in _DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)
    print("Схема создана (идемпотентно).\n")

    tables = [
        "imports",
        "query_facts",
        "gsc_facts_by_appearance",
        "gsc_daily_totals",
        "query_unified",
    ]
    for t in tables:
        print(f"── DESCRIBE {t} ──")
        print(con.execute(f"DESCRIBE {t}").df().to_string(index=False))
        print()

    con.close()


if __name__ == "__main__":
    db_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("DATABASE_PATH", "./tempus.duckdb")
    )
    create_schema(db_path)
