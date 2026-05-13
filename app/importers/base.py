import uuid
import logging
from datetime import datetime
from app.db import execute, get_conn

logger = logging.getLogger(__name__)


class BaseImporter:
    source: str = ""

    def __init__(self, region="russia", platform="mobile", search_engine="all"):
        self.region = region
        self.platform = platform
        self.search_engine = search_engine
        self.upload_id = str(uuid.uuid4())[:8]

    def register_upload(self, filename: str, rows: int, period_start=None, period_end=None, granularity=None):
        execute(
            "INSERT INTO uploads (id, source, region, platform, search_engine, period_start, period_end, granularity, filename, rows_imported) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [self.upload_id, self.source, self.region, self.platform, self.search_engine, period_start, period_end, granularity, filename, rows]
        )
        logger.info(f"[{self.source}] Registered upload {self.upload_id}: {filename} ({rows} rows)")

    def import_file(self, filepath: str) -> dict:
        raise NotImplementedError
