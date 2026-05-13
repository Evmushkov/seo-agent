import csv
import io
import logging
from app.importers.base import BaseImporter
from app.db import get_conn

logger = logging.getLogger(__name__)

BATCH_SIZE = 10000


class DirectImporter(BaseImporter):
    source = "direct"

    def import_file(self, filepath: str) -> dict:
        conn = get_conn()
        total = 0
        batch = []

        with open(filepath, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                query = (row.get("Поисковый запрос") or "").strip()
                if not query or query in ("Итого", ""):
                    continue
                try:
                    shows = int(row.get("Показы", 0) or 0)
                    clicks = int(row.get("Клики", 0) or 0)
                    spend_raw = (row.get("Расход, ₽") or row.get("Расход, \u20bd") or "0").replace(",", ".").replace(" ", "")
                    spend = float(spend_raw) if spend_raw else 0
                    conversions = int(row.get("Конверсии", 0) or 0)
                    cr_raw = (row.get("CR, %") or "0").replace(",", ".").replace(" ", "")
                    cr = float(cr_raw) / 100 if float(cr_raw) > 1 else float(cr_raw)
                    rev_raw = (row.get("Доход, ₽") or row.get("Доход, \u20bd") or "0").replace(",", ".").replace(" ", "")
                    revenue = float(rev_raw) if rev_raw else 0
                    cpa_raw = (row.get("CPA, ₽") or row.get("CPA, \u20bd") or "0").replace(",", ".").replace(" ", "")
                    cpa = float(cpa_raw) if cpa_raw else 0
                    bounce_raw = (row.get("Отказы, %") or "0").replace(",", ".").replace(" ", "")
                    bounce = float(bounce_raw) / 100 if float(bounce_raw) > 1 else float(bounce_raw)
                    depth_raw = (row.get("Глубина просмотра") or "0").replace(",", ".")
                    depth = float(depth_raw) if depth_raw else 0

                    batch.append((
                        self.upload_id, self.source, query,
                        self.region, self.platform, "yandex",
                        shows, clicks,
                        clicks / shows if shows > 0 else 0,
                        0,  # position not applicable for direct
                        0,  # demand
                        spend, conversions, cr, revenue, cpa, bounce, depth
                    ))

                    if len(batch) >= BATCH_SIZE:
                        conn.executemany(
                            "INSERT INTO search_queries (upload_id,source,query,region,platform,search_engine,shows,clicks,ctr,position,demand,spend,conversions,cr,revenue,cpa,bounce_rate,depth) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            batch
                        )
                        total += len(batch)
                        batch = []
                        if total % 50000 == 0:
                            logger.info(f"[direct] Imported {total} rows...")

                except (ValueError, TypeError) as e:
                    continue

        if batch:
            conn.executemany(
                "INSERT INTO search_queries (upload_id,source,query,region,platform,search_engine,shows,clicks,ctr,position,demand,spend,conversions,cr,revenue,cpa,bounce_rate,depth) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                batch
            )
            total += len(batch)

        self.register_upload(filepath.split("/")[-1], total)
        return {"rows": total}
