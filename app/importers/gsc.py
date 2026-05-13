import csv
import io
import zipfile
import logging
from app.importers.base import BaseImporter
from app.db import get_conn

logger = logging.getLogger(__name__)


class GSCImporter(BaseImporter):
    source = "gsc"

    def import_file(self, filepath: str) -> dict:
        queries, pages = [], []

        with open(filepath, "rb") as f:
            content = f.read()

        if filepath.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for name in zf.namelist():
                    text = zf.read(name).decode("utf-8-sig")
                    if "Queries" in name or "queries" in name:
                        queries = self._parse_queries(text)
                    elif "Pages" in name or "pages" in name:
                        pages = self._parse_pages(text)
        elif filepath.endswith(".csv"):
            text = content.decode("utf-8-sig")
            if "Top queries" in text or "Query" in text:
                queries = self._parse_queries(text)
            elif "Top pages" in text or "Page" in text:
                pages = self._parse_pages(text)

        conn = get_conn()
        total = 0

        if queries:
            conn.executemany(
                "INSERT INTO search_queries (upload_id,source,query,region,platform,search_engine,clicks,shows,ctr,position) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(self.upload_id, self.source, q["query"], self.region, self.platform, "google", q["clicks"], q["impressions"], q["ctr"], q["position"]) for q in queries]
            )
            total += len(queries)

        if pages:
            conn.executemany(
                "INSERT INTO search_queries (upload_id,source,query,url,region,platform,search_engine,clicks,shows,ctr,position) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(self.upload_id, self.source, p["page"], p["page"], self.region, self.platform, "google", p["clicks"], p["impressions"], p["ctr"], p["position"]) for p in pages]
            )
            total += len(pages)

        self.register_upload(filepath.split("/")[-1], total)
        return {"queries": len(queries), "pages": len(pages)}

    def _parse_queries(self, text):
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                ctr_raw = row.get("CTR", "0").replace("%", "").replace(",", ".")
                ctr_val = float(ctr_raw)
                rows.append({
                    "query": row.get("Top queries", row.get("Query", "")),
                    "clicks": int(row.get("Clicks", 0)),
                    "impressions": int(row.get("Impressions", 0)),
                    "ctr": round(ctr_val / 100 if ctr_val > 1 else ctr_val, 4),
                    "position": round(float(row.get("Position", "0").replace(",", ".")), 1),
                })
            except (ValueError, TypeError):
                continue
        return rows

    def _parse_pages(self, text):
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                ctr_raw = row.get("CTR", "0").replace("%", "").replace(",", ".")
                ctr_val = float(ctr_raw)
                rows.append({
                    "page": row.get("Top pages", row.get("Page", "")),
                    "clicks": int(row.get("Clicks", 0)),
                    "impressions": int(row.get("Impressions", 0)),
                    "ctr": round(ctr_val / 100 if ctr_val > 1 else ctr_val, 4),
                    "position": round(float(row.get("Position", "0").replace(",", ".")), 1),
                })
            except (ValueError, TypeError):
                continue
        return rows
