import os
import json
import glob
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from app.db import init_db, query, query_df, execute, get_conn
from app.tagger import init_default_tags, compute_all_tags
from app.importers.gsc import GSCImporter
from app.importers.webmaster import WebmasterImporter
from app.importers.metrika import MetrikaImporter
from app.importers.topvisor import TopVisorImporter
from app.importers.direct import DirectImporter

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_default_tags()
    # Auto-import in background thread (don't block startup)
    import threading
    t = threading.Thread(target=auto_import_data, daemon=True)
    t.start()
    yield


app = FastAPI(title="SEO Content Agent v2", lifespan=lifespan)


# ── Auto-import from data/ directory ─────────────────────────────────
def auto_import_data():
    data_dir = os.getenv("DATA_DIR", "data")
    if not os.path.exists(data_dir):
        logger.info(f"No data/ directory found at {data_dir}")
        return

    # Check which files are already imported
    existing = {r[0] for r in query("SELECT filename FROM uploads")}

    imported = 0
    for subdir, importer_class, kwargs in [
        ("gsc", GSCImporter, {}),
        ("webmaster", WebmasterImporter, {}),
        ("metrika", MetrikaImporter, {}),
        ("topvisor", TopVisorImporter, {}),
        ("direct", DirectImporter, {}),
    ]:
        path = os.path.join(data_dir, subdir)
        if not os.path.exists(path):
            continue
        for f in sorted(glob.glob(os.path.join(path, "*"))):
            fname = os.path.basename(f)
            if fname in existing or fname.startswith("."):
                continue
            # Skip irrelevant GSC files
            if subdir == "gsc" and fname not in ("Queries.csv", "Pages.csv") and not fname.endswith(".zip"):
                continue
            try:
                # Detect region/platform from filename
                kw = _detect_meta_from_filename(fname, subdir)
                imp = importer_class(**{**kwargs, **kw})
                result = imp.import_file(f)
                logger.info(f"Auto-imported {fname}: {result}")
                imported += 1
            except Exception as e:
                logger.error(f"Failed to import {fname}: {e}")

    if imported:
        logger.info(f"Auto-imported {imported} files. Computing tags...")
        compute_all_tags()
    else:
        logger.info("No new files to import")


def _detect_meta_from_filename(fname: str, source: str) -> dict:
    fname_lower = fname.lower()
    meta = {}
    if "moscow" in fname_lower or "msk" in fname_lower or "москв" in fname_lower:
        meta["region"] = "moscow"
    else:
        meta["region"] = "russia"
    if "desktop" in fname_lower:
        meta["platform"] = "desktop"
    else:
        meta["platform"] = "mobile"
    if "google" in fname_lower:
        meta["search_engine"] = "google"
    elif "yandex" in fname_lower or source in ("webmaster", "direct"):
        meta["search_engine"] = "yandex"
    # Webmaster granularity
    if source == "webmaster":
        if "daily" in fname_lower or "day" in fname_lower or "дн" in fname_lower:
            meta["granularity"] = "daily"
        else:
            meta["granularity"] = "monthly"
    return meta


# ── Models ───────────────────────────────────────────────────────────
class TagCreate(BaseModel):
    name: str
    icon: str = "🏷️"
    description: str = ""
    formula: dict
    sort_order: int = 0


# ── API: Sources & Import ────────────────────────────────────────────
@app.get("/api/sources")
async def get_sources():
    rows = query("""
        SELECT source, COUNT(*) as uploads, SUM(rows_imported) as total_rows, MAX(uploaded_at) as last_update
        FROM uploads GROUP BY source ORDER BY source
    """)
    sources = {}
    for src, count, total, last in rows:
        sources[src] = {"uploads": count, "total_rows": total, "last_update": str(last)}

    # Total counts per table
    sq_count = query("SELECT COUNT(*) FROM search_queries")[0][0]
    pm_count = query("SELECT COUNT(*) FROM page_metrics")[0][0]
    pt_count = query("SELECT COUNT(*) FROM position_tracking")[0][0]

    return {
        "sources": sources,
        "totals": {"search_queries": sq_count, "page_metrics": pm_count, "position_tracking": pt_count}
    }


@app.post("/api/import/upload")
async def upload_file(file: UploadFile = File(...), source: str = Form(...),
                       region: str = Form("russia"), platform: str = Form("mobile"),
                       search_engine: str = Form("all")):
    # Save file temporarily
    tmp_path = f"/tmp/{file.filename}"
    with open(tmp_path, "wb") as f:
        f.write(await file.read())

    try:
        importers = {
            "gsc": GSCImporter,
            "webmaster": WebmasterImporter,
            "metrika": MetrikaImporter,
            "topvisor": TopVisorImporter,
            "direct": DirectImporter,
        }
        cls = importers.get(source)
        if not cls:
            raise HTTPException(400, f"Unknown source: {source}")

        kwargs = {"region": region, "platform": platform}
        if source not in ("metrika",):
            kwargs["search_engine"] = search_engine
        if source == "webmaster":
            kwargs["granularity"] = "daily" if "daily" in file.filename.lower() else "monthly"

        imp = cls(**kwargs)
        result = imp.import_file(tmp_path)

        # Recompute tags after new data
        tag_count = compute_all_tags()
        result["tags_computed"] = tag_count

        return result
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        os.remove(tmp_path)


@app.post("/api/import/recompute-tags")
async def recompute_tags():
    count = compute_all_tags()
    return {"tags_computed": count}


# ── API: Queries ─────────────────────────────────────────────────────
@app.get("/api/queries")
async def get_queries(region: str = None, platform: str = None, search_engine: str = None,
                       tag: str = None, limit: int = 100, offset: int = 0,
                       sort: str = "clicks", order: str = "desc"):
    where = ["sq.source IN ('webmaster','gsc')"]
    params = []
    if region:
        where.append("sq.region = ?")
        params.append(region)
    if platform:
        where.append("sq.platform = ?")
        params.append(platform)
    if search_engine:
        where.append("sq.search_engine = ?")
        params.append(search_engine)

    where_str = " AND ".join(where)
    safe_sort = sort if sort in ("clicks", "shows", "position", "ctr", "demand") else "clicks"
    safe_order = "DESC" if order == "desc" else "ASC"

    if tag:
        sql = f"""
            SELECT sq.query, sq.url,
                   SUM(sq.clicks) as clicks, SUM(sq.shows) as shows,
                   AVG(sq.position) as position, AVG(sq.ctr) as ctr,
                   MAX(sq.demand) as demand
            FROM search_queries sq
            INNER JOIN query_tags qt ON sq.query = qt.query AND qt.tag_id = ?
            WHERE {where_str}
            GROUP BY sq.query, sq.url
            ORDER BY {safe_sort} {safe_order}
            LIMIT ? OFFSET ?
        """
        params = [tag] + params + [limit, offset]
    else:
        sql = f"""
            SELECT sq.query, sq.url,
                   SUM(sq.clicks) as clicks, SUM(sq.shows) as shows,
                   AVG(sq.position) as position, AVG(sq.ctr) as ctr,
                   MAX(sq.demand) as demand
            FROM search_queries sq
            WHERE {where_str}
            GROUP BY sq.query, sq.url
            ORDER BY {safe_sort} {safe_order}
            LIMIT ? OFFSET ?
        """
        params = params + [limit, offset]

    rows = query(sql, params)

    # Get tags for these queries
    queries_list = list(set(r[0] for r in rows))
    tag_map = {}
    if queries_list:
        placeholders = ",".join(["?" for _ in queries_list])
        tag_rows = query(f"""
            SELECT qt.query, t.id, t.name, t.icon
            FROM query_tags qt JOIN tags t ON qt.tag_id = t.id
            WHERE qt.query IN ({placeholders})
        """, queries_list)
        for q, tid, tname, ticon in tag_rows:
            tag_map.setdefault(q, []).append({"id": tid, "name": tname, "icon": ticon})

    results = []
    for q, url, clicks, shows, pos, ctr, demand in rows:
        results.append({
            "query": q, "url": url,
            "clicks": clicks, "shows": shows,
            "position": round(pos, 1) if pos else 0,
            "ctr": round(ctr, 4) if ctr else 0,
            "demand": demand or 0,
            "tags": tag_map.get(q, []),
        })

    # Total count
    count_sql = f"SELECT COUNT(DISTINCT query) FROM search_queries sq WHERE {' AND '.join(where[:len(where)])}"
    total = query(count_sql, params[:len(where)-1] if params else [])[0][0]

    return {"queries": results, "total": total, "limit": limit, "offset": offset}


# ── API: Cannibalization ─────────────────────────────────────────────
@app.get("/api/cannibalization")
async def get_cannibalization(limit: int = 50):
    rows = query("""
        SELECT query, url, SUM(clicks) as clicks, AVG(position) as pos
        FROM search_queries
        WHERE source = 'webmaster' AND url IS NOT NULL AND url != ''
          AND query IN (
            SELECT query FROM search_queries WHERE source = 'webmaster' AND url IS NOT NULL AND url != ''
            GROUP BY query HAVING COUNT(DISTINCT url) >= 2
          )
        GROUP BY query, url
        ORDER BY query, clicks DESC
        LIMIT ?
    """, [limit * 5])

    groups = {}
    for q, url, clicks, pos in rows:
        groups.setdefault(q, []).append({"url": url, "clicks": clicks, "position": round(pos, 1)})

    result = [{"query": q, "urls": urls} for q, urls in list(groups.items())[:limit]]
    return {"cannibalization": result, "total": len(groups)}


# ── API: Tags ────────────────────────────────────────────────────────
@app.get("/api/tags")
async def get_tags():
    rows = query("SELECT id, name, icon, description, formula, sort_order, is_active FROM tags ORDER BY sort_order")
    tags = []
    for tid, name, icon, desc, formula, order, active in rows:
        count = query("SELECT COUNT(*) FROM query_tags WHERE tag_id = ?", [tid])[0][0]
        tags.append({
            "id": tid, "name": name, "icon": icon, "description": desc,
            "formula": json.loads(formula), "sort_order": order,
            "is_active": active, "query_count": count,
        })
    return tags


@app.post("/api/tags")
async def create_tag(tag: TagCreate):
    import uuid
    tid = str(uuid.uuid4())[:8]
    execute(
        "INSERT INTO tags (id,name,icon,description,formula,sort_order) VALUES (?,?,?,?,?,?)",
        [tid, tag.name, tag.icon, tag.description, json.dumps(tag.formula), tag.sort_order]
    )
    compute_all_tags()
    return {"id": tid}


@app.delete("/api/tags/{tag_id}")
async def delete_tag(tag_id: str):
    execute("DELETE FROM query_tags WHERE tag_id = ?", [tag_id])
    execute("DELETE FROM tags WHERE id = ?", [tag_id])
    return {"ok": True}


# ── API: Dashboard stats ─────────────────────────────────────────────
@app.get("/api/dashboard")
async def dashboard():
    sources = query("""
        SELECT source, SUM(rows_imported) as rows, MAX(uploaded_at) as last
        FROM uploads GROUP BY source
    """)

    tag_stats = query("""
        SELECT t.id, t.name, t.icon, COUNT(qt.query) as cnt
        FROM tags t LEFT JOIN query_tags qt ON t.id = qt.tag_id
        WHERE t.is_active = true
        GROUP BY t.id, t.name, t.icon
        ORDER BY t.sort_order
    """)

    total_queries = query("SELECT COUNT(DISTINCT query) FROM search_queries")[0][0]
    total_pages = query("SELECT COUNT(DISTINCT url) FROM search_queries WHERE url IS NOT NULL AND url != ''")[0][0]

    top_queries = query("""
        SELECT query, SUM(clicks) as clicks, AVG(position) as pos
        FROM search_queries WHERE source IN ('webmaster','gsc')
        GROUP BY query ORDER BY clicks DESC LIMIT 10
    """)

    return {
        "sources": [{"source": s, "rows": r, "last_update": str(l)} for s, r, l in sources],
        "tags": [{"id": i, "name": n, "icon": ic, "count": c} for i, n, ic, c in tag_stats],
        "total_queries": total_queries,
        "total_pages": total_pages,
        "top_queries": [{"query": q, "clicks": c, "position": round(p, 1)} for q, c, p in top_queries],
    }


# ── Serve frontend ──────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def index():
    return FileResponse("frontend/index.html")
