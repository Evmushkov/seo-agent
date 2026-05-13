import csv
import io
import logging
from app.importers.base import BaseImporter
from app.db import get_conn

logger = logging.getLogger(__name__)


class MetrikaImporter(BaseImporter):
    source = "metrika"

    def import_file(self, filepath: str) -> dict:
        with open(filepath, encoding="utf-8-sig") as f:
            text = f.read()

        if "Страница входа" in text and "Источник трафика" in text:
            return self._import_landing_pages(text, filepath)
        elif "Поисковая система" in text:
            return self._import_search_systems(text, filepath)
        else:
            return {"error": "Неизвестный формат Метрики"}

    def _import_landing_pages(self, text: str, filepath: str) -> dict:
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            url = row.get("Страница входа", "").strip()
            if not url or url == "Итого и средние":
                continue
            try:
                visits = int(row.get("Визиты", 0) or 0)
                visitors = int(row.get("Посетители", 0) or 0)
                bounce = float(row.get("Отказы", 0) or 0)
                depth = float(row.get("Глубина просмотра", 0) or 0)
                time_s = row.get("Время на сайте", "")
                goals = int(row.get("Достижения избранных целей", 0) or 0)
                rev_raw = row.get("Доход по избранным целям, RUB", "0") or "0"
                revenue = float(rev_raw.replace(",", ".").replace(" ", ""))
                conv_raw = row.get("Конверсия посетителей по избранным целям", "0") or "0"
                conversion = float(conv_raw.replace(",", "."))
                traffic_src = row.get("Источник трафика (детально)", "")
                phrase = row.get("Поисковая фраза", "")
                if phrase == "Не определено":
                    phrase = ""

                rows.append((
                    self.upload_id, self.source, url, traffic_src, phrase,
                    visits, visitors, bounce, depth, time_s, goals, revenue, conversion
                ))
            except (ValueError, TypeError):
                continue

        conn = get_conn()
        if rows:
            conn.executemany(
                "INSERT INTO page_metrics (upload_id,source,url,traffic_source,search_phrase,visits,visitors,bounce_rate,depth,time_on_site,goal_completions,revenue,conversion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )

        self.register_upload(filepath.split("/")[-1], len(rows))
        return {"rows": len(rows), "type": "landing_pages"}

    def _import_search_systems(self, text: str, filepath: str) -> dict:
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            url = row.get("Страница входа", "").strip()
            if not url or url == "Итого и средние":
                continue
            try:
                engine = row.get("Поисковая система", "")
                engine_detail = row.get("Поисковая система (детально)", "")
                phrase = row.get("Поисковая фраза", "")
                if phrase == "Не определено":
                    phrase = ""
                visits = int(row.get("Визиты", 0) or 0)
                visitors = int(row.get("Посетители", 0) or 0)
                bounce = float(row.get("Отказы", 0) or 0)
                depth = float(row.get("Глубина просмотра", 0) or 0)
                time_s = row.get("Время на сайте", "")
                goals = int(row.get("Достижения избранных целей", 0) or 0)
                rev_raw = row.get("Доход по избранным целям, RUB", "0") or "0"
                revenue = float(rev_raw.replace(",", ".").replace(" ", ""))
                conv_raw = row.get("Конверсия посетителей по избранным целям", "0") or "0"
                conversion = float(conv_raw.replace(",", "."))

                traffic_src = f"{engine}: {engine_detail}" if engine_detail else engine

                rows.append((
                    self.upload_id, self.source, url, traffic_src, phrase,
                    visits, visitors, bounce, depth, time_s, goals, revenue, conversion
                ))
            except (ValueError, TypeError):
                continue

        conn = get_conn()
        if rows:
            conn.executemany(
                "INSERT INTO page_metrics (upload_id,source,url,traffic_source,search_phrase,visits,visitors,bounce_rate,depth,time_on_site,goal_completions,revenue,conversion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )

        self.register_upload(filepath.split("/")[-1], len(rows))
        return {"rows": len(rows), "type": "search_systems"}
