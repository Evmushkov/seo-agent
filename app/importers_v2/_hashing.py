"""Shared folder-hash utility for all importers and the batch CLI."""

import hashlib
from pathlib import Path

# Only data formats used across all 5 importers: GSC/Direct/Metrika → csv,
# Topvisor/Webmaster → xlsx. No importer reads .json input files.
_DATA_SUFFIXES = frozenset({".csv", ".xlsx"})


def folder_hash(folder: Path) -> str:
    """SHA-256 over sorted data files (csv/xlsx) in folder.

    Covers content changes, added/removed files, and renames.
    Excludes hidden files (.DS_Store etc.) and non-data files.
    Sort is by filename to guarantee platform-independent ordering.
    """
    sha = hashlib.sha256()
    for f in sorted(
        (
            f for f in folder.iterdir()
            if f.is_file()
            and not f.name.startswith(".")
            and f.suffix in _DATA_SUFFIXES
        ),
        key=lambda f: f.name,
    ):
        sha.update(f.name.encode())
        sha.update(f.read_bytes())
    return sha.hexdigest()
