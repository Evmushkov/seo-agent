import uuid
import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from app.database import init_db, create_report, update_report, get_report, list_reports, delete_report
from app.crawler import discover_pages, fetch_page_content, classify_page
from app.analyzer import analyze_content
from app.ai import suggest_keywords, generate_recommendations, list_models, get_api_key

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        get_api_key()
        logger.info("OpenRouter API key configured")
    except ValueError as e:
        logger.warning(f"API not configured: {e}")
    yield


app = FastAPI(title="SEO Content Agent", lifespan=lifespan)


# ── Models ───────────────────────────────────────────────────────────
class AuditRequest(BaseModel):
    url: str
    max_pages: int = 12
    model: str = "claude-sonnet-4"


class AuditStatus(BaseModel):
    report_id: str
    status: str


# ── Background audit task ────────────────────────────────────────────
async def run_audit(report_id: str, base_url: str, max_pages: int, model: str):
    try:
        logger.info(f"[{report_id}] Starting audit: {base_url} (model: {model})")

        # Step 1: Discover pages
        await update_report(report_id, status="discovering")
        pages = await discover_pages(base_url, max_pages=max_pages)
        logger.info(f"[{report_id}] Discovered {len(pages)} pages")

        if not pages:
            await update_report(report_id, status="error",
                                summary={"error": "Не удалось найти страницы на сайте"})
            return

        # Step 2: Analyze each page
        await update_report(report_id, status="analyzing")
        results = []

        for i, page_info in enumerate(pages):
            try:
                logger.info(f"[{report_id}] Analyzing {i+1}/{len(pages)}: {page_info['url']}")

                # Fetch full page content
                content = await fetch_page_content(page_info["url"])

                # AI: suggest keywords
                keywords = await suggest_keywords(content, model=model)

                # Analyze word frequencies and content quality
                analysis = analyze_content(content, keywords)

                # AI: generate recommendations
                recommendations = await generate_recommendations(content, analysis, model=model)

                result = {
                    "url": page_info["url"],
                    "title": content.get("title", page_info.get("title", "")),
                    "type": page_info.get("type", ""),
                    "h1": content.get("h1", ""),
                    "meta_description": content.get("meta_description", ""),
                    "keywords": keywords,
                    "top_words": analysis["top_words"][:20],
                    "total_words": analysis["total_words"],
                    "tech_ratio": analysis["tech_ratio"],
                    "content_score": analysis["content_score"],
                    "keywords_found": analysis["keywords_found"],
                    "keywords_total": analysis["keywords_total"],
                    "keyword_presence": analysis["keyword_presence"],
                    "has_h1": analysis["has_h1"],
                    "has_meta_description": analysis["has_meta_description"],
                    "recommendations": recommendations,
                }
                results.append(result)

                # Save progress
                avg_score = round(sum(r["content_score"] for r in results) / len(results))
                await update_report(report_id, pages=results,
                                    summary={"progress": f"{i+1}/{len(pages)}", "avg_score": avg_score, "analyzed": len(results), "model": model})

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"[{report_id}] Error analyzing {page_info['url']}: {e}")
                results.append({
                    "url": page_info["url"],
                    "title": page_info.get("title", ""),
                    "type": page_info.get("type", ""),
                    "error": str(e),
                    "content_score": 0,
                })

        # Step 3: Final summary
        scored = [r for r in results if "error" not in r]
        avg = round(sum(r["content_score"] for r in scored) / len(scored)) if scored else 0
        good = len([r for r in scored if r["content_score"] >= 60])
        bad = len([r for r in scored if r["content_score"] < 30])

        summary = {
            "total_pages": len(results),
            "analyzed": len(scored),
            "errors": len(results) - len(scored),
            "avg_score": avg,
            "good_pages": good,
            "needs_work": bad,
            "model": model,
        }

        await update_report(report_id, status="done", summary=summary, pages=results)
        logger.info(f"[{report_id}] Audit complete. Avg score: {avg}")

    except Exception as e:
        logger.error(f"[{report_id}] Audit failed: {e}")
        await update_report(report_id, status="error", summary={"error": str(e)})


# ── API Routes ───────────────────────────────────────────────────────
@app.post("/api/audit", response_model=AuditStatus)
async def start_audit(req: AuditRequest, bg: BackgroundTasks):
    parsed = urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(400, "Invalid URL")

    domain = parsed.netloc
    report_id = str(uuid.uuid4())[:8]

    await create_report(report_id, domain, req.url)
    bg.add_task(run_audit, report_id, req.url, req.max_pages, req.model)

    return AuditStatus(report_id=report_id, status="started")


@app.get("/api/report/{report_id}")
async def get_report_api(report_id: str):
    report = await get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@app.get("/api/reports")
async def list_reports_api(domain: str = None):
    return await list_reports(domain)


@app.delete("/api/report/{report_id}")
async def delete_report_api(report_id: str):
    await delete_report(report_id)
    return {"ok": True}


@app.get("/api/models")
async def get_models():
    return list_models()


# ── Serve frontend ──────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def index():
    return FileResponse("frontend/index.html")
