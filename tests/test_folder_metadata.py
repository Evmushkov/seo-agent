import pytest
from datetime import date
from pathlib import Path

from app.importers_v2.base import parse_folder_metadata


BASE = Path("/fake/data/uploads")


def make_path(project, source, region_platform, dates_folder, filename=None):
    """Build a fake path under the 4-level metadata hierarchy."""
    p = BASE / project / source / region_platform / dates_folder
    if filename:
        p = p / filename
    return p


# ── Happy path ────────────────────────────────────────────────────────────────

def test_valid_folder_path():
    path = make_path("tempus.ru", "y.direct", "moscow_desktop", "2026-04-01_2026-04-30")
    result = parse_folder_metadata(path)
    assert result == {
        "project":     "tempus.ru",
        "domain":      "https://tempus.ru",
        "source":      "y.direct",
        "region":      "moscow",
        "platform":    "desktop",
        "date_from":   date(2026, 4, 1),
        "date_to":     date(2026, 4, 30),
        "folder_path": str(path),
    }


def test_valid_file_path():
    path = make_path("tempus.ru", "g.search.console", "moscow_desktop",
                     "2026-04-01_2026-04-30", "Queries.csv")
    result = parse_folder_metadata(path)
    assert result["source"]   == "g.search.console"
    assert result["domain"]   == "https://tempus.ru"
    assert result["date_from"] == date(2026, 4, 1)


def test_valid_moscow_district():
    path = make_path("tempus.ru", "topvisor", "moscow.district_mobile",
                     "2026-03-01_2026-03-31")
    result = parse_folder_metadata(path)
    assert result["region"]   == "moscow.district"
    assert result["platform"] == "mobile"


def test_valid_russia_tablet():
    path = make_path("shop.example.com", "y.metrika", "russia_tablet",
                     "2025-01-01_2025-12-31")
    result = parse_folder_metadata(path)
    assert result["region"]  == "russia"
    assert result["platform"] == "tablet"
    assert result["project"] == "shop.example.com"
    assert result["domain"]  == "https://shop.example.com"


def test_valid_y_webmaster():
    path = make_path("tempus.ru", "y.webmaster", "moscow_desktop",
                     "2026-04-01_2026-04-30")
    result = parse_folder_metadata(path)
    assert result["source"] == "y.webmaster"


def test_g_search_console_dots_in_source_name():
    """Dots in source name (g.search.console, y.webmaster) must work."""
    path = make_path("tempus.ru", "g.search.console", "russia_desktop",
                     "2026-01-01_2026-01-31")
    result = parse_folder_metadata(path)
    assert result["source"] == "g.search.console"
    assert result["region"] == "russia"


# ── Invalid source ────────────────────────────────────────────────────────────

def test_invalid_source_raises_with_allowed_values():
    path = make_path("tempus.ru", "baidu", "moscow_desktop", "2026-04-01_2026-04-30")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    msg = str(exc.value)
    assert "baidu"   in msg
    assert "y.direct" in msg   # allowed values listed


# ── Invalid region ────────────────────────────────────────────────────────────

def test_invalid_region_raises_with_allowed_values():
    path = make_path("tempus.ru", "y.direct", "spb_desktop", "2026-04-01_2026-04-30")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    msg = str(exc.value)
    assert "spb"    in msg
    assert "moscow" in msg


# ── Invalid platform ──────────────────────────────────────────────────────────

def test_invalid_platform_raises_with_allowed_values():
    path = make_path("tempus.ru", "y.direct", "moscow_smarttv", "2026-04-01_2026-04-30")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    msg = str(exc.value)
    assert "smarttv" in msg
    assert "desktop" in msg


# ── Bad date format ───────────────────────────────────────────────────────────

def test_bad_date_format_dd_mm_yyyy():
    """Dates like 01.04.2026 are not ISO — regex won't find them."""
    path = make_path("tempus.ru", "y.direct", "moscow_desktop",
                     "01.04.2026_30.04.2026")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    msg = str(exc.value)
    assert "YYYY-MM-DD" in msg or "ISO" in msg


# ── date_from > date_to ───────────────────────────────────────────────────────

def test_date_from_after_date_to_raises():
    path = make_path("tempus.ru", "y.direct", "moscow_desktop",
                     "2026-05-01_2026-04-01")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    assert "date_from" in str(exc.value)
    assert "date_to"   in str(exc.value)


# ── Too short path ────────────────────────────────────────────────────────────

def test_too_short_path_missing_project_raises():
    # Only one level above the dates folder (source and project missing)
    path = Path("/fake/moscow_desktop/2026-04-01_2026-04-30")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    assert "короткий" in str(exc.value) or "project" in str(exc.value).lower()


# ── No underscore between region and platform ─────────────────────────────────

def test_no_underscore_between_region_platform_raises():
    """'moscowdesktop' has no '_' separator — must raise with format hint."""
    path = make_path("tempus.ru", "y.direct", "moscowdesktop",
                     "2026-04-01_2026-04-30")
    with pytest.raises(ValueError) as exc:
        parse_folder_metadata(path)
    msg = str(exc.value)
    assert "_" in msg or "разобрать" in msg
