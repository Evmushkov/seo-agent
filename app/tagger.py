import json
import logging
from app.db import execute, query, query_df, get_conn

logger = logging.getLogger(__name__)

DEFAULT_TAGS = [
    {
        "id": "top3", "name": "Топ-3", "icon": "🏆",
        "description": "Средняя позиция ≤ 3 в Яндексе (Вебмастер)",
        "formula": {"conditions": [{"source": "webmaster", "metric": "position", "aggregation": "avg", "operator": "<=", "value": 3}]},
        "sort_order": 1
    },
    {
        "id": "almost_top", "name": "Почти топ", "icon": "🎯",
        "description": "Средняя позиция 4-10 — низко висящие фрукты",
        "formula": {"conditions": [
            {"source": "webmaster", "metric": "position", "aggregation": "avg", "operator": ">=", "value": 4},
            {"logic": "AND", "source": "webmaster", "metric": "position", "aggregation": "avg", "operator": "<=", "value": 10}
        ]},
        "sort_order": 2
    },
    {
        "id": "potential", "name": "Потенциал", "icon": "📈",
        "description": "Средняя позиция 11-50 — нужна работа",
        "formula": {"conditions": [
            {"source": "webmaster", "metric": "position", "aggregation": "avg", "operator": ">=", "value": 11},
            {"logic": "AND", "source": "webmaster", "metric": "position", "aggregation": "avg", "operator": "<=", "value": 50}
        ]},
        "sort_order": 3
    },
    {
        "id": "invisible", "name": "Невидимка", "icon": "👻",
        "description": "Средняя позиция > 50 или нет в выдаче",
        "formula": {"conditions": [{"source": "webmaster", "metric": "position", "aggregation": "avg", "operator": ">", "value": 50}]},
        "sort_order": 4
    },
    {
        "id": "high_demand", "name": "Высокий спрос", "icon": "🔥",
        "description": "Частотность (Вордстат) ≥ 500",
        "formula": {"conditions": [{"source": "topvisor", "metric": "frequency_exact", "aggregation": "max", "operator": ">=", "value": 500}]},
        "sort_order": 5
    },
    {
        "id": "low_ctr", "name": "Низкий CTR", "icon": "👀",
        "description": "Много показов, мало кликов — проблема сниппета",
        "formula": {"conditions": [
            {"source": "webmaster", "metric": "shows", "aggregation": "sum", "operator": ">=", "value": 100},
            {"logic": "AND", "source": "webmaster", "metric": "ctr", "aggregation": "avg", "operator": "<=", "value": 0.02}
        ]},
        "sort_order": 6
    },
    {
        "id": "ad_converter", "name": "Рекл. конвертер", "icon": "💎",
        "description": "Конвертирует в Директе (CR ≥ 5%, ≥ 3 конверсии)",
        "formula": {"conditions": [
            {"source": "direct", "metric": "cr", "aggregation": "avg", "operator": ">=", "value": 0.05},
            {"logic": "AND", "source": "direct", "metric": "conversions", "aggregation": "sum", "operator": ">=", "value": 3}
        ]},
        "sort_order": 7
    },
    {
        "id": "cannibal", "name": "Каннибал", "icon": "⚔️",
        "description": "2+ страницы конкурируют по одному запросу",
        "formula": {"conditions": [{"source": "webmaster", "metric": "cannibalization_count", "aggregation": "count", "operator": ">=", "value": 2}]},
        "sort_order": 8
    },
]


def init_default_tags():
    existing = query("SELECT id FROM tags")
    existing_ids = {r[0] for r in existing}
    for tag in DEFAULT_TAGS:
        if tag["id"] not in existing_ids:
            execute(
                "INSERT INTO tags (id,name,icon,description,formula,sort_order) VALUES (?,?,?,?,?,?)",
                [tag["id"], tag["name"], tag["icon"], tag["description"], json.dumps(tag["formula"]), tag["sort_order"]]
            )
    logger.info(f"Tags initialized: {len(DEFAULT_TAGS)} defaults")


def compute_all_tags():
    """Recompute all active tags."""
    execute("DELETE FROM query_tags")
    tags = query("SELECT id, formula FROM tags WHERE is_active = true ORDER BY sort_order")

    total = 0
    for tag_id, formula_str in tags:
        formula = json.loads(formula_str)
        count = _compute_tag(tag_id, formula)
        total += count
        logger.info(f"Tag '{tag_id}': {count} matches")

    return total


def _compute_tag(tag_id: str, formula: dict) -> int:
    conditions = formula.get("conditions", [])
    if not conditions:
        return 0

    # Handle special computed metrics
    first = conditions[0]
    if first.get("metric") == "cannibalization_count":
        return _compute_cannibalization(tag_id)

    # Build SQL for standard metrics
    sql_parts = []
    for cond in conditions:
        source = cond.get("source", "webmaster")
        metric = cond.get("metric", "position")
        agg = cond.get("aggregation", "avg")
        operator = cond.get("operator", ">=")
        value = cond.get("value", 0)

        if source in ("webmaster", "gsc", "direct"):
            table = "search_queries"
            agg_expr = f"{agg}({metric})"
            sql = f"SELECT query, url FROM {table} WHERE source = '{source}' AND {metric} > 0 GROUP BY query, url HAVING {agg_expr} {operator} {value}"
        elif source == "topvisor":
            table = "position_tracking"
            if metric == "frequency_exact":
                sql = f"SELECT query, NULL as url FROM {table} WHERE frequency_exact IS NOT NULL GROUP BY query HAVING {agg}(frequency_exact) {operator} {value}"
            elif metric == "frequency_phrase":
                sql = f"SELECT query, NULL as url FROM {table} WHERE frequency_phrase IS NOT NULL GROUP BY query HAVING {agg}(frequency_phrase) {operator} {value}"
            else:
                sql = f"SELECT query, NULL as url FROM {table} WHERE position IS NOT NULL GROUP BY query HAVING {agg}(position) {operator} {value}"
        else:
            continue

        sql_parts.append(sql)

    if not sql_parts:
        return 0

    # Combine with AND/OR
    if len(sql_parts) == 1:
        final_sql = sql_parts[0]
    else:
        # Default: AND (intersect)
        logic = conditions[1].get("logic", "AND") if len(conditions) > 1 else "AND"
        if logic == "AND":
            final_sql = f"SELECT a.query, a.url FROM ({sql_parts[0]}) a INNER JOIN ({sql_parts[1]}) b ON a.query = b.query"
            for i, extra in enumerate(sql_parts[2:], 2):
                alias = chr(ord('a') + i)
                final_sql = f"SELECT a.query, a.url FROM ({final_sql}) a INNER JOIN ({extra}) {alias} ON a.query = {alias}.query"
        else:
            final_sql = " UNION ".join(sql_parts)

    try:
        conn = get_conn()
        results = conn.execute(final_sql).fetchall()
        if results:
            conn.executemany(
                "INSERT INTO query_tags (query, url, tag_id, source) VALUES (?, ?, ?, ?)",
                [(r[0], r[1], tag_id, "computed") for r in results]
            )
        return len(results)
    except Exception as e:
        logger.error(f"Tag {tag_id} computation failed: {e}")
        return 0


def _compute_cannibalization(tag_id: str) -> int:
    """Find queries that rank with 2+ different URLs."""
    sql = """
        SELECT query, COUNT(DISTINCT url) as url_count
        FROM search_queries
        WHERE source = 'webmaster' AND url IS NOT NULL AND url != ''
        GROUP BY query
        HAVING COUNT(DISTINCT url) >= 2
    """
    try:
        conn = get_conn()
        results = conn.execute(sql).fetchall()
        if results:
            conn.executemany(
                "INSERT INTO query_tags (query, url, tag_id, source, meta) VALUES (?, NULL, ?, 'computed', ?)",
                [(r[0], tag_id, json.dumps({"url_count": r[1]})) for r in results]
            )
        return len(results)
    except Exception as e:
        logger.error(f"Cannibalization computation failed: {e}")
        return 0
