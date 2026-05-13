import logging
import pandas as pd
from app.importers.base import BaseImporter
from app.db import get_conn

logger = logging.getLogger(__name__)


class TopVisorImporter(BaseImporter):
    source = "topvisor"

    def import_file(self, filepath: str) -> dict:
        df = pd.read_excel(filepath)
        query_col = df.columns[0]  # "Запросы"

        # Detect frequency columns and date columns
        freq_exact_col = None
        freq_phrase_col = None
        date_cols = []

        for col in df.columns[1:]:
            col_str = str(col)
            if "!Частота" in col_str and "[" not in col_str:
                freq_exact_col = col
            elif "[!Частота]" in col_str or "[Частота]" in col_str:
                freq_phrase_col = col
            elif hasattr(col, 'strftime'):
                date_cols.append(col)

        rows = []
        for _, row in df.iterrows():
            q = str(row[query_col]).strip()
            if not q or q == 'nan':
                continue

            freq_e = int(row[freq_exact_col]) if freq_exact_col is not None and pd.notna(row.get(freq_exact_col)) else None
            freq_p = int(row[freq_phrase_col]) if freq_phrase_col is not None and pd.notna(row.get(freq_phrase_col)) else None

            for date_col in date_cols:
                date_str = date_col.strftime("%Y-%m-%d")
                val = row.get(date_col)
                if pd.isna(val) or str(val).strip() in ("--", "", "—"):
                    pos = None
                else:
                    try:
                        pos = int(float(val))
                    except (ValueError, TypeError):
                        pos = None

                rows.append((
                    self.upload_id, q, self.search_engine, self.region, self.platform,
                    date_str, pos, freq_e, freq_p
                ))

        conn = get_conn()
        if rows:
            conn.executemany(
                "INSERT INTO position_tracking (upload_id,query,search_engine,region,platform,date,position,frequency_exact,frequency_phrase) VALUES (?,?,?,?,?,?,?,?,?)",
                rows
            )

        period_start = min(r[5] for r in rows) if rows else None
        period_end = max(r[5] for r in rows) if rows else None
        self.register_upload(filepath.split("/")[-1], len(rows), period_start, period_end, "daily")
        return {"rows": len(rows), "queries": len(set(r[1] for r in rows)), "dates": len(date_cols)}
