"""Deterministic DIN Cover Letter PDF Builder (Zero LLM bloat, clean separate fields)."""
from __future__ import annotations

from datetime import datetime
import json
import re
import sqlite3
from typing import Any

from backend.config import ASSETS_DIR, PDF_LETTER_DIR, TEMPLATES_DIR, load_master_profile, now_iso, today_de
from backend.documents.latex_common import (
    LaTeXCompileError,
    PageLimitError,
    compile_tex_to_pdf,
    escape_latex,
    slugify_filename,
)

LETTER_TEMPLATE_PATH = TEMPLATES_DIR / "letter_template.tex"
SIGNATURE_PATH = ASSETS_DIR / "signature.png"

_EN_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def today_for_lang(lang: str = "de") -> str:
    now = datetime.now()
    if lang == "en":
        return f"{_EN_MONTHS[now.month]} {now.day}, {now.year}"
    return f"{now.day:02d}.{now.month:02d}.{now.year}"


def format_date_for_lang(raw_date: str, lang: str = "de") -> str:
    s = str(raw_date or "").strip()
    if not s:
        return today_for_lang(lang)
    m_de = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m_de:
        d, mo, y = int(m_de.group(1)), int(m_de.group(2)), int(m_de.group(3))
        if lang == "en" and 1 <= mo <= 12:
            return f"{_EN_MONTHS[mo]} {d}, {y}"
        return f"{d:02d}.{mo:02d}.{y}"
    m_en = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if m_en and lang == "de":
        mon_name = m_en.group(1).lower()
        for idx in range(1, 13):
            if _EN_MONTHS[idx].lower() == mon_name:
                return f"{int(m_en.group(2)):02d}.{idx:02d}.{m_en.group(3)}"
    return s


def _normalize_closing(closing: str, lang: str) -> str:
    c = str(closing or "").strip()
    if not c:
        defaults = load_master_profile().get("letter_defaults", {})
        c = defaults.get("closing_en" if lang == "en" else "closing_de", "")
    if lang == "en":
        if c and not c.endswith((",", ".", "!")):
            c += ","
    else:
        c = c.rstrip(",")
    return c


def render_letter_tex(data: dict[str, Any], personal: dict[str, Any] | None = None) -> str:
    if personal is None:
        personal = load_master_profile()["personal"]

    tpl = LETTER_TEMPLATE_PATH.read_text(encoding="utf-8")
    lang_code = "en" if data.get("lang") == "en" else "de"
    lang = "english" if lang_code == "en" else "ngerman"
    defaults = load_master_profile().get("letter_defaults", {})

    # Sender block (top right)
    city_line = f"{personal.get('zip', '')} {personal.get('city', '')}".strip()
    sender_lines = [
        personal.get("name", "Alex Muster"),
        personal.get("street", ""),
        city_line,
    ]
    if lang_code == "en":
        sender_lines.append("Germany")
    sender_lines.extend([
        str(personal.get("phone", "") or ""),
        personal.get("email", ""),
    ])
    sender_block = " \\\\\n".join(escape_latex(line) for line in sender_lines if line)

    # Recipient block (DIN 5008: company, contact person, street, zip/city — Job-ID belongs in Subject!)
    recipient_lines: list[str] = []
    for raw_line in (
        data.get("company", ""),
        data.get("contact_person", ""),
        data.get("street", ""),
        data.get("zip_city", ""),
    ):
        val = str(raw_line or "").strip()
        if val and not re.match(r"^Standort prüfen\s*:", val, re.I):
            recipient_lines.append(val)
    recipient_block = " \\\\\n".join(escape_latex(line) for line in recipient_lines) if recipient_lines else "~"

    # Place & Date line
    place = str(data.get("place") or personal.get("city") or "München").strip()
    date_str = format_date_for_lang(str(data.get("date_str") or ""), lang_code)
    date_line = escape_latex(f"{place}, {date_str}" if place else date_str)

    # Subject, Salutation, Body paragraphs (preserve manual text verbatim!), Closing
    subject = escape_latex(str(data.get("subject") or "").strip())
    raw_salutation = str(data.get("salutation") or "").strip() or (
        defaults.get("salutation_en", "Dear Hiring Team,") if lang_code == "en" else defaults.get("salutation_de", "Sehr geehrte Damen und Herren,")
    )
    salutation = escape_latex(raw_salutation)

    raw_body = str(data.get("body_text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw_body) if p.strip()]
    body_tex = "\n\n".join(escape_latex(p, keep_linebreaks=True) for p in paragraphs)

    closing = escape_latex(_normalize_closing(str(data.get("closing") or ""), lang_code))

    include_sig = bool(data.get("include_signature", True))
    try:
        width_cm = float(data.get("signature_width_cm") or 3.7)
    except (ValueError, TypeError):
        width_cm = 3.7
    if not 2.0 <= width_cm <= 5.5:
        width_cm = 3.7

    if include_sig and SIGNATURE_PATH.exists():
        sig_posix = str(SIGNATURE_PATH).replace("\\", "/")
        signature_tex = (
            f"\\par\\vspace{{0.1cm}}\\includegraphics[width={width_cm}cm,height=1.2cm,keepaspectratio]"
            f"{{\\detokenize{{{sig_posix}}}}}\\par"
        )
    else:
        signature_tex = r"\vspace{0.7cm}"

    replacements = {
        "@@LANG@@": lang,
        "@@SENDER_BLOCK@@": sender_block,
        "@@RECIPIENT_BLOCK@@": recipient_block,
        "@@DATE_LINE@@": date_line,
        "@@SUBJECT@@": subject,
        "@@SALUTATION@@": salutation,
        "@@BODY@@": body_tex,
        "@@CLOSING@@": closing,
        "@@SIGNATURE@@": signature_tex,
        "@@SENDER_NAME@@": escape_latex(personal.get("name", "Alex Muster")),
    }
    token_pattern = re.compile("|".join(re.escape(k) for k in replacements))
    return token_pattern.sub(lambda m: replacements[m.group(0)], tpl)


def build_letter_pdf(
    data: dict[str, Any],
    letter_id: int | None = None,
    strict_single_page: bool = True,
) -> dict[str, Any]:
    tex_source = render_letter_tex(data)
    comp_slug = slugify_filename(data.get("company") or "Anschreiben")
    prefix = f"letter_{letter_id}_" if letter_id else "letter_"
    target_pdf = PDF_LETTER_DIR / f"{prefix}{comp_slug}.pdf"

    page_warning = ""
    try:
        res = compile_tex_to_pdf(tex_source, target_pdf, passes=1, enforce_single_page=strict_single_page)
        pages = res["pages"]
        if pages != 1:
            page_warning = f"Das Anschreiben hat {pages} Seiten (Ziel: exakt 1 A4-Seite). Bitte Inhalt leicht kürzen."
    except PageLimitError as exc:
        if strict_single_page:
            raise
        pages = exc.pages
        page_warning = str(exc)

    return {
        "pdf_path": str(target_pdf),
        "tex_path": str(target_pdf.with_suffix(".tex")),
        "pages": pages,
        "warning": page_warning,
    }


def save_letter_draft_record(
    con: sqlite3.Connection,
    payload: dict[str, Any],
    letter_id: int | None = None,
) -> dict[str, Any]:
    stamp = now_iso()
    defaults = load_master_profile()["letter_defaults"]
    lang = "en" if payload.get("lang") == "en" else "de"

    try:
        sig_w = float(payload.get("signature_width_cm") or defaults.get("signature_width_cm", 3.7))
    except (ValueError, TypeError):
        sig_w = float(defaults.get("signature_width_cm", 3.7))

    record = {
        "job_id": payload.get("job_id"),
        "name": str(payload.get("name") or payload.get("company") or "Anschreiben").strip(),
        "lang": lang,
        "company": str(payload.get("company") or "").strip(),
        "contact_person": str(payload.get("contact_person") or "").strip(),
        "street": str(payload.get("street") or "").strip(),
        "zip_city": str(payload.get("zip_city") or "").strip(),
        "job_ref": str(payload.get("job_ref") or "").strip(),
        "place": str(payload.get("place") or defaults.get("place", "Ingolstadt")).strip(),
        "date_str": format_date_for_lang(str(payload.get("date_str") or ""), lang),
        "subject": str(payload.get("subject") or "").strip(),
        "salutation": str(
            payload.get("salutation")
            or (defaults["salutation_en"] if lang == "en" else defaults["salutation_de"])
        ).strip(),
        "body_text": str(payload.get("body_text") or "").strip(),
        "closing": _normalize_closing(
            str(payload.get("closing") or (defaults["closing_en"] if lang == "en" else defaults["closing_de"])),
            lang,
        ),
        "include_signature": 1 if payload.get("include_signature", True) else 0,
        "signature_width_cm": max(2.0, min(5.5, sig_w)),
    }

    raw_addr_source = payload.get("address_source")
    addr_source_str = (
        json.dumps(raw_addr_source, ensure_ascii=False)
        if isinstance(raw_addr_source, (dict, list))
        else str(raw_addr_source or "")
    )

    with con:
        # If no explicit letter_id but job_id is provided, find any existing letter for this job
        if not letter_id and record["job_id"]:
            existing_row = con.execute(
                "SELECT id, pdf_path FROM cover_letters WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1",
                (record["job_id"],),
            ).fetchone()
            if existing_row:
                letter_id = existing_row["id"]
                existing_pdf = existing_row["pdf_path"]
            else:
                existing_pdf = ""
        elif letter_id:
            existing_row = con.execute(
                "SELECT pdf_path FROM cover_letters WHERE id = ?",
                (letter_id,),
            ).fetchone()
            if not existing_row:
                raise ValueError(f"Anschreiben #{letter_id} wurde nicht gefunden.")
            existing_pdf = existing_row["pdf_path"] or ""
        else:
            existing_pdf = ""

        if letter_id:
            con.execute(
                """
                UPDATE cover_letters SET
                  job_id=?, name=?, lang=?, company=?, contact_person=?, street=?, zip_city=?,
                  job_ref=?, place=?, date_str=?, subject=?, salutation=?, body_text=?, closing=?,
                  include_signature=?, signature_width_cm=?, address_source=?, updated_at=?
                WHERE id=?
                """,
                (
                    record["job_id"], record["name"], record["lang"], record["company"],
                    record["contact_person"], record["street"], record["zip_city"], record["job_ref"],
                    record["place"], record["date_str"], record["subject"], record["salutation"],
                    record["body_text"], record["closing"], record["include_signature"],
                    record["signature_width_cm"], addr_source_str, stamp, letter_id,
                ),
            )
        else:
            cur = con.execute(
                """
                INSERT INTO cover_letters(
                  job_id, name, lang, company, contact_person, street, zip_city, job_ref,
                  place, date_str, subject, salutation, body_text, closing,
                  include_signature, signature_width_cm, address_source, pdf_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["job_id"], record["name"], record["lang"], record["company"],
                    record["contact_person"], record["street"], record["zip_city"], record["job_ref"],
                    record["place"], record["date_str"], record["subject"], record["salutation"],
                    record["body_text"], record["closing"], record["include_signature"],
                    record["signature_width_cm"], addr_source_str, "", stamp, stamp,
                ),
            )
            letter_id = cur.lastrowid

    return {
        "letter_id": letter_id,
        "record": {
            **record,
            "id": letter_id,
            "pdf_path": existing_pdf,
            "address_source": raw_addr_source,
            "updated_at": stamp,
        },
    }


def save_and_compile_letter(
    con: sqlite3.Connection,
    payload: dict[str, Any],
    letter_id: int | None = None,
    strict_single_page: bool = False,
) -> dict[str, Any]:
    draft_res = save_letter_draft_record(con, payload, letter_id=letter_id)
    letter_id = draft_res["letter_id"]
    record = draft_res["record"]

    built = build_letter_pdf(record, letter_id=letter_id, strict_single_page=strict_single_page)
    stamp = now_iso()
    with con:
        con.execute("UPDATE cover_letters SET pdf_path=?, updated_at=? WHERE id=?", (built["pdf_path"], stamp, letter_id))

    return {
        "letter_id": letter_id,
        "pdf_path": built["pdf_path"],
        "pages": built["pages"],
        "warning": built["warning"],
        "record": {**record, "id": letter_id, "pdf_path": built["pdf_path"], "updated_at": stamp},
    }
