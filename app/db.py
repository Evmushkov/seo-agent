import duckdb
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "/data/seo.duckdb")
_conn = None


def get_conn() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = duckdb.connect(DB_PATH)
        logger.info(f"DuckDB connected: {DB_PATH}")
    return _conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id              VARCHAR PRIMARY KEY,
            source          VARCHAR NOT NULL,
            region          VARCHAR DEFAULT 'russia',
            platform        VARCHAR DEFAULT 'mobile',
            search_engine   VARCHAR DEFAULT 'all',
            period_start    DATE,
            period_end      DATE,
            granularity     VARCHAR,
            filename        VARCHAR,
            rows_imported   INTEGER DEFAULT 0,
            uploaded_at     TIMESTAMP DEFAULT now()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_queries (
            upload_id       VARCHAR,
            source          VARCHAR NOT NULL,
            date            DATE,
            query           VARCHAR,
            url             VARCHAR,
            region          VARCHAR,
            platform        VARCHAR,
            search_engine   VARCHAR,
            shows           INTEGER DEFAULT 0,
            clicks          INTEGER DEFAULT 0,
            ctr             DOUBLE DEFAULT 0,
            position        DOUBLE DEFAULT 0,
            demand          INTEGER DEFAULT 0,
            spend           DOUBLE DEFAULT 0,
            conversions     INTEGER DEFAULT 0,
            cr              DOUBLE DEFAULT 0,
            revenue         DOUBLE DEFAULT 0,
            cpa             DOUBLE DEFAULT 0,
            bounce_rate     DOUBLE DEFAULT 0,
            depth           DOUBLE DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS page_metrics (
            upload_id       VARCHAR,
            source          VARCHAR NOT NULL,
            url             VARCHAR NOT NULL,
            traffic_source  VARCHAR,
            search_phrase   VARCHAR,
            visits          INTEGER DEFAULT 0,
            visitors        INTEGER DEFAULT 0,
            bounce_rate     DOUBLE DEFAULT 0,
            depth           DOUBLE DEFAULT 0,
            time_on_site    VARCHAR,
            goal_completions INTEGER DEFAULT 0,
            revenue         DOUBLE DEFAULT 0,
            conversion      DOUBLE DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_tracking (
            upload_id       VARCHAR,
            query           VARCHAR NOT NULL,
            search_engine   VARCHAR NOT NULL,
            region          VARCHAR,
            platform        VARCHAR,
            date            DATE NOT NULL,
            position        INTEGER,
            frequency_exact INTEGER,
            frequency_phrase INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id              VARCHAR PRIMARY KEY,
            name            VARCHAR NOT NULL,
            icon            VARCHAR DEFAULT '🏷️',
            description     VARCHAR,
            formula         VARCHAR NOT NULL,
            sort_order      INTEGER DEFAULT 0,
            is_active       BOOLEAN DEFAULT true,
            created_at      TIMESTAMP DEFAULT now()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_tags (
            query           VARCHAR NOT NULL,
            url             VARCHAR,
            tag_id          VARCHAR,
            source          VARCHAR,
            computed_at     TIMESTAMP DEFAULT now(),
            meta            VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_reports (
            id              VARCHAR PRIMARY KEY,
            created_at      TIMESTAMP DEFAULT now(),
            domain          VARCHAR,
            url             VARCHAR,
            status          VARCHAR DEFAULT 'running',
            summary         VARCHAR DEFAULT '{}',
            pages           VARCHAR DEFAULT '[]',
            model           VARCHAR
        )
    """)
    logger.info("DuckDB schema initialized")


def query(sql: str, params=None):
    conn = get_conn()
    if params:
        return conn.execute(sql, params).fetchall()
    return conn.execute(sql).fetchall()


def query_df(sql: str, params=None):
    conn = get_conn()
    if params:
        return conn.execute(sql, params).fetchdf()
    return conn.execute(sql).fetchdf()


def execute(sql: str, params=None):
    conn = get_conn()
    if params:
        conn.execute(sql, params)
    else:
        conn.execute(sql)
