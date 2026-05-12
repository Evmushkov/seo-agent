import aiosqlite
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/data/seo_agent.db")


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                domain TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                summary TEXT DEFAULT '{}',
                pages TEXT DEFAULT '[]'
            )
        """)
        await db.commit()


async def create_report(report_id: str, domain: str, url: str) -> dict:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reports (id, created_at, domain, url, status) VALUES (?, ?, ?, ?, ?)",
            (report_id, now, domain, url, "running"),
        )
        await db.commit()
    return {"id": report_id, "created_at": now, "domain": domain, "url": url, "status": "running"}


async def update_report(report_id: str, status: str = None, summary: dict = None, pages: list = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            await db.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))
        if summary is not None:
            await db.execute("UPDATE reports SET summary = ? WHERE id = ?", (json.dumps(summary, ensure_ascii=False), report_id))
        if pages is not None:
            await db.execute("UPDATE reports SET pages = ? WHERE id = ?", (json.dumps(pages, ensure_ascii=False), report_id))
        await db.commit()


async def get_report(report_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "created_at": row["created_at"],
                "domain": row["domain"],
                "url": row["url"],
                "status": row["status"],
                "summary": json.loads(row["summary"]),
                "pages": json.loads(row["pages"]),
            }


async def list_reports(domain: str = None) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if domain:
            query = "SELECT id, created_at, domain, url, status, summary FROM reports WHERE domain = ? ORDER BY created_at DESC"
            params = (domain,)
        else:
            query = "SELECT id, created_at, domain, url, status, summary FROM reports ORDER BY created_at DESC LIMIT 50"
            params = ()
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "domain": r["domain"],
                    "url": r["url"],
                    "status": r["status"],
                    "summary": json.loads(r["summary"]),
                }
                for r in rows
            ]


async def delete_report(report_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        await db.commit()
