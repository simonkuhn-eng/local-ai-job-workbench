"""Sanitizer und Prompt-Injection-Detektor für externe Stellenausschreibungen.

Schützt KI-Prompts vor versteckten Steuerzeichen (Zero-Width, Bidi-Overrides,
Unicode Tags) sowie Prompt-Injection-Versuchen in externen Stellentexten.
"""
from __future__ import annotations

import re
from typing import Any

# Unsichtbare Unicode-Zeichen, Bidi-Steuerung und Unicode-Tags (U+E0000 - U+E007F)
INVISIBLE_CHARS_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\U000e0000-\U000e007f]"
)

# Verdächtige Prompt-Injection-Muster in Job-Beschreibungen
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above|system)\s+(?:instructions?|prompts?|rules?)\b", re.I)),
    ("ignore_instructions_de", re.compile(r"\b(?:ignoriere|missachte|vergiss)\s+(?:alle\s+)?(?:vorherigen|bisherigen|obigen|system)?\s*(?:anweisungen|instruktionen|regeln|prompts?)\b", re.I)),
    ("system_override", re.compile(r"\b(?:override\s+system\s+prompt|bypass\s+(?:the\s+)?system\s+prompt|follow\s+(?:these\s+)?new\s+instructions|developer\s+mode|jailbreak)\b", re.I)),
    ("hide_from_user", re.compile(r"\b(?:do\s+not|don'?t|never|nicht|niemals)\s+(?:mention|tell|reveal|inform|erwähnen|verraten|sagen)\b[^\n.]{0,90}\b(?:to\s+the\s+(?:user|candidate|applicant)|(?:dem|an\s+den)\s+(?:nutzer|bewerber|kandidat))\b", re.I)),
    ("score_manipulation", re.compile(r"\b(?:rate\s+this\s+candidate|set\s+overall_score\s+to|always\s+return\s+100|immer\s+100\s*punkte|bewerte\s+mit\s+100)\b", re.I)),
    ("markup_breakout", re.compile(r"</?(?:system|assistant|tool_call|function_call|instructions)\b[^>]*>", re.I)),
]


def sanitize_job_posting(text: str | None) -> dict[str, Any]:
    """Bereinigt Stellenbeschreibungen von unsichtbaren Zeichen und entschärft Prompt-Injections."""
    if not text:
        return {"clean_text": "", "verdict": "clean", "findings": []}

    findings: list[str] = []
    raw = str(text)

    # 1. Unsichtbare Zeichen erkennen und entfernen
    invisible_matches = INVISIBLE_CHARS_RE.findall(raw)
    cleaned = INVISIBLE_CHARS_RE.sub("", raw)
    if invisible_matches:
        findings.append(f"{len(invisible_matches)} unsichtbare Unicode-Steuerzeichen entfernt")

    # 2. Bekannte Injection-Patterns entschärfen
    for label, pattern in INJECTION_PATTERNS:
        if pattern.search(cleaned):
            findings.append(f"Verdächtiges Injection-Muster entschärft: {label}")
            cleaned = pattern.sub("[ENTFERNT: POTENZIELL UNSICHERER STELLENTEXT]", cleaned)

    verdict = "suspicious" if findings else "clean"
    return {
        "clean_text": cleaned,
        "verdict": verdict,
        "findings": findings,
    }
