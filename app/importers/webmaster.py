import re
import logging
import pandas as pd
from datetime import datetime
from app.importers.base import BaseImporter
from app.db import get_conn

logger = logging.getLogger(__name__)


class WebmasterImporter(BaseImporter):
    source = "webmaster"

    def __init__(self, region="russia", platform="mobile", search_engine="yandex", granularity="monthly"):
        super().__init__(region, platform, search_engine)
        self.granularity = granularity

    def import_file(self, filepath: str) -> dict:
        df = pd.read_excel(filepath)

        # Columns: Query, Url, then repeating groups of date_metric
        # e.g. 2026-04_shows, 2026-04_position, 2026-04_demand, 2026-04_ctr, 2026-04_clicks
        # or date columns as datetime objects for daily data

        query_col = df.columns[0]  # "Query" or "Запросы"
        url_col = df.columns[1]    # "Url"

        # Detect date columns and their metrics
        date_cols = df.columns[2:]
        dates_metrics = {}

        for col in date_cols:
            col_str = str(col)
            # Monthly format: "2026-04_shows"
            m = re.match(r"(\d{4}-\d{2})_(\w+)", col_str)
            if m:
                date_str, metric = m.group(1), m.group(2)
                dates_metrics.setdefault(date_str, {})[metric] = col
                continue
            # Daily format: datetime object or "2026-05-11_shows"
            m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[_ T].*)?_(\w+)", col_str)
            if m:
                date_str, metric = m.group(1), m.group(2)
                dates_metrics.setdefault(date_str, {})[metric] = col
                continue
            # Datetime column object (pandas reads dates as datetime)
            if hasattr(col, 'strftime'):
                date_str = col.strftime("%Y-%m-%d")
                # For datetime columns without metric suffix, group consecutive
                # These are from daily data where each date has 5 consecutive columns
                dates_metrics.setdefault(date_str, {})

        # If datetime columns detected without metric suffix, map by position
        if any(hasattr(c, 'strftime') for c in date_cols):
            return self._import_wide_datetime(df, query_col, url_col, filepath)

        # Standard named columns
        rows = []
        for _, row in df.iterrows():
            q = str(row[query_col]).strip()
            u = str(row[url_col]).strip()
            if not q or q == 'nan':
                continue

            for date_str, metrics in dates_metrics.items():
                try:
                    shows = int(row.get(metrics.get("shows", ""), 0) or 0)
                    clicks = int(row.get(metrics.get("clicks", ""), 0) or 0)
                    pos = float(row.get(metrics.get("position", ""), 0) or 0)
                    demand = int(row.get(metrics.get("demand", ""), 0) or 0)
                    ctr = float(row.get(metrics.get("ctr", ""), 0) or 0)

                    if shows == 0 and clicks == 0:
                        continue

                    rows.append((
                        self.upload_id, self.source, date_str + "-01" if len(date_str) == 7 else date_str,
                        q, u, self.region, self.platform, self.search_engine,
                        shows, clicks, ctr / 100 if ctr > 1 else ctr, pos, demand
                    ))
                except (ValueError, TypeError):
                    continue

        conn = get_conn()
        if rows:
            conn.executemany(
                "INSERT INTO search_queries (upload_id,source,date,query,url,region,platform,search_engine,shows,clicks,ctr,position,demand) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )

        period_start = min(r[2] for r in rows) if rows else None
        period_end = max(r[2] for r in rows) if rows else None
        self.register_upload(filepath.split("/")[-1], len(rows), period_start, period_end, self.granularity)
        return {"rows": len(rows), "queries": len(set(r[3] for r in rows))}

    def _import_wide_datetime(self, df, query_col, url_col, filepath) -> dict:
        """Handle XLSX where date columns are datetime objects with repeating metric groups."""
        date_cols = df.columns[2:]
        # Group into sets of 5: shows, position, demand, ctr, clicks
        metrics_order = ["shows", "position", "demand", "ctr", "clicks"]
        date_groups = []
        i = 0
        while i < len(date_cols):
            col = date_cols[i]
            if hasattr(col, 'strftime'):
                date_str = col.strftime("%Y-%m-%d")
                group = {"date": date_str}
                for j, metric in enumerate(metrics_order):
                    if i + j < len(date_cols):
                        group[metric] = date_cols[i + j]
                date_groups.append(group)
                i += 5
            else:
                i += 1

        rows = []
        for _, row in df.iterrows():
            q = str(row[query_col]).strip()
            u = str(row[url_col]).strip()
            if not q or q == 'nan':
                continue

            for group in date_groups:
                try:
                    shows = int(row.get(group.get("shows", ""), 0) or 0)
                    clicks = int(row.get(group.get("clicks", ""), 0) or 0)
                    pos = float(row.get(group.get("position", ""), 0) or 0)
                    demand = int(row.get(group.get("demand", ""), 0) or 0)
                    ctr = float(row.get(group.get("ctr", ""), 0) or 0)

                    if shows == 0 and clicks == 0:
                        continue

                    rows.append((
                        self.upload_id, self.source, group["date"],
                        q, u, self.region, self.platform, self.search_engine,
                        shows, clicks, ctr / 100 if ctr > 1 else ctr, pos, demand
                    ))
                except (ValueError, TypeError):
                    continue

        conn = get_conn()
        if rows:
            conn.executemany(
                "INSERT INTO search_queries (upload_id,source,date,query,url,region,platform,search_engine,shows,clicks,ctr,position,demand) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )

        period_start = min(r[2] for r in rows) if rows else None
        period_end = max(r[2] for r in rows) if rows else None
        self.register_upload(filepath.split("/")[-1], len(rows), period_start, period_end, "daily")
        return {"rows": len(rows), "queries": len(set(r[3] for r in rows))}
