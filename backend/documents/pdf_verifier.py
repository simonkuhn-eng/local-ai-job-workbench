"""ATS- & Layout-Qualitätsprüfung für generierte Lebenslauf- und Anschreiben-PDFs.

Inspiriert von ai-job-search/tools/verify_pdf.py & verify_layout.py,
adaptiert auf pypdfium2 und pypdf ohne externe Zusatzabhängigkeiten.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import load_master_profile

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

_PDFIUM_LOCK = threading.Lock()

DATE_ENDASH_RE = re.compile(
    r"(?:\b(?:0?[1-9]|1[0-2])/\d{4}|\b(?:19|20)\d{2})\s*[\u2013\u2014]\s*"
    r"(?:(?:0?[1-9]|1[0-2])/\d{4}|(?:19|20)\d{2}|heute|present|current|now)\b",
    re.IGNORECASE,
)


def extract_pdf_text_and_stats(pdf_path: Path) -> dict[str, Any]:
    """Liest Seitenanzahl und extrahierbaren Text-Layer über pypdfium2 (oder pypdf Fallback) aus."""
    if not pdf_path.exists():
        return {"page_count": 0, "full_text": "", "char_count": 0}

    # 1. Bevorzugt pypdfium2 (thread-sicher geschützt, da PDFium unter Windows nicht reentrant ist)
    if pdfium is not None:
        with _PDFIUM_LOCK:
            try:
                doc = pdfium.PdfDocument(str(pdf_path))
                try:
                    page_count = len(doc)
                    pages_text: list[str] = []
                    for idx in range(page_count):
                        page = doc[idx]
                        textpage = page.get_textpage()
                        pages_text.append(textpage.get_text_range() or "")
                        textpage.close()
                        page.close()
                    full_text = "\n".join(pages_text)
                    return {
                        "page_count": page_count,
                        "full_text": full_text,
                        "char_count": len(full_text.strip()),
                    }
                finally:
                    doc.close()
            except Exception:
                pass

    # 2. Fallback: pypdf
    if PdfReader is not None:
        try:
            reader = PdfReader(str(pdf_path))
            pages = reader.pages
            page_count = len(pages)
            full_text = "\n".join(page.extract_text() or "" for page in pages)
            return {
                "page_count": page_count,
                "full_text": full_text,
                "char_count": len(full_text.strip()),
            }
        except Exception:
            pass

    return {"page_count": 0, "full_text": "", "char_count": 0}


def extract_job_keywords(job: dict[str, Any] | None, max_keywords: int = 18) -> list[str]:
    """Extrahiert die wichtigsten technischen Fachbegriffe aus einer Stelle."""
    if not job:
        return []
    raw_parts = [
        str(job.get("title") or ""),
        str(job.get("description") or ""),
    ]
    ai_data = job.get("ai_parsed") or job.get("ai_data") or {}
    if isinstance(ai_data, str):
        try:
            ai_data = json.loads(ai_data)
        except Exception:
            ai_data = {}
    if isinstance(ai_data, dict):
        for s in (ai_data.get("evidence") or ai_data.get("strengths") or []):
            raw_parts.append(str(s))
        for g in ai_data.get("gaps") or []:
            raw_parts.append(str(g))
    blob = " ".join(raw_parts)

    tech_tokens = re.findall(
        r"(?<![A-Za-z0-9_])(?:C\+\+|C#|\.NET|ROS\s?2?|Python|PyTorch|TensorFlow|Docker|Linux|Git(?:Hub|Lab)?|CMake|SLAM|LiDAR|Radar|"
        r"BEV(?:Fusion)?|ADAS|Path\s+Planning|Trajectory|Sensor\s*fusion|Computer\s+Vision|Deep\s+Learning|"
        r"Machine\s+Learning|OpenCV|CUDA|Simulink|MATLAB|CARLA|Gazebo|MuJoCo|Nav2|Foxglove|RAG|LLM|Agents?|"
        r"FastAPI|SQL|PostgreSQL|CI/CD|ISO\s?26262|AUTOSAR|Java|Azure|LangChain|REST[- ]?APIs?|Microservices?|"
        r"Vue\.js|TypeScript|VLM|Jetson|TensorRT|ONNX|Agentic|Tool\s+Calling|Prompt\s+Engineering|Vector\s+Search|"
        r"Objektdetektion|MLOps|LLMOps|MCP|"
        r"Control\s+Systems?|Regelungstechnik|Avionik|HIL|SIL|MIL|Testautomation|Embedded[- ]Linux)(?![A-Za-z0-9_])",
        blob,
        flags=re.IGNORECASE,
    )

    counts: dict[str, tuple[str, int]] = {}
    for tok in tech_tokens:
        norm = re.sub(r"\s+", " ", tok.strip()).lower()
        orig, c = counts.get(norm, (tok.strip(), 0))
        counts[norm] = (orig, c + 1)

    ranked = sorted(counts.values(), key=lambda item: item[1], reverse=True)
    return [orig for orig, _ in ranked[:max_keywords]]


def verify_pdf_quality(
    pdf_path: Path,
    doc_type: str = "cv",
    job: dict[str, Any] | None = None,
    expected_email: str | None = None,
) -> dict[str, Any]:
    """Führt ATS-, Workday-Datums-, Ligatur-/Wortklebungs-, Seiten- und Keyword-Prüfung auf einem PDF aus."""
    stats = extract_pdf_text_and_stats(pdf_path)
    page_count = stats["page_count"]
    full_text = stats["full_text"]
    char_count = stats["char_count"]
    dehyphenated_text = re.sub(r"-\s*\n\s*", "", full_text)
    text_lower = dehyphenated_text.lower()

    personal: dict[str, Any] = {}
    try:
        personal = load_master_profile().get("personal", {})
    except Exception:
        personal = {}

    if expected_email is None:
        expected_email = personal.get("email", "alex.muster@example.com")
    full_name = str(personal.get("name") or "Alex Muster").strip().lower()
    name_parts = [p for p in full_name.split() if len(p) >= 2] or ["alex", "muster"]

    issues: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    # 1. Seitenanzahl (1 Seite DIN A4 ist Ziel)
    page_ok = page_count == 1
    checks.append({
        "id": "page_count",
        "label": f"Seitenanzahl: {page_count} Seite(n)",
        "ok": page_ok,
    })
    if page_count > 1:
        issues.append({
            "severity": "error" if doc_type == "cv" else "warn",
            "message": f"Dokument läuft auf {page_count} Seiten über (Ziel: 1 DIN-A4-Seite).",
        })
    elif page_count == 0:
        issues.append({
            "severity": "error",
            "message": "PDF konnte nicht gelesen werden (0 Seiten).",
        })

    # 2. Text-Layer, Ligaturen, Doppel-Header & Kaputte Glyphen
    has_replacement = "\ufffd" in full_text
    has_ligatures = any(0xFB00 <= ord(ch) <= 0xFB04 for ch in full_text)
    has_dup_header = ("lebenslauflebenslauf" in text_lower) or ("cvcv" in text_lower)
    collapsed_words = [
        w for w in dehyphenated_text.split()
        if len(w) >= 38 and "http" not in w.lower() and "@" not in w and "/" not in w and "-" not in w
    ]
    text_layer_ok = char_count >= 200 and not has_replacement and not collapsed_words and not has_dup_header
    checks.append({
        "id": "text_layer",
        "label": "ATS Text-Layer sauber",
        "ok": text_layer_ok and not has_ligatures,
    })
    if has_replacement:
        issues.append({
            "severity": "error",
            "message": "PDF enthält kaputte Unicode-Glyphen (U+FFFD) im Text-Layer.",
        })
    elif char_count < 200:
        issues.append({
            "severity": "warn",
            "message": f"Sehr wenig Text im PDF erkannt ({char_count} Zeichen).",
        })
    if has_dup_header:
        issues.append({
            "severity": "error",
            "message": "Doppelter Header-Text ('LebenslaufLebenslauf' / 'CVCV') im Text-Layer erkannt.",
        })
    if collapsed_words:
        issues.append({
            "severity": "warn",
            "message": f"ATS-Wortverklebung erkannt (z. B. '{collapsed_words[0][:30]}…').",
        })
    if has_ligatures:
        issues.append({
            "severity": "warn",
            "message": "Unicode-Ligaturen (fi/fl/ffi) im Text-Layer erkannt.",
        })

    # 3. Kontaktdaten im Text-Layer
    email_clean = (expected_email or "").strip().lower()
    email_local = email_clean.split("@")[0] if "@" in email_clean else email_clean
    email_ok = bool(email_clean and (email_clean in text_lower or (email_local and email_local in text_lower)))
    name_ok = all(part in text_lower for part in name_parts)
    contact_ok = (email_ok and name_ok) if doc_type in ("cv", "letter") else name_ok
    checks.append({
        "id": "contact",
        "label": "Kontaktdaten ATS-extrahierbar",
        "ok": contact_ok,
    })
    if not contact_ok:
        issues.append({
            "severity": "warn",
            "message": "Name oder E-Mail-Adresse im PDF-Text-Layer nicht eindeutig auffindbar.",
        })

    # 4. Workday ASCII-Datums-Check (keine En-Dashes in Datumsbereichen)
    endash_matches = DATE_ENDASH_RE.findall(full_text)
    dates_ok = len(endash_matches) == 0
    checks.append({
        "id": "ascii_dates",
        "label": "Workday Datums-Format (ASCII '-')",
        "ok": dates_ok,
    })
    if not dates_ok:
        issues.append({
            "severity": "warn",
            "message": f"En-Dash (–) in Datum gefunden ({endash_matches[0]}): Workday-ATS kann dieses Datum verschlucken!",
        })

    # 5. Keyword-Coverage gegen Ziel-Stelle (auch direkt vor Satzpunkt '.' erkennen!)
    keywords = extract_job_keywords(job)
    matched_keywords: list[str] = []
    missing_keywords: list[str] = []
    for kw in keywords:
        kw_low = kw.lower()
        kw_flex = re.escape(kw_low).replace(r"\ ", r"[\s-]+")
        if kw_low.startswith("."):
            pat = rf"(?<![A-Za-z0-9+#]){kw_flex}(?![A-Za-z0-9+#])"
        else:
            pat = rf"(?<![A-Za-z0-9+#]){kw_flex}(?![A-Za-z0-9+#])"
        if re.search(pat, text_lower):
            matched_keywords.append(kw)
        else:
            missing_keywords.append(kw)

    coverage_pct = round((len(matched_keywords) / len(keywords)) * 100) if keywords else None

    status = "ok"
    if any(i["severity"] == "error" for i in issues):
        status = "error"
    elif issues:
        status = "warn"

    return {
        "status": status,
        "page_count": page_count,
        "char_count": char_count,
        "checks": checks,
        "issues": issues,
        "keyword_coverage": {
            "coverage_pct": coverage_pct,
            "matched": matched_keywords[:12],
            "missing": missing_keywords[:8],
        } if keywords else None,
    }


def _detect_body_lang(text: str) -> str:
    low = f" {text.lower()} "
    de_hits = len(re.findall(r"\b(und|der|die|das|mit|während|meiner|für|entwicklung|erfahrung|position|gespräch|freue|bereich|sowie|ich|mein)\b", low))
    en_hits = len(re.findall(r"\b(and|the|with|during|my|in|for|software|development|experience|position|regards|would|happy|team|this)\b", low))
    if en_hits > de_hits + 2:
        return "en"
    if de_hits > en_hits + 2:
        return "de"
    return "unknown"


_SAFE_PAST_EMPLOYERS = {"example robotics", "sample ai systems", "technische universität musterstadt"}
_SEED_KNOWN_COMPANIES = [
    "ARX Robotics", "Helsing", "Quantum Systems", "Quantum-Systems", "NEURA Robotics",
    "MBDA Deutschland", "MBDA", "Airbus Defence and Space", "Airbus", "Siemens",
]


def run_letter_preflight(
    letter_data: dict[str, Any],
    job_row: dict[str, Any] | None = None,
    con: sqlite3.Connection | None = None,
    pdf_pages: int | None = None,
) -> dict[str, Any]:
    """Pre-Flight Absende-Gate für Anschreiben & Export (Fremdfirmen-Leaks, Sprachmix, kaputte Refs, Platzhalter)."""
    blockers: list[str] = []
    warnings: list[str] = []

    company = str(letter_data.get("company") or (job_row.get("company") if job_row else "") or "").strip()
    subject = str(letter_data.get("subject") or "").strip()
    body = str(letter_data.get("body_text") or "").strip()
    zip_city = str(letter_data.get("zip_city") or "").strip()
    job_ref = str(letter_data.get("job_ref") or "").strip()
    date_str = str(letter_data.get("date_str") or "").strip()
    lang = "en" if letter_data.get("lang") == "en" else "de"
    combined = f"{subject}\n{body}"
    combined_low = combined.lower()

    # 1. Seitenanzahl
    if pdf_pages is not None and pdf_pages > 1:
        blockers.append(f"Anschreiben hat {pdf_pages} Seiten statt 1 Seite — bitte Text etwas kürzen.")

    # 2. Platzhalter / Mindestlänge
    if not body:
        blockers.append("Brieftext ist noch leer.")
    elif len(body) < 120:
        blockers.append(f"Brieftext ist verdächtig kurz ({len(body)} Zeichen) — Test- oder Entwurfsrest?")
    if re.search(r"Neuer Absatz|\{\{[A-Z_]+\}\}|\[FIRMA\]|\[COMPANY\]|TODO|FIXME|lorem ipsum", combined, re.I):
        blockers.append("Unersetzter Platzhalter ('Neuer Absatz …' / '{{...}}' / TODO) im Text gefunden.")

    # 3. Kaputte Referenznummern oder Scanner-Hinweise
    ref_probe = f"{job_ref} {subject}"
    if re.search(r"\b(?:erence|uirements?|uired|erral)\b", ref_probe, re.I) or (
        job_ref and not any(ch.isdigit() for ch in job_ref)
    ):
        blockers.append(f"Kaputte Referenznummer erkannt ('{job_ref or subject}') — bitte korrigieren oder entfernen.")
    if re.search(r"Standort prüfen\s*:", zip_city, re.I):
        warnings.append(f"Interne Scanner-Notiz im Adressfeld: '{zip_city}' bitte durch echten Ort ersetzen.")

    # 4. Fremdfirmennamen-Leak-Erkennung
    target_tokens = {t for t in re.split(r"[\s\-_.,/()]+", company.lower()) if len(t) >= 3}
    target_tokens -= {"gmbh", "inc", "ltd", "corp", "group", "deutschland", "germany", "technologies", "systems"}
    candidates = list(_SEED_KNOWN_COMPANIES)
    if con is not None:
        try:
            for r in con.execute("SELECT DISTINCT company FROM cover_letters WHERE COALESCE(company,'') != ''"):
                candidates.append(str(r[0]))
        except Exception:
            pass

    leaked_found: set[str] = set()
    for cand in candidates:
        c_clean = cand.strip()
        c_low = c_clean.lower()
        if not c_clean or len(c_clean) < 4 or c_low in _SAFE_PAST_EMPLOYERS:
            continue
        if c_low in company.lower() or company.lower() in c_low:
            continue
        # Skip generic single words like 'siemens' if target is 'Siemens Energy'
        cand_words = {w for w in re.split(r"[\s\-_.,/()]+", c_low) if len(w) >= 3} - {"gmbh", "inc", "ag", "se"}
        if cand_words and cand_words & target_tokens:
            continue
        # Match full name or distinctive single-brand token (except common English 'reply' unless preceded by 'bei '/'at ')
        if c_low == "reply":
            if re.search(r"\b(?:bei|at|für|for)\s+reply\b", combined_low):
                leaked_found.add("Reply")
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(c_low)}(?![A-Za-z0-9])", combined_low):
            leaked_found.add(c_clean)

    if leaked_found:
        blockers.append(
            f"Fremder Firmenname im Anschreiben für '{company or 'Zielstelle'}' gefunden: {', '.join(sorted(leaked_found))}!"
        )

    # 5. Sprachkonsistenz
    body_lang = _detect_body_lang(body)
    if body_lang in ("de", "en") and body_lang != lang:
        blockers.append(
            f"Sprachmix: Brief-Einstellung ist '{lang.upper()}', aber der Text ist auf '{body_lang.upper()}' verfasst."
        )
    if lang == "en" and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", date_str):
        warnings.append(f"Deutsches Datumsformat '{date_str}' in englischem Brief — wird beim Rendern automatisch auf EN formatiert.")

    # 6. Veraltete Daten / Verfügbarkeiten
    if re.search(r"\b(?:mitte|ende|anfang)?\s*(?:november|mid-november|early november)\s+2026\b", body_low := body.lower()):
        warnings.append("Verfügbarkeitsdatum ('Mitte November 2026') liegt bereits in der Vergangenheit — bitte aktualisieren.")
    if date_str:
        parsed_d: datetime | None = None
        m_de = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", date_str)
        if m_de:
            try:
                parsed_d = datetime(int(m_de.group(3)), int(m_de.group(2)), int(m_de.group(1)))
            except ValueError:
                parsed_d = None
        else:
            for fmt in ("%B %d, %Y", "%Y-%m-%d"):
                try:
                    parsed_d = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    pass
        if parsed_d and (datetime.now() - parsed_d).days > 7:
            warnings.append(f"Briefdatum '{date_str}' ist älter als 7 Tage.")

    return {
        "ok": len(blockers) == 0,
        "blockers": blockers,
        "warnings": warnings,
    }
