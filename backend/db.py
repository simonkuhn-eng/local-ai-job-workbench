"""SQLite database layer, lifecycle tracking, workflow audit, and document storage."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import threading
from typing import Any

from backend.config import (
    APPLICATIONS_DIR,
    DB_PATH,
    FESTANSTELLUNG_BEWERBUNG_DIR,
    TZ,
    ensure_dirs,
    load_master_profile,
    load_sources_config,
    now_iso,
)
from backend.documents.latex_common import slugify_filename
from backend.scanner.geo_scoring import (
    MIN_AI_RULE_SCORE,
    TIER_LABELS,
    canonical_company,
    canonical_url,
    hard_exclusion,
    location_tier,
    matches_city_area,
    relevance,
    rule_eligibility,
)

STATUSES = {
    "neu": "Neu",
    "interessant": "Interessant",
    "vorbereiten": "Vorbereiten",
    "beworben": "Beworben",
    "eingeladen": "Einladung",
    "gespraech": "Im Gespräch",
    "angebot": "Angebot",
    "angenommen": "Angenommen",
    "abgeschlossen": "Abgeschlossen",
    "absage": "Absage",
    "zurueckgezogen": "Zurückgezogen",
    "uninteressant": "Uninteressant",
    "ignoriert": "Ignoriert",
}
ACTIVE_APPLICATIONS = ("vorbereiten", "beworben", "eingeladen", "gespraech", "angebot")
ARCHIVE_STATUSES = ("angenommen", "abgeschlossen", "absage", "zurueckgezogen", "uninteressant", "ignoriert")
FEEDBACK_LABELS = {
    "fits": "Passt gut",
    "interesting": "Besonders spannend",
    "unsure": "Unsicher",
    "wrong_focus": "Falscher Tech-Stack / Fokus",
    "too_senior": "Zu senior / Überqualifiziert",
    "too_junior": "Zu junior / Praktikum",
    "location_bad": "Standort unpassend",
    "travel_high": "Zu viel Reisetätigkeit",
    "salary_low": "Gehalt / Konditionen",
    "company_dislike": "Unternehmen unpassend",
    "not_fit": "Unpassend",
    "other": "Sonstiges",
}
UNAVAILABLE_REASONS = {
    "absent_complete_feed": "Im vollständigen Arbeitgeber-Feed nicht mehr gelistet",
    "original_closed": "Originalanzeige geschlossen (HTTP 404/410 oder Bewerbung beendet)",
    "historical": "Historisch inaktiv (vor Einführung der Datumsprotokollierung geschlossen)",
}


_MIGRATE_LOCK = threading.Lock()
_MIGRATED_DBS: set[str] = set()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    target = (db_path or DB_PATH).resolve()
    con = sqlite3.connect(target, timeout=20, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    con.execute("PRAGMA foreign_keys = ON")
    key = str(target)
    if key not in _MIGRATED_DBS:
        with _MIGRATE_LOCK:
            if key not in _MIGRATED_DBS:
                con.execute("PRAGMA journal_mode = WAL")
                migrate(con)
                _MIGRATED_DBS.add(key)
    return con


def migrate(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS companies (
          name TEXT PRIMARY KEY COLLATE NOCASE,
          preference TEXT NOT NULL CHECK(preference IN ('preferred','excluded','neutral'))
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY,
          company TEXT NOT NULL,
          title TEXT NOT NULL,
          location TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          url TEXT NOT NULL,
          source TEXT NOT NULL,
          source_id TEXT,
          tier INTEGER NOT NULL,
          score INTEGER NOT NULL,
          reason TEXT NOT NULL,
          verification TEXT NOT NULL DEFAULT 'feed',
          active INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'neu',
          note TEXT NOT NULL DEFAULT '',
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS identities (
          kind TEXT NOT NULL,
          key TEXT NOT NULL,
          job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          PRIMARY KEY (kind, key)
        );
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY,
          started TEXT NOT NULL,
          finished TEXT,
          kind TEXT NOT NULL,
          found INTEGER NOT NULL DEFAULT 0,
          added INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_sources (
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          source TEXT NOT NULL,
          url TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          last_checked TEXT NOT NULL DEFAULT '',
          available INTEGER NOT NULL DEFAULT 1,
          reason TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(job_id, source)
        );
        CREATE TABLE IF NOT EXISTS availability_events (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          event TEXT NOT NULL CHECK(event IN ('unlisted','relisted')),
          observed_at TEXT NOT NULL,
          source TEXT NOT NULL,
          reason TEXT NOT NULL,
          run_id INTEGER REFERENCES runs(id)
        );
        CREATE TABLE IF NOT EXISTS feed_health (
          source TEXT PRIMARY KEY,
          company TEXT NOT NULL DEFAULT '',
          last_check TEXT NOT NULL DEFAULT '',
          last_good_count INTEGER,
          current_count INTEGER,
          missing_texts INTEGER NOT NULL DEFAULT 0,
          warning TEXT NOT NULL DEFAULT '',
          pending_missing TEXT NOT NULL DEFAULT '',
          pending_since TEXT NOT NULL DEFAULT '',
          pending_checks INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS portal_cursors (
          source TEXT NOT NULL,
          query TEXT NOT NULL,
          offset INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(source, query)
        );
        CREATE TABLE IF NOT EXISTS portal_job_cache (
          source TEXT NOT NULL,
          url TEXT NOT NULL,
          metadata TEXT NOT NULL,
          payload TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL DEFAULT 'pending',
          checked TEXT NOT NULL DEFAULT '',
          next_check TEXT NOT NULL DEFAULT '',
          last_seen TEXT NOT NULL,
          error TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(source, url)
        );
        CREATE TABLE IF NOT EXISTS application_events (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          kind TEXT NOT NULL,
          old_value TEXT NOT NULL,
          new_value TEXT NOT NULL,
          created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_snapshots (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          content TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          reason TEXT NOT NULL,
          created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_documents (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          kind TEXT NOT NULL,
          label TEXT NOT NULL,
          original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          created TEXT NOT NULL
        );

        -- New Bewerbungs-Manager tables
        CREATE TABLE IF NOT EXISTS scan_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL DEFAULT '',
          duration_ms INTEGER NOT NULL DEFAULT 0,
          sources_total INTEGER NOT NULL DEFAULT 0,
          sources_ok INTEGER NOT NULL DEFAULT 0,
          sources_failed INTEGER NOT NULL DEFAULT 0,
          found_count INTEGER NOT NULL DEFAULT 0,
          new_count INTEGER NOT NULL DEFAULT 0,
          closed_count INTEGER NOT NULL DEFAULT 0,
          relisted_count INTEGER NOT NULL DEFAULT 0,
          changed_count INTEGER NOT NULL DEFAULT 0,
          new_job_ids_json TEXT NOT NULL DEFAULT '[]',
          closed_job_ids_json TEXT NOT NULL DEFAULT '[]',
          relisted_job_ids_json TEXT NOT NULL DEFAULT '[]',
          errors_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS cv_variants (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
          name TEXT NOT NULL,
          lang TEXT NOT NULL DEFAULT 'de',
          cv_data_json TEXT NOT NULL,
          history_json TEXT NOT NULL DEFAULT '[]',
          pdf_de_path TEXT NOT NULL DEFAULT '',
          pdf_en_path TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cover_letters (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
          name TEXT NOT NULL,
          lang TEXT NOT NULL DEFAULT 'de',
          company TEXT NOT NULL DEFAULT '',
          contact_person TEXT NOT NULL DEFAULT '',
          street TEXT NOT NULL DEFAULT '',
          zip_city TEXT NOT NULL DEFAULT '',
          job_ref TEXT NOT NULL DEFAULT '',
          place TEXT NOT NULL DEFAULT 'München',
          date_str TEXT NOT NULL DEFAULT '',
          subject TEXT NOT NULL DEFAULT '',
          salutation TEXT NOT NULL DEFAULT 'Sehr geehrte Damen und Herren,',
          body_text TEXT NOT NULL DEFAULT '',
          closing TEXT NOT NULL DEFAULT 'Mit freundlichen Grüßen',
          include_signature INTEGER NOT NULL DEFAULT 1,
          signature_width_cm REAL NOT NULL DEFAULT 3.7,
          pdf_path TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS interview_preps (
          job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
          prep_json TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS letter_paragraphs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          text TEXT NOT NULL,
          text_hash TEXT NOT NULL UNIQUE,
          lang TEXT NOT NULL DEFAULT 'de',
          category TEXT NOT NULL DEFAULT 'body',
          label TEXT NOT NULL DEFAULT '',
          company TEXT NOT NULL DEFAULT '',
          job_title TEXT NOT NULL DEFAULT '',
          job_id INTEGER,
          source_kind TEXT NOT NULL DEFAULT 'sent',
          usage_count INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_active_tier ON jobs(active, tier, score DESC, first_seen DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen DESC);
        CREATE INDEX IF NOT EXISTS idx_cv_variants_job ON cv_variants(job_id);
        CREATE INDEX IF NOT EXISTS idx_cover_letters_job ON cover_letters(job_id);
        CREATE INDEX IF NOT EXISTS idx_letter_paragraphs_lang ON letter_paragraphs(lang, category);
        """
    )

    job_cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    extra_cols = {
        "content_hash": "TEXT NOT NULL DEFAULT ''",
        "ai_hash": "TEXT NOT NULL DEFAULT ''",
        "ai_score": "INTEGER",
        "ai_data": "TEXT NOT NULL DEFAULT ''",
        "ai_date": "TEXT NOT NULL DEFAULT ''",
        "ai_model": "TEXT NOT NULL DEFAULT ''",
        "ai_version": "INTEGER NOT NULL DEFAULT 0",
        "ai_signature": "TEXT NOT NULL DEFAULT ''",
        "ai_review_requested": "INTEGER NOT NULL DEFAULT 0",
        "ai_review_data": "TEXT NOT NULL DEFAULT ''",
        "ai_review_date": "TEXT NOT NULL DEFAULT ''",
        "found_via": "TEXT NOT NULL DEFAULT ''",
        "hard_exclusion": "TEXT NOT NULL DEFAULT ''",
        "unavailable_since": "TEXT NOT NULL DEFAULT ''",
        "unavailable_reason": "TEXT NOT NULL DEFAULT ''",
        "availability_checked_at": "TEXT NOT NULL DEFAULT ''",
        "bookmarked": "INTEGER NOT NULL DEFAULT 0",
        "priority": "INTEGER NOT NULL DEFAULT 0",
        "priority_rank": "INTEGER NOT NULL DEFAULT 0",
        "application_date": "TEXT NOT NULL DEFAULT ''",
        "deadline": "TEXT NOT NULL DEFAULT ''",
        "followup_date": "TEXT NOT NULL DEFAULT ''",
        "role_override": "TEXT NOT NULL DEFAULT ''",
        "work_mode_override": "TEXT NOT NULL DEFAULT ''",
        "feedback": "TEXT NOT NULL DEFAULT ''",
        "next_step": "TEXT NOT NULL DEFAULT ''",
        "contact": "TEXT NOT NULL DEFAULT ''",
        "interview_notes": "TEXT NOT NULL DEFAULT ''",
        "cv_version": "TEXT NOT NULL DEFAULT ''",
        "cover_version": "TEXT NOT NULL DEFAULT ''",
        "document_links": "TEXT NOT NULL DEFAULT ''",
        "viewed_at": "TEXT NOT NULL DEFAULT ''",
        "viewed_hash": "TEXT NOT NULL DEFAULT ''",
        "changed_at": "TEXT NOT NULL DEFAULT ''",
    }
    for col, definition in extra_cols.items():
        if col not in job_cols:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {definition}")

    letter_cols = {r[1] for r in con.execute("PRAGMA table_info(cover_letters)")}
    if "address_source" not in letter_cols:
        con.execute("ALTER TABLE cover_letters ADD COLUMN address_source TEXT NOT NULL DEFAULT ''")

    # Ensure preferred companies exist
    for name in (
        "ARX Robotics", "HENSOLDT", "MBDA Deutschland", "Siemens",
        "Siemens Healthineers", "Siemens Energy", "Agile Robots", "Alpine Eagle", "NavVis", "Quantum Systems",
    ):
        con.execute("INSERT OR IGNORE INTO companies(name, preference) VALUES (?, 'preferred')", (name,))

    sources = load_sources_config()
    for portal in sources.get("company_portals", []):
        con.execute(
            "INSERT OR IGNORE INTO companies(name, preference) VALUES (?, 'neutral')",
            (canonical_company(portal["company"]),),
        )

    # One-time fast rule score refresh with upgraded profile terms (preserves all AI evaluations!)
    bm_ver = con.execute("SELECT value FROM settings WHERE key='bm_rules_version'").fetchone()
    if not bm_ver or bm_ver[0] != "4":
        # Data fixes for existing Arqus title bug and Diehl location bug
        for row in con.execute("SELECT id, title, url, location FROM jobs WHERE company='Arqus' OR location LIKE '% Funktion'"):
            clean_title = row["title"]
            clean_loc = row["location"]
            if "guardian in orbit" in clean_title.lower() and "/jobs-" in row["url"]:
                slug_part = row["url"].rstrip("/").split("/jobs-")[-1].replace("-", " ").strip()
                if slug_part:
                    clean_title = slug_part.title()
            if clean_loc.endswith(" Funktion"):
                clean_loc = clean_loc[:-9].strip()
            if clean_title != row["title"] or clean_loc != row["location"]:
                con.execute("UPDATE jobs SET title=?, location=? WHERE id=?", (clean_title, clean_loc, row["id"]))

        prefs = {
            r["name"].casefold(): r["preference"]
            for r in con.execute("SELECT name, preference FROM companies")
        }
        updates = []
        for row in con.execute("SELECT id, company, title, location, description FROM jobs"):
            pref = prefs.get(canonical_company(row["company"]).casefold(), "neutral")
            tier = location_tier(row["location"])
            excl = hard_exclusion(row["title"], row["location"])
            if excl:
                sc, rsn = 0, excl
            else:
                sc, rsn = relevance(row["title"], row["description"], pref == "preferred")
            updates.append((tier, sc, rsn, excl, row["id"]))
        with con:
            con.executemany(
                "UPDATE jobs SET tier=?, score=?, reason=?, hard_exclusion=? WHERE id=?",
                updates,
            )
            con.execute(
                "INSERT INTO settings(key, value) VALUES ('bm_rules_version', '4') "
                "ON CONFLICT(key) DO UPDATE SET value='4'"
            )

    # Ensure default Master CV variant exists
    default_cv = con.execute("SELECT id FROM cv_variants WHERE job_id IS NULL LIMIT 1").fetchone()
    if not default_cv:
        profile = load_master_profile()
        stamp = now_iso()
        with con:
            con.execute(
                "INSERT INTO cv_variants(job_id, name, lang, cv_data_json, history_json, created_at, updated_at) "
                "VALUES (NULL, ?, 'de', ?, '[]', ?, ?)",
                (
                    "Standard-Lebenslauf (MBDA-Layout)",
                    json.dumps(profile["cv_base"], ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )

    # Normalize prioritized jobs to single-star priority=1 + contiguous priority_rank
    single_star = con.execute("SELECT value FROM settings WHERE key='bm_single_star_v1'").fetchone()
    if not single_star:
        rows = con.execute(
            """
            SELECT id FROM jobs
            WHERE bookmarked = 1 OR priority > 0
            ORDER BY priority DESC, COALESCE(ai_score, score) DESC, tier ASC, id DESC
            """
        ).fetchall()
        with con:
            for idx, r in enumerate(rows, start=1):
                con.execute(
                    "UPDATE jobs SET bookmarked = 1, priority = 1, priority_rank = ? WHERE id = ?",
                    (idx, r["id"]),
                )
            con.execute("UPDATE jobs SET priority = 0, priority_rank = 0 WHERE bookmarked = 0 AND priority = 0")
            con.execute(
                "INSERT INTO settings(key, value) VALUES ('bm_single_star_v1', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    seed_letter_paragraphs(con)

    # Initial profile CV cleanup guard (no-op for fresh demo databases)
    if not con.execute("SELECT 1 FROM settings WHERE key='bm_cv1_cleanup_v1'").fetchone():
        con.execute(
            "INSERT INTO settings(key, value) VALUES ('bm_cv1_cleanup_v1', '1') ON CONFLICT(key) DO UPDATE SET value='1'"
        )

    con.commit()


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt
    except ValueError:
        return None


def latest_scan_run(con: sqlite3.Connection) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        # Fallback to legacy runs table if no scan_runs yet
        legacy = con.execute("SELECT * FROM runs WHERE kind='feed' ORDER BY id DESC LIMIT 1").fetchone()
        if not legacy:
            return None
        return {
            "id": legacy["id"],
            "started_at": legacy["started"],
            "finished_at": legacy["finished"] or "",
            "duration_ms": 0,
            "sources_total": 0,
            "sources_ok": 0,
            "sources_failed": 1 if legacy["error"] else 0,
            "found_count": legacy["found"],
            "new_count": legacy["added"],
            "closed_count": 0,
            "relisted_count": 0,
            "changed_count": 0,
            "new_job_ids": [],
            "closed_job_ids": [],
            "relisted_job_ids": [],
            "errors": [e for e in (legacy["error"] or "").splitlines() if e.strip()],
            "legacy": True,
        }
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "sources_total": row["sources_total"],
        "sources_ok": row["sources_ok"],
        "sources_failed": row["sources_failed"],
        "found_count": row["found_count"],
        "new_count": row["new_count"],
        "closed_count": row["closed_count"],
        "relisted_count": row["relisted_count"],
        "changed_count": row["changed_count"],
        "new_job_ids": json.loads(row["new_job_ids_json"] or "[]"),
        "closed_job_ids": json.loads(row["closed_job_ids_json"] or "[]"),
        "relisted_job_ids": json.loads(row["relisted_job_ids_json"] or "[]"),
        "errors": json.loads(row["errors_json"] or "[]"),
        "legacy": False,
    }


_SECTION_HEADER_RE = re.compile(
    r"(?:^|\n+|\s{2,})"
    r"(?:[•\-*#►–—]+\s*)?"
    r"("
    r"Deine Aufgaben|Ihre Aufgaben|Zu deinen Aufgaben gehören|Zu Ihren Aufgaben gehören|Aufgaben|Tätigkeit(?:en)?|"
    r"Was dich erwartet|Was Sie erwartet|Deine Mission|Ihre Mission|Deine Rolle|Ihre Rolle|"
    r"What is your Day to Day Mission|Key Responsibilities|Your Responsibilities|Responsibilities|"
    r"Your Mission|What you(?:'ll| will) do|Your Tasks|Your Role|Core Tasks|Scope of Work|"
    r"Dein Profil|Ihr Profil|Was du mitbringst|Das bringst du mit|Was Sie mitbringen|Das bringen Sie mit|"
    r"Qualifikationen|Anforderungen|Dein Background|Ihr Background|"
    r"Essential Skills|Required Skills|Basic Qualifications|Minimum Qualifications|Preferred Qualifications|Qualifications|"
    r"Your Profile|What [Yy]ou [Bb]ring(?: to the team)?|Requirements|Nice[- ]to[- ][Hh]ave(?:s)?|"
    r"Desired Skills|Bonus Points|"
    r"Was wir (?:dir )?bieten|Wir bieten|Deine Benefits|Unsere Benefits|Unser Angebot|Our Benefits|"
    r"What we offer|Benefits|Perks|Why Join [A-Za-z0-9& -]{1,25}\??|Why Join Us\??|"
    r"What Makes This [A-Za-z ]{3,35}\?|"
    r"Beschreibung der Stelle|Über uns|About Us|About the Role|About the Team|Who We Are|Company Overview"
    r")"
    r"\s*[:?]?\s*(?=[A-ZÄÖÜa-z•\-*#0-9\n])",
    flags=re.IGNORECASE,
)

_SENT_SPLIT_RE = re.compile(
    r"(?<!\binkl)(?<!\bbzw)(?<!\bca)(?<!\bvs)(?<!\betc)(?<!\be\.g)(?<!\bi\.e)(?<!\bz\.B)(?<!\bu\.a)"
    r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])"
)

_TASK_STARTERS = (
    r"Konzeption|Entwicklung|Unterstützung|Aufbau|Schnelles Prototyping|Prototyping|"
    r"Enge Zusammenarbeit|Zusammenarbeit|Deployment|Sicherstellung|Analyse|Integration|"
    r"Abstimmung|Aktive Mitarbeit|Mitarbeit|Durchführung|Implementierung|Gestaltung|"
    r"Optimierung|Auswahl|Steuerung|Erprobung|Programmierung|Verantwortung|Begleitung|Erstellung|"
    r"Build|Develop|Design|Implement|Architect|Create|Collect|Supervise|Evaluate|Deploy|"
    r"Collaborate|Work\s+(?:in|with|on|closely)|Drive|Lead|Maintain|Optimize|Integrate|"
    r"Support|Test|Research|Contribute|Analyze|Shape|Grow|Benefit\s+from|Enjoy"
)

_PROFILE_STARTERS = (
    r"Abgeschlossenes\s+(?:Hochschul)?[Ss]tudium|Studium\s+(?:der|im|in)|"
    r"Fundierte\s+Kenntnisse|Sehr\s+gute\s+Kenntnisse|Gute\s+Kenntnisse|Solide\s+Kenntnisse|Kenntnisse\s+(?:in|im)|"
    r"Praktische\s+Erfahrung|Mehrjährige\s+Erfahrung|Erste\s+Erfahrung|Berufserfahrung|Erfahrung\s+(?:in|mit|im|bei)|"
    r"Sicherer\s+Umgang|Vertrautheit\s+mit|Sehr\s+gute\s+Deutsch|Fließende\s+(?:Deutsch|Englisch)|"
    r"Verhandlungssichere|Freude\s+an|Begeisterung\s+für|Teamfähigkeit|Selbstständige\s+und|"
    r"Strong\s+experience|Hands-on\s+experience|Proven\s+(?:experience|ability)|Practical\s+experience|"
    r"Solid\s+(?:experience|understanding)|Experience\s+(?:in|with|building|developing)|"
    r"Proficiency\s+in|Ability\s+to|Familiarity\s+with|Completed\s+degree|Master'?s\s+degree|"
    r"Bachelor'?s\s+degree|Degree\s+in|Fluent\s+in|Good\s+command|Knowledge\s+of|Deep\s+understanding"
)

_INLINE_TASK_SPLIT_RE = re.compile(rf"(?<=[a-zäöüß0-9.)])\s+(?=(?:{_TASK_STARTERS})\b)")
_INLINE_PROFILE_SPLIT_RE = re.compile(rf"(?<=[a-zäöüß0-9.)])\s+(?=(?:{_PROFILE_STARTERS})\b)")
_LABEL_COLON_ITEM_RE = re.compile(
    r"(?:^|(?<=[.!?])\s+)([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9/&,\- ]{2,42}):\s+([A-ZÄÖÜ].+?)(?=(?:(?<=[.!?])\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9/&,\- ]{2,42}:\s+[A-ZÄÖÜ])|$)"
)


def _classify_section(header: str) -> tuple[str, str]:
    h = header.strip().rstrip(":?").strip()
    low = h.casefold()
    if any(k in low for k in ("profil", "mitbring", "qualifikation", "anforderung", "background", "skill", "requirement", "bring to the team", "what you bring", "nice-to-have", "nice to have", "bonus")):
        return "profile", h
    if any(k in low for k in ("aufgabe", "tätigkeit", "erwartet", "mission", "responsibilit", "what you do", "what you will do", "what you'll do", "your tasks", "your role", "core tasks", "scope of work")):
        return "tasks", h
    if any(k in low for k in ("bieten", "benefit", "angebot", "offer", "perk", "why join", "what makes this")):
        return "benefits", h
    return "overview", h or "Überblick & Rolle"


def _group_sentences_into_paragraphs(text: str, max_chars: int = 360) -> list[str]:
    sentences = [s.strip() for s in _SENT_SPLIT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return [text.strip()] if text.strip() else []
    paragraphs: list[str] = []
    buf: list[str] = []
    cur_len = 0
    for s in sentences:
        if buf and (cur_len + len(s) > max_chars or len(buf) >= 3):
            paragraphs.append(" ".join(buf))
            buf = [s]
            cur_len = len(s)
        else:
            buf.append(s)
            cur_len += len(s) + 1
    if buf:
        paragraphs.append(" ".join(buf))
    return paragraphs


def _split_list_body(body: str, category: str) -> tuple[list[str], list[dict[str, str]]]:
    """Return (lead_paragraphs, bullet_items) for tasks/profile/benefits sections."""
    lead_paragraphs: list[str] = []
    working = body.strip()
    if not working:
        return [], []

    # If explicit newline bullets exist, honor them first
    if "\n" in working:
        lines = [ln.strip() for ln in working.splitlines() if ln.strip()]
        items: list[dict[str, str]] = []
        for ln in lines:
            if ln.startswith(("•", "-", "*", "–")):
                clean = ln.lstrip("•-*– ").strip()
                if clean:
                    m = re.match(r"^([^:]{3,44}):\s+(.+)$", clean)
                    if m:
                        items.append({"lead": m.group(1).strip(), "text": m.group(2).strip()})
                    else:
                        items.append({"lead": "", "text": clean})
            elif not items:
                lead_paragraphs.append(ln)
            else:
                items.append({"lead": "", "text": ln})
        if len(items) >= 2:
            return lead_paragraphs, items

    # Extract optional introductory sentence ending with ':' (e.g., "You will contribute to technologies such as:")
    lead_colon = re.match(r"^(.{18,280}?(?:gehören|folgende[ns]?|such as|including|follows|bieten dir|expect|bringst du mit|offer you))\s*:\s+(.+)$", working, flags=re.IGNORECASE)
    if lead_colon:
        lead_paragraphs.extend(_group_sentences_into_paragraphs(lead_colon.group(1).strip() + ":"))
        working = lead_colon.group(2).strip()

    # Check 'Bold Label: Description.' style (e.g., Agile Robots)
    colon_matches = list(_LABEL_COLON_ITEM_RE.finditer(working))
    if len(colon_matches) >= 2:
        items = [{"lead": m.group(1).strip(), "text": m.group(2).strip()} for m in colon_matches]
        first_start = colon_matches[0].start()
        if first_start > 15:
            lead_paragraphs.extend(_group_sentences_into_paragraphs(working[:first_start]))
        return lead_paragraphs, items

    # Split flattened unpunctuated bullets via domain starter verbs/nouns
    splitter = _INLINE_PROFILE_SPLIT_RE if category == "profile" else _INLINE_TASK_SPLIT_RE
    raw_parts: list[str] = []
    last_idx = 0
    for m in splitter.finditer(working):
        if m.start() - last_idx >= 26:
            raw_parts.append(working[last_idx:m.start()].strip())
            last_idx = m.end()
    if last_idx > 0:
        raw_parts.append(working[last_idx:].strip())

    # Further split any long sentence-separated chunks inside list sections
    refined: list[str] = []
    source_chunks = raw_parts if len(raw_parts) >= 2 else [working]
    for chunk in source_chunks:
        sents = [s.strip() for s in _SENT_SPLIT_RE.split(chunk) if s.strip()]
        refined.extend(sents if len(sents) >= 2 else [chunk])

    if len(refined) >= 2:
        out_items: list[dict[str, str]] = []
        for it in refined:
            m = re.match(r"^([A-ZÄÖÜ][^:.]{2,42}):\s+(.+)$", it)
            if m:
                out_items.append({"lead": m.group(1).strip(), "text": m.group(2).strip()})
            else:
                out_items.append({"lead": "", "text": it})
        return lead_paragraphs, out_items

    return _group_sentences_into_paragraphs(working), []


def structure_description(raw_text: str) -> list[dict[str, Any]]:
    """Segment raw or flattened single-line job descriptions into clean, readable sections."""
    if not raw_text or not raw_text.strip():
        return []

    txt = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    # Clean markdown link syntax e.g. [Stellenanzeige](https://...) -> Stellenanzeige
    txt = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", txt)
    # Fix spaced punctuation artifacts from HTML scraping e.g. "mIoU , and" -> "mIoU, and"
    txt = re.sub(r"[ \t]+([,.;!?])", r"\1", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt).strip()

    matches = list(_SECTION_HEADER_RE.finditer(txt))
    raw_sections: list[tuple[str, str, str]] = []

    if not matches:
        raw_sections.append(("overview", "Überblick & Rolle", txt))
    else:
        first_pos = matches[0].start()
        if first_pos > 25:
            intro = txt[:first_pos].strip()
            if intro:
                raw_sections.append(("overview", "Überblick & Rolle", intro))

        for idx, m in enumerate(matches):
            header_text = m.group(1)
            cat, clean_title = _classify_section(header_text)
            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(txt)
            body = txt[body_start:body_end].strip()
            if body:
                raw_sections.append((cat, clean_title, body))

    structured: list[dict[str, Any]] = []
    for cat, title, body in raw_sections:
        if cat in ("tasks", "profile", "benefits"):
            paragraphs, items = _split_list_body(body, cat)
            structured.append(
                {
                    "category": cat,
                    "title": title,
                    "kind": "list" if items else "prose",
                    "paragraphs": paragraphs,
                    "items": items,
                }
            )
        else:
            paragraphs = [ln.strip() for ln in body.splitlines() if ln.strip()] if "\n" in body else _group_sentences_into_paragraphs(body)
            structured.append(
                {
                    "category": "overview",
                    "title": title,
                    "kind": "prose",
                    "paragraphs": paragraphs,
                    "items": [],
                }
            )
    return structured


def resolve_job_folder_name(company: str, title: str = "") -> str:
    comp_clean = slugify_filename(company or "Bewerbung")
    if not title or not title.strip():
        return comp_clean
    clean_title = re.sub(
        r"\s*\(?\s*(m\/w\/d|m\/f\/d|w\/m\/d|d\/m\/w|all genders|gn)\s*\)?",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    words = [w for w in re.split(r"[^A-Za-z0-9äöüÄÖÜß]+", clean_title) if w]
    slug_parts: list[str] = []
    total_len = 0
    for w in words:
        slug_w = slugify_filename(w)
        if not slug_w:
            continue
        if total_len + len(slug_w) + (1 if slug_parts else 0) > 36:
            if not slug_parts:
                slug_parts.append(slug_w[:36])
            break
        slug_parts.append(slug_w)
        total_len += len(slug_w) + (1 if len(slug_parts) > 1 else 0)

    title_slug = "_".join(slug_parts)
    return f"{comp_clean}_{title_slug}" if title_slug else comp_clean


def resolve_company_folder_name(company: str) -> str:
    return slugify_filename(company or "Bewerbung")


def resolve_application_folder(company: str, title: str = "", document_links: str = "") -> Path:
    if document_links:
        p = Path(document_links)
        if p.is_absolute():
            return p
    base = FESTANSTELLUNG_BEWERBUNG_DIR if FESTANSTELLUNG_BEWERBUNG_DIR.exists() else APPLICATIONS_DIR
    return base / resolve_job_folder_name(company, title)


def enrich_job_row(
    row: sqlite3.Row,
    now_dt: datetime,
    scan_new_ids: set[int],
    relisted_ids: set[int],
    cv_job_ids: set[int],
    letter_job_ids: set[int],
    existing_folders: set[str] | None = None,
    include_description: bool = False,
) -> dict[str, Any]:
    first_dt = _parse_iso(row["first_seen"])
    unavail_dt = _parse_iso(row["unavailable_since"])
    last_seen_dt = _parse_iso(row["last_seen"])
    active = bool(row["active"])
    job_id = row["id"]

    age_days = max(0, int((now_dt - first_dt).total_seconds() // 86400)) if first_dt else 0
    is_new_24h = bool(first_dt and (now_dt - first_dt) <= timedelta(hours=36))
    is_new_7d = bool(first_dt and (now_dt - first_dt) <= timedelta(days=7))
    is_new_scan = job_id in scan_new_ids or is_new_24h
    was_relisted = job_id in relisted_ids

    online_duration_days: float | None = None
    offline_days: int | None = None
    disappeared_dt = unavail_dt or (last_seen_dt if not active else None)
    first_seen_date = first_dt.strftime("%d.%m.%Y") if first_dt else "—"
    disappeared_date = disappeared_dt.strftime("%d.%m.%Y") if disappeared_dt else ""

    if active:
        online_duration_days = round(max(0.0, (now_dt - first_dt).total_seconds() / 86400), 1) if first_dt else 0.0
        if was_relisted:
            tenure_kind = "relisted"
            tenure_badge = f"Wieder da · {age_days} Tg."
        elif is_new_scan and age_days == 0:
            tenure_kind = "fresh"
            tenure_badge = "Heute neu"
        elif is_new_7d:
            tenure_kind = "fresh"
            tenure_badge = f"Neu · {age_days} Tg."
        else:
            tenure_kind = "active"
            tenure_badge = f"Aktiv · {age_days} Tg."
    else:
        tenure_kind = "closed"
        tenure_badge = "Nicht mehr verfügbar"
        if disappeared_dt and first_dt:
            online_duration_days = round(max(0.0, (disappeared_dt - first_dt).total_seconds() / 86400), 1)
            offline_days = max(0, int((now_dt - disappeared_dt).total_seconds() // 86400))
        elif disappeared_dt:
            offline_days = max(0, int((now_dt - disappeared_dt).total_seconds() // 86400))

    preference = row["preference"] if "preference" in row.keys() else "neutral"
    rule_score_val = int(row["score"] or 0)
    hard_excl_val = row["hard_exclusion"] or ""
    tier_val = int(row["tier"] or 4)
    rule_eligible, rule_ineligible_reason = rule_eligibility(rule_score_val, hard_excl_val, tier_val, preference)

    has_ai = row["ai_score"] is not None and bool(row["ai_data"])
    ai_parsed: dict[str, Any] | None = None
    if has_ai:
        try:
            ai_parsed = json.loads(row["ai_data"])
        except ValueError:
            has_ai = False

    if hard_excl_val:
        rank_score = 0
    elif has_ai:
        base_ai = int(row["ai_score"] or 0)
        rank_score = 0 if base_ai == 0 else min(100, base_ai + (7 if preference == "preferred" else 0))
    else:
        rank_score = rule_score_val

    if has_ai and int(row["ai_version"] or 0) >= 3:
        score_source = "agy"
    elif has_ai:
        score_source = "ai_legacy"
    else:
        score_source = "rules"

    first_seen_short = first_dt.strftime("%d.%m.") if first_dt else "—"
    first_seen_full = first_dt.strftime("%d.%m.%Y %H:%M") if first_dt else row["first_seen"]

    doc_links = row["document_links"] if "document_links" in row.keys() else ""
    folder_path = resolve_application_folder(row["company"], row["title"], doc_links)
    if existing_folders is not None:
        folder_exists = (
            folder_path.name.casefold() in existing_folders
            or resolve_company_folder_name(row["company"]).casefold() in existing_folders
            or (bool(doc_links) and folder_path.exists())
        )
    else:
        folder_exists = folder_path.exists() or resolve_application_folder(row["company"], "").exists()

    result: dict[str, Any] = {
        "id": job_id,
        "company": row["company"],
        "title": row["title"],
        "location": row["location"],
        "url": row["url"],
        "source": row["source"],
        "tier": tier_val,
        "tier_label": TIER_LABELS.get(tier_val, "Sonstige"),
        "preference": preference,
        "active": active,
        "status": row["status"],
        "status_label": STATUSES.get(row["status"], row["status"]),
        "bookmarked": bool(row["bookmarked"]),
        "priority": int(row["priority"] or 0),
        "priority_rank": int(row["priority_rank"] or 0) if "priority_rank" in row.keys() else 0,
        "feedback": row["feedback"],
        "feedback_label": FEEDBACK_LABELS.get(row["feedback"], row["feedback"] or ""),
        "note": row["note"],
        "next_step": row["next_step"],
        "contact": row["contact"],
        "application_date": row["application_date"],
        "deadline": row["deadline"],
        "followup_date": row["followup_date"],
        "first_seen": row["first_seen"],
        "first_seen_date": first_seen_date,
        "first_seen_short": first_seen_short,
        "first_seen_full": first_seen_full,
        "last_seen": row["last_seen"],
        "changed_at": row["changed_at"],
        "unavailable_since": row["unavailable_since"],
        "disappeared_date": disappeared_date,
        "unavailable_reason": row["unavailable_reason"],
        "unavailable_reason_label": UNAVAILABLE_REASONS.get(row["unavailable_reason"], "Nicht mehr im Stellenportal gelistet"),
        "age_days": age_days,
        "online_duration_days": online_duration_days,
        "offline_days": offline_days,
        "is_new_scan": is_new_scan,
        "is_new_7d": is_new_7d,
        "was_relisted": was_relisted,
        "tenure_kind": tenure_kind,
        "tenure_badge": tenure_badge,
        "rule_score": rule_score_val,
        "rule_reason": row["reason"],
        "hard_exclusion": hard_excl_val,
        "rule_eligible": rule_eligible,
        "rule_ineligible_reason": rule_ineligible_reason,
        "ai_score": row["ai_score"],
        "ai_model": row["ai_model"],
        "ai_date": row["ai_date"],
        "rank_score": rank_score,
        "score_source": score_source,
        "summary_reason": (ai_parsed.get("reason") if ai_parsed else None) or row["reason"],
        "fit_band": (ai_parsed.get("classification", {}) or {}).get("fit_band", "") if ai_parsed else "",
        "role_tag": row["role_override"] or ((ai_parsed.get("classification", {}) or {}).get("role", "") if ai_parsed else ""),
        "work_mode": row["work_mode_override"] or ((ai_parsed.get("classification", {}) or {}).get("work_mode", "") if ai_parsed else ""),
        "has_cv": job_id in cv_job_ids,
        "has_letter": job_id in letter_job_ids,
        "export_folder": str(folder_path),
        "folder_exists": folder_exists,
        "is_unviewed": not row["viewed_at"] or (bool(row["content_hash"]) and row["viewed_hash"] != row["content_hash"]),
        "ai_is_stale": bool(has_ai and row["ai_date"] and row["changed_at"] and row["changed_at"] > row["ai_date"]),
    }

    if include_description:
        result["description"] = row["description"]
        result["structured_description"] = structure_description(row["description"] or "")
        result["interview_notes"] = row["interview_notes"]
        result["ai_parsed"] = ai_parsed
        if row["ai_review_data"]:
            try:
                result["ai_deep_review"] = json.loads(row["ai_review_data"])
            except ValueError:
                result["ai_deep_review"] = None
        else:
            result["ai_deep_review"] = (ai_parsed or {}).get("application_review")
    return result


def _aux_sets(con: sqlite3.Connection) -> tuple[set[int], set[int], set[int], set[int], set[str]]:
    scan = latest_scan_run(con)
    scan_new_ids = set(scan["new_job_ids"]) if scan else set()
    cutoff_30d = (datetime.now(TZ) - timedelta(days=30)).isoformat(timespec="seconds")
    relisted_ids = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT job_id FROM availability_events WHERE event='relisted' AND observed_at>=?",
            (cutoff_30d,),
        )
    }
    cv_ids = {r[0] for r in con.execute("SELECT DISTINCT job_id FROM cv_variants WHERE job_id IS NOT NULL")}
    letter_ids = {r[0] for r in con.execute("SELECT DISTINCT job_id FROM cover_letters WHERE job_id IS NOT NULL")}
    base = FESTANSTELLUNG_BEWERBUNG_DIR if FESTANSTELLUNG_BEWERBUNG_DIR.exists() else APPLICATIONS_DIR
    existing_folders: set[str] = set()
    try:
        if base.exists():
            existing_folders = {p.name.casefold() for p in base.iterdir() if p.is_dir()}
    except OSError:
        pass
    return scan_new_ids, relisted_ids, cv_ids, letter_ids, existing_folders


def query_jobs(
    con: sqlite3.Connection,
    lifecycle: str = "relevant_all",  # 'relevant_all' | 'new' | 'active' | 'closed' | 'relisted' | 'bookmarked' | 'pipeline'
    q: str = "",
    tier: str = "",
    city_area: str = "",
    status: str = "",
    min_score: int = 1,
    include_excluded: bool = False,
    hide_prioritized: bool = False,
    hide_applied: bool = False,
    sort_by: str = "tier_score",  # 'tier_score' | 'score' | 'newest' | 'oldest' | 'recently_closed'
    limit: int = 450,
) -> dict[str, Any]:
    now_dt = datetime.now(TZ)
    scan_new_ids, relisted_ids, cv_ids, letter_ids, existing_folders = _aux_sets(con)

    sql = """
        SELECT
          jobs.id, jobs.company, jobs.title, jobs.location, jobs.url, jobs.source,
          jobs.tier, jobs.score, jobs.reason, jobs.active, jobs.status, jobs.note,
          jobs.first_seen, jobs.last_seen, jobs.changed_at, jobs.content_hash,
          jobs.hard_exclusion, jobs.ai_score, jobs.ai_data, jobs.ai_date, jobs.ai_model,
          jobs.ai_version, jobs.unavailable_since, jobs.unavailable_reason,
          jobs.bookmarked, jobs.priority, jobs.priority_rank, jobs.feedback, jobs.next_step, jobs.contact,
          jobs.application_date, jobs.deadline, jobs.followup_date,
          jobs.role_override, jobs.work_mode_override, jobs.viewed_at, jobs.viewed_hash,
          jobs.document_links,
          COALESCE(companies.preference, 'neutral') AS preference
        FROM jobs
        LEFT JOIN companies ON jobs.company = companies.name COLLATE NOCASE
        WHERE 1=1
    """
    args: list[Any] = []

    if not include_excluded:
        sql += " AND COALESCE(companies.preference, 'neutral') != 'excluded'"

    if hide_prioritized and lifecycle not in ("bookmarked", "prioritized"):
        sql += " AND jobs.bookmarked = 0 AND COALESCE(jobs.priority, 0) = 0"

    if hide_applied and lifecycle not in ("bookmarked", "prioritized"):
        sql += (
            " AND jobs.status NOT IN ('beworben', 'eingeladen', 'gespraech', 'angebot', 'angenommen', 'absage', 'zurueckgezogen')"
            " AND (jobs.application_date IS NULL OR TRIM(jobs.application_date) = '' OR jobs.status IN ('neu', 'ignoriert'))"
        )

    if lifecycle == "new":
        cutoff_7d = (now_dt - timedelta(days=7)).isoformat(timespec="seconds")
        if scan_new_ids:
            placeholders = ",".join("?" for _ in scan_new_ids)
            sql += f" AND jobs.active = 1 AND jobs.status NOT IN ('uninteressant', 'ignoriert') AND (jobs.first_seen >= ? OR jobs.id IN ({placeholders}))"
            args.extend([cutoff_7d, *sorted(scan_new_ids)])
        else:
            sql += " AND jobs.active = 1 AND jobs.status NOT IN ('uninteressant', 'ignoriert') AND jobs.first_seen >= ?"
            args.append(cutoff_7d)
    elif lifecycle == "active":
        sql += " AND jobs.active = 1 AND jobs.status NOT IN ('uninteressant', 'ignoriert')"
    elif lifecycle == "closed":
        sql += " AND jobs.active = 0 AND jobs.status NOT IN ('uninteressant', 'ignoriert')"
    elif lifecycle == "relisted":
        if not relisted_ids:
            return {"items": [], "total_matching": 0}
        placeholders = ",".join("?" for _ in relisted_ids)
        sql += f" AND jobs.id IN ({placeholders}) AND jobs.status NOT IN ('uninteressant', 'ignoriert')"
        args.extend(sorted(relisted_ids))
    elif lifecycle in ("bookmarked", "prioritized"):
        applied_statuses = ("beworben", "eingeladen", "gespraech", "angebot", "angenommen", "absage", "abgeschlossen", "zurueckgezogen", "uninteressant", "ignoriert")
        placeholders = ",".join("?" for _ in applied_statuses)
        sql += f" AND (jobs.bookmarked = 1 OR jobs.priority > 0) AND jobs.status NOT IN ({placeholders}) AND (jobs.application_date IS NULL OR jobs.application_date = '')"
        args.extend(applied_statuses)
    elif lifecycle == "uninteresting":
        sql += " AND (jobs.status IN ('uninteressant', 'ignoriert'))"
    elif lifecycle == "pipeline":
        pipeline_statuses = ("interessant", *ACTIVE_APPLICATIONS, *ARCHIVE_STATUSES)
        placeholders = ",".join("?" for _ in pipeline_statuses)
        sql += f" AND jobs.status IN ({placeholders}) AND jobs.status NOT IN ('uninteressant', 'ignoriert')"
        args.extend(pipeline_statuses)
    elif lifecycle == "relevant_all":
        sql += " AND jobs.status NOT IN ('uninteressant', 'ignoriert')"
    # 'relevant_all' intentionally includes both active=1 and active=0 so unavailable jobs stay visible in red!

    if status and status in STATUSES:
        sql += " AND jobs.status = ?"
        args.append(status)

    if tier in ("1", "2", "3", "4"):
        sql += " AND jobs.tier = ?"
        args.append(int(tier))

    if q.strip():
        needle = f"%{q.strip()}%"
        sql += " AND (jobs.company LIKE ? OR jobs.title LIKE ? OR jobs.location LIKE ? OR jobs.description LIKE ?)"
        args.extend([needle, needle, needle, needle])

    rows = con.execute(sql, args).fetchall()

    enriched: list[dict[str, Any]] = []
    for r in rows:
        if city_area and not matches_city_area(r["location"], city_area):
            continue
        item = enrich_job_row(
            r, now_dt, scan_new_ids, relisted_ids, cv_ids, letter_ids, existing_folders, include_description=False
        )
        if min_score > 0 and lifecycle not in ("closed", "bookmarked", "prioritized", "pipeline", "uninteresting") and item["rank_score"] < min_score:
            continue
        enriched.append(item)

    if lifecycle in ("bookmarked", "prioritized") or sort_by == "priority":
        # Dedicated Prioritized Tab or explicit priority sorting: custom drag-and-drop rank first, then match score
        enriched.sort(
            key=lambda x: (
                x["priority_rank"] if x.get("priority_rank", 0) > 0 else 999999,
                -x["rank_score"],
                x["tier"],
                -x["id"],
            )
        )
    elif sort_by in ("score", "hot"):
        # Pure Match Score (Beste Passung): pure match score DESC, active jobs before inactive, tie-break by tier and id
        enriched.sort(key=lambda x: (-x["rank_score"], -(1 if x["active"] else 0), x["tier"], -(x.get("id") or 0)))
    elif sort_by == "tier_score":
        # Geographically grouped: Tier 1 (Bayern) -> Tier 2 (DE) -> Tier 3/4, then strictly by match score DESC
        enriched.sort(key=lambda x: (x["tier"], -x["rank_score"], -(1 if x["active"] else 0), -(x.get("id") or 0)))
    elif sort_by == "newest":
        enriched.sort(key=lambda x: (x["first_seen"] or "", x["rank_score"]), reverse=True)
    elif sort_by == "oldest":
        enriched.sort(key=lambda x: (x["first_seen"] or "9999", -x["rank_score"]))
    elif sort_by == "recently_closed":
        enriched.sort(key=lambda x: (x["unavailable_since"] or "0000", x["last_seen"], x["rank_score"]), reverse=True)
    else:
        # Default fallback to Beste Passung
        enriched.sort(key=lambda x: (-x["rank_score"], -(1 if x["active"] else 0), x["tier"], -(x.get("id") or 0)))

    total_matching = len(enriched)
    return {
        "items": enriched[:limit],
        "total_matching": total_matching,
    }


def get_job_detail(con: sqlite3.Connection, job_id: int, mark_viewed: bool = True) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT jobs.*, COALESCE(companies.preference, 'neutral') AS preference
        FROM jobs
        LEFT JOIN companies ON jobs.company = companies.name COLLATE NOCASE
        WHERE jobs.id = ?
        """,
        (job_id,),
    ).fetchone()
    if not row:
        return None

    row_data: Any = row
    if mark_viewed and (not row["viewed_at"] or (bool(row["content_hash"]) and row["viewed_hash"] != row["content_hash"])):
        stamp = now_iso()
        with con:
            con.execute(
                "UPDATE jobs SET viewed_at = ?, viewed_hash = content_hash WHERE id = ?",
                (stamp, job_id),
            )
        row_dict = dict(row)
        row_dict["viewed_at"] = stamp
        row_dict["viewed_hash"] = row["content_hash"]
        row_data = row_dict

    now_dt = datetime.now(TZ)
    scan_new_ids, relisted_ids, cv_ids, letter_ids, existing_folders = _aux_sets(con)
    detail = enrich_job_row(
        row_data, now_dt, scan_new_ids, relisted_ids, cv_ids, letter_ids, existing_folders, include_description=True
    )

    detail["availability_history"] = [
        dict(r)
        for r in con.execute(
            "SELECT id, event, observed_at, source, reason FROM availability_events WHERE job_id = ? ORDER BY observed_at DESC, id DESC LIMIT 20",
            (job_id,),
        )
    ]
    detail["workflow_events"] = [
        {
            "id": r["id"],
            "kind": r["kind"],
            "old_value": json.loads(r["old_value"]),
            "new_value": json.loads(r["new_value"]),
            "created": r["created"],
        }
        for r in con.execute(
            "SELECT * FROM application_events WHERE job_id = ? ORDER BY id DESC LIMIT 15",
            (job_id,),
        )
    ]
    detail["cv_variants"] = [
        {
            "id": r["id"],
            "name": r["name"],
            "lang": r["lang"],
            "pdf_de_ready": bool(r["pdf_de_path"] and Path(r["pdf_de_path"]).exists()),
            "pdf_en_ready": bool(r["pdf_en_path"] and Path(r["pdf_en_path"]).exists()),
            "updated_at": r["updated_at"],
        }
        for r in con.execute(
            "SELECT id, name, lang, pdf_de_path, pdf_en_path, updated_at FROM cv_variants WHERE job_id = ? ORDER BY updated_at DESC",
            (job_id,),
        )
    ]
    detail["cover_letters"] = [
        {
            "id": r["id"],
            "name": r["name"],
            "lang": r["lang"],
            "subject": r["subject"],
            "pdf_ready": bool(r["pdf_path"] and Path(r["pdf_path"]).exists()),
            "updated_at": r["updated_at"],
        }
        for r in con.execute(
            "SELECT id, name, lang, subject, pdf_path, updated_at FROM cover_letters WHERE job_id = ? ORDER BY updated_at DESC",
            (job_id,),
        )
    ]
    other_app = con.execute(
        """
        SELECT id, title, status, application_date FROM jobs
        WHERE id != ? AND LOWER(company) = LOWER(?)
          AND status IN ('beworben', 'eingeladen', 'gespraech', 'angebot', 'angenommen')
        ORDER BY application_date DESC, id DESC LIMIT 1
        """,
        (job_id, row["company"]),
    ).fetchone()
    if other_app:
        dt_part = f" (am {other_app['application_date']})" if other_app["application_date"] else ""
        st_lbl = STATUSES.get(other_app["status"], other_app["status"])
        detail["duplicate_applied_warning"] = (
            f"Hinweis: Bei {row['company']} existiert bereits eine aktive Bewerbung ({st_lbl}{dt_part}: „{other_app['title']}“)."
        )
    else:
        detail["duplicate_applied_warning"] = ""
    return detail


def lifecycle_summary(con: sqlite3.Connection) -> dict[str, Any]:
    now_dt = datetime.now(TZ)
    cutoff_7d = (now_dt - timedelta(days=7)).isoformat(timespec="seconds")
    cutoff_30d = (now_dt - timedelta(days=30)).isoformat(timespec="seconds")
    scan = latest_scan_run(con)
    scan_new_ids = set(scan["new_job_ids"]) if scan else set()

    new_cond = "jobs.first_seen >= ?"
    sql_params: list[Any] = [cutoff_7d]
    if scan_new_ids:
        placeholders = ",".join("?" for _ in scan_new_ids)
        new_cond = f"(jobs.first_seen >= ? OR jobs.id IN ({placeholders}))"
        sql_params.extend(sorted(scan_new_ids))
    sql_params.extend([cutoff_7d, cutoff_30d, MIN_AI_RULE_SCORE])

    counts = con.execute(
        f"""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN active = 1 AND status NOT IN ('uninteressant', 'ignoriert') THEN 1 ELSE 0 END) AS active_all,
          SUM(CASE WHEN active = 1 AND status NOT IN ('uninteressant', 'ignoriert') AND (CASE WHEN ai_score IS NOT NULL THEN ai_score > 0 ELSE score > 0 END) AND hard_exclusion = '' THEN 1 ELSE 0 END) AS active_relevant,
          SUM(CASE WHEN status NOT IN ('uninteressant', 'ignoriert') AND (CASE WHEN ai_score IS NOT NULL THEN ai_score > 0 ELSE score > 0 END) AND hard_exclusion = '' THEN 1 ELSE 0 END) AS relevant_all,
          SUM(CASE WHEN active = 0 AND status NOT IN ('uninteressant', 'ignoriert') AND (CASE WHEN ai_score IS NOT NULL THEN ai_score > 0 ELSE score > 0 END) AND hard_exclusion = '' THEN 1 ELSE 0 END) AS closed_relevant,
          SUM(CASE WHEN active = 1 AND {new_cond} AND status NOT IN ('uninteressant', 'ignoriert') AND (CASE WHEN ai_score IS NOT NULL THEN ai_score > 0 ELSE score > 0 END) AND hard_exclusion = '' THEN 1 ELSE 0 END) AS new_7d,
          SUM(CASE WHEN active = 0 AND status NOT IN ('uninteressant', 'ignoriert') THEN 1 ELSE 0 END) AS closed_all,
          SUM(CASE WHEN active = 0 AND status NOT IN ('uninteressant', 'ignoriert') AND unavailable_since >= ? THEN 1 ELSE 0 END) AS closed_7d,
          SUM(CASE WHEN active = 0 AND status NOT IN ('uninteressant', 'ignoriert') AND unavailable_since >= ? THEN 1 ELSE 0 END) AS closed_30d,
          SUM(CASE WHEN (bookmarked = 1 OR priority > 0) AND status NOT IN ('beworben', 'eingeladen', 'gespraech', 'angebot', 'angenommen', 'absage', 'abgeschlossen', 'zurueckgezogen', 'uninteressant', 'ignoriert') AND (application_date IS NULL OR application_date = '') THEN 1 ELSE 0 END) AS bookmarked,
          SUM(CASE WHEN (bookmarked = 1 OR priority > 0) AND status NOT IN ('beworben', 'eingeladen', 'gespraech', 'angebot', 'angenommen', 'absage', 'abgeschlossen', 'zurueckgezogen', 'uninteressant', 'ignoriert') AND (application_date IS NULL OR application_date = '') THEN 1 ELSE 0 END) AS prioritized,
          SUM(CASE WHEN active = 1 AND (bookmarked = 1 OR priority > 0) AND status NOT IN ('beworben', 'eingeladen', 'gespraech', 'angebot', 'angenommen', 'absage', 'abgeschlossen', 'zurueckgezogen', 'uninteressant', 'ignoriert') AND (application_date IS NULL OR application_date = '') THEN 1 ELSE 0 END) AS prioritized_active,
          SUM(CASE WHEN status IN ('uninteressant', 'ignoriert') THEN 1 ELSE 0 END) AS uninteresting,
          SUM(CASE WHEN ai_score IS NOT NULL THEN 1 ELSE 0 END) AS ai_evaluated,
          SUM(CASE WHEN active = 1 AND status NOT IN ('uninteressant', 'ignoriert') AND hard_exclusion = '' AND score >= ? AND (ai_score IS NULL OR ai_version < 3) THEN 1 ELSE 0 END) AS rule_eligible_pending
        FROM jobs
        LEFT JOIN companies ON jobs.company = companies.name COLLATE NOCASE
        WHERE COALESCE(companies.preference, 'neutral') != 'excluded'
        """,
        sql_params,
    ).fetchone()

    relisted_30d = con.execute(
        """
        SELECT COUNT(DISTINCT ae.job_id)
        FROM availability_events ae
        JOIN jobs j ON j.id = ae.job_id
        LEFT JOIN companies c ON j.company = c.name COLLATE NOCASE
        WHERE ae.event = 'relisted'
          AND ae.observed_at >= ?
          AND j.status NOT IN ('uninteressant', 'ignoriert')
          AND COALESCE(c.preference, 'neutral') != 'excluded'
        """,
        (cutoff_30d,),
    ).fetchone()[0]

    pipeline_counts = {
        r["status"]: r["cnt"]
        for r in con.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status")
    }

    tier_counts = [
        {
            "tier": r["tier"],
            "label": TIER_LABELS.get(r["tier"], "Sonstige"),
            "active": r["active_cnt"],
            "closed": r["closed_cnt"],
        }
        for r in con.execute(
            """
            SELECT tier,
              SUM(CASE WHEN active = 1 AND status NOT IN ('uninteressant', 'ignoriert') AND (CASE WHEN ai_score IS NOT NULL THEN ai_score > 0 ELSE score > 0 END) AND hard_exclusion = '' THEN 1 ELSE 0 END) AS active_cnt,
              SUM(CASE WHEN active = 0 AND status NOT IN ('uninteressant', 'ignoriert') AND (CASE WHEN ai_score IS NOT NULL THEN ai_score > 0 ELSE score > 0 END) AND hard_exclusion = '' THEN 1 ELSE 0 END) AS closed_cnt
            FROM jobs
            LEFT JOIN companies ON jobs.company = companies.name COLLATE NOCASE
            WHERE COALESCE(companies.preference, 'neutral') != 'excluded'
            GROUP BY tier
            ORDER BY tier
            """
        )
    ]

    durations = []
    for r in con.execute("SELECT first_seen, unavailable_since FROM jobs WHERE active = 0 AND unavailable_since != ''"):
        d1, d2 = _parse_iso(r["first_seen"]), _parse_iso(r["unavailable_since"])
        if d1 and d2 and d2 >= d1:
            durations.append((d2 - d1).total_seconds() / 86400)

    feed_warnings = [
        dict(r)
        for r in con.execute("SELECT source, company, warning, last_check FROM feed_health WHERE warning != '' ORDER BY company")
    ]

    return {
        "total_stored": counts["total"] or 0,
        "active_all": counts["active_all"] or 0,
        "active_relevant": counts["active_relevant"] or 0,
        "relevant_all": counts["relevant_all"] or 0,
        "closed_relevant": counts["closed_relevant"] or 0,
        "rule_eligible_pending": counts["rule_eligible_pending"] or 0,
        "new_7d": counts["new_7d"] or 0,
        "closed_all": counts["closed_all"] or 0,
        "closed_7d": counts["closed_7d"] or 0,
        "closed_30d": counts["closed_30d"] or 0,
        "relisted_30d": relisted_30d or 0,
        "bookmarked": counts["bookmarked"] or 0,
        "prioritized": counts["prioritized"] or counts["bookmarked"] or 0,
        "prioritized_active": counts["prioritized_active"] or 0,
        "uninteresting": counts["uninteresting"] or 0,
        "ai_evaluated": counts["ai_evaluated"] or 0,
        "pipeline_active": sum(pipeline_counts.get(s, 0) for s in ACTIVE_APPLICATIONS),
        "pipeline_counts": pipeline_counts,
        "tier_counts": tier_counts,
        "median_online_days": round(statistics.median(durations), 1) if durations else None,
        "latest_scan": scan,
        "feed_warnings": feed_warnings,
    }


def update_job_workflow(
    con: sqlite3.Connection,
    job_id: int,
    changes: dict[str, Any],
    confirmed_sent: bool = False,
    event_kind: str = "edit",
) -> dict[str, Any]:
    allowed_fields = {
        "status", "bookmarked", "priority", "priority_rank", "feedback", "note", "next_step",
        "contact", "interview_notes", "application_date", "deadline", "followup_date",
        "role_override", "work_mode_override",
    }
    unknown = set(changes) - allowed_fields
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")

    row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise ValueError("Stelle nicht gefunden")

    clean: dict[str, Any] = {}
    for k, v in changes.items():
        if k == "bookmarked":
            bv = 1 if bool(v) else 0
            clean[k] = bv
            if "priority" not in changes:
                clean["priority"] = 1 if bv == 1 else 0
            if bv == 1 and int(row["priority_rank"] or 0) == 0 and "priority_rank" not in changes:
                max_rank = con.execute(
                    "SELECT COALESCE(MAX(priority_rank), 0) FROM jobs WHERE bookmarked = 1 OR priority > 0"
                ).fetchone()[0]
                clean["priority_rank"] = int(max_rank or 0) + 1
            elif bv == 0 and "priority_rank" not in changes:
                clean["priority_rank"] = 0
        elif k == "priority":
            iv = 1 if int(v) > 0 else 0
            clean[k] = iv
            if "bookmarked" not in changes:
                clean["bookmarked"] = iv
            if iv == 1 and int(row["priority_rank"] or 0) == 0 and "priority_rank" not in changes:
                max_rank = con.execute(
                    "SELECT COALESCE(MAX(priority_rank), 0) FROM jobs WHERE bookmarked = 1 OR priority > 0"
                ).fetchone()[0]
                clean["priority_rank"] = int(max_rank or 0) + 1
            elif iv == 0 and "priority_rank" not in changes:
                clean["priority_rank"] = 0
        elif k == "priority_rank":
            clean[k] = max(0, int(v or 0))
        elif k == "status":
            if v not in STATUSES:
                raise ValueError("Ungültiger Status")
            clean[k] = v
            if v in ("uninteressant", "ignoriert"):
                if "priority" not in changes:
                    clean["priority"] = 0
                if "bookmarked" not in changes:
                    clean["bookmarked"] = 0
                if "priority_rank" not in changes:
                    clean["priority_rank"] = 0
        elif k == "feedback":
            clean[k] = str(v or "").strip()[:100]
        elif k in ("application_date", "deadline", "followup_date"):
            s = str(v or "").strip()
            if s:
                date.fromisoformat(s)
            clean[k] = s
        else:
            clean[k] = str(v or "")[:20000]

    if clean.get("status") == "beworben" and row["status"] != "beworben":
        if not confirmed_sent:
            raise ValueError("Bitte bestätigen, dass die Bewerbung tatsächlich abgesendet wurde.")
        if not clean.get("application_date") and not row["application_date"]:
            clean["application_date"] = datetime.now(TZ).date().isoformat()
        if not clean.get("followup_date") and not row["followup_date"]:
            clean["followup_date"] = (datetime.now(TZ).date() + timedelta(days=10)).isoformat()

    diff = {k: v for k, v in clean.items() if row[k] != v}
    if not diff:
        return {"updated": False, "event_id": None}

    old = {k: row[k] for k in diff}
    stamp = now_iso()
    with con:
        set_clause = ", ".join(f"{k} = ?" for k in diff)
        con.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", (*diff.values(), job_id))
        if diff.get("status") in ("vorbereiten", "beworben"):
            snap = {k: row[k] for k in ("company", "title", "location", "description", "url")}
            con.execute(
                "INSERT INTO job_snapshots(job_id, content, content_hash, reason, created) VALUES (?, ?, ?, ?, ?)",
                (job_id, json.dumps(snap, ensure_ascii=False), row["content_hash"], diff["status"], stamp),
            )
            let_row = con.execute(
                "SELECT body_text, lang, company, subject FROM cover_letters WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if let_row and (let_row["body_text"] or "").strip():
                ingest_paragraphs_from_body(
                    con,
                    let_row["body_text"],
                    lang=let_row["lang"] or "de",
                    company=let_row["company"] or row["company"],
                    job_title=row["title"] or let_row["subject"] or "",
                    job_id=job_id,
                    source_kind="sent",
                    commit=False,
                )
        cur = con.execute(
            "INSERT INTO application_events(job_id, kind, old_value, new_value, created) VALUES (?, ?, ?, ?, ?)",
            (job_id, event_kind, json.dumps(old, ensure_ascii=False), json.dumps(diff, ensure_ascii=False), stamp),
        )
    return {"updated": True, "event_id": cur.lastrowid}


def undo_job_event(con: sqlite3.Connection, event_id: int) -> dict[str, Any]:
    event = con.execute("SELECT * FROM application_events WHERE id = ?", (event_id,)).fetchone()
    if not event or event["kind"] == "undo":
        raise ValueError("Änderung kann nicht rückgängig gemacht werden")
    old_values = json.loads(event["old_value"])
    return update_job_workflow(con, event["job_id"], old_values, confirmed_sent=True, event_kind="undo")


def get_interview_prep(con: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM interview_preps WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["prep_json"])
    except Exception:
        data = {}
    return {
        "job_id": row["job_id"],
        "model": row["model"],
        "updated_at": row["updated_at"],
        "prep": data,
    }


def save_interview_prep(con: sqlite3.Connection, job_id: int, prep: dict[str, Any], model: str = "") -> dict[str, Any]:
    stamp = now_iso()
    prep_json = json.dumps(prep, ensure_ascii=False)
    with con:
        con.execute(
            """
            INSERT INTO interview_preps (job_id, prep_json, model, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                prep_json = excluded.prep_json,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (job_id, prep_json, model, stamp),
        )
    return {
        "job_id": job_id,
        "model": model,
        "updated_at": stamp,
        "prep": prep,
    }


def get_skill_gap_heatmap(con: sqlite3.Connection, min_score: int = 55) -> dict[str, Any]:
    """Aggregiert die häufigsten Skill-Lücken (gaps) über alle relevanten KI-bewerteten Stellen."""
    rows = con.execute(
        """
        SELECT jobs.id, jobs.company, jobs.title, jobs.score, jobs.ai_score, jobs.status, jobs.bookmarked, jobs.ai_data
        FROM jobs
        LEFT JOIN companies ON jobs.company = companies.name COLLATE NOCASE
        WHERE jobs.ai_data IS NOT NULL AND jobs.ai_data != ''
          AND jobs.active = 1
          AND jobs.status NOT IN ('uninteressant', 'ignoriert')
          AND COALESCE(companies.preference, 'neutral') != 'excluded'
          AND (COALESCE(jobs.ai_score, 0) >= ? OR jobs.bookmarked = 1 OR jobs.status != 'neu')
        ORDER BY COALESCE(jobs.ai_score, jobs.score) DESC
        """,
        (min_score,),
    ).fetchall()

    KNOWN_CLUSTERS: list[tuple[str, list[str]]] = [
        ("Classic AUTOSAR / Adaptive AUTOSAR", [r"autosar"]),
        ("ISO 26262 / Functional Safety (FuSa)", [r"26262", r"functional safety", r"fusa", r"sicherheitsnorm", r"asil"]),
        ("Mehrjährige Vollzeit-Berufserfahrung / Seniorität", [r"berufserfahrung", r"years of experience", r"senior", r"mehrjährig", r"industrieerfahrung"]),
        ("Embedded C / Mikrocontroller / RTOS", [r"embedded", r"mikrocontroller", r"rtos", r"firmware", r"bare[- ]metal"]),
        ("Cloud / MLOps / Kubernetes / AWS / Azure", [r"kubernetes", r"k8s", r"aws", r"azure", r"gcp", r"mlops", r"cloud"]),
        ("CUDA / TensorRT / GPU-Optimierung", [r"cuda", r"tensorrt", r"gpu", r"onnx"]),
        ("SLAM / Sensorfusion / Kalman-Filter", [r"slam", r"kalman", r"sensor\s*fusion", r"lokalisierung", r"localization"]),
        ("SPS / PLC / TwinCAT / Beckhoff / Siemens", [r"\bsps\b", r"\bplc\b", r"twincat", r"beckhoff", r"siemens", r"tia"]),
        ("CI/CD / DevOps / Software-Architektur", [r"ci/cd", r"devops", r"jenkins", r"gitlab", r"architektur"]),
        ("Promotion / PhD", [r"promotion", r"\bphd\b", r"doktor"]),
    ]

    clusters: dict[str, dict[str, Any]] = {}
    analyzed_jobs = 0

    for row in rows:
        try:
            ai = json.loads(row["ai_data"] or "{}")
        except Exception:
            continue
        gaps = ai.get("gaps") or []
        if not gaps:
            continue
        analyzed_jobs += 1
        for gap_text in gaps:
            gap_str = str(gap_text).strip()
            if not gap_str:
                continue
            matched_label = None
            for label, patterns in KNOWN_CLUSTERS:
                if any(re.search(p, gap_str, re.I) for p in patterns):
                    matched_label = label
                    break
            if not matched_label:
                matched_label = gap_str[:55] + ("…" if len(gap_str) > 55 else "")

            entry = clusters.setdefault(
                matched_label,
                {"topic": matched_label, "count": 0, "examples": [], "companies": []},
            )
            entry["count"] += 1
            if len(entry["examples"]) < 3 and gap_str not in entry["examples"]:
                entry["examples"].append(gap_str)
            comp = str(row["company"] or "").strip()
            if comp and comp not in entry["companies"] and len(entry["companies"]) < 5:
                entry["companies"].append(comp)

    ranked = sorted(clusters.values(), key=lambda x: x["count"], reverse=True)[:10]
    return {
        "analyzed_jobs": analyzed_jobs,
        "items": ranked,
    }


def get_pipeline_sankey_data(con: sqlite3.Connection) -> dict[str, Any]:
    """Generates balanced Sankey flow-chart data (Reddit / SankeyMATIC style) for the job application pipeline."""
    rows = con.execute(
        """
        SELECT id, company, title, status, application_date, followup_date 
        FROM jobs 
        WHERE status NOT IN ('neu', 'ignoriert', 'uninteressant')
        """
    ).fetchall()

    current_status_map = {r[0]: r[3] for r in rows}
    job_ids = list(current_status_map.keys())

    interview_job_ids = set()
    if job_ids:
        placeholders = ",".join("?" for _ in job_ids)
        events = con.execute(
            f"""
            SELECT job_id, kind, new_value, old_value
            FROM application_events
            WHERE job_id IN ({placeholders})
            ORDER BY id ASC
            """,
            job_ids,
        ).fetchall()

        per_job_history: dict[int, list[tuple[str | None, str | None]]] = {}
        for jid, kind, new_val_s, old_val_s in events:
            if kind == "undo":
                stack = per_job_history.get(jid)
                if stack:
                    stack.pop()
                continue
            try:
                new_v = json.loads(new_val_s) if new_val_s else {}
                old_v = json.loads(old_val_s) if old_val_s else {}
                st_new = new_v.get("status")
                st_old = old_v.get("status")
                if st_new is not None or st_old is not None:
                    per_job_history.setdefault(jid, []).append((st_new, st_old))
            except Exception:
                pass
        for jid, hist in per_job_history.items():
            if any(
                st_new in ("eingeladen", "gespraech", "angebot", "angenommen")
                or st_old in ("eingeladen", "gespraech", "angebot", "angenommen")
                for st_new, st_old in hist
            ):
                interview_job_ids.add(jid)

    interessant_cnt = 0
    vorbereiten_cnt = 0
    warten_cnt = 0
    direkt_absage_cnt = 0
    interview_aktiv_cnt = 0
    interview_absage_cnt = 0
    zurueckgezogen_pre_cnt = 0
    zurueckgezogen_post_cnt = 0
    angebot_cnt = 0
    angenommen_cnt = 0

    for jid, st in current_status_map.items():
        if st == "interessant":
            interessant_cnt += 1
        elif st == "vorbereiten":
            vorbereiten_cnt += 1
        elif st == "beworben":
            warten_cnt += 1
        elif st in ("eingeladen", "gespraech"):
            interview_aktiv_cnt += 1
        elif st == "angebot":
            angebot_cnt += 1
        elif st == "angenommen":
            angenommen_cnt += 1
        elif st == "zurueckgezogen":
            if jid in interview_job_ids:
                zurueckgezogen_post_cnt += 1
            else:
                zurueckgezogen_pre_cnt += 1
        elif st in ("absage", "abgeschlossen"):
            if jid in interview_job_ids:
                interview_absage_cnt += 1
            else:
                direkt_absage_cnt += 1

    offer_total = angebot_cnt + angenommen_cnt
    interview_total = interview_aktiv_cnt + interview_absage_cnt + zurueckgezogen_post_cnt + offer_total
    applied_total = warten_cnt + direkt_absage_cnt + zurueckgezogen_pre_cnt + interview_total
    pipeline_total = interessant_cnt + vorbereiten_cnt + applied_total

    raw_nodes = [
        {"id": "pipeline", "label": "Pipeline", "stage": 0, "color": "#475569", "value": pipeline_total},
        {"id": "interessant", "label": "Interessant", "stage": 1, "color": "#3b82f6", "value": interessant_cnt},
        {"id": "vorbereiten", "label": "In Vorbereitung", "stage": 1, "color": "#8b5cf6", "value": vorbereiten_cnt},
        {"id": "beworben", "label": "Beworben", "stage": 1, "color": "#0284c7", "value": applied_total},
        {"id": "warten", "label": "Warten auf Rückmeldung", "stage": 2, "color": "#f59e0b", "value": warten_cnt},
        {"id": "direkt_absage", "label": "Direkte Absage", "stage": 2, "color": "#ef4444", "value": direkt_absage_cnt},
        {"id": "zurueckgezogen_pre", "label": "Zurückgezogen", "stage": 2, "color": "#94a3b8", "value": zurueckgezogen_pre_cnt},
        {"id": "interview", "label": "Interview / Gespräch", "stage": 2, "color": "#10b981", "value": interview_total},
        {"id": "im_gespraech", "label": "Im Gespräch", "stage": 3, "color": "#06b6d4", "value": interview_aktiv_cnt},
        {"id": "interview_absage", "label": "Absage nach Gespräch", "stage": 3, "color": "#f43f5e", "value": interview_absage_cnt},
        {"id": "zurueckgezogen_post", "label": "Zurückgezogen (n. Gespräch)", "stage": 3, "color": "#94a3b8", "value": zurueckgezogen_post_cnt},
        {"id": "angebot", "label": "Angebot", "stage": 3, "color": "#10b981", "value": offer_total},
        {"id": "angebot_offen", "label": "Angebot vorliegend", "stage": 4, "color": "#059669", "value": angebot_cnt},
        {"id": "angenommen", "label": "Angenommen 🏆", "stage": 4, "color": "#eab308", "value": angenommen_cnt},
    ]

    flows = []
    if interessant_cnt > 0:
        flows.append({"source": "pipeline", "target": "interessant", "source_label": "Pipeline", "target_label": "Interessant", "value": interessant_cnt})
    if vorbereiten_cnt > 0:
        flows.append({"source": "pipeline", "target": "vorbereiten", "source_label": "Pipeline", "target_label": "In Vorbereitung", "value": vorbereiten_cnt})
    if applied_total > 0:
        flows.append({"source": "pipeline", "target": "beworben", "source_label": "Pipeline", "target_label": "Beworben", "value": applied_total})

    if warten_cnt > 0:
        flows.append({"source": "beworben", "target": "warten", "source_label": "Beworben", "target_label": "Warten auf Rückmeldung", "value": warten_cnt})
    if direkt_absage_cnt > 0:
        flows.append({"source": "beworben", "target": "direkt_absage", "source_label": "Beworben", "target_label": "Direkte Absage", "value": direkt_absage_cnt})
    if zurueckgezogen_pre_cnt > 0:
        flows.append({"source": "beworben", "target": "zurueckgezogen_pre", "source_label": "Beworben", "target_label": "Zurückgezogen", "value": zurueckgezogen_pre_cnt})
    if interview_total > 0:
        flows.append({"source": "beworben", "target": "interview", "source_label": "Beworben", "target_label": "Interview / Gespräch", "value": interview_total})

    if interview_aktiv_cnt > 0:
        flows.append({"source": "interview", "target": "im_gespraech", "source_label": "Interview / Gespräch", "target_label": "Im Gespräch", "value": interview_aktiv_cnt})
    if interview_absage_cnt > 0:
        flows.append({"source": "interview", "target": "interview_absage", "source_label": "Interview / Gespräch", "target_label": "Absage nach Gespräch", "value": interview_absage_cnt})
    if zurueckgezogen_post_cnt > 0:
        flows.append({"source": "interview", "target": "zurueckgezogen_post", "source_label": "Interview / Gespräch", "target_label": "Zurückgezogen (n. Gespräch)", "value": zurueckgezogen_post_cnt})
    if offer_total > 0:
        flows.append({"source": "interview", "target": "angebot", "source_label": "Interview / Gespräch", "target_label": "Angebot", "value": offer_total})

    if angebot_cnt > 0:
        flows.append({"source": "angebot", "target": "angebot_offen", "source_label": "Angebot", "target_label": "Angebot vorliegend", "value": angebot_cnt})
    if angenommen_cnt > 0:
        flows.append({"source": "angebot", "target": "angenommen", "source_label": "Angebot", "target_label": "Angenommen 🏆", "value": angenommen_cnt})

    sankeymatic_text = "\n".join(f"{f['source_label']} [{f['value']}] {f['target_label']}" for f in flows)

    demo_nodes = [
        {"id": "pipeline", "label": "Pipeline", "stage": 0, "color": "#475569", "value": 45},
        {"id": "interessant", "label": "Interessant", "stage": 1, "color": "#3b82f6", "value": 8},
        {"id": "vorbereiten", "label": "In Vorbereitung", "stage": 1, "color": "#8b5cf6", "value": 5},
        {"id": "beworben", "label": "Beworben", "stage": 1, "color": "#0284c7", "value": 32},
        {"id": "warten", "label": "Warten auf Rückmeldung", "stage": 2, "color": "#f59e0b", "value": 14},
        {"id": "direkt_absage", "label": "Direkte Absage", "stage": 2, "color": "#ef4444", "value": 11},
        {"id": "zurueckgezogen_pre", "label": "Zurückgezogen", "stage": 2, "color": "#94a3b8", "value": 1},
        {"id": "interview", "label": "Interview / Gespräch", "stage": 2, "color": "#10b981", "value": 6},
        {"id": "im_gespraech", "label": "Im Gespräch", "stage": 3, "color": "#06b6d4", "value": 2},
        {"id": "interview_absage", "label": "Absage nach Gespräch", "stage": 3, "color": "#f43f5e", "value": 2},
        {"id": "angebot", "label": "Angebot", "stage": 3, "color": "#10b981", "value": 2},
        {"id": "angebot_offen", "label": "Angebot vorliegend", "stage": 4, "color": "#059669", "value": 1},
        {"id": "angenommen", "label": "Angenommen 🏆", "stage": 4, "color": "#eab308", "value": 1},
    ]

    demo_flows = [
        {"source": "pipeline", "target": "interessant", "source_label": "Pipeline", "target_label": "Interessant", "value": 8},
        {"source": "pipeline", "target": "vorbereiten", "source_label": "Pipeline", "target_label": "In Vorbereitung", "value": 5},
        {"source": "pipeline", "target": "beworben", "source_label": "Pipeline", "target_label": "Beworben", "value": 32},
        {"source": "beworben", "target": "warten", "source_label": "Beworben", "target_label": "Warten auf Rückmeldung", "value": 14},
        {"source": "beworben", "target": "direkt_absage", "source_label": "Beworben", "target_label": "Direkte Absage", "value": 11},
        {"source": "beworben", "target": "zurueckgezogen_pre", "source_label": "Beworben", "target_label": "Zurückgezogen", "value": 1},
        {"source": "beworben", "target": "interview", "source_label": "Beworben", "target_label": "Interview / Gespräch", "value": 6},
        {"source": "interview", "target": "im_gespraech", "source_label": "Interview / Gespräch", "target_label": "Im Gespräch", "value": 2},
        {"source": "interview", "target": "interview_absage", "source_label": "Interview / Gespräch", "target_label": "Absage nach Gespräch", "value": 2},
        {"source": "interview", "target": "angebot", "source_label": "Interview / Gespräch", "target_label": "Angebot", "value": 2},
        {"source": "angebot", "target": "angebot_offen", "source_label": "Angebot", "target_label": "Angebot vorliegend", "value": 1},
        {"source": "angebot", "target": "angenommen", "source_label": "Angebot", "target_label": "Angenommen 🏆", "value": 1},
    ]
    demo_text = "\n".join(f"{f['source_label']} [{f['value']}] {f['target_label']}" for f in demo_flows)

    response_rate = round(((direkt_absage_cnt + interview_total) / applied_total) * 100, 1) if applied_total > 0 else 0.0
    interview_rate = round((interview_total / applied_total) * 100, 1) if applied_total > 0 else 0.0
    offer_rate = round((offer_total / interview_total) * 100, 1) if interview_total > 0 else 0.0

    return {
        "pipeline_total": pipeline_total,
        "interessant": interessant_cnt,
        "vorbereiten": vorbereiten_cnt,
        "applied_total": applied_total,
        "warten": warten_cnt,
        "direkt_absage": direkt_absage_cnt,
        "interview_total": interview_total,
        "interview_aktiv": interview_aktiv_cnt,
        "interview_absage": interview_absage_cnt,
        "angebot": angebot_cnt,
        "angenommen": angenommen_cnt,
        "response_rate": response_rate,
        "interview_rate": interview_rate,
        "offer_rate": offer_rate,
        "nodes": [n for n in raw_nodes if n["value"] > 0],
        "flows": flows,
        "sankeymatic_text": sankeymatic_text,
        "demo_nodes": demo_nodes,
        "demo_flows": demo_flows,
        "demo_text": demo_text,
        "is_empty": pipeline_total == 0,
    }


def get_companies_overview(con: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate integration status, feed health, and historical job statistics per company."""
    from backend.scanner.parsers import is_complete_source, source_key

    config = load_sources_config()
    feeds_raw: list[dict[str, Any]] = list(config.get("feeds", []))
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_search'").fetchone():
        for r in con.execute(
            "SELECT source_json FROM company_search WHERE coverage='feed' AND dynamic=1 AND source_json!=''"
        ):
            try:
                feeds_raw.append(json.loads(r["source_json"]))
            except ValueError:
                pass

    health_map = {
        r["source"]: dict(r)
        for r in con.execute("SELECT * FROM feed_health")
    }
    prefs = {
        r["name"].casefold(): r["preference"]
        for r in con.execute("SELECT name, preference FROM companies")
    }
    latest_scan_row = con.execute(
        "SELECT finished_at, started_at, errors_json FROM scan_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    latest_scan_at = (
        (latest_scan_row["finished_at"] or latest_scan_row["started_at"])
        if latest_scan_row
        else ""
    )

    portal_refs: dict[str, dict[str, Any]] = {}
    for cp in config.get("company_portals", []):
        cname = canonical_company(cp.get("company", ""))
        if cname:
            portal_refs[cname.casefold()] = cp

    # Group configured feeds by canonical company
    comp_names: dict[str, str] = {}
    feeds_by_comp: dict[str, list[dict[str, Any]]] = {}
    seen_feed_keys: set[str] = set()
    for feed in feeds_raw:
        cname = canonical_company(feed.get("company", ""))
        if not cname:
            continue
        ckey = cname.casefold()
        comp_names.setdefault(ckey, cname)
        skey = source_key(feed)
        if skey in seen_feed_keys:
            continue
        seen_feed_keys.add(skey)
        fh = health_map.get(skey) or {}
        warning = str(fh.get("warning") or "").strip()
        last_check = str(fh.get("last_check") or "")
        current_count = fh.get("current_count")
        last_good_count = fh.get("last_good_count")
        enabled = bool(feed.get("enabled", True))
        if not enabled:
            f_status = "disabled"
        elif warning and not (
            warning.startswith("Auffälliger Rückgang")
            or warning.startswith("Feed lieferte 0 Stellen")
        ):
            f_status = "error"
        elif warning:
            f_status = "warning"
        elif not last_check:
            f_status = "pending"
        elif (current_count or 0) > 0:
            f_status = "ok"
        else:
            f_status = "empty"

        url = str(feed.get("url") or "")
        if not url:
            if feed.get("type") == "greenhouse" and feed.get("board"):
                url = f"https://boards.greenhouse.io/{feed['board']}"
            elif feed.get("type") == "ashby" and feed.get("board"):
                url = f"https://jobs.ashbyhq.com/{feed['board']}"
            elif feed.get("type") == "lever" and feed.get("board"):
                host = "jobs.eu.lever.co" if feed.get("region") == "eu" else "jobs.lever.co"
                url = f"https://{host}/{feed['board']}"
            elif feed.get("type") == "smartrecruiters" and feed.get("board"):
                url = f"https://careers.smartrecruiters.com/{feed['board']}"
            elif feed.get("type") == "join" and feed.get("board"):
                url = f"https://join.com/companies/{feed['board']}"

        feeds_by_comp.setdefault(ckey, []).append(
            {
                "source": skey,
                "type": str(feed.get("type") or ""),
                "parser": str(feed.get("parser") or ""),
                "url": url,
                "enabled": enabled,
                "complete_feed": is_complete_source(skey),
                "last_check": last_check,
                "current_count": current_count,
                "last_good_count": last_good_count,
                "warning": warning,
                "status": f_status,
            }
        )

    # Aggregate jobs statistics per canonical company
    job_stats: dict[str, dict[str, Any]] = {}
    job_samples: dict[str, list[dict[str, Any]]] = {}
    for row in con.execute(
        """
        SELECT id, company, title, location, url, source, tier, score, ai_score,
               hard_exclusion, status, active, bookmarked, priority, first_seen, last_seen
        FROM jobs
        """
    ):
        cname = canonical_company(row["company"] or "")
        if not cname:
            continue
        ckey = cname.casefold()
        comp_names.setdefault(ckey, cname)
        st = job_stats.setdefault(
            ckey,
            {
                "active_jobs": 0,
                "total_jobs": 0,
                "closed_jobs": 0,
                "bayern_active": 0,
                "bayern_total": 0,
                "qualified_active": 0,
                "max_score": 0,
                "_scores": [],
                "applied_count": 0,
                "bookmarked_count": 0,
                "first_seen_min": "",
                "last_seen_max": "",
            },
        )
        eff_score = row["ai_score"] if row["ai_score"] is not None else row["score"]
        is_act = bool(row["active"])
        st["total_jobs"] += 1
        if is_act:
            st["active_jobs"] += 1
            if row["tier"] == 1:
                st["bayern_active"] += 1
            if not (row["hard_exclusion"] or "") and row["score"] >= MIN_AI_RULE_SCORE:
                st["qualified_active"] += 1
        else:
            st["closed_jobs"] += 1
        if row["tier"] == 1:
            st["bayern_total"] += 1
        if eff_score > st["max_score"]:
            st["max_score"] = eff_score
        if not (row["hard_exclusion"] or "") and eff_score > 0:
            st["_scores"].append(eff_score)
        if row["status"] in ACTIVE_APPLICATIONS or row["status"] in ("absage", "abgeschlossen", "zurueckgezogen"):
            st["applied_count"] += 1
        if row["bookmarked"] or (row["priority"] or 0) > 0:
            st["bookmarked_count"] += 1
        fs = row["first_seen"] or ""
        ls = row["last_seen"] or ""
        if fs and (not st["first_seen_min"] or fs < st["first_seen_min"]):
            st["first_seen_min"] = fs
        if ls and (not st["last_seen_max"] or ls > st["last_seen_max"]):
            st["last_seen_max"] = ls

        job_samples.setdefault(ckey, []).append(
            {
                "id": row["id"],
                "title": row["title"],
                "location": row["location"],
                "score": eff_score,
                "tier": row["tier"],
                "active": is_act,
                "status": row["status"],
                "url": row["url"],
            }
        )

    for ckey, cp in portal_refs.items():
        comp_names.setdefault(ckey, canonical_company(cp.get("company", "")))

    # Build final company rows
    items: list[dict[str, Any]] = []
    status_counts = {
        "ok": 0,
        "error": 0,
        "warning": 0,
        "empty": 0,
        "pending": 0,
        "excluded": 0,
        "reference": 0,
        "manual": 0,
    }
    connected_total = 0
    total_active_jobs = 0
    total_historical_jobs = 0

    for ckey, cname in comp_names.items():
        cfeeds = feeds_by_comp.get(ckey, [])
        pref = prefs.get(ckey, "neutral")
        cp_ref = portal_refs.get(ckey) or {}
        raw_st = job_stats.get(ckey)
        active_jobs = raw_st["active_jobs"] if raw_st else 0
        total_jobs = raw_st["total_jobs"] if raw_st else 0
        closed_jobs = raw_st["closed_jobs"] if raw_st else 0
        bayern_active = raw_st["bayern_active"] if raw_st else 0
        bayern_total = raw_st["bayern_total"] if raw_st else 0
        qualified_active = raw_st["qualified_active"] if raw_st else 0
        max_score = raw_st["max_score"] if raw_st else 0
        avg_score = round(statistics.mean(raw_st["_scores"]), 1) if raw_st and raw_st["_scores"] else 0.0
        applied_count = raw_st["applied_count"] if raw_st else 0
        bookmarked_count = raw_st["bookmarked_count"] if raw_st else 0
        first_seen_min = raw_st["first_seen_min"] if raw_st else ""
        last_seen_max = raw_st["last_seen_max"] if raw_st else ""

        # Upgrade 'empty' feed status to 'ok' if active jobs exist (e.g. partial feed or just synced)
        for fd in cfeeds:
            if fd["status"] == "empty" and active_jobs > 0 and not fd["warning"]:
                fd["status"] = "ok"

        if cfeeds:
            integration = "feed"
            connected_total += 1
            if pref == "excluded":
                status = "excluded"
                status_label = "Ausgeschlossen"
            elif all(f["status"] == "disabled" for f in cfeeds):
                status = "empty"
                status_label = "Feed pausiert"
            elif any(f["status"] == "error" for f in cfeeds):
                status = "error"
                status_label = "Abruf fehlgeschlagen"
            elif any(f["status"] == "warning" for f in cfeeds):
                status = "warning"
                status_label = "Warnung"
            elif all(f["status"] == "pending" for f in cfeeds):
                status = "pending"
                status_label = "Noch nicht gescannt"
            elif any(f["status"] == "ok" for f in cfeeds) or active_jobs > 0:
                status = "ok"
                status_label = "Erfolgreich"
            else:
                status = "empty"
                status_label = "Abgerufen (0 Stellen)"
        elif cp_ref:
            integration = "reference"
            status = "reference"
            status_label = "Nur Referenz (kein Feed)"
        else:
            integration = "manual"
            status = "manual"
            status_label = "Manuell / Websuche"

        status_counts[status] += 1
        total_active_jobs += active_jobs
        total_historical_jobs += total_jobs

        last_check = max((f["last_check"] for f in cfeeds if f["last_check"]), default="")
        primary_error = next((f["warning"] for f in cfeeds if f["warning"]), "")
        samples = sorted(
            job_samples.get(ckey, []),
            key=lambda x: (1 if x["active"] else 0, x["score"]),
            reverse=True,
        )[:4]

        primary_url = (
            next((f["url"] for f in cfeeds if f["url"]), "")
            or str(cp_ref.get("url") or "")
            or str(cp_ref.get("official_careers_url") or "")
        )

        items.append(
            {
                "company": cname,
                "integration": integration,
                "status": status,
                "status_label": status_label,
                "preference": pref,
                "last_check": last_check,
                "error_message": primary_error,
                "primary_url": primary_url,
                "official_careers_url": str(cp_ref.get("official_careers_url") or cp_ref.get("url") or primary_url),
                "focus": str(cp_ref.get("focus") or ""),
                "notes": str(cp_ref.get("notes") or ""),
                "search_priority": str(cp_ref.get("search_priority") or ""),
                "feeds": cfeeds,
                "active_jobs": active_jobs,
                "total_jobs": total_jobs,
                "closed_jobs": closed_jobs,
                "bayern_active": bayern_active,
                "bayern_total": bayern_total,
                "qualified_active": qualified_active,
                "max_score": max_score,
                "avg_score": avg_score,
                "applied_count": applied_count,
                "bookmarked_count": bookmarked_count,
                "first_seen_min": first_seen_min,
                "last_seen_max": last_seen_max,
                "sample_jobs": samples,
            }
        )

    items.sort(
        key=lambda it: (
            0 if it["integration"] == "feed" else 1 if it["integration"] == "manual" else 2,
            0 if it["status"] in ("error", "warning") else 1 if it["status"] == "ok" else 2,
            -it["active_jobs"],
            -it["total_jobs"],
            it["company"].casefold(),
        )
    )

    return {
        "summary": {
            "connected_total": connected_total,
            "ok_count": status_counts["ok"],
            "error_count": status_counts["error"],
            "warning_count": status_counts["warning"],
            "empty_count": status_counts["empty"],
            "pending_count": status_counts["pending"],
            "excluded_count": status_counts["excluded"],
            "reference_count": status_counts["reference"],
            "manual_count": status_counts["manual"],
            "total_companies": len(items),
            "total_active_jobs": total_active_jobs,
            "total_historical_jobs": total_historical_jobs,
            "latest_scan_at": latest_scan_at,
        },
        "items": items,
    }


def reorder_prioritized_jobs(con: sqlite3.Connection, job_ids: list[int]) -> list[int]:
    """Persist explicit drag-and-drop ranking (1..N) for prioritized jobs."""
    clean_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in job_ids:
        jid = int(raw_id)
        if jid not in seen:
            seen.add(jid)
            clean_ids.append(jid)

    with con:
        rank = 1
        kept_ids: list[int] = []
        for jid in clean_ids:
            cur = con.execute(
                "UPDATE jobs SET priority_rank = ? WHERE id = ? AND (bookmarked = 1 OR priority > 0)",
                (rank, jid),
            )
            if cur.rowcount > 0:
                kept_ids.append(jid)
                rank += 1
        remaining = con.execute(
            """
            SELECT id FROM jobs
            WHERE (bookmarked = 1 OR priority > 0)
            ORDER BY CASE WHEN priority_rank > 0 THEN priority_rank ELSE 999999 END ASC,
                     COALESCE(ai_score, score) DESC, id DESC
            """
        ).fetchall()
        kept_set = set(kept_ids)
        for r in remaining:
            if r["id"] not in kept_set:
                con.execute("UPDATE jobs SET priority_rank = ? WHERE id = ?", (rank, r["id"]))
                kept_ids.append(r["id"])
                rank += 1
    return kept_ids


def get_company_application_history(con: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Return past/ongoing applications per canonical company key (casefolded)."""
    rows = con.execute(
        """
        SELECT id, company, title, status, application_date
        FROM jobs
        WHERE status IN (
            'beworben', 'eingeladen', 'gespraech', 'angebot',
            'angenommen', 'absage', 'zurueckgezogen', 'abgeschlossen'
        )
           OR (application_date IS NOT NULL AND TRIM(application_date) != '' AND status NOT IN ('neu', 'ignoriert'))
        ORDER BY CASE WHEN application_date IS NOT NULL AND application_date != '' THEN application_date ELSE '0000-00-00' END DESC,
                 id DESC
        """
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        comp_raw = str(r["company"] or "").strip()
        if not comp_raw:
            continue
        key = canonical_company(comp_raw).casefold()
        entry = {
            "job_id": r["id"],
            "company": comp_raw,
            "title": r["title"] or "",
            "status": r["status"],
            "status_label": STATUSES.get(r["status"], r["status"]),
            "application_date": r["application_date"] or "",
        }
        grouped.setdefault(key, []).append(entry)
    return grouped


def get_prioritized_ai_ranking(con: sqlite3.Connection) -> dict[str, Any] | None:
    """Read stored AI comparison ranking for prioritized jobs from settings."""
    row = con.execute("SELECT value FROM settings WHERE key = 'priority_ai_ranking_v1'").fetchone()
    if not row or not row["value"]:
        return None
    try:
        data = json.loads(row["value"])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_prioritized_ai_ranking(con: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist AI comparison ranking for prioritized jobs in settings atomically."""
    encoded = json.dumps(payload, ensure_ascii=False)
    with con:
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES ('priority_ai_ranking_v1', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (encoded,),
        )
    return payload


def get_prioritized_jobs_for_ai_ranking(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load active prioritized jobs in current order enriched with description & parsed AI data."""
    res = query_jobs(con, lifecycle="prioritized", sort_by="priority", limit=500)
    active_items = [it for it in res.get("items", []) if it.get("active")]
    if not active_items:
        return []
    ids = [int(it["id"]) for it in active_items]
    placeholders = ",".join("?" for _ in ids)
    extra_rows = con.execute(
        f"SELECT id, description, ai_data FROM jobs WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    extra_by_id: dict[int, tuple[str, dict[str, Any]]] = {}
    for r in extra_rows:
        parsed_ai: dict[str, Any] = {}
        if r["ai_data"]:
            try:
                cand = json.loads(r["ai_data"])
                if isinstance(cand, dict):
                    parsed_ai = cand
            except Exception:
                parsed_ai = {}
        extra_by_id[int(r["id"])] = (r["description"] or "", parsed_ai)

    enriched: list[dict[str, Any]] = []
    for it in active_items:
        jid = int(it["id"])
        desc, parsed_ai = extra_by_id.get(jid, ("", {}))
        enriched.append({
            **it,
            "description": desc,
            "ai_parsed": parsed_ai,
        })
    return enriched


def _detect_paragraph_lang(text: str, fallback: str = "de") -> str:
    low = f" {text.lower()} "
    en_hits = len(re.findall(r"\b(the|and|with|during|my|in|for|software|development|experience|position|regards|would|happy)\b", low))
    de_hits = len(re.findall(r"\b(und|der|die|das|mit|während|meiner|für|entwicklung|erfahrung|position|gespräch|freue|bereich|sowie)\b", low))
    if en_hits > de_hits + 1:
        return "en"
    if de_hits > en_hits + 1:
        return "de"
    return "en" if str(fallback).lower() == "en" else "de"


def _classify_paragraph(text: str, index: int = 0, total: int = 1) -> tuple[str, str]:
    low = text.lower()
    if len(text) < 380 and (
        (total >= 2 and index == total - 1)
        or re.search(r"persönlichen gespräch|einstieg ist mir|available to start|personal interview|diskutieren|talk soon|freue ich mich", low)
    ):
        category = "closing"
    elif index == 0 and total >= 2 and re.search(r"position|stelle|verbindet|aligns|entspricht|combines|background|hintergrund", low):
        category = "intro"
    elif re.search(r"ros ?2|ros-2|lidar|studienprojekt|student project|bachelor", low):
        category = "projects"
    elif re.search(r"masterarbeit|master[’']?s thesis|werkstudent|working student|praxissemester|experience", low):
        category = "experience"
    elif re.search(r"docker|ci/cd|azure|git|devops|bereitstellung|deploy|kubernetes|cloud", low):
        category = "body"
    else:
        category = "body"

    if category == "intro":
        if re.search(r"\b(rag|agent\w*|llm\w*)\b", low):
            label = "Einleitung · AI Agents, LLM & Backend"
        else:
            label = "Einleitung · Robotik, Sensorik & ML"
    elif category == "closing":
        if re.search(r"\b(einstieg|available|sofort|immediately)\b", low):
            label = "Abschluss · Verfügbarkeit & Gesprächswunsch"
        else:
            label = "Abschluss · Lernbereitschaft & Gesprächswunsch"
    elif re.search(r"\b(agent\w*|tool calling)\b", low) and re.search(r"\b(masterarbeit|master[’']?s thesis)\b", low):
        label = "Erfahrung · Agentische LLM-Architektur (Masterarbeit)"
    elif re.search(r"\b(rag|vektorsuche|vector search|retrieval-augmented|fastapi)\b", low):
        label = "Erfahrung · Python, FastAPI, RAG & LLM-Pipelines"
    elif re.search(r"\b(ros ?2|ros-2|sensorfusion|lidar)\b", low):
        label = "Projekt · ROS 2, Sensorfusion & PyTorch 3D"
    elif re.search(r"\b(docker|ci/cd|azure)\b", low):
        label = "Deployment · Docker, Git, CI/CD & Cloud"
    else:
        label = text[:52].rstrip() + ("…" if len(text) > 52 else "")

    return category, label


def upsert_letter_paragraph(
    con: sqlite3.Connection,
    text: str,
    lang: str = "",
    category: str = "",
    label: str = "",
    company: str = "",
    job_title: str = "",
    job_id: int | None = None,
    source_kind: str = "sent",
    index: int = 0,
    total: int = 1,
    increment_usage: bool = False,
    commit: bool = True,
) -> dict[str, Any] | None:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) < 35:
        return None
    # Reject obvious corrupted placeholder paragraphs
    if re.search(r"Neuer Absatz|^\s*test\s*$", cleaned, re.I):
        return None
    eff_lang = lang if lang in ("de", "en") else _detect_paragraph_lang(cleaned)
    auto_cat, auto_label = _classify_paragraph(cleaned, index=index, total=total)
    eff_cat = category if category in ("intro", "experience", "projects", "closing", "body") else auto_cat
    eff_label = (label or "").strip() or auto_label
    eff_kind = source_kind if source_kind in ("sent", "saved", "manual") else "manual"
    text_hash = hashlib.sha256(cleaned.casefold().encode("utf-8")).hexdigest()[:24]
    stamp = now_iso()

    def _run() -> dict[str, Any] | None:
        con.execute(
            """
            INSERT INTO letter_paragraphs(
              text, text_hash, lang, category, label, company, job_title, job_id, source_kind, usage_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(text_hash) DO UPDATE SET
              lang = excluded.lang,
              category = CASE WHEN ? != '' THEN excluded.category
                              WHEN letter_paragraphs.category = 'body' AND excluded.category != 'body' THEN excluded.category
                              ELSE letter_paragraphs.category END,
              label = CASE WHEN letter_paragraphs.label = '' THEN excluded.label ELSE letter_paragraphs.label END,
              company = CASE WHEN letter_paragraphs.company = '' THEN excluded.company ELSE letter_paragraphs.company END,
              job_title = CASE WHEN letter_paragraphs.job_title = '' THEN excluded.job_title ELSE letter_paragraphs.job_title END,
              job_id = COALESCE(letter_paragraphs.job_id, excluded.job_id),
              source_kind = CASE
                WHEN letter_paragraphs.source_kind = 'sent' OR excluded.source_kind = 'sent' THEN 'sent'
                WHEN excluded.source_kind = 'saved' THEN 'saved'
                ELSE letter_paragraphs.source_kind
              END,
              usage_count = letter_paragraphs.usage_count + CASE WHEN ? THEN 1 ELSE 0 END,
              updated_at = excluded.updated_at
            """,
            (
                cleaned,
                text_hash,
                eff_lang,
                eff_cat,
                eff_label,
                (company or "").strip(),
                (job_title or "").strip(),
                job_id,
                eff_kind,
                stamp,
                stamp,
                category if category in ("intro", "experience", "projects", "closing", "body") else "",
                1 if increment_usage else 0,
            ),
        )
        row = con.execute("SELECT * FROM letter_paragraphs WHERE text_hash = ?", (text_hash,)).fetchone()
        return dict(row) if row else None

    if commit:
        with con:
            return _run()
    return _run()


def ingest_paragraphs_from_body(
    con: sqlite3.Connection,
    body_text: str,
    lang: str = "",
    company: str = "",
    job_title: str = "",
    job_id: int | None = None,
    source_kind: str = "sent",
    increment_usage: bool = False,
    commit: bool = True,
) -> int:
    raw_paras = [p.strip() for p in re.split(r"\n\s*\n", (body_text or "").replace("\r\n", "\n")) if len(p.strip()) >= 35]
    if not raw_paras:
        return 0
    total = len(raw_paras)
    added = 0
    for idx, p in enumerate(raw_paras):
        eff_lang = _detect_paragraph_lang(p, fallback=lang or "de")
        res = upsert_letter_paragraph(
            con,
            text=p,
            lang=eff_lang,
            company=company,
            job_title=job_title,
            job_id=job_id,
            source_kind=source_kind,
            index=idx,
            total=total,
            increment_usage=increment_usage,
            commit=False,
        )
        if res:
            added += 1
    if commit:
        con.commit()
    return added


_PROTECTED_EMPLOYER_TERMS = {"example robotics", "sample ai systems", "technische universität musterstadt"}
_DEFAULT_FOREIGN_COMPANIES = [
    "ARX Robotics", "Helsing", "Quantum Systems", "Quantum-Systems",
    "NEURA Robotics", "MBDA Deutschland", "Airbus Defence and Space", "Siemens",
]


def adapt_paragraph_for_target(
    con: sqlite3.Connection | None,
    text: str,
    target_company: str,
    target_title: str = "",
    target_city: str = "",
    lang: str = "de",
) -> dict[str, Any]:
    """Deterministically adapt a reusable paragraph for a new target company (replace foreign company names & stale dates)."""
    out = str(text or "")
    replacements: list[str] = []
    comp_clean = re.sub(r"\s+(?:GmbH|AG|SE|Inc\.|Ltd\.|Co\.\s*KG)\b", "", (target_company or "").strip(), flags=re.I).strip()
    display_company = comp_clean or (target_company or "").strip()

    # 1. Refresh expired availability dates
    for pat, repl in (
        (r"\b(?:ab\s+)?(?:Mitte|Ende|Anfang)\s+November\s+2026\b", "ab sofort"),
        (r"\bin\s+(?:mid-November|early\s+November|late\s+November)\s+2026\b", "immediately"),
    ):
        if re.search(pat, out, re.I):
            out = re.sub(pat, repl, out, flags=re.I)
            replacements.append(f"Startdatum aktualisiert → {repl}")

    # 2. Replace foreign target company mentions (while protecting past employers from CV)
    if display_company:
        candidates = list(_DEFAULT_FOREIGN_COMPANIES)
        try:
            for exp in (load_master_profile().get("cv_base") or {}).get("experience") or []:
                c = str(exp.get("company") or "").split(",")[0].strip().lower()
                if c:
                    _PROTECTED_EMPLOYER_TERMS.add(c)
        except Exception:
            pass
        if con is not None:
            try:
                for r in con.execute("SELECT DISTINCT company FROM cover_letters WHERE COALESCE(company,'') != ''"):
                    candidates.append(str(r[0]))
            except Exception:
                pass
        # Longest names first
        target_low = display_company.lower()
        target_tokens = {t for t in re.split(r"[\s\-_.,/()]+", target_low) if len(t) >= 3}
        seen_cands: list[str] = []
        for c in sorted(set(candidates), key=len, reverse=True):
            c_clean = c.strip()
            c_low = c_clean.lower()
            if not c_clean or len(c_clean) < 4 or c_low in _PROTECTED_EMPLOYER_TERMS:
                continue
            if c_low in target_low or target_low in c_low:
                continue
            cand_tokens = {t for t in re.split(r"[\s\-_.,/()]+", c_low) if len(t) >= 3} - {"gmbh", "inc", "ag", "se"}
            if cand_tokens and cand_tokens & target_tokens:
                continue
            if c_low == "reply":
                new_out, cnt = re.subn(r"\b(bei|at|für|for)\s+Reply\b", rf"\1 {display_company}", out, flags=re.I)
                if cnt:
                    out = new_out
                    replacements.append(f"Firma 'Reply' → '{display_company}'")
                continue
            pat = rf"(?<![A-Za-z0-9]){re.escape(c_clean)}(?![A-Za-z0-9])"
            if re.search(pat, out, re.I):
                out = re.sub(pat, display_company, out, flags=re.I)
                replacements.append(f"Firma '{c_clean}' → '{display_company}'")
            seen_cands.append(c_clean)

    return {"text": out, "replacements": replacements}


def seed_letter_paragraphs(con: sqlite3.Connection) -> None:
    seeded = con.execute("SELECT value FROM settings WHERE key='bm_paragraphs_seeded_v1'").fetchone()
    if not seeded:
        demo_de = "\n\n".join(
            [
                "Die ausgeschriebene Position verbindet meinen Hintergrund in der Robotik mit meiner praktischen Erfahrung in der Python-Backend- und KI-Entwicklung.",
                "Während meiner Werkstudententätigkeit bei der Example Robotics GmbH entwickelte ich Python- und FastAPI-Services sowie containerisierte Docker-Workflows zur Verarbeitung technischer Sensordaten.",
                "Ein Einstieg ist mir ab sofort möglich. Gerne stelle ich meine bisherigen Projekte in einem persönlichen Gespräch näher vor.",
            ]
        )
        demo_en = "\n\n".join(
            [
                "The advertised position aligns closely with my background in robotics and hands-on experience in Python backend and applied AI development.",
                "During my working-student role at Example Robotics GmbH, I developed Python and FastAPI services and containerized Docker workflows for processing technical sensor data.",
                "I am available to start immediately and would welcome the opportunity to discuss the role in an interview.",
            ]
        )

        ingest_paragraphs_from_body(
            con, demo_de, lang="de", company="Demo Robotics GmbH",
            job_title="AI & Robotics Software Engineer (m/w/d)",
            source_kind="saved", commit=False,
        )
        ingest_paragraphs_from_body(
            con, demo_en, lang="en", company="Demo AI Systems GmbH",
            job_title="Software Engineer Applied AI (m/f/d)",
            source_kind="saved", commit=False,
        )

        con.execute(
            "INSERT INTO settings(key, value) VALUES ('bm_paragraphs_seeded_v1', '1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )


def list_letter_paragraphs(con: sqlite3.Connection, lang: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM letter_paragraphs"
    args: list[Any] = []
    if lang in ("de", "en"):
        sql += " WHERE lang = ?"
        args.append(lang)
    sql += """
        ORDER BY
          CASE source_kind WHEN 'sent' THEN 0 WHEN 'saved' THEN 1 ELSE 2 END,
          usage_count DESC,
          CASE category WHEN 'intro' THEN 0 WHEN 'experience' THEN 1 WHEN 'projects' THEN 2 WHEN 'body' THEN 3 ELSE 4 END,
          updated_at DESC, id DESC
    """
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def delete_letter_paragraph(con: sqlite3.Connection, paragraph_id: int) -> bool:
    with con:
        cur = con.execute("DELETE FROM letter_paragraphs WHERE id = ?", (paragraph_id,))
    return cur.rowcount > 0
