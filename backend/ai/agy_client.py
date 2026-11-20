"""Subscription-backed Google Antigravity CLI (agy) client for Job Fit & CV Tailoring.

Uses Gemini 4 Hydrogen (default) and Gemini 4 Argon via the user's logged-in Antigravity
subscription (no API key required!) with JSON schema enforcement and strict rule-based pre-gating.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from typing import Any

from backend.config import AGY_WORKDIR, ensure_dirs, load_master_profile, now_iso
from backend.scanner.geo_scoring import canonical_company, rule_eligibility
from backend.ai.posting_guard import sanitize_job_posting
from backend.ai.style_guard import (
    audit_text_for_ai_markers,
    sanitize_cv_style,
    sanitize_letter_style,
)

VENDOR_DIR = Path(__file__).resolve().parent.parent.parent / "vendor"
HUMANIZER_SKILL_FILE = VENDOR_DIR / "humanizer" / "SKILL.md"


@lru_cache(maxsize=1)
def load_humanizer_skill_instructions() -> str:
    """Load canonical blader/humanizer SKILL.md from disk once (cached per process)."""
    if not HUMANIZER_SKILL_FILE.exists():
        return ""
    try:
        raw = HUMANIZER_SKILL_FILE.read_text(encoding="utf-8")
        lines = []
        skip = False
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Before") or stripped.startswith("**After"):
                skip = True
                continue
            if stripped.startswith("### ") or stripped.startswith("## ") or stripped.startswith("# "):
                skip = False
            if skip:
                if stripped.startswith(">") or not stripped:
                    continue
                else:
                    skip = False
            lines.append(line)
        filtered = "\n".join(lines).strip()
        filtered = re.sub(r"^---.*?---\s*", "", filtered, flags=re.S)
        filtered = re.sub(r"### What to return.*?(?=## [A-Z]\. )", "", filtered, flags=re.S)
        return filtered.strip()
    except Exception:
        return ""


def _smart_truncate(text: str, limit: int) -> str:
    """Preserve head (70%) and tail (30%) of long postings so Qualifications/Requirements aren't cut off."""
    s = str(text or "")
    if len(s) <= limit or limit < 400:
        return s[:limit]
    head = int(limit * 0.70)
    tail = max(0, limit - head - 18)
    return s[:head].rstrip() + "\n[... gekürzt ...]\n" + s[-tail:].lstrip()


def _build_dynamic_profile_facts(profile: dict[str, Any]) -> str:
    """Summarize candidate's authentic facts dynamically from master_profile.json."""
    rp = profile.get("ranking_profile") or {}
    parts = [f"Ausbildung: {rp.get('education', 'M.Sc. / M.Eng. & B.Eng.')}"]
    for exp in rp.get("professional_experience") or []:
        parts.append(f"- {exp.get('company', '')} ({exp.get('from', '')} - {exp.get('to', '')}, {exp.get('type', '')}): {exp.get('focus', '')}")
    for proj in rp.get("project_evidence") or []:
        parts.append(f"- Projekt: {proj}")
    skills = rp.get("skill_levels") or {}
    if skills:
        parts.append("Skill-Grenzen: " + "; ".join(f"{k}: {v}" for k, v in skills.items()))
    if rp.get("study_timeline_note"):
        parts.append(f"Studienverlauf: {rp['study_timeline_note']}")
    if rp.get("references_and_artifacts"):
        parts.append(f"Nachweise & Referenzen: {rp['references_and_artifacts']}")
    if rp.get("security_clearance"):
        parts.append(f"Sicherheitsüberprüfung: {rp['security_clearance']}")
    mob = rp.get("mobility_and_work_model")
    if isinstance(mob, dict) and mob:
        mob_txt = ", ".join(f"{k}: {v}" for k, v in mob.items())
        parts.append(f"Mobilität & Arbeitsmodell: {mob_txt}")
    comp = rp.get("compensation_and_contract")
    if isinstance(comp, dict) and comp:
        comp_txt = ", ".join(f"{k}: {v}" for k, v in comp.items())
        parts.append(f"Vertrag & Gehalt: {comp_txt}")
    if rp.get("career_direction"):
        parts.append(f"Karriere-Fokus: {rp['career_direction']}")
    return "\n".join(parts)


HYDROGEN_DEFAULT_MODEL = "gemini-4-hydrogen-medium"
ARGON_DEFAULT_MODEL = "gemini-4-argon-high"
NEON_DEFAULT_MODEL = "gemini-4-neon-high"

MODEL_MAP = {
    "hydrogen": HYDROGEN_DEFAULT_MODEL,
    "hydrogen-low": "gemini-4-hydrogen-low",
    "hydrogen-medium": "gemini-4-hydrogen-medium",
    "hydrogen-high": "gemini-4-hydrogen-high",
    "hydrogen-xhigh": "gemini-4-hydrogen-xhigh",
    # Legacy aliases route to Hydrogen as requested
    "flash": HYDROGEN_DEFAULT_MODEL,
    "flash-low": "gemini-4-hydrogen-low",
    "flash-high": "gemini-4-hydrogen-high",
    "argon": ARGON_DEFAULT_MODEL,
    "argon-medium": "gemini-4-argon-medium",
    "argon-high": "gemini-4-argon-high",
    "neon": NEON_DEFAULT_MODEL,
    "neon-low": "gemini-4-neon-low",
    "neon-medium": "gemini-4-neon-medium",
    "neon-high": "gemini-4-neon-high",
    "neon-xhigh": "gemini-4-neon-xhigh",
}

MODEL_LABELS = {
    "hydrogen": "Gemini 4 Hydrogen",
    "argon": "Gemini 4 Argon",
    "neon": "Gemini 4 Neon",
}

INTERVIEW_PREP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pitch_60s", "star_stories", "gap_bridges", "smart_questions"],
    "properties": {
        "pitch_60s": {
            "type": "string",
            "description": "Prägnante 60-Sekunden Vorstellung ('Erzählen Sie etwas über sich') zugeschnitten auf diese Stelle und das Kandidatenprofil",
        },
        "star_stories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "project", "situation_task", "action", "result"],
                "properties": {
                    "question": {"type": "string", "description": "Erwartete Fach- oder Verhaltensfrage"},
                    "project": {"type": "string", "description": "Echtes Projekt aus dem Kandidatenprofil (z.B. Masterarbeit, Werkstudententätigkeit, Robotik-Studienprojekt)"},
                    "situation_task": {"type": "string", "description": "Situation und konkrete Aufgabenstellung"},
                    "action": {"type": "string", "description": "Konkret durchgeführte Maßnahmen, Algorithmen, Tools"},
                    "result": {"type": "string", "description": "Messbares oder greifbares Projektergebnis"},
                },
            },
        },
        "gap_bridges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gap", "honest_bridge"],
                "properties": {
                    "gap": {"type": "string", "description": "Kritischer Punkt oder fehlende Anforderung"},
                    "honest_bridge": {"type": "string", "description": "Ehrliche Brücken-Antwort: Lücke kurz anerkennen -> angrenzende echte Erfahrung nennen -> schnellen Einarbeitungsplan aufzeigen (keine Erfahrungen erfinden!)"},
                },
            },
        },
        "smart_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "4-5 intelligente, fachlich fundierte Rückfragen an das Team",
        },
    },
}

PARAGRAPH_RECOMMEND_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "recommendations"],
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["paragraph_id", "fit_score", "recommended", "suggested_slot", "reason"],
                "properties": {
                    "paragraph_id": {"type": "integer"},
                    "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "recommended": {"type": "boolean"},
                    "suggested_slot": {"type": "integer", "minimum": 1, "maximum": 6},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

LETTER_BLOCKS_ADAPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["adapted_paragraphs", "changes", "summary"],
    "properties": {
        "adapted_paragraphs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Leicht an Ziel-Firma, Sprache und Stelle angepasste Absätze in identischer Reihenfolge",
        },
        "changes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Kurze Liste der vorgenommenen minimalen Anpassungen",
        },
        "summary": {"type": "string"},
    },
}

LETTER_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "salutation", "contact_person", "body_text", "closing", "summary"],
    "properties": {
        "subject": {"type": "string"},
        "salutation": {"type": "string"},
        "contact_person": {"type": "string"},
        "body_text": {"type": "string"},
        "closing": {"type": "string"},
        "summary": {"type": "string"},
        "removed_patterns": {"type": "array", "items": {"type": "string"}},
    },
}

JOB_ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "skills", "tasks", "seniority", "domain", "eligible", "employment",
        "confidence", "reason", "evidence", "gaps", "work_authorization",
        "classification",
    ],
    "properties": {
        "skills": {"type": "integer", "minimum": 0, "maximum": 40},
        "tasks": {"type": "integer", "minimum": 0, "maximum": 30},
        "seniority": {"type": "integer", "minimum": 0, "maximum": 20},
        "domain": {"type": "integer", "minimum": 0, "maximum": 10},
        "eligible": {"type": "boolean"},
        "employment": {"type": "string", "enum": ["full_time", "part_time", "other", "unclear"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "work_authorization": {"type": "string"},
        "classification": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "role", "sectors", "entry_level", "fit_band", "required_years",
                "experience_importance", "experience_evidence", "work_mode",
                "remote_restrictions", "flags",
            ],
            "properties": {
                "role": {"type": "string", "enum": ["perception", "ai_ml", "robotics", "embedded", "simulation", "systems", "energy", "other"]},
                "sectors": {"type": "array", "items": {"type": "string", "enum": ["robotics", "defence", "energy", "medtech", "adas", "automotive", "other"]}},
                "entry_level": {"type": "string", "enum": ["explicit", "plausible", "experienced", "senior", "unknown"]},
                "fit_band": {"type": "string", "enum": ["strong", "review", "gaps", "low", "unclear"]},
                "required_years": {"type": ["integer", "null"]},
                "experience_importance": {"type": "string", "enum": ["must", "preferred", "unclear"]},
                "experience_evidence": {"type": "string"},
                "work_mode": {"type": "string", "enum": ["onsite", "hybrid", "remote", "unknown"]},
                "remote_restrictions": {"type": "string"},
                "flags": {"type": "array", "items": {"type": "string", "enum": ["skill_gap", "experience_gap", "metadata_conflict", "authorization_unknown", "missing_information"]}},
            },
        },
    },
}

DEEP_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "requirements", "project_hooks", "interview_questions"],
    "properties": {
        "summary": {"type": "string"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement", "importance", "ad_evidence", "profile_evidence", "gap", "verdict"],
                "properties": {
                    "requirement": {"type": "string"},
                    "importance": {"type": "string", "enum": ["must", "nice", "unclear"]},
                    "ad_evidence": {"type": "string"},
                    "profile_evidence": {"type": "string"},
                    "gap": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["matched", "partial", "missing", "unknown"]},
                },
            },
        },
        "project_hooks": {"type": "array", "items": {"type": "string"}},
        "interview_questions": {"type": "array", "items": {"type": "string"}},
    },
}

PRIORITIZED_RANKING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strategy_summary", "ranking"],
    "properties": {
        "strategy_summary": {"type": "string"},
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "job_id", "rank", "wave", "chance_level", "urgency",
                    "scores", "rationale", "tradeoff", "next_action",
                ],
                "properties": {
                    "job_id": {"type": "integer"},
                    "rank": {"type": "integer", "minimum": 1},
                    "wave": {"type": "string", "enum": ["jetzt", "danach", "spaeter", "pruefen"]},
                    "chance_level": {"type": "string", "enum": ["hoch", "mittel", "ambitioniert"]},
                    "urgency": {"type": "string", "enum": ["hoch", "mittel", "niedrig"]},
                    "scores": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["chance", "fit", "location", "timing"],
                        "properties": {
                            "chance": {"type": "integer", "minimum": 0, "maximum": 100},
                            "fit": {"type": "integer", "minimum": 0, "maximum": 100},
                            "location": {"type": "integer", "minimum": 0, "maximum": 100},
                            "timing": {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                    },
                    "rationale": {"type": "string"},
                    "tradeoff": {"type": "string"},
                    "next_action": {"type": "string"},
                },
            },
        },
    },
}


class AgyError(RuntimeError):
    pass


def find_agy_binary() -> str | None:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    candidates = [
        shutil.which("agy"),
        shutil.which("agy.exe"),
        str(Path(local_app_data) / "agy" / "bin" / "agy.exe"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return None


def check_agy_status() -> dict[str, Any]:
    exe = find_agy_binary()
    if not exe:
        return {
            "installed": False,
            "logged_in": False,
            "executable": "",
            "models": MODEL_LABELS,
            "message": "Antigravity CLI (agy) wurde nicht gefunden.",
        }
    try:
        proc = subprocess.run(
            [exe, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        has_models = "gemini-" in out
        return {
            "installed": True,
            "logged_in": proc.returncode == 0 and has_models,
            "executable": exe,
            "models": MODEL_LABELS,
            "message": "Verbunden über Google Antigravity Abo (kein API-Key nötig)"
            if (proc.returncode == 0 and has_models)
            else "Bitte einmalig 'agy' im Terminal ausführen, um das Abo anzumelden.",
        }
    except Exception as exc:
        return {
            "installed": True,
            "logged_in": False,
            "executable": exe,
            "models": MODEL_LABELS,
            "message": f"Statusprüfung fehlgeschlagen: {exc}",
        }


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            val = json.loads(fence.group(1).strip())
            if isinstance(val, dict):
                return val
        except ValueError:
            pass
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    best_obj: dict[str, Any] | None = None
    best_span = -1
    pos = 0
    while True:
        idx = text.find("{", pos)
        if idx == -1:
            break
        try:
            cand, end_idx = decoder.raw_decode(text[idx:])
            if isinstance(cand, dict) and end_idx > best_span:
                best_obj = cand
                best_span = end_idx
            pos = idx + max(1, end_idx)
        except ValueError:
            pos = idx + 1
    if best_obj is not None:
        return best_obj
    raise AgyError(f"KI-Antwort enthielt kein gültiges JSON-Objekt: {raw_text[:240]}")


def run_agy_json(
    prompt: str,
    schema: dict[str, Any] | None = None,
    model_key: str = "hydrogen",
    timeout: int = 150,
    allow_argon: bool = False,
    retries: int = 1,
) -> tuple[dict[str, Any], str]:
    """Execute a non-interactive agy prompt via stream-json stdin and return (parsed_json, resolved_model)."""
    ensure_dirs()
    exe = find_agy_binary()
    if not exe:
        raise AgyError("agy.exe nicht gefunden (%LOCALAPPDATA%\\agy\\bin\\agy.exe).")

    resolved_model = MODEL_MAP.get(model_key, HYDROGEN_DEFAULT_MODEL)
    if not allow_argon and ("argon" in resolved_model.lower() or "neon" in resolved_model.lower()):
        resolved_model = HYDROGEN_DEFAULT_MODEL
    cmd = [
        exe,
        "--model", resolved_model,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--disable-slash-commands",
        "--sandbox",
    ]

    schema_file: Path | None = None
    if schema is not None:
        schema_txt = json.dumps(schema, sort_keys=True, ensure_ascii=False)
        schema_hash = hashlib.sha256(schema_txt.encode("utf-8")).hexdigest()[:12]
        schema_file = AGY_WORKDIR / f"schema_{schema_hash}.json"
        if not schema_file.exists():
            tmp_schema = AGY_WORKDIR / f"schema_{schema_hash}_{uuid.uuid4().hex[:8]}.tmp"
            tmp_schema.write_text(schema_txt, encoding="utf-8")
            try:
                os.replace(tmp_schema, schema_file)
            except PermissionError:
                tmp_schema.unlink(missing_ok=True)
        cmd.extend(["--json-schema", str(schema_file)])

    stdin_payload = json.dumps(
        {"event": "user", "message": {"role": "user", "content": prompt}},
        ensure_ascii=False,
    ) + "\n"

    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_payload,
                cwd=str(AGY_WORKDIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            last_err = AgyError(f"KI-Anfrage hat das Zeitlimit ({timeout}s) überschritten.")
            if attempt < retries:
                time.sleep(0.8)
                continue
            raise last_err from exc
        except OSError as exc:
            raise AgyError(f"agy-Aufruf fehlgeschlagen: {exc}") from exc

        stdout = (proc.stdout or "").strip()
        if proc.returncode != 0 and not stdout:
            err = (proc.stderr or "").strip()
            last_err = AgyError(f"agy Fehler ({proc.returncode}): {err[:400]}")
            if attempt < retries:
                time.sleep(0.8)
                continue
            raise last_err

        envelope = None
        for line in reversed(stdout.splitlines()):
            line_s = line.strip()
            if not (line_s.startswith("{") and line_s.endswith("}")):
                continue
            try:
                cand = json.loads(line_s)
            except ValueError:
                continue
            if not isinstance(cand, dict):
                continue
            if cand.get("event") == "result" and isinstance(cand.get("result"), dict):
                envelope = cand["result"]
                break
            if "response" in cand or "structured_output" in cand:
                envelope = cand
                break

        try:
            if envelope is None:
                if proc.returncode != 0:
                    raise AgyError(f"agy Fehler ({proc.returncode}): {(proc.stderr or stdout)[:300]}")
                return _extract_json_object(stdout), resolved_model

            if envelope.get("status") not in ("SUCCESS", None):
                raise AgyError(f"agy Status {envelope.get('status')}: {str(envelope)[:300]}")

            if isinstance(envelope.get("structured_output"), dict):
                return envelope["structured_output"], resolved_model

            resp = envelope.get("response", "")
            if isinstance(resp, dict):
                return resp, resolved_model
            return _extract_json_object(str(resp)), resolved_model
        except AgyError as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.8)
                continue
            raise

    raise last_err or AgyError("KI-Anfrage fehlgeschlagen.")


def assess_single_job(con: sqlite3.Connection, job_id: int, model_key: str = "hydrogen", with_deep_review: bool = False) -> dict[str, Any]:
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
        raise ValueError("Stelle nicht gefunden")

    eligible_by_rules, reject_reason = rule_eligibility(
        int(row["score"] or 0),
        row["hard_exclusion"] or "",
        int(row["tier"] or 4),
        row["preference"] or "neutral",
    )
    if not eligible_by_rules:
        raise AgyError(f"Stelle kommt regelbasiert nicht in Frage: {reject_reason}")

    raw_desc = (row["description"] or "").strip()
    if len(raw_desc) < 40:
        raise AgyError("Stellenbeschreibung fehlt oder ist zu kurz für eine belastbare KI-Bewertung.")

    master = load_master_profile()
    profile = master["ranking_profile"]
    clean_desc = _smart_truncate(sanitize_job_posting(raw_desc)["clean_text"], 14000)
    job_payload = {
        "company": sanitize_job_posting(row["company"] or "")["clean_text"][:200],
        "title": sanitize_job_posting(row["title"] or "")["clean_text"][:300],
        "location": sanitize_job_posting(row["location"] or "")["clean_text"][:200],
        "url": row["url"],
        "description": clean_desc,
    }

    candidate_name = str((master.get("personal") or {}).get("name") or "den Kandidaten").strip()
    facts_summary = _build_dynamic_profile_facts(master)
    prompt = (
        f"Du bist ein präziser, nüchterner Karriere-Analyst für {candidate_name}.\n"
        f"VERIFIZIERTES PROFIL:\n{facts_summary}\n\n"
        "WICHTIG: Erfinde KEINE Fähigkeiten. C/C++ ist auf Grundlagen-Niveau, mit Bereitschaft zur Einarbeitung "
        "(bei Junior-/Einstiegs- oder Python/C++-Mix-Stellen nur leicht berücksichtigen; nur bei zwingend mehrjähriger Experten-C++-Systemprogrammierung deutlich abwerten). "
        "Werkstudenten-/Abschlussarbeitszeit zählt NICHT als 3+ Jahre Vollzeit-Berufserfahrung.\n\n"
        "WICHTIG ZU RAHMENBEDINGUNGEN & SENIOR-STELLEN: 4–5 Tage vor Ort (Büro/Labor), Sicherheitsüberprüfung (Ü1/Ü2), "
        "befristete Verträge (z. B. DLR/Fraunhofer), Traineeprogramme und fehlende Datenbank-Erfahrung (schnelle Einarbeitung ok) sind KEIN Nachteil. "
        "Senior- oder erfahrene Rollen führen bei guter inhaltlicher Tech-Passung NICHT zum Ausschluss (eligible bleibt true); "
        "die Seniorität wird ausschließlich über den Teilscore 'seniority' (z. B. 4-8 statt 16-20 Punkte) moderat abgewertet. "
        "Nur echte K.O.-Kriterien (z. B. Praktikum/Ausbildung statt Vollzeit oder völlig fachfremde Domäne) setzen eligible auf false.\n\n"
        "Nutze KEINE Tools oder Dateizugriffe. Antworte AUSSCHLIESSLICH mit dem geforderten JSON-Objekt auf Deutsch.\n"
        "Bewertungsskala:\n"
        "- skills (0..40): Passung der geforderten Tech-Skills zu Python, FastAPI, React/TypeScript, LLM/RAG/Agentic AI, Azure/Azure AI Search, MATLAB/Simulink, PyTorch/CV/BEV, ROS 2, C#/Java, Docker, CAN-Bus.\n"
        "- tasks (0..30): Passung der Aufgaben (angewandte KI-/Robotik-/Fullstack-Entwicklung sowie praxisnahe Systemintegration, Erprobung oder ingenieurnahe Software an realen technischen Systemen vs. reine Doku/reines PCB-Schaltungsdesign).\n"
        "- seniority (0..20): 16-20 klarer Berufseinstieg/Junior/Trainee/0-2 Jahre; 10-15 plausibel; 5-9 Senior/Lead (Minuspunkt, aber kein Ausschluss); 0-4 massive Führung.\n"
        "- domain (0..10): Interesse an KI, Robotik, Defence, Autonomie, Energie, MedTech sowie technologiegetriebener Industrie.\n\n"
        f"KANDIDATENPROFIL:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"STELLENANZEIGE:\n{json.dumps(job_payload, ensure_ascii=False)}"
    )

    allow_argon = str(model_key or "").startswith(("argon", "neon"))
    parsed, resolved_model = run_agy_json(prompt, schema=JOB_ASSESSMENT_SCHEMA, model_key=model_key, allow_argon=allow_argon)
    if not isinstance(parsed.get("classification"), dict):
        parsed["classification"] = {}
    parsed["classification"]["source_url"] = row["url"]
    parsed.setdefault("eligible", True)
    parsed.setdefault("reason", "")

    total_score = (
        int(parsed.get("skills") or 0)
        + int(parsed.get("tasks") or 0)
        + int(parsed.get("seniority") or 0)
        + int(parsed.get("domain") or 0)
    )
    if not parsed["eligible"]:
        is_explicit_non_ft = bool(
            re.search(r"\b(?:pflichtpraktikum|ausbildungsstelle|duales?\s+studium|werkstudent(?:in|en)?|keine?\s+vollzeit)\b", str(parsed.get("reason") or ""), re.I)
        )
        if parsed.get("employment") in ("part_time", "other") or is_explicit_non_ft:
            total_score = 0
        else:
            total_score = min(total_score, 45)
    total_score = max(0, min(100, total_score))

    deep_review = None
    if with_deep_review:
        rev_prompt = (
            "Erstelle eine tiefgehende Anforderungs-Matrix (Must-have vs. Nice-to-have), konkrete Projekt-Aufhänger "
            "für Anschreiben/Gespräch sowie kritische Interview-Rückfragen für diese Stelle. "
            "Nutze KEINE Tools. Antworte AUSSCHLIESSLICH als JSON auf Deutsch.\n\n"
            f"PROFIL:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"STELLE:\n{json.dumps(job_payload, ensure_ascii=False)}"
        )
        deep_review, _ = run_agy_json(rev_prompt, schema=DEEP_REVIEW_SCHEMA, model_key=model_key, allow_argon=allow_argon)

    parsed["application_review"] = deep_review
    stamp = now_iso()

    with con:
        con.execute(
            """
            UPDATE jobs SET
              ai_score = ?,
              ai_data = ?,
              ai_date = ?,
              ai_model = ?,
              ai_version = 3,
              ai_hash = content_hash,
              ai_review_data = CASE WHEN ? != '' THEN ?
                                    WHEN COALESCE(ai_hash, '') != COALESCE(content_hash, '') THEN ''
                                    ELSE ai_review_data END,
              ai_review_date = CASE WHEN ? != '' THEN ?
                                    WHEN COALESCE(ai_hash, '') != COALESCE(content_hash, '') THEN ''
                                    ELSE ai_review_date END,
              ai_review_requested = 0
            WHERE id = ?
            """,
            (
                total_score,
                json.dumps(parsed, ensure_ascii=False),
                stamp,
                f"agy:{resolved_model}",
                json.dumps(deep_review, ensure_ascii=False) if deep_review else "",
                json.dumps(deep_review, ensure_ascii=False) if deep_review else "",
                stamp if deep_review else "",
                stamp if deep_review else "",
                job_id,
            ),
        )
    return {"job_id": job_id, "ai_score": total_score, "model": resolved_model, "assessment": parsed}


def _enforce_cv_fact_guardrails(orig_cv: dict[str, Any], merged_cv: dict[str, Any], master_profile: dict[str, Any]) -> list[str]:
    """Deterministic post-AI validation locking employers, timeframes, grades, and warning on exaggerated phrases."""
    warnings: list[str] = []
    # Lock experience timeframes & companies to prevent invented employers or stretched timelines
    orig_exps = orig_cv.get("experience") or []
    merged_exps = merged_cv.get("experience") or []
    if len(merged_exps) > len(orig_exps) + 1:
        warnings.append("Mehr Stationen als im Ausgangs-CV — auf echte Stationen prüfen.")
    for idx, exp in enumerate(merged_exps):
        if not isinstance(exp, dict):
            continue
        if idx < len(orig_exps) and isinstance(orig_exps[idx], dict):
            o_comp = str(orig_exps[idx].get("company") or "")
            o_tf = str(orig_exps[idx].get("timeframe") or "")
            if o_comp and exp.get("company") != o_comp:
                exp["company"] = o_comp
                warnings.append(f"Arbeitgeber '{o_comp}' beibehalten.")
            if o_tf and exp.get("timeframe") != o_tf:
                exp["timeframe"] = o_tf
                warnings.append(f"Zeitraum '{o_tf}' beibehalten.")

    # Lock education timeframes, institutions, and grades
    orig_edus = orig_cv.get("education") or []
    for idx, edu in enumerate(merged_cv.get("education") or []):
        if not isinstance(edu, dict):
            continue
        if idx < len(orig_edus) and isinstance(orig_edus[idx], dict):
            for k in ("timeframe", "institution"):
                if orig_edus[idx].get(k) and edu.get(k) != orig_edus[idx].get(k):
                    edu[k] = orig_edus[idx][k]

    # Guard profile text against false multi-year full-time exaggeration
    if isinstance(merged_cv.get("profile"), dict):
        de_p = str(merged_cv["profile"].get("de") or "")
        en_p = str(merged_cv["profile"].get("en") or "")
        if re.search(r"mehrjährig\w*\s+(?:beruf\w*|erfahrung)", de_p, re.I):
            fixed_de = re.sub(r"mehrjährig\w*\s+beruf(?:serfahrung)?", "zweijähriger Werkstudenten- und Masterarbeitserfahrung", de_p, flags=re.I)
            merged_cv["profile"]["de"] = fixed_de
            warnings.append("Übertreibung ('mehrjährige Berufserfahrung') im DE-Profil korrigiert.")
        if re.search(r"(?:multiple|several)\s+years\s+of\s+(?:professional|full-time)\s+experience", en_p, re.I):
            fixed_en = re.sub(r"(?:multiple|several)\s+years\s+of\s+(?:professional|full-time)\s+experience", "two years of working-student and Master's thesis experience", en_p, flags=re.I)
            merged_cv["profile"]["en"] = fixed_en
            warnings.append("Übertreibung ('multiple years') im EN-Profil korrigiert.")

    # Warn if skills violate ranking_profile skill_levels ceiling (e.g. C++ > Grundlagen, LangChain > Praxis)
    rp_skills = {str(k).lower(): str(v).lower() for k, v in (master_profile.get("ranking_profile", {}).get("skill_levels") or {}).items()}
    for sk in merged_cv.get("skills") or []:
        if not isinstance(sk, dict):
            continue
        name = str(sk.get("name_de") or sk.get("name_en") or "").lower()
        lvl = str(sk.get("level_de") or sk.get("level_en") or "").lower()
        if "c++" in name and lvl in ("sehr gut", "proficient", "expert", "fließend"):
            sk["level_de"] = "Grundlagen"
            sk["level_en"] = "Basics"
            warnings.append("C++-Level gemäß Fakten-Lock auf 'Grundlagen' begrenzt.")
        if "langchain" in name and lvl in ("sehr gut", "proficient", "expert") and any("grundlagen" in v for k, v in rp_skills.items() if "langchain" in k):
            sk["level_de"] = "Praxis"
            sk["level_en"] = "Practical use"
            warnings.append("LangChain-Level gemäß Profil begrenzt.")

    return warnings


def adjust_cv_data_with_ai(
    cv_data: dict[str, Any],
    instruction: str,
    job_context: dict[str, Any] | None = None,
    model_key: str = "hydrogen",
) -> dict[str, Any]:
    """Tailor the structured bilingual CV JSON strictly in the candidate's authentic human engineering style."""
    master = load_master_profile()
    candidate_name = str((master.get("personal") or {}).get("name") or "den Kandidaten").strip()
    facts_summary = _build_dynamic_profile_facts(master)
    context_block = ""
    if job_context:
        clean_desc = _smart_truncate(sanitize_job_posting(job_context.get("description") or "")["clean_text"], 8000)
        context_block = (
            "\nKONTEXT DER ZIEL-STELLENANZEIGE:\n"
            + json.dumps(
                {
                    "company": sanitize_job_posting(job_context.get("company") or "")["clean_text"][:200],
                    "title": sanitize_job_posting(job_context.get("title") or "")["clean_text"][:300],
                    "location": sanitize_job_posting(job_context.get("location") or "")["clean_text"][:200],
                    "description": clean_desc,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    prompt = (
        f"Du bist der persönliche Karriere- und Bewerbungs-Assistent für {candidate_name}.\n"
        f"VERIFIZIERTE FAKTENGRENZEN (UNVERÄNDERLICH):\n{facts_summary}\n"
        "Deine Aufgabe ist es, den strukturierten zweisprachigen Lebenslauf (Deutsch 'de' & Englisch 'en') im einseitigen Standardlayout "
        "zielgerichtet und minimal-invasiv an die Wunsch-Stelle oder Anweisung des Nutzers anzupassen.\n\n"
        "═══════════════════════════════════════════════════════════════════════════════\n"
        "1. AUTHENTISCHE SCHREIBSTIL-DNA (MENSCHLICH, NÜCHTERN, INGENIEURMÄSSIG):\n"
        "═══════════════════════════════════════════════════════════════════════════════\n"
        f"- {candidate_name} schreibt wie ein Ingenieur: faktenbasiert, ruhig, präzise und bescheiden. Kein Marketing, kein Sales-Pitch, kein KI-Jargon.\n"
        "- DEUTSCH: Bulletpoints beginnen IMMER mit einem substantivierten Aktions-Nomen ('Konzeption und Implementierung...', 'Entwicklung...', 'Integration...', 'Containerisierung...', 'Optimierung...', 'Technische Beratung...') oder einem prägnanten Satz. Niemals wertende Adjektive voranstellen!\n"
        "- ENGLISCH: Bulletpoints beginnen IMMER mit einem klaren Aktions-Verb im Simple Past ('Designed and implemented...', 'Developed...', 'Integrated...', 'Containerized...', 'Provided...').\n"
        "- MINIMAL-INVASIVES VORGEHEN: Überschreibe bestehende Texte NICHT mit generischen KI-Neudichtungen! Behalte die echten Formulierungen bei und passe nur gezielt Nuancen oder Reihenfolgen an, um die geforderten Technologien der Stellenanzeige wahrheitsgetreu hervorzuheben.\n"
        "- NIEMALS HALLUZINIEREN: Erfinde niemals Arbeitgeber, Stationen, Abschlüsse, Noten oder Technologien, die nicht im Profil stehen.\n\n"
        "═══════════════════════════════════════════════════════════════════════════════\n"
        "2. STRIKTE VERBOTE (AI-ZEICHEN & FLOSKELN):\n"
        "═══════════════════════════════════════════════════════════════════════════════\n"
        "- ABSOLUTES VERBOT VON AI-ZEICHEN (- ; :):\n"
        "  * KEINE Gedankenstriche (—, –, ' - ' oder '--') zur Gedankentrennung in Bulletpoints oder Profiltexten. Verwende stattdessen Kommas oder teile den Satz mit einem Punkt. (Technische Kopplungen wie 'Python-basiert', 'ROS-2', 'Tool-Calling', '3D-Druck' bleiben normale Bindestriche).\n"
        "  * KEINE Semikolons (;). Semikolons sind ein verräterisches KI-Merkmal. Nutze Kommas, Punkte oder 'und' / 'sowie' / 'and'.\n"
        "  * KEINE Doppelpunkte (:) in Aufzählungspunkten! Schreibe niemals 'Fokus: ...', 'Aufgaben: ...', 'Backend: ...' oder ähnliche künstliche KI-Kategoriepräfixe. Beginne den Punkt sofort mit dem Aktions-Nomen oder Verb.\n"
        "- ABSOLUTES VERBOT VON HYPE-WÖRTERN:\n"
        "  * Verwende NIEMALS Wörter wie: leidenschaftlich, hochmodern, revolutionär, innovativ, bahnbrechend, nahtlos, herausragend, tiefgreifend, maßgeschneidert, passioniert, passionate, cutting-edge, state-of-the-art, groundbreaking, spearheaded, leveraged, seamless.\n\n"
        "═══════════════════════════════════════════════════════════════════════════════\n"
        "3. VOLLE GESTALTUNGS- & LAYOUT-KONTROLLE (DU KANNST ALLES ANPASSEN!):\n"
        "═══════════════════════════════════════════════════════════════════════════════\n"
        "Du hast VOLLEN Zugriff auf alle Textinhalte, Sektionen UND die gesamte visuelle Gestaltung und Struktur des Lebenslaufs.\n"
        "Wenn der Nutzer Layout-, Farb-, Spalten-, Rand- oder Formatierungswünsche äußert, setze sie direkt als Top-Level-Felder in cv_data um (NICHT in ein verschachteltes 'layout'-Unterobjekt schreiben, damit der Direkt-Editor synchron bleibt):\n\n"
        "- FARBGESTALTUNG:\n"
        "  * 'accent_color': Hex-Farbcode (z.B. '#1e3a8a' Navy, '#0f766e' Teal, '#3b4d61' Schiefer-Standard, '#15803d' Grün, '#881337' Bordeaux, '#334155' Dunkelgrau) oder Farbname ('navy', 'blue', 'teal', 'slate', 'burgundy', 'dark', 'black'). Steuert alle Header-Balken, Trennlinien und Datums-Badges!\n"
        "  * 'main_color': Primäre Textfarbe (z.B. '#404040' oder '#1e293b').\n\n"
        "- SPALTEN & RÄNDER:\n"
        "  * 'column_ratio': Verhältnis der linken Spalte zur Gesamtbreite (Standard 0.31, Bereich 0.22 bis 0.45). Eignet sich perfekt, um mehr Platz für die Berufserfahrung zu schaffen (z.B. 0.28) oder die linke Spalte breiter zu machen (z.B. 0.35).\n"
        "  * 'margins': {'top': '0.7cm', 'bottom': '0.6cm', 'left': '1cm', 'right': '1cm'}. Reduziere die Ränder (z.B. top 0.55cm, bottom 0.45cm), wenn Inhalte mehr Platz benötigen, um perfekt auf 1 Seite zu passen.\n"
        "  * 'section_spacing': Vertikaler Abstand zwischen Sektionen (Standard '9pt', z.B. '6pt', '8pt', '11pt').\n"
        "  * 'font_size': Grundschriftgröße (Standard '10pt', Optionen: '9pt', '9.5pt', '10pt', '10.5pt', '11pt').\n\n"
        "- ELEMENT- & SEKTIONS-ABSTÄNDE (MEHR ABSTAND / WENIGER GEQUETSCHT):\n"
        "  * 'density': 'compact' | 'normal' | 'relaxed' | 'generous' (Schnellsteuerung für die Gesamtdichte des Lebenslaufs).\n"
        "  * 'left_section_spacing': Vertikaler Abstand vor Sektionen in der linken Spalte (Skills, Interessen, Kontakt). Standard '18pt', für mehr Luft z.B. '22pt' bis '28pt'.\n"
        "  * 'skill_item_spacing': Abstand zwischen einzelnen Kenntnis-Zeilen (Standard '7.5pt', für mehr Abstand '9pt' bis '11pt').\n"
        "  * 'skill_lang_spacing': Abstand zwischen Skills und Sprachen (z.B. zwischen LLM APIs und German/Deutsch). Standard '18pt', für deutliche Trennung '22pt' bis '28pt'!\n"
        "  * 'interest_item_spacing': Abstand zwischen Interessen (Standard '6pt', für mehr Abstand '8pt' bis '10pt').\n"
        "  * 'contact_item_spacing': Abstand zwischen Kontaktzeilen (Standard '5pt', für mehr Abstand '7pt' bis '9pt').\n"
        "  * 'main_section_spacing': Abstand vor Hauptsektionen (Profil, Berufserfahrung, Ausbildung). Standard '6pt', für mehr Luft '8pt' bis '12pt'.\n"
        "  * 'event_spacing': Abstand zwischen einzelnen Arbeits- und Ausbildungsstationen in der Hauptspalte (Standard '6pt', für weniger gequetscht '8pt' bis '10pt').\n"
        "  * 'item_spacing': Abstand zwischen Aufzählungspunkten (Standard '3pt', z.B. '3.5pt' oder '4pt').\n"
        "  * WICHTIG: Wenn der Nutzer 'mehr Abstand', 'nicht so gequetscht', 'Abstand linke Spalte' oder 'Abstand zwischen Elementen' fordert:\n"
        "    1. Setze 'density': 'relaxed' oder erhöhe die obigen Abstände gezielt.\n"
        "    2. Passe gleichzeitig die Ränder ('margins': {'top': '0.55cm', 'bottom': '0.45cm', 'left': '0.9cm', 'right': '0.9cm'}) an, damit trotz der großzügigen Abstände das Dokument harmonisch auf EXAKT 1 Seite bleibt!\n\n"
        "- SEKTIONS-REIHENFOLGE & EIN-/AUSBLENDEN:\n"
        "  * 'section_order_right': Liste der Sektionen in der Hauptspalte, Standard: ['profile', 'experience', 'education', 'signature']. Du kannst jede Reihenfolge wählen, z.B. ['profile', 'education', 'experience', 'signature'] (Ausbildung vor Berufserfahrung)!\n"
        "  * 'section_order_left': Liste der Sektionen in der linken Spalte, Standard: ['portrait', 'header', 'skills', 'interests', 'contact'].\n"
        "  * 'show_profile': true | false (Profil ein- oder ausblenden)\n"
        "  * 'show_interests': true | false (Interessen ein- oder ausblenden)\n"
        "  * 'show_languages': true | false (Sprachen ein- oder ausblenden)\n"
        "  * 'show_skills': true | false (Kenntnisse ein- oder ausblenden)\n"
        "  * 'show_contact': true | false (Kontaktdaten ein- oder ausblenden)\n"
        "  * 'show_signature': true | false (Unterschriftenblock ein- oder ausblenden)\n"
        "  * 'section_titles': Objekt mit individuellen Titeln für jede Sektion, z.B.:\n"
        "    {\n"
        "      'skills': {'de': 'Kernkompetenzen & Tech-Stack', 'en': 'Core Skills & Tech Stack'},\n"
        "      'experience': {'de': 'Beruflicher Werdegang', 'en': 'Work Experience'},\n"
        "      'education': {'de': 'Akademische Ausbildung', 'en': 'Education'}\n"
        "    }\n\n"
        "- PORTRÄTFOTO-STEUERUNG:\n"
        "  * 'include_portrait': true | false (Porträtfoto einbinden)\n"
        "  * 'portrait_align': 'left' (Standard, bündig mit Name) | 'center' (zentriert) | 'right' (rechtsbündig)\n"
        "  * 'portrait_width': 0.75 (Standardbreite, Wertebereich 0.40 bis 1.0 als Breitenanteil der Spalte)\n"
        "  * 'portrait_offset_x': 0 (horizontaler Feinabgleich in pt, z.B. 10 oder -10)\n"
        "  * 'portrait_offset_y': 0 (vertikaler Feinabgleich in pt)\n\n"
        "- FORMALE RESTRIKTIONEN:\n"
        "  * EXAKT 1 DIN-A4-SEITE: Passe Textumfang, Spaltenbreite, Ränder oder Abstände so ausbalanciert an, dass das Dokument luftig und harmonisch auf exakt 1 Seite passt.\n"
        "  * BEIDE SPRACHEN SYNCHRON: 'de' und 'en' bzw. *_de und *_en müssen inhaltlich synchron gepflegt werden.\n"
        "- Nutze KEINE Tools und greife auf KEINE Dateien zu. Gib ausschließlich ein JSON-Objekt mit genau zwei Schlüsseln zurück:\n"
        '  * "summary": Kurze deutsche Zusammenfassung (1–2 Sätze), was genau angepasst wurde.\n'
        '  * "cv_data": Das vollständige aktualisierte cv_data-Objekt mit exakt derselben JSON-Struktur wie der Input.\n\n'
        f"AKTUELLER LEBENSLAUF (JSON):\n{json.dumps(cv_data, ensure_ascii=False)}\n"
        f"{context_block}\n"
        f"ÄNDERUNGSWUNSCH DES NUTZERS:\n{instruction.strip()}\n"
    )

    result, resolved_model = run_agy_json(
        prompt, schema=None, model_key=model_key, timeout=210, allow_argon=True
    )
    if "cv_data" not in result or not isinstance(result["cv_data"], dict):
        raise AgyError("KI-Antwort enthielt kein gültiges 'cv_data'-Objekt.")

    # Preserve mandatory structural keys and flatten any legacy nested layout into top-level keys
    merged = dict(cv_data)
    raw_cv = result["cv_data"]
    if isinstance(raw_cv.get("layout"), dict):
        for lk, lv in raw_cv["layout"].items():
            if lk not in raw_cv:
                raw_cv[lk] = lv
    raw_cv.pop("layout", None)
    merged.pop("layout", None)

    required_list_keys = ("skills", "languages", "interests", "experience", "education")
    required_dict_keys = ("header_title", "role_line1", "role_line2", "profile")
    for k, v in raw_cv.items():
        if k in required_list_keys and not isinstance(v, list):
            continue
        if k in required_dict_keys and not isinstance(v, dict):
            continue
        merged[k] = v

    # Validate nested element structures inside experience/education/skills
    if isinstance(merged.get("skills"), list):
        merged["skills"] = [s for s in merged["skills"] if isinstance(s, dict)]
    if isinstance(merged.get("languages"), list):
        merged["languages"] = [l for l in merged["languages"] if isinstance(l, dict)]
    if isinstance(merged.get("experience"), list):
        merged["experience"] = [e for e in merged["experience"] if isinstance(e, dict)]
    if isinstance(merged.get("education"), list):
        merged["education"] = [ed for ed in merged["education"] if isinstance(ed, dict)]

    if "show_signature" in merged and "include_signature" not in raw_cv:
        merged["include_signature"] = bool(merged.pop("show_signature"))
    elif "show_signature" in merged:
        merged.pop("show_signature", None)

    fact_fixes = _enforce_cv_fact_guardrails(cv_data, merged, master)

    # Algorithmic style guard: remove any stray AI punctuation or artifacts that slipped through
    cleaned_cv, style_fixes = sanitize_cv_style(merged)
    summary_text = str(result.get("summary") or "Lebenslauf gemäß Anweisung angepasst.")
    if fact_fixes:
        summary_text += f" (Fakten-Lock: {'; '.join(fact_fixes)})"
    if style_fixes:
        summary_text += f" (Stil-Wächter: {len(style_fixes)} KI-Zeichen/Präfixe bereinigt)"

    return {
        "summary": summary_text,
        "cv_data": cleaned_cv,
        "model": resolved_model,
        "style_fixes": style_fixes + fact_fixes,
    }


def generate_cover_letter_with_ai(
    current_draft: dict[str, Any],
    instruction: str = "",
    job_context: dict[str, Any] | None = None,
    model_key: str = "hydrogen",
    proofread_only: bool = False,
    humanize_only: bool = False,
) -> dict[str, Any]:
    """Generate or refine a tailored 1-page DIN cover letter strictly in the candidate's authentic voice,
    perform pure grammar and spell checking without modifying content, or humanize text using blader/humanizer principles."""
    profile = load_master_profile()
    candidate_name = str((profile.get("personal") or {}).get("name") or "den Kandidaten").strip()
    lang = "en" if current_draft.get("lang") == "en" else "de"
    lang_label = "Englisch (EN)" if lang == "en" else "Deutsch (DE)"

    existing_body = (current_draft.get("body_text") or "").strip()
    clean_job_desc = sanitize_job_posting(job_context.get("description") or "")["clean_text"] if job_context else ""

    if humanize_only:
        if not existing_body:
            raise ValueError("Kein Anschreiben-Text vorhanden, der humanisiert werden kann.")

        humanizer_spec = load_humanizer_skill_instructions()
        spec_header = (
            f"=== OFFIZIELLES 'blader/humanizer'-REGELWERK (DIREKT AUS GEKLONTEM REPO: vendor/humanizer/SKILL.md) ===\n"
            f"{humanizer_spec}\n"
            f"=== ENDE OFFIZIELLES REGELWERK ===\n\n"
        ) if humanizer_spec else ""

        prompt = (
            f"{spec_header}"
            f"Du führst das obige offizielle 'blader/humanizer'-Regelwerk für dieses Anschreiben aus. Zielsprache: {lang_label}.\n\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "AUFGABE: HUMANIZE-MODUS (ENTFERNUNG VON KI-TYPISCHEN MUSTERN NACH BLADER/HUMANIZER)\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "Überarbeite den vorliegenden Brieftext des Nutzers so, dass er natürlich, direkt und menschlich klingt — wie von einem echten Ingenieur geschrieben, nicht von einem Chatbot.\n\n"
            "WENDE DIE BLADER/HUMANIZER-REGELN STRIKT AN:\n"
            "1. STAGING STATT AUSSAGEN ENTFERNEN (§1-§5):\n"
            "   - KEINE 'Nicht nur X, sondern auch Y'-Konstruktionen (not X but Y). Formuliere stattdessen direkte, informative Aussagen.\n"
            "   - KEINE melodramatischen Einzeiler-Schlussfolgerungen ('Das ist der wahre Schlüssel', 'Hier schließt sich der Kreis', 'Read that again').\n"
            "   - KEINE schein-philosophischen Metaphern ('Im Grunde genommen', 'Das Herzstück', 'Die Währung von...', 'a testament to', 'at its core').\n"
            "   - KEINE künstlichen Vorankündigungen ('Lassen Sie mich erklären', 'Hier ist das Entscheidende', 'Here's the thing', 'Let's be honest').\n"
            "   - KEIN Diskutieren mit Phantom-Gegnern ('Das bedeutet nicht, dass...', 'Man könnte meinen... aber', 'To be clear').\n\n"
            "2. RHYTHMUS NACH SCHEMA F DURCHBRECHEN (§6-§8):\n"
            "   - KEINE erzwungenen Dreiergruppen (Forced Triads wie 'präzise, skalierbar und zukunftssicher'). Beschränke dich auf die 1–2 echten, substanziellen Punkte.\n"
            "   - KEINE Gedankenstriche (—, –, ' - ' oder '--'). Nutze Kommas oder Satzpunkte.\n"
            "   - KEINE Semikolons (;) und KEINE Doppelpunkte (:) im Fließtext.\n"
            "   - VARIIERE DIE SATZLÄNGE (BURSTINESS): Keine monotonen 18–22-Wort-Sätze. Wechsle kurze, klare Hauptsätze mit informativen Sätzen ab.\n\n"
            "3. BEDEUTUNGSINFLATION & HYPE-WÖRTER ENTFERNEN (§9-§13):\n"
            "   - Streiche alle KI-Superlative: 'leidenschaftlich', 'hochmodern', 'revolutionär', 'innovativ', 'bahnbrechend', 'nahtlos', 'herausragend', 'tiefgreifend', 'maßgeschneidert', 'passioniert', 'cutting-edge', 'state-of-the-art', 'groundbreaking', 'seamless', 'spearhead', 'tapestry', 'beacon', 'robust'.\n"
            f"   - {candidate_name} ist Ingenieur: Konkrete Fakten, Technologien und echte Projektergebnisse tragen den Text, keine künstliche Euphorie.\n\n"
            "4. CHATBOT-RÜCKSTÄNDE ELIMINIEREN (§18-§20):\n"
            "   - Keine Standard-Phrasen wie 'Mit großem Interesse habe ich Ihre Anzeige gelesen', 'Hiermit bewerbe ich mich', 'Ich hoffe, diese Bewerbung findet Ihr Interesse'.\n"
            "   - Steige direkt, lebendig und mit echtem technologischem Bezug ein.\n\n"
            "5. 100% FAKTEN- & INHALTSSCHUTZ (ZWEI-PHASEN-PRÜFUNG):\n"
            "   - Behalte alle Fakten, Firmen, Noten, Abschlüsse und Programmiersprachen aus dem Originaltext zu 100% bei.\n"
            "   - Verändere nicht den inhaltlichen Kern, sondern die sprachliche Natürlichkeit.\n"
            "   - Halte den Umfang exakt bei 3 bis 4 Absätzen für eine DIN-A4-Seite (ca. 200–245 Wörter).\n\n"
            "Gib ausschließlich ein JSON-Objekt mit folgenden Schlüsseln zurück:\n"
            "{\n"
            '  "subject": "Betreffzeile des Briefes (unverändert beibehalten)",\n'
            '  "salutation": "Anrede (unverändert beibehalten)",\n'
            '  "contact_person": "Ansprechpartner (unverändert beibehalten)",\n'
            '  "body_text": "Der humanisierte Brieftext (Absätze getrennt durch \\n\\n, OHNE Anrede und Grußformel)",\n'
            '  "closing": "Grußformel (unverändert beibehalten)",\n'
            '  "summary": "Kurze deutsche Zusammenfassung (1 Satz), welche KI-Muster bereinigt wurden",\n'
            '  "removed_patterns": ["Liste", "der", "konkret", "entfernten", "KI-Muster"]\n'
            "}\n\n"
            f"BETREFF: {current_draft.get('subject', '')}\n"
            f"ANREDE: {current_draft.get('salutation', '')}\n"
            f"ANSPRECHPARTNER: {current_draft.get('contact_person', '')}\n"
            f"GRUSSFORMEL: {current_draft.get('closing', '')}\n\n"
            f"ZU HUMANISIERENDER BRIEFTEXT:\n{existing_body}\n"
        )
    elif proofread_only:
        if not existing_body:
            raise ValueError("Kein Anschreiben-Text vorhanden, der korrigiert werden kann.")

        prompt = (
            f"Du bist ein hochpräziser Korrektor und Lektor für Bewerbungsschreiben. Zielsprache: {lang_label}.\n\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "AUFGABE: KORREKTURMODUS (NUR GRAMMATIK, RECHTSCHREIBUNG & ZEICHENSETZUNG)\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "Prüfe und korrigiere im vorgelegten Text des Nutzers AUSSCHLIESSLICH:\n"
            "- Rechtschreibfehler und Tippfehler (Groß-/Kleinschreibung, Getrennt-/Zusammenschreibung, 'dass' vs. 'das', ss vs. ß).\n"
            "- Grammatikfehler (Fälle: Dativ vs. Akkusativ, Kongruenz zwischen Subjekt und Verb, fehlerhafte Zeitformen).\n"
            "- Zeichensetzung (notwendige Kommas bei Nebensätzen, Infinitivgruppen und Aufzählungen).\n\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "STRIKTE VERBOTE & REGELN (100% INHALTS- & STILSCHUTZ):\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "1. VERÄNDERE DEN INHALT NICHT: Alle Aussagen, Erfahrungen, Firmennamen, Projekte, Technologien und Qualifikationen bleiben unverändert.\n"
            "2. VERÄNDERE DEN SCHREIBSTIL NICHT: Schreibe KEINE Sätze um, außer sie sind grammatikalisch unvollständig oder fehlerhaft. Verwandle die authentische Stimme NIEMALS in generische KI-Floskeln.\n"
            "3. BEHALTE DIE ABSATZSTRUKTUR BEI (Absätze durch doppelte Zeilenumbrüche '\\n\\n' getrennt).\n"
            "4. ABSOLUTES VERBOT VON KI-ZEICHEN (- ; :):\n"
            "   * KEINE Gedankenstriche (—, –, ' - ' oder '--'). Nutze Kommas oder Satzpunkte.\n"
            "   * KEINE Semikolons (;). Nutze Kommas oder 'und' / 'and'.\n"
            "   * KEINE Doppelpunkte (:) im Fließtext.\n"
            "5. Behalte 'subject', 'salutation', 'contact_person' und 'closing' exakt wie im Entwurf bei (korrigiere nur offensichtliche Tippfehler darin).\n"
            "6. Setze in 'summary' eine präzise kurze Rückmeldung (z.B. 'Rechtschreibung & Grammatik korrigiert (Inhalt & Stil 100% unverändert)').\n\n"
            f"BETREFF: {current_draft.get('subject', '')}\n"
            f"ANREDE: {current_draft.get('salutation', '')}\n"
            f"ANSPRECHPARTNER: {current_draft.get('contact_person', '')}\n"
            f"GRUSSFORMEL: {current_draft.get('closing', '')}\n\n"
            f"BRIEFTEXT DES NUTZERS:\n{existing_body}\n"
        )
    else:
        job_block = ""
        if job_context:
            ai_parsed = job_context.get("ai_parsed") or {}
            job_block = (
                "\nZIEL-STELLENANZEIGE:\n"
                + json.dumps(
                    {
                        "company": sanitize_job_posting(job_context.get("company") or "")["clean_text"][:200],
                        "title": sanitize_job_posting(job_context.get("title") or "")["clean_text"][:300],
                        "location": sanitize_job_posting(job_context.get("location") or "")["clean_text"][:200],
                        "description": _smart_truncate(clean_job_desc, 9500),
                        "matched_strengths": ai_parsed.get("evidence") or [],
                        "potential_gaps": ai_parsed.get("gaps") or [],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        user_instr = instruction.strip() or (
            "Überarbeite und schärfe den vorhandenen Entwurf passend zur Stelle."
            if existing_body
            else "Verfasse ein überzeugendes, zukunfts- und problemorientiertes Anschreiben für diese Stelle."
        )
        facts_summary = _build_dynamic_profile_facts(profile)

        prompt = (
            f"Du bist ein persönlicher Bewerbungs-Texter für {candidate_name}. Verfasse oder verfeinere ein Anschreiben in der Sprache: {lang_label}.\n\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "1. ZUKUNFTS- & PROBLEMLÖSER-FOKUS (RECRUITER-GOLDSTANDARD):\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "- 'Dein Lebenslauf handelt von der Vergangenheit, dein Anschreiben von deiner Zukunft.'\n"
            "- KEINE CHRONOLOGISCHE CV-WIEDERHOLUNG: Zähle nicht auf, wann studiert wurde oder welche Noten vorlagen. Der Lebenslauf liegt bereits vor.\n"
            "- FOKUS AUF DIE HERAUSFORDERUNG DES UNTERNEHMENS: Zeige, wie die Fähigkeiten ganz konkret ein Problem lösen oder ein Vorhaben des Unternehmens voranbringen.\n"
            "- SPEZIFISCHER UNTERNEHMENSBEZUG: Beziehe dich direkt auf das Unternehmen, das Produkt, die technologische Mission oder das konkrete Team.\n"
            "- ABSOLUT KEINE GENERISCHEN EINSTIEGSFLOSKELN: Niemals 'Hiermit bewerbe ich mich...' oder 'Mit großem Interesse...'. Starte direkt mit dem inhaltlichen Match.\n\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "2. AUTHENTISCHER SCHREIBSTIL & STRIKTE VERBOTE:\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "- Ruhig, sachlich, technisch präzise und bodenständig.\n"
            "- KEINE Gedankenstriche (—, –, ' - ', '--'), KEINE Semikolons (;), KEINE Doppelpunkte (:) im Fließtext, KEINE Hype-Wörter.\n"
            f"- KEINE HALLUZINATIONEN (FAKTENGRENZEN):\n{facts_summary}\n\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "3. UMFANG & STRUKTUR FÜR EXAKT 1 DIN-A4-SEITE:\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "- 'body_text' enthält NUR 3 bis 4 Absätze (getrennt durch '\\n\\n'), OHNE Anrede/Grußformel, idealerweise 200 bis 245 Wörter.\n"
            "- Falls in der Anzeige eine Person genannt ist, trage sie in 'contact_person' ein; sonst leer lassen.\n"
            "- Nutze KEINE Tools. Gib ausschließlich das geforderte JSON-Objekt zurück.\n\n"
            f"KANDIDATENPROFIL (JSON):\n{json.dumps({'cv_base': profile.get('cv_base', {}), 'ranking_profile': profile.get('ranking_profile', {})}, ensure_ascii=False)}\n"
            f"{job_block}\n"
            f"AKTUELLER ENTWURF:\n{json.dumps({k: current_draft.get(k, '') for k in ('company', 'contact_person', 'subject', 'salutation', 'body_text', 'closing')}, ensure_ascii=False)}\n\n"
            f"ANWEISUNG DES NUTZERS:\n{user_instr}\n"
        )

    result, resolved_model = run_agy_json(
        prompt,
        schema=LETTER_DRAFT_SCHEMA,
        model_key=model_key,
        timeout=210,
        allow_argon=True,
    )

    raw_body_text = str(result.get("body_text") or "").strip()
    # Algorithmic style guard: remove any stray AI punctuation from cover letter paragraphs
    body_text_res, style_fixes = sanitize_letter_style(raw_body_text)
    audit_warnings = audit_generated_letter(body_text_res, profile=profile, job_desc=clean_job_desc)

    contact_val = (
        str(result["contact_person"]).strip()
        if "contact_person" in result and result["contact_person"] is not None
        else str(current_draft.get("contact_person") or "").strip()
    )

    return {
        "subject": str(result.get("subject") or current_draft.get("subject") or "").strip(),
        "salutation": str(result.get("salutation") or current_draft.get("salutation") or "").strip(),
        "contact_person": contact_val,
        "body_text": body_text_res,
        "closing": str(result.get("closing") or current_draft.get("closing") or "").strip(),
        "summary": str(result.get("summary") or "Anschreiben-Entwurf erstellt.").strip(),
        "removed_patterns": list(result.get("removed_patterns") or []),
        "audit_warnings": audit_warnings,
        "style_fixes": style_fixes,
        "model": resolved_model,
    }


def audit_generated_letter(
    body: str,
    profile: dict[str, Any] | None = None,
    job_desc: str = "",
) -> list[str]:
    """Prüft generierte Anschreiben auf verbotene Floskeln, Gedankenstriche und nicht belegte Zahlen."""
    warnings: list[str] = []
    if not body:
        return warnings

    # 1. AI marker and punctuation audit via style guard
    warnings.extend(audit_text_for_ai_markers(body))

    body_lower = body.lower()

    # 2. Anti-Floskel-Check (inspiriert von ai-job-search 03-writing-style.md)
    cliche_patterns = [
        ("hiermit bewerbe ich mich", "Vermeide die Standard-Floskel 'hiermit bewerbe ich mich' — steige direkt mit deiner Motivation/Fachkompetenz ein."),
        ("mit großem interesse", "Floskel 'mit großem Interesse' erkannt — formuliere stattdessen konkret, welches Projekt/Thema dich begeistert."),
        ("passionate about", "Cliché 'passionate about' detected — focus on concrete engineering outcomes instead."),
        ("hit the ground running", "Cliché 'hit the ground running' detected — replace with specific domain readiness."),
        ("synergies", "Buzzword 'synergies' detected — describe concrete technical collaboration."),
        ("synergien", "Buzzword 'Synergien' erkannt — beschreibe konkrete technische Zusammenarbeit."),
        ("great fit", "Floskel 'great fit' erkannt — belege die Passung anhand konkreter Aufgaben."),
    ]
    for pattern, msg in cliche_patterns:
        if pattern in body_lower:
            warnings.append(msg)

    # 3. Metrik-/Halluzinations-Audit: Zahlen mit Einheiten (% / ms / Hz / fps)
    if profile is None:
        try:
            profile = load_master_profile()
        except Exception:
            profile = {}

    profile_dump = json.dumps(profile, ensure_ascii=False).lower()
    job_desc_lower = job_desc.lower()

    metrics = re.findall(r"\b\d+(?:[.,]\d+)?\s*(?:%|prozent|percent|ms|hz|fps)\b", body, flags=re.IGNORECASE)
    for m in metrics:
        norm_m = re.sub(r"\s+", "", m.lower())
        norm_prof = re.sub(r"\s+", "", profile_dump)
        norm_job = re.sub(r"\s+", "", job_desc_lower)
        pat_m = rf"(?<![0-9.,]){re.escape(norm_m)}"
        if not re.search(pat_m, norm_prof) and not re.search(pat_m, norm_job):
            warnings.append(f"Prüfe Metrik/Zahl '{m}': Steht nicht wörtlich im Master-Profil (Halluzinationsgefahr).")

    return warnings


def generate_interview_prep_with_ai(
    job: dict[str, Any],
    profile: dict[str, Any] | None = None,
    lang: str = "de",
    model_key: str = "hydrogen",
) -> dict[str, Any]:
    """Generiert ein maßgeschneidertes Interview-Prep-Paket mit STAR-Stories und ehrlichen Gap-Bridges."""
    if profile is None:
        profile = load_master_profile()

    is_en = lang.lower() == "en"
    lang_label = "Englisch (EN)" if is_en else "Deutsch (DE)"

    job_title = sanitize_job_posting(job.get("title") or "")["clean_text"][:300]
    company = sanitize_job_posting(job.get("company") or "")["clean_text"][:200]
    location = sanitize_job_posting(job.get("location") or "")["clean_text"][:200]
    raw_desc = job.get("description", "")
    clean_desc = _smart_truncate(sanitize_job_posting(raw_desc)["clean_text"], 9500)

    ai_data = job.get("ai_parsed") or job.get("ai_data") or {}
    if isinstance(ai_data, str):
        try:
            ai_data = json.loads(ai_data)
        except Exception:
            ai_data = {}

    gaps = [sanitize_job_posting(str(g))["clean_text"] for g in (ai_data.get("gaps") or [])]
    strengths = [sanitize_job_posting(str(s))["clean_text"] for s in (ai_data.get("evidence") or ai_data.get("strengths") or [])]

    job_payload = {
        "company": company,
        "title": job_title,
        "location": location,
        "matched_strengths": strengths,
        "potential_gaps": gaps,
        "description": clean_desc,
    }
    candidate_name = str((profile.get("personal") or {}).get("name") or "den Kandidaten").strip()
    facts_summary = _build_dynamic_profile_facts(profile)

    prompt = (
        f"Du bist ein exzellenter technischer Interview-Coach für {candidate_name}. Bereite auf das Vorstellungsgespräch vor. "
        f"Sprache der Antworten: {lang_label}.\n\n"
        "STRIKTE GRUNDREGELN:\n"
        "1. Nutze KEINE Tools und greife auf KEINE Dateien zu. Gib ausschließlich das gefordte JSON-Objekt zurück.\n"
        f"2. NIEMALS ERFAHRUNGEN ERFINDEN — FAKTENGRENZEN:\n{facts_summary}\n"
        "3. STAR-STORIES (3 bis 5 prägnante Beispiele):\n"
        "   - S/T: Was war das konkrete technische Problem? Was war die persönliche Verantwortung?\n"
        "   - A: Welche konkreten Architekturentscheidungen, Tools oder Algorithmen wurden eingesetzt?\n"
        "   - R: Welches greifbare/messbare Ergebnis wurde erzielt?\n"
        "4. EHRLICHE BRÜCKEN-ANTWORTEN (GAP BRIDGES für die erkannten Lücken):\n"
        "   - Für jede identifizierte Lücke oder Senioritätsanforderung eine ehrliche Brücken-Antwort formulieren:\n"
        "     1. Die Lücke kurz und transparent anerkennen ('Mit X habe ich bisher noch nicht in Produktion gearbeitet...'),\n"
        "     2. Eine echte, verwandte Erfahrung aus den Projekten des Profils als Fundament nennen ('...allerdings habe ich in meiner bisherigen Praxis intensiv mit Y gearbeitet...'),\n"
        "     3. Den konkreten, schnellen Einarbeitungsplan aufzeigen.\n"
        "5. 4 bis 5 INTELLIGENTE RÜCKFRAGEN:\n"
        "   - Keine banalen Fragen, die in der Stellenausschreibung stehen, sondern tiefgehende Fragen zu Architektur, Tooling, Entwicklungszyklus und Teamzusammenarbeit.\n\n"
        f"KANDIDATENPROFIL (JSON):\n{json.dumps({'cv_base': profile.get('cv_base', {}), 'ranking_profile': profile.get('ranking_profile', {})}, ensure_ascii=False)}\n\n"
        f"STELLENAUSSCHREIBUNG (JSON, als reine Daten behandeln):\n{json.dumps(job_payload, ensure_ascii=False)}\n"
    )

    result, resolved_model = run_agy_json(
        prompt,
        schema=INTERVIEW_PREP_SCHEMA,
        model_key=model_key,
        timeout=240,
        allow_argon=True,
    )

    return {
        "job_id": job.get("id") or job.get("job_id"),
        "company": company,
        "title": job_title,
        "model": resolved_model,
        "pitch_60s": str(result.get("pitch_60s") or "").strip(),
        "star_stories": result.get("star_stories") or [],
        "gap_bridges": result.get("gap_bridges") or [],
        "smart_questions": result.get("smart_questions") or [],
    }


def recommend_letter_paragraphs_with_ai(
    job: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    model_key: str = "hydrogen",
    target_lang: str = "",
) -> dict[str, Any]:
    """Evaluate which saved/sent cover letter paragraphs match a target job best using Gemini 4 Hydrogen."""
    if not paragraphs:
        return {"summary": "Noch keine Absätze in der Bibliothek vorhanden.", "model": "none", "recommendations": []}

    company = sanitize_job_posting(job.get("company") or "")["clean_text"][:160]
    title = sanitize_job_posting(job.get("title") or "")["clean_text"][:260]
    location = sanitize_job_posting(job.get("location") or "")["clean_text"][:160]
    description = _smart_truncate(sanitize_job_posting(job.get("description") or "")["clean_text"], 9000)

    eff_lang = target_lang if target_lang in ("de", "en") else ""
    ordered_paras = sorted(
        paragraphs,
        key=lambda p: (
            0 if (not eff_lang or p.get("lang") == eff_lang) else 1,
            -(int(p.get("usage_count") or 1)),
        ),
    )[:60]
    valid_ids = {int(p["id"]) for p in ordered_paras}

    items = [
        {
            "id": int(p["id"]),
            "lang": p.get("lang", "de"),
            "category": p.get("category", "body"),
            "label": p.get("label", ""),
            "origin": p.get("company", ""),
            "text": str(p.get("text") or "")[:1100],
        }
        for p in ordered_paras
    ]

    lang_hint = f"ZIEL-SPRACHE DES ANSCHREIBENS: {eff_lang.upper()} — bevorzuge Absätze mit lang=='{eff_lang}'.\n" if eff_lang else ""
    prompt = (
        "Du hilfst dem Kandidaten dabei, aus der Bibliothek bewährter Anschreiben-Absätze "
        "die besten Bausteine für die unten stehende Ziel-Stelle auszuwählen und passend anzuordnen.\n\n"
        f"{lang_hint}"
        "AUFGABE:\n"
        "1. Bewerte JEDEN bereitgestellten Absatz mit einem `fit_score` (0..100) bezogen auf die Ziel-Stelle.\n"
        "2. Setze `recommended: true` für die 3 bis 5 am besten zueinander passenden Absätze, die gemeinsam "
        "ein starkes, thematisch stimmiges Gesamt-Anschreiben ergeben (typisch: Slot 1 = Einleitung, "
        "Slot 2/3 = Haupt-Erfahrung/Projekte mit bestem Fachbezug zur Stelle, Slot 4 = Einsatzbereich/Brücke/Deployment, Slot 5 = Abschluss).\n"
        "3. Vergib für jeden Absatz `suggested_slot` (1..6) entsprechend der sinnvollsten Position im Brief.\n"
        "4. Schreibe zu jedem Absatz eine prägnante deutsche `reason` (max. 130 Zeichen), warum er zur Stelle passt (oder warum eher nicht, z. B. anderer Tech-Fokus / andere Sprache).\n"
        "5. Gib als `summary` 1–2 Sätze Gesamtfazit (welcher Schwerpunkt für diese Stelle optimal kombiniert werden sollte).\n"
        "Nutze KEINE Tools oder Dateizugriffe. Antworte AUSSCHLIESSLICH mit dem geforderten JSON.\n\n"
        f"ZIEL-STELLE:\n{json.dumps({'company': company, 'title': title, 'location': location, 'description': description}, ensure_ascii=False)}\n\n"
        f"ABSATZ-BIBLIOTHEK:\n{json.dumps(items, ensure_ascii=False)}\n"
    )

    allow_argon = str(model_key or "").startswith(("argon", "neon"))
    parsed, resolved_model = run_agy_json(
        prompt,
        schema=PARAGRAPH_RECOMMEND_SCHEMA,
        model_key=model_key or "hydrogen",
        timeout=150,
        allow_argon=allow_argon,
    )
    recs = [r for r in (parsed.get("recommendations") or []) if isinstance(r, dict) and r.get("paragraph_id") in valid_ids]
    return {
        "summary": str(parsed.get("summary") or "").strip(),
        "model": resolved_model,
        "recommendations": recs,
    }


def adapt_letter_paragraphs_with_ai(
    job: dict[str, Any] | None,
    paragraphs: list[str],
    lang: str = "de",
    model_key: str = "hydrogen",
) -> dict[str, Any]:
    """Light-touch adaptation of assembled building blocks to a target company/role without losing candidate wording."""
    cleaned_in = [str(p or "").strip() for p in paragraphs if str(p or "").strip()]
    if not cleaned_in:
        raise ValueError("Keine Absätze zum Anpassen vorhanden.")

    profile = load_master_profile()
    candidate_name = str((profile.get("personal") or {}).get("name") or "den Kandidaten").strip()
    facts_summary = _build_dynamic_profile_facts(profile)
    is_en = lang == "en"
    lang_label = "Englisch (EN)" if is_en else "Deutsch (DE)"

    job_payload: dict[str, Any] = {}
    if job:
        job_payload = {
            "company": sanitize_job_posting(job.get("company") or "")["clean_text"][:160],
            "title": sanitize_job_posting(job.get("title") or "")["clean_text"][:260],
            "location": sanitize_job_posting(job.get("location") or "")["clean_text"][:160],
            "description": _smart_truncate(sanitize_job_posting(job.get("description") or "")["clean_text"], 7000),
        }

    prompt = (
        f"Du hilfst {candidate_name} dabei, bereits zusammengestellte Textbausteine des Anschreibens LEICHT an die Ziel-Stelle anzupassen (Sprache: {lang_label}).\n\n"
        "STRIKTE MINIMAL-INVASIV-REGELN:\n"
        "1. BEHALTE die bewährten Formulierungen, Satzstruktur und den technischen Stil weitgehend wörtlich bei! KEIN komplettes Neuschreiben.\n"
        "2. Passe NUR gezielt an:\n"
        "   - Alte/fremde Firmennamen, Städte oder alte Startdaten (z. B. 'Mitte November 2026') durch die Ziel-Firma, aktuellen Ort und 'ab sofort'/'immediately' ersetzen.\n"
        "   - Leichte Schwerpunkt-Betonung auf Technologien/Aufgaben der Ziel-Stelle.\n"
        "   - Falls ein Baustein in der falschen Sprache ist, sauber in die Ziel-Sprache übertragen.\n"
        "   - Glätte holprige Übergänge zwischen Absätzen minimal.\n"
        "3. KEINE Gedankenstriche (—, –, ' - ', '--'), KEINE Semikolons (;), KEINE Hype-Wörter ('leidenschaftlich', 'innovativ', 'cutting-edge', 'leveraged').\n"
        f"4. NIEMALS FAKTEN ERFINDEN:\n{facts_summary}\n"
        "5. Liefere exakt dieselbe Anzahl Absätze in `adapted_paragraphs` zurück.\n\n"
        f"ZIEL-STELLE:\n{json.dumps(job_payload, ensure_ascii=False)}\n\n"
        f"BAUSTEINE (JSON ARRAY):\n{json.dumps(cleaned_in, ensure_ascii=False)}\n"
    )

    allow_argon = str(model_key or "").startswith(("argon", "neon"))
    parsed, resolved_model = run_agy_json(
        prompt,
        schema=LETTER_BLOCKS_ADAPT_SCHEMA,
        model_key=model_key or "hydrogen",
        timeout=150,
        allow_argon=allow_argon,
    )
    out_raw = [str(p or "").strip() for p in (parsed.get("adapted_paragraphs") or []) if str(p or "").strip()]
    if not out_raw:
        out_raw = cleaned_in
    sanitized_joined, style_fixes = sanitize_letter_style("\n\n".join(out_raw))
    final_paras = [p.strip() for p in sanitized_joined.split("\n\n") if p.strip()] or out_raw
    changes = [str(c) for c in (parsed.get("changes") or []) if str(c).strip()]
    changes.extend(style_fixes)

    return {
        "adapted_paragraphs": final_paras,
        "body_text": "\n\n".join(final_paras),
        "changes": changes,
        "summary": str(parsed.get("summary") or f"{len(final_paras)} Bausteine leicht angepasst.").strip(),
        "model": resolved_model,
    }


def _clamp_score(val: Any, default: int = 50) -> int:
    try:
        return max(0, min(100, int(round(float(val)))))
    except Exception:
        return default


def _normalize_prioritized_ranking_result(
    parsed: dict[str, Any],
    jobs: list[dict[str, Any]],
    focus: str,
    instruction: str,
    resolved_model: str,
) -> dict[str, Any]:
    """Validate, deduplicate, and complete AI ranking items for all active prioritized jobs."""
    valid_waves = {"jetzt", "danach", "spaeter", "pruefen"}
    valid_chances = {"hoch", "mittel", "ambitioniert"}
    valid_urgencies = {"hoch", "mittel", "niedrig"}
    jobs_by_id = {int(j["id"]): j for j in jobs}

    normalized: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    raw_ranking = parsed.get("ranking") or []
    # Respect model's explicit rank field when well-formed, preserving list order on ties
    sorted_raw = sorted(
        [r for r in raw_ranking if isinstance(r, dict)],
        key=lambda r: (int(r["rank"]) if isinstance(r.get("rank"), (int, float)) else 999999),
    )

    for item in sorted_raw:
        try:
            jid = int(item.get("job_id"))
        except Exception:
            continue
        if jid not in jobs_by_id or jid in seen_ids:
            continue
        seen_ids.add(jid)
        wave = str(item.get("wave") or "pruefen").strip().lower()
        if wave not in valid_waves:
            wave = "pruefen"
        chance_level = str(item.get("chance_level") or "mittel").strip().lower()
        if chance_level not in valid_chances:
            chance_level = "mittel"
        urgency = str(item.get("urgency") or "mittel").strip().lower()
        if urgency not in valid_urgencies:
            urgency = "mittel"
        raw_scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        normalized.append({
            "job_id": jid,
            "rank": len(normalized) + 1,
            "wave": wave,
            "chance_level": chance_level,
            "urgency": urgency,
            "scores": {
                "chance": _clamp_score(raw_scores.get("chance"), 60),
                "fit": _clamp_score(raw_scores.get("fit"), 60),
                "location": _clamp_score(raw_scores.get("location"), 60),
                "timing": _clamp_score(raw_scores.get("timing"), 55),
            },
            "rationale": str(item.get("rationale") or "").strip()[:360],
            "tradeoff": str(item.get("tradeoff") or "").strip()[:280],
            "next_action": str(item.get("next_action") or "").strip()[:260],
        })

    for j in jobs:
        jid = int(j["id"])
        if jid in seen_ids:
            continue
        seen_ids.add(jid)
        fallback_score = _clamp_score(j.get("ai_score") if j.get("ai_score") is not None else j.get("rule_score"), 50)
        tier = int(j.get("tier") or 4)
        loc_score = {1: 90, 2: 65, 3: 40, 4: 25}.get(tier, 40)
        normalized.append({
            "job_id": jid,
            "rank": len(normalized) + 1,
            "wave": "pruefen",
            "chance_level": "mittel",
            "urgency": "niedrig",
            "scores": {
                "chance": fallback_score,
                "fit": fallback_score,
                "location": loc_score,
                "timing": 50,
            },
            "rationale": "Automatisch an bestehender Position eingereiht.",
            "tradeoff": "",
            "next_action": "",
        })

    ranked_ids = [it["job_id"] for it in normalized]
    return {
        "created_at": now_iso(),
        "model": resolved_model,
        "focus": focus,
        "instruction": instruction,
        "strategy_summary": str(parsed.get("strategy_summary") or "").strip(),
        "ranked_ids": ranked_ids,
        "items": normalized,
        "items_by_id": {str(it["job_id"]): it for it in normalized},
    }


def _build_ranking_job_payload(
    idx: int,
    job: dict[str, Any],
    company_counts: dict[str, int],
    company_history: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    comp = str(job.get("company") or "").strip()
    comp_key = canonical_company(comp).casefold()
    hist_entries = company_history.get(comp_key) or []
    hist_parts = [
        f"{h.get('status_label') or h.get('status')} am {h.get('application_date') or '?'} ({str(h.get('title') or '')[:55]})"
        for h in hist_entries[:3]
    ]
    ai_parsed = job.get("ai_parsed") if isinstance(job.get("ai_parsed"), dict) else {}
    cls_data = ai_parsed.get("classification") if isinstance(ai_parsed.get("classification"), dict) else {}

    entry: dict[str, Any] = {
        "job_id": int(job["id"]),
        "current_rank": idx,
        "company": comp,
        "company_jobs_in_shortlist": company_counts.get(comp_key, 1),
        "company_history": "; ".join(hist_parts),
        "preference": job.get("preference") or "neutral",
        "title": sanitize_job_posting(job.get("title") or "")["clean_text"][:200],
        "location": str(job.get("location") or "")[:120],
        "tier": int(job.get("tier") or 4),
        "tier_label": str(job.get("tier_label") or ""),
        "work_mode": cls_data.get("work_mode") or job.get("work_mode") or "unknown",
        "age_days": job.get("age_days"),
        "first_seen_date": job.get("first_seen_date") or "",
        "was_relisted": bool(job.get("was_relisted")),
        "deadline": str(job.get("deadline") or ""),
        "rule_score": job.get("rule_score"),
        "ai_score": job.get("ai_score"),
        "fit_band": cls_data.get("fit_band") or job.get("fit_band") or "",
        "entry_level": cls_data.get("entry_level") or "",
        "required_years": cls_data.get("required_years"),
        "experience_importance": cls_data.get("experience_importance") or "",
        "ai_reason": str(ai_parsed.get("reason") or "")[:220],
        "evidence": [str(e)[:90] for e in (ai_parsed.get("evidence") or [])[:3]],
        "gaps": [str(g)[:90] for g in (ai_parsed.get("gaps") or [])[:3]],
        "status": str(job.get("status") or "neu"),
        "has_cv": bool(job.get("has_cv")),
        "has_letter": bool(job.get("has_letter")),
        "note": sanitize_job_posting(job.get("note") or "")["clean_text"][:180],
    }
    if job.get("ai_score") is None and job.get("description"):
        entry["description_snippet"] = sanitize_job_posting(job.get("description") or "")["clean_text"][:450]
    return entry


def rank_prioritized_jobs_with_ai(
    jobs: list[dict[str, Any]],
    company_history: dict[str, list[dict[str, Any]]],
    focus: str = "balanced",
    instruction: str = "",
    model_key: str = "hydrogen",
) -> dict[str, Any]:
    """Compare all active prioritized jobs head-to-head and propose an actionable application order."""
    if not jobs:
        raise ValueError("Keine aktiven priorisierten Stellen zum Abwägen vorhanden.")

    focus_clean = focus if focus in ("balanced", "chance", "location", "urgency") else "balanced"
    clean_instruction = str(instruction or "").strip()[:500]
    jobs_to_rank = jobs[:60]

    company_counts: dict[str, int] = {}
    for j in jobs_to_rank:
        ck = canonical_company(str(j.get("company") or "")).casefold()
        company_counts[ck] = company_counts.get(ck, 0) + 1

    compact_jobs = [
        _build_ranking_job_payload(i + 1, j, company_counts, company_history)
        for i, j in enumerate(jobs_to_rank)
    ]

    profile = load_master_profile()
    candidate_name = str((profile.get("personal") or {}).get("name") or "den Kandidaten").strip()
    facts_summary = _build_dynamic_profile_facts(profile)
    personal = profile.get("personal") or {}
    home_loc = f"{personal.get('zip', '')} {personal.get('city', 'München')}".strip()
    today_str = now_iso()[:10]

    focus_rules = {
        "balanced": (
            "FOKUS 'balanced' (Ausgewogene Gesamtabwägung):\n"
            "Gewichte Einladungschance (35%), fachliche Passung (30%), Standort/Bayern-Nähe (20%) "
            "und Timing/Dringlichkeit (15%) ausgewogen."
        ),
        "chance": (
            "FOKUS 'chance' (Maximale Einladungschance zuerst):\n"
            "Gewichte realistische Einladungschance (55%), fachliche Passung (25%), Standort (10%), Timing (10%). "
            "Bevorzuge Einsteiger-/Junior-/Absolventenrollen mit geringen Lücken ohne kürzliche Firmen-Absagen "
            "klar vor ambitionierten Senior-/Staff-Stellen."
        ),
        "location": (
            "FOKUS 'location' (Standort & Pendelbarkeit priorisieren):\n"
            "Gewichte Standort/Zone (45%), Einladungschance (25%), fachliche Passung (20%), Timing (10%). "
            "Stellen in Zone 1 (Raum Ingolstadt, München, Augsburg, Nürnberg/Erlangen, Regensburg) bzw. nah an "
            "Ingolstadt oder mit sauberem Hybrid-Modell gehören deutlich nach vorne vor weite Umzugsstandorte."
        ),
        "urgency": (
            "FOKUS 'urgency' (Timing & Fristen priorisieren):\n"
            "Gewichte Timing/Dringlichkeit (45%), Einladungschance (25%), fachliche Passung (20%), Standort (10%). "
            "Ziehe Stellen mit expliziter naher Bewerbungsfrist (deadline) oder sehr frischem Veröffentlichungsdatum "
            "(age_days <= 10, First-Mover-Vorteil) nach vorne vor wochenlange Daueranzeigen."
        ),
    }[focus_clean]

    user_wish_block = (
        f"\nZUSÄTZLICHER NUTZER-WUNSCH (mit höchster Priorität berücksichtigen):\n{clean_instruction}\n"
        if clean_instruction
        else ""
    )

    prompt = (
        f"Du bist der Karriere-Strategie-Berater für {candidate_name}. Vergleiche alle unten stehenden AKTIVEN "
        "priorisierten Stellen gegeneinander und erstelle eine konkrete, realistische Bewerbungsreihenfolge "
        "von Platz 1 (sofort bewerben) bis Platz N.\n\n"
        f"HEUTIGES DATUM: {today_str}\n"
        f"WOHNORT & BASIS: {home_loc}\n"
        f"PROFIL-FAKTEN (strikt wahrheitsgetreu, nichts erfinden):\n{facts_summary}\n\n"
        f"{focus_rules}\n"
        f"{user_wish_block}\n"
        "WICHTIGE ABWÄGUNGSREGELN:\n"
        "1. Vergleiche alle Stellen RELATIV ZUEINANDER und vergib jeden Rang 1..N genau EINMAL ohne Lücke. "
        "Jede übergebene `job_id` muss exakt einmal in `ranking` erscheinen.\n"
        "2. Berücksichtige `company_history`: Eine kürzliche Absage oder bereits laufende Bewerbung bei derselben "
        "Firma senkt die unmittelbare Einladungschance (Abkühlungsphase / Doppelbewerbungs-Risiko). Nenne dies klar "
        "in `tradeoff` und stufe die Stelle meist zurück (`spaeter` oder `pruefen`), außer das Team/Profil ist klar verschieden.\n"
        "3. Mehrfach-Stellen beim selben Arbeitgeber (`company_jobs_in_shortlist > 1`): Stapel nicht 3 Stellen "
        "derselben Firma blind auf Platz 1–3! Ziehe die chancenreichste/passendste Rolle vor und staffle die "
        "anderen dahinter (mit Hinweis im `tradeoff`).\n"
        "4. Realistische Senioritäts-Prüfung anhand des verifizierten Profils (Abschluss, Werkstudenten- und "
        "Praktikumspraxis vs. mehrjährige Vollzeit-Senior-Erfahrung). Stellen mit `required_years >= 3` oder "
        "'Staff'/'Senior'/'Lead' ehrlich als `ambitioniert` (`spaeter` oder `pruefen`) einordnen statt vor starke Einsteiger-Passungen.\n"
        "5. Online-Alter & Fristen: Nahe `deadline` oder frisch (`age_days <= 10`) -> höhere Dringlichkeit; "
        "sehr lange online (`> 45 Tage`) oder `was_relisted` -> in `tradeoff` als Daueranzeige/Prüfpunkt benennen.\n"
        "6. Momentum-Plus: Wenn `status == 'vorbereiten'`, `has_cv == true` oder `has_letter == true`, liegt schon "
        "Arbeit vor — berücksichtige das positiv.\n"
        "7. Standort & Mobilität gemäß `ranking_profile.mobility_and_work_model` sowie `location_priority` berücksichtigen.\n"
        "8. Chancen-Pluspunkte aus `ranking_profile.references_and_artifacts` und `compensation_and_contract` einbeziehen.\n"
        "9. Wellen-Definition (`wave`):\n"
        "   - 'jetzt': Top-Priorität für die nächste Bewerbungsrunde (hohe Passung + gute Chance).\n"
        "   - 'danach': Solide zweite Welle nach den Top-Kandidaten.\n"
        "   - 'spaeter': Warten/nachgelagert (z. B. Firmen-Abkühlung, 2. Stelle derselben Firma, weiterer Standort).\n"
        "   - 'pruefen': Kritisch prüfen wegen großer Senioritäts- oder Skill-Lücke bzw. unklarer Erfolgsaussicht.\n"
        "10. Schreibstil: Prägnant, konkret auf Deutsch, keine Gedankenstriche (—, –), keine Floskeln. "
        "Nutze KEINE Tools oder Dateizugriffe. Antworte AUSSCHLIESSLICH mit dem JSON gemäß Schema.\n\n"
        f"PRIORISIERTE STELLEN ({len(compact_jobs)} aktive):\n"
        f"{json.dumps(compact_jobs, ensure_ascii=False)}\n"
    )

    allow_argon = str(model_key or "").startswith(("argon", "neon"))
    parsed, resolved_model = run_agy_json(
        prompt,
        schema=PRIORITIZED_RANKING_SCHEMA,
        model_key=model_key or "hydrogen",
        timeout=210,
        allow_argon=allow_argon,
    )
    return _normalize_prioritized_ranking_result(
        parsed=parsed,
        jobs=jobs,
        focus=focus_clean,
        instruction=clean_instruction,
        resolved_model=resolved_model,
    )
