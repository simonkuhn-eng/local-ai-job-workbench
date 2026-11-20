"""Style Guard & Sanitizer for Human, Authentic Non-AI Engineering Tone.

Ensures that CVs and Cover Letters:
1. Maintain an authentic human engineering writing style (factual, precise, sober German/English).
2. Never contain AI buzzwords or marketing fluff (e.g. 'leidenschaftlich', 'hochmodern', 'innovativ', 'cutting-edge').
3. Strictly enforce the prohibition of AI punctuation markers:
   - NO semicolons (';') -> converted to commas or periods.
   - NO thought dashes ('—', '–', ' - ', '--') -> converted to commas or periods,
     while strictly preserving valid technical compound hyphens (e.g. 'Python-basiert', 'ROS-2', 'Tool-Calling', '3D-Druck').
   - NO colon prefixes or AI category headers in bullets (e.g. 'Fokus: ', 'Backend: ', 'Aufgaben: ').
   - NO inline colons in bullet points.
"""
from __future__ import annotations

import copy
import re
from typing import Any

# AI hype words avoided in technical CVs and cover letters
BANNED_HYPE_WORDS_DE = [
    "leidenschaftlich",
    "hochmodern",
    "revolutionär",
    "revolutioniert",
    "innovativ",
    "innovative",
    "innovativen",
    "innovativer",
    "bahnbrechend",
    "bahnbrechende",
    "nahtlos",
    "nahtlose",
    "nahtlosen",
    "herausragend",
    "herausragende",
    "herausragenden",
    "tiefgreifend",
    "tiefgreifende",
    "maßgeschneidert",
    "maßgeschneiderte",
    "maßgeschneiderten",
    "vorreiter",
    "synergien",
    "game-changer",
    "herzblut",
]

BANNED_HYPE_WORDS_EN = [
    "passionate",
    "cutting-edge",
    "state-of-the-art",
    "groundbreaking",
    "revolutionary",
    "revolutionized",
    "seamless",
    "seamlessly",
    "spearheaded",
    "leveraged",
    "leverage",
    "game-changing",
    "game-changer",
    "synergies",
]

ALL_BANNED_WORDS = set(BANNED_HYPE_WORDS_DE + BANNED_HYPE_WORDS_EN)

_DATE_RANGE_RE = re.compile(
    r"\b(?:\d{1,2}/)?(?:19|20)\d{2}\s*(?:--|[\-\u2013\u2014])\s*(?:(?:\d{1,2}/)?(?:19|20)\d{2}|heute|present|current|now)\b",
    re.IGNORECASE,
)


def _dash_replacement(match: re.Match[str], full_text: str) -> str:
    after = full_text[match.end():].lstrip()
    # If the clause after the dash starts with an uppercase sentence opener, use a period instead of comma
    if re.match(r"^(?:Bei|In|Im|Während|Zudem|Dabei|Danach|Hier|Dort|During|In|For|Additionally|At)\b", after):
        return ". "
    return ", "


def sanitize_human_text(text: str, is_bullet: bool = True) -> str:
    """Sanitizes text to strictly enforce a human, non-AI style without AI punctuation artifacts.

    Preserves compound technical terms (e.g. 'Python-basiert', 'ROS-2', 'Tool-Calling', 'CI/CD')
    and ATS date ranges (e.g. '04/2026 - 09/2026').
    """
    if not text:
        return ""

    s = str(text).strip()

    # 1. Remove leading AI bullet markers like "- ", "* ", "• "
    s = re.sub(r"^[\s\-\*\•\–\—]+\s*", "", s)

    # Protect date ranges temporarily so we never mangle "04/2026 - 09/2026"
    protected_dates: list[str] = []

    def _hold_date(m: re.Match[str]) -> str:
        protected_dates.append(m.group(0))
        return f"\x01DATE{len(protected_dates) - 1}\x01"

    s = _DATE_RANGE_RE.sub(_hold_date, s)

    # 2. Strip AI category prefixes at line/text start
    if is_bullet:
        s = re.sub(
            r"^(?:Fokus|Schwerpunkt|Aufgabe|Aufgaben|Ergebnis|Resultat|Ziel|Nutzen|Mehrwert|Methodik|Technologie|Stack|Impact|Outcome|Focus|Task|Result|Goal|Benefit|Methodology|Backend|Frontend|Verantwortung|Projekt|Erfolge|Rolle|Key [A-Za-z]+|Tech Stack|Core Focus|Highlights?)\s*:\s*",
            "",
            s,
            flags=re.IGNORECASE,
        )

    # 3. Remove semicolons
    s = re.sub(r";\s*$", ".", s)
    s = re.sub(r";\s*", ", ", s)

    # 4. Remove AI thought dashes (—, –, " - ", "--") safely
    dash_pat = re.compile(r"\s*[\u2014]\s*|\s+[\u2013]\s+|\s+--\s+|\s+-\s+")
    while True:
        m = dash_pat.search(s)
        if not m:
            break
        repl = _dash_replacement(m, s)
        s = s[:m.start()] + repl + s[m.end():]

    # Restore protected date ranges
    for idx, val in enumerate(protected_dates):
        s = s.replace(f"\x01DATE{idx}\x01", val)

    # 5. Normalize duplicate commas, periods and spaces
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r"\.\s*,+", ".", s)
    s = re.sub(r",\s*\.", ".", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = s.strip()

    # Ensure bullet ends with period if it's a sentence or multi-word clause
    if is_bullet and s and not s.endswith((".", "!", "?")):
        s += "."

    return s


def sanitize_cv_style(cv_data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Sanitizes all text in cv_data and returns (cleaned_cv_data, changes_made)."""
    cleaned = copy.deepcopy(cv_data)
    changes: list[str] = []

    # Profile
    if isinstance(cleaned.get("profile"), dict):
        for lang_k in ("de", "en"):
            orig = cleaned["profile"].get(lang_k, "")
            if orig:
                fixed = sanitize_human_text(orig, is_bullet=False)
                if fixed != orig:
                    changes.append(f"Profil ({lang_k}) bereinigt: AI-Zeichen entfernt.")
                    cleaned["profile"][lang_k] = fixed

    # Experience bullets (preserve structural "--" role/degree separators)
    for exp in cleaned.get("experience", []):
        if not isinstance(exp, dict):
            continue
        for t_k in ("title_de", "title_en"):
            orig_t = exp.get(t_k, "")
            if orig_t:
                exp[t_k] = re.sub(r";", ",", str(orig_t))

        for b_k in ("bullets_de", "bullets_en"):
            bullets = exp.get(b_k) or []
            new_bullets: list[str] = []
            for b in bullets:
                orig_b = str(b)
                if not orig_b.strip():
                    continue
                fixed_b = sanitize_human_text(orig_b, is_bullet=True)
                if fixed_b != orig_b:
                    changes.append(f"Erfahrung [{exp.get('company', '')}] Bullet bereinigt.")
                new_bullets.append(fixed_b)
            exp[b_k] = new_bullets

    # Education bullets
    for edu in cleaned.get("education", []):
        if not isinstance(edu, dict):
            continue
        for deg_k in ("degree_de", "degree_en"):
            orig_deg = edu.get(deg_k, "")
            if orig_deg:
                edu[deg_k] = re.sub(r";", ",", str(orig_deg))

        for b_k in ("bullets_de", "bullets_en"):
            bullets = edu.get(b_k) or []
            new_edu_bullets: list[Any] = []
            for b in bullets:
                if isinstance(b, dict):
                    lbl = str(b.get("label", "")).strip().rstrip(":")
                    orig_txt = str(b.get("text", ""))
                    if not orig_txt.strip() and not lbl:
                        continue
                    is_grade = bool(re.search(r"note|grade|gpa", lbl, re.I)) or bool(
                        re.fullmatch(r"\d+(?:[.,]\d+)?\.?(?:\s*\([^)]+\))?", orig_txt.strip())
                    )
                    txt = sanitize_human_text(orig_txt, is_bullet=not is_grade)
                    if is_grade:
                        txt = txt.rstrip(".")
                    if txt != orig_txt or lbl != b.get("label"):
                        changes.append(f"Ausbildung [{edu.get('institution', '')}] Detail bereinigt.")
                    new_edu_bullets.append({"label": lbl, "text": txt})
                else:
                    orig_b = str(b)
                    if not orig_b.strip():
                        continue
                    fixed_b = sanitize_human_text(orig_b, is_bullet=True)
                    if fixed_b != orig_b:
                        changes.append(f"Ausbildung [{edu.get('institution', '')}] Bullet bereinigt.")
                    new_edu_bullets.append(fixed_b)
            edu[b_k] = new_edu_bullets

    return cleaned, changes


def sanitize_letter_style(body_text: str) -> tuple[str, list[str]]:
    """Sanitizes cover letter body text paragraphs."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body_text or "") if p.strip()]
    cleaned_paras: list[str] = []
    changes: list[str] = []

    for idx, p in enumerate(paragraphs):
        fixed = sanitize_human_text(p, is_bullet=False)
        if fixed != p:
            changes.append(f"Absatz {idx + 1} bereinigt: AI-Zeichen entfernt.")
        cleaned_paras.append(fixed)

    return "\n\n".join(cleaned_paras), changes


def audit_text_for_ai_markers(text: str) -> list[str]:
    """Audits text and returns detailed warnings if AI markers or forbidden buzzwords are found."""
    warnings: list[str] = []
    if not text:
        return warnings

    stripped_dates = _DATE_RANGE_RE.sub("", text)

    # 1. Punctuation checks
    if ";" in stripped_dates:
        warnings.append("Semikolon (;) gefunden: Unnatürliches KI-Zeichen, durch Komma oder Punkt ersetzen.")
    if "—" in stripped_dates or " – " in stripped_dates or " - " in stripped_dates or " -- " in stripped_dates:
        warnings.append("Gedankenstrich (—, –, ' - ' oder '--') gefunden: Durch Komma oder Satzpunkt ersetzen.")
    if re.search(
        r"^(?:Fokus|Schwerpunkt|Aufgabe|Aufgaben|Ergebnis|Resultat|Methodik|Technologie|Stack|Impact|Outcome|Focus|Task|Result|Backend|Frontend|Verantwortung|Rolle)\s*:",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    ):
        warnings.append("Doppelpunkt-Präfix ('Fokus:', 'Aufgaben:') gefunden: Bulletpoints direkt mit Aktions-Nomen oder Verb starten.")

    # 2. Hype buzzwords (avoid duplicates with audit_generated_letter clichés)
    text_lower = text.lower()
    for word in ALL_BANNED_WORDS:
        if word in ("synergies", "synergien"):
            continue
        if re.search(rf"\b{re.escape(word)}\b", text_lower):
            warnings.append(f"KI-Hype-Wort '{word}' erkannt: Durch sachliche, ingenieurmäßige Beschreibung ersetzen.")

    return warnings
