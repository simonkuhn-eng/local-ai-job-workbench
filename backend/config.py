"""Central paths, timezone, and profile helpers for Local AI Job Workbench."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobs.sqlite3"
SOURCES_PATH = DATA_DIR / "sources.json"
SOURCES_EXAMPLE_PATH = DATA_DIR / "sources.example.json"
MASTER_PROFILE_PATH = DATA_DIR / "master_profile.json"
MASTER_PROFILE_EXAMPLE_PATH = DATA_DIR / "master_profile.example.json"

TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = TEMPLATES_DIR / "assets"

PDF_DIR = DATA_DIR / "pdfs"
PDF_CV_DIR = PDF_DIR / "cv"
PDF_LETTER_DIR = PDF_DIR / "letters"
APPLICATIONS_DIR = DATA_DIR / "applications"
BUILD_TEMP_DIR = DATA_DIR / "tex_build"
AGY_WORKDIR = DATA_DIR / "agy_workdir"

FESTANSTELLUNG_BEWERBUNG_DIR = Path(
    os.environ.get("WORKBENCH_EXPORT_DIR", str(DATA_DIR / "exports"))
)
ALLGEMEINE_UNTERLAGEN_DIR = Path(
    os.environ.get("WORKBENCH_ATTACHMENTS_DIR", str(DATA_DIR / "attachments"))
)

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"

TZ = ZoneInfo("Europe/Berlin")
USER_AGENT = "LocalAiJobWorkbench/0.2 (+local job workbench)"


def ensure_dirs() -> None:
    for d in (
        DATA_DIR,
        PDF_CV_DIR,
        PDF_LETTER_DIR,
        APPLICATIONS_DIR,
        BUILD_TEMP_DIR,
        AGY_WORKDIR,
        FESTANSTELLUNG_BEWERBUNG_DIR,
        ALLGEMEINE_UNTERLAGEN_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def today_de() -> str:
    return datetime.now(TZ).strftime("%d.%m.%Y")


def load_sources_config() -> dict:
    path = SOURCES_PATH if SOURCES_PATH.exists() else SOURCES_EXAMPLE_PATH
    if not path.exists():
        return {"feeds": [], "company_portals": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_master_profile() -> dict:
    path = MASTER_PROFILE_PATH if MASTER_PROFILE_PATH.exists() else MASTER_PROFILE_EXAMPLE_PATH
    if not path.exists():
        return {
            "personal": {"name": "Alex Muster", "city": "München", "email": "alex.muster@example.com"},
            "cv_base": {},
            "letter_defaults": {"place": "München", "salutation_de": "Sehr geehrte Damen und Herren,", "closing_de": "Mit freundlichen Grüßen", "salutation_en": "Dear Hiring Team,", "closing_en": "Kind regards,"},
            "ranking_profile": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_master_profile(profile: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = MASTER_PROFILE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, MASTER_PROFILE_PATH)
