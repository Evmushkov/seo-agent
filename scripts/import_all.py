"""Batch importer: traverse a project folder and import all sources into schema-v2.

Usage:
    python -m scripts.import_all data/uploads/tempus.ru/ [--db path/to/db]
"""

import argparse
import hashlib
import importlib
import logging
import os
import sys
import time
from pathlib import Path

import duckdb

from app.importers_v2.base import parse_folder_metadata, rebuild_query_unified
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_IMPORTER_PATHS = {
    "y.direct":         "app.importers_v2.y_direct",
    "g.search.console": "app.importers_v2.gsc",
    "y.metrika":        "app.importers_v2.y_metrika",
    "topvisor":         "app.importers_v2.topvisor",
    "y.webmaster":      "app.importers_v2.y_webmaster",
}


def _folder_hash(folder: Path) -> str:
    """sha256 over all non-hidden files (sorted by name) including filenames.

    Covers: content changes, added/removed files, renames.
    For folders with a single large xlsx (~200MB) this adds ~2s per check —
    acceptable given imports themselves take minutes.
    """
    sha = hashlib.sha256()
    for f in sorted(f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")):
        sha.update(f.name.encode())
        sha.update(f.read_bytes())
    return sha.hexdigest()


def _find_import_folders(project_root: Path):
    """Yield (source_name, dates_folder) at depth project/source/region_platform/dates."""
    for source_dir in sorted(project_root.iterdir()):
        if not source_dir.is_dir() or source_dir.name.startswith("."):
            continue
        if source_dir.name not in _IMPORTER_PATHS:
            logger.warning("unknown source directory, skipping: %s", source_dir.name)
            continue
        for rp_dir in sorted(source_dir.iterdir()):
            if not rp_dir.is_dir() or rp_dir.name.startswith("."):
                continue
            for dates_dir in sorted(rp_dir.iterdir()):
                if not dates_dir.is_dir() or dates_dir.name.startswith("."):
                    continue
                yield source_dir.name, dates_dir


def _already_imported(con: duckdb.DuckDBPyConnection, meta: dict, fhash: str) -> bool:
    row = con.execute(
        "SELECT id FROM imports "
        "WHERE project=? AND source=? AND region=? AND platform=? "
        "  AND date_from=? AND date_to=? AND file_hash=?",
        [meta["project"], meta["source"], meta["region"], meta["platform"],
         meta["date_from"], meta["date_to"], fhash],
    ).fetchone()
    return row is not None


def _suppress_rebuilds(modules: dict) -> None:
    """Replace rebuild_query_unified in each importer module with a no-op."""
    for mod in modules.values():
        mod.rebuild_query_unified = lambda con: None  # noqa: E731


def _print_summary(rows: list) -> None:
    headers = ["source", "region", "platform", "period", "files", "facts", "time_s", "status"]
    widths = {h: len(h) for h in headers}
    for r in rows:
        for h in headers:
            widths[h] = max(widths[h], len(str(r.get(h, ""))))

    sep = "+-" + "-+-".join("-" * widths[h] for h in headers) + "-+"
    fmt = "| " + " | ".join(f"{{:<{widths[h]}}}" for h in headers) + " |"

    print()
    print(sep)
    print(fmt.format(*headers))
    print(sep)
    for r in rows:
        print(fmt.format(*[str(r.get(h, "")) for h in headers]))
    print(sep)

    imported = sum(1 for r in rows if r["status"] == "imported")
    skipped  = sum(1 for r in rows if r["status"] == "skipped (idempotent)")
    errors   = sum(1 for r in rows if r["status"].startswith("error"))
    total_facts = sum(r["facts"] for r in rows)
    print(f"\nimported={imported}  skipped={skipped}  errors={errors}  "
          f"total_facts={total_facts:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-import all sources for a project.")
    parser.add_argument("project_path",
                        help="Path to project folder, e.g. data/uploads/tempus.ru/")
    parser.add_argument("--db",
                        default=os.environ.get("DB_PATH", "/data/seo.duckdb"),
                        help="DuckDB path (default: $DB_PATH or /data/seo.duckdb)")
    parser.add_argument("--no-raw", action="store_true", default=False,
                        help="after rebuild_query_unified, export compact DB without "
                             "query_facts and atomically replace the original (use for "
                             "production volume)")
    args = parser.parse_args()

    project_root = Path(args.project_path).resolve()
    if not project_root.is_dir():
        parser.error(f"not a directory: {project_root}")

    logger.info("project : %s", project_root)
    logger.info("database: %s", args.db)

    con = duckdb.connect(args.db)

    try:
        con.execute("SELECT 1 FROM imports LIMIT 1")
    except duckdb.CatalogException:
        logger.info("schema not found, creating (first run)")
        from scripts.create_schema_v2 import create_schema
        create_schema(con)

    # Pre-load all importer modules and suppress per-import rebuilds
    importer_mods = {
        name: importlib.import_module(path)
        for name, path in _IMPORTER_PATHS.items()
    }
    _suppress_rebuilds(importer_mods)

    summary = []

    for source_name, folder in _find_import_folders(project_root):
        label = f"{source_name}/{folder.parent.name}/{folder.name}"

        # Parse metadata
        try:
            meta = parse_folder_metadata(folder)
        except ValueError as exc:
            logger.error("metadata error %s: %s", label, exc)
            summary.append({
                "source": source_name, "region": "?", "platform": "?",
                "period": folder.name, "files": 0, "facts": 0,
                "time_s": 0, "status": f"error: {exc}",
            })
            continue

        period  = f"{meta['date_from']}_{meta['date_to']}"
        n_files = sum(1 for f in folder.iterdir()
                      if f.is_file() and not f.name.startswith("."))
        fhash   = _folder_hash(folder)

        # Idempotency check
        if _already_imported(con, meta, fhash):
            logger.info("skip (idempotent): %s", label)
            summary.append({
                "source": source_name, "region": meta["region"], "platform": meta["platform"],
                "period": period, "files": n_files, "facts": 0,
                "time_s": 0, "status": "skipped (idempotent)",
            })
            continue

        # Run import (rebuild suppressed inside)
        t0 = time.time()
        try:
            n_facts = importer_mods[source_name].import_folder(folder, con)
            elapsed = round(time.time() - t0, 1)

            # Store hash for future idempotency checks
            con.execute(
                "UPDATE imports SET file_hash=? "
                "WHERE project=? AND source=? AND region=? AND platform=? "
                "  AND date_from=? AND date_to=? AND file_hash IS NULL",
                [fhash, meta["project"], meta["source"], meta["region"],
                 meta["platform"], meta["date_from"], meta["date_to"]],
            )

            logger.info("imported %s → %d facts in %.1fs", label, n_facts, elapsed)
            summary.append({
                "source": source_name, "region": meta["region"], "platform": meta["platform"],
                "period": period, "files": n_files, "facts": n_facts,
                "time_s": elapsed, "status": "imported",
            })

        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            logger.error("failed %s: %s", label, exc, exc_info=True)
            summary.append({
                "source": source_name, "region": meta["region"], "platform": meta["platform"],
                "period": period, "files": n_files, "facts": 0,
                "time_s": elapsed, "status": f"error: {exc}",
            })

    # Single rebuild at the end
    logger.info("rebuilding query_unified ...")
    t0 = time.time()
    rebuild_query_unified(con)
    logger.info("rebuild done in %.1fs", time.time() - t0)

    if args.no_raw:
        db_path = args.db
        tmp_path = db_path + ".new"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        logger.info("--no-raw: exporting compact DB to %s", tmp_path)
        from scripts.create_schema_v2 import create_schema
        con_new = duckdb.connect(tmp_path)
        create_schema(con_new)
        for tbl in ("imports", "query_unified", "gsc_facts_by_appearance", "gsc_daily_totals"):
            df = con.execute(f"SELECT * FROM {tbl}").df()
            con_new.register("_t", df)
            con_new.execute(f"INSERT INTO {tbl} SELECT * FROM _t")
            con_new.unregister("_t")
            logger.info("  copied %s: %d rows", tbl, len(df))
        con_new.close()
        con.close()
        os.rename(tmp_path, db_path)
        logger.info("--no-raw: replaced %s", db_path)
    else:
        con.close()

    _print_summary(summary)
    if args.no_raw:
        print("note: query_facts excluded, DB compacted (--no-raw)")
    errors = sum(1 for r in summary if r["status"].startswith("error"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
