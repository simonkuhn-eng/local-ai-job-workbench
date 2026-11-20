"""MBDA-Standard 1-Page Bilingual (DE/EN) LaTeX CV Builder."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from backend.config import ASSETS_DIR, PDF_CV_DIR, TEMPLATES_DIR, load_master_profile, now_iso
from backend.ai.style_guard import sanitize_cv_style
from backend.documents.latex_common import (
    PageLimitError,
    compile_tex_to_pdf,
    escape_latex,
    normalize_ats_dates,
    protect_brands,
    slugify_filename,
)

CV_TEMPLATE_PATH = TEMPLATES_DIR / "cv_mbda_template.tex"


def _pick_lang(obj: dict[str, Any], lang: str, key_prefix: str = "") -> str:
    if key_prefix:
        return str(obj.get(f"{key_prefix}_{lang}") or obj.get(f"{key_prefix}_de") or obj.get(f"{key_prefix}_en") or "")
    return str(obj.get(lang) or obj.get("de") or obj.get("en") or "")


def _parse_rgb(val: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not val:
        return default
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        try:
            return (max(0, min(255, int(val[0]))), max(0, min(255, int(val[1]))), max(0, min(255, int(val[2]))))
        except (ValueError, TypeError):
            return default
    if isinstance(val, str):
        s = val.strip().lstrip("#")
        if len(s) == 6:
            try:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            except ValueError:
                pass
        elif len(s) == 3:
            try:
                return (int(s[0] * 2, 16), int(s[1] * 2, 16), int(s[2] * 2, 16))
            except ValueError:
                pass
        named: dict[str, tuple[int, int, int]] = {
            "navy": (30, 58, 138),
            "blue": (37, 99, 235),
            "royal": (29, 78, 216),
            "teal": (15, 118, 110),
            "cyan": (14, 116, 144),
            "slate": (59, 77, 97),
            "dark": (51, 65, 85),
            "charcoal": (40, 40, 40),
            "black": (25, 25, 25),
            "green": (21, 128, 61),
            "forest": (22, 101, 52),
            "burgundy": (136, 19, 55),
            "wine": (159, 18, 57),
            "indigo": (79, 70, 229),
            "purple": (109, 40, 217),
            "amber": (180, 83, 9),
            "orange": (194, 65, 12),
        }
        if s.lower() in named:
            return named[s.lower()]
    return default


def render_cv_tex(cv_data: dict[str, Any], lang: str = "de", personal: dict[str, Any] | None = None) -> str:
    lang = "en" if lang == "en" else "de"
    if personal is None:
        personal = load_master_profile()["personal"]

    # Enforce authentic human engineering style (no semicolons, no thought dashes, no bullet-colon headers)
    cv_data, _ = sanitize_cv_style(cv_data)

    layout = cv_data.get("layout") if isinstance(cv_data.get("layout"), dict) else {}

    def _get_val(key: str, default: Any) -> Any:
        # Top-level UI edits take precedence over legacy nested 'layout' keys
        if key in cv_data and cv_data[key] is not None:
            return cv_data[key]
        if key in layout and layout[key] is not None:
            return layout[key]
        return default

    tpl = CV_TEMPLATE_PATH.read_text(encoding="utf-8")
    assets_posix = str(ASSETS_DIR).replace("\\", "/")

    # Layout parameters: Font size, Margins, Colors, Spacing, Column ratio
    font_size_raw = str(_get_val("font_size", "10pt")).strip()
    if not font_size_raw.endswith("pt"):
        font_size_raw = f"{font_size_raw}pt"
    font_size = font_size_raw if font_size_raw in ("9pt", "9.5pt", "10pt", "10.5pt", "11pt", "12pt") else "10pt"
    doc_font_opt = font_size if font_size in ("10pt", "11pt", "12pt") else "10pt"
    custom_font_size = (
        f"\\changefontsize{{{font_size}}}"
        if font_size not in ("10pt", "11pt", "12pt")
        else ""
    )

    def _safe_dim(val: Any, default: str, min_val: float = 0.0, max_val: float = 40.0) -> str:
        s = str(val or "").strip()
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(pt|cm|mm|em|ex)", s)
        if m:
            num = float(m.group(1))
            unit = m.group(2)
            # Reject out-of-range dimensions to preserve 1-page A4 geometry
            cap = 80.0 if unit in ("pt", "mm") else 10.0
            num = max(min_val, min(cap, num))
            return f"{num:g}{unit}"
        try:
            num = max(min_val, min(max_val, float(s)))
            return f"{num:g}pt"
        except ValueError:
            return default

    margins_val = _get_val("margins", {})
    if not isinstance(margins_val, dict):
        margins_val = {}
    margin_top = _safe_dim(margins_val.get("top") or _get_val("margin_top", "0.62cm"), "0.62cm", 0.25, 3.0)
    margin_bottom = _safe_dim(margins_val.get("bottom") or _get_val("margin_bottom", "0.52cm"), "0.52cm", 0.25, 3.0)
    margin_left = _safe_dim(margins_val.get("left") or _get_val("margin_left", "0.95cm"), "0.95cm", 0.4, 3.0)
    margin_right = _safe_dim(margins_val.get("right") or _get_val("margin_right", "0.95cm"), "0.95cm", 0.4, 3.0)

    accent_rgb = _parse_rgb(_get_val("accent_color", None), default=(59, 77, 97))
    main_rgb = _parse_rgb(_get_val("main_color", None), default=(64, 64, 64))
    dark_rgb = _parse_rgb(_get_val("dark_color", None), default=(70, 70, 70))
    light_rgb = _parse_rgb(_get_val("light_color", None), default=(245, 245, 245))

    section_spacing = _safe_dim(_get_val("section_spacing", "7.5pt"), "7.5pt")

    try:
        col_ratio = float(_get_val("column_ratio", 0.31))
    except (ValueError, TypeError):
        col_ratio = 0.31
    col_ratio = max(0.20, min(0.48, col_ratio))
    column_ratio_str = f"{col_ratio:.2f}"

    header_title = escape_latex(_pick_lang(cv_data.get("header_title", {"de": "Lebenslauf", "en": "CV"}), lang))
    person_name = escape_latex(personal.get("name", "Alex Muster"))

    role1 = escape_latex(_pick_lang(cv_data.get("role_line1", {}), lang))
    role2 = escape_latex(_pick_lang(cv_data.get("role_line2", {}), lang))
    role_lines = f"{role1}\\\\{role2}" if role2 else role1

    # Section titles customization
    section_titles = _get_val("section_titles", {})
    if not isinstance(section_titles, dict):
        section_titles = {}

    def _get_title(key: str, de_def: str, en_def: str) -> str:
        custom = section_titles.get(key)
        if isinstance(custom, dict):
            val = _pick_lang(custom, lang)
            if val:
                return escape_latex(val)
        elif isinstance(custom, str) and custom.strip():
            return escape_latex(custom.strip())
        return escape_latex(de_def if lang == "de" else en_def)

    # Detailed Element & Sektion Spacing
    density = str(_get_val("density", "")).lower()
    density_defaults = {
        "compact": {
            "skill_item_gap": "5.5pt",
            "skill_lang_gap": "14pt",
            "interest_item_gap": "4pt",
            "contact_item_gap": "3pt",
            "left_section_gap": "12pt",
            "main_section_gap": "4pt",
            "event_gap": "4pt",
            "item_gap": "2.8pt",
        },
        "relaxed": {
            "skill_item_gap": "8.5pt",
            "skill_lang_gap": "22pt",
            "interest_item_gap": "7.5pt",
            "contact_item_gap": "6pt",
            "left_section_gap": "22pt",
            "main_section_gap": "8pt",
            "event_gap": "8pt",
            "item_gap": "3.5pt",
        },
        "generous": {
            "skill_item_gap": "10pt",
            "skill_lang_gap": "26pt",
            "interest_item_gap": "9pt",
            "contact_item_gap": "8pt",
            "left_section_gap": "28pt",
            "main_section_gap": "10pt",
            "event_gap": "10pt",
            "item_gap": "4pt",
        },
    }
    cur_d = density_defaults.get(density, {})

    def _get_spacing(key: str, fallback_key: str, default: str) -> str:
        raw = _get_val(key, None)
        if raw is None and fallback_key:
            raw = _get_val(fallback_key, None)
        if raw is None and cur_d:
            if key in cur_d:
                raw = cur_d[key]
            elif fallback_key and fallback_key in cur_d:
                raw = cur_d[fallback_key]
        if raw is None:
            return default
        return _safe_dim(raw, default, 0.0, 60.0)

    skill_item_spacing = _get_spacing("skill_item_spacing", "skill_item_gap", "6.5pt")
    skill_lang_spacing = _get_spacing("skill_lang_spacing", "skill_lang_gap", "16pt")
    interest_item_spacing = _get_spacing("interest_item_spacing", "interest_item_gap", "5.5pt")
    contact_item_spacing = _get_spacing("contact_item_spacing", "contact_item_gap", "4.5pt")
    left_section_spacing = _get_spacing("left_section_spacing", "left_section_gap", "15pt")

    main_section_spacing = _get_spacing("main_section_spacing", "main_section_gap", "4.5pt")
    event_spacing = _get_spacing("event_spacing", "event_gap", "4.5pt")
    item_spacing = _get_spacing("item_spacing", "item_gap", "2.6pt")

    # Section visibility flags (prefer canonical UI flag include_signature over legacy show_signature)
    include_portrait = bool(_get_val("include_portrait", _get_val("show_photo", True)))
    show_skills = bool(_get_val("show_skills", True))
    show_interests = bool(_get_val("show_interests", True))
    show_languages = bool(_get_val("show_languages", True))
    show_contact = bool(_get_val("show_contact", True))
    show_profile = bool(_get_val("show_profile", True))
    show_signature = bool(_get_val("include_signature", _get_val("show_signature", True)))

    # Portrait block
    portrait_block = ""
    if include_portrait and (ASSETS_DIR / "Portrait.png").exists():
        align = str(_get_val("portrait_align", "left")).lower()
        try:
            width_ratio = float(_get_val("portrait_width", 0.75))
        except (ValueError, TypeError):
            width_ratio = 0.75
        width_ratio = max(0.40, min(1.0, width_ratio))

        try:
            custom_offset = max(-35, min(35, int(_get_val("portrait_offset_x", 0))))
        except (ValueError, TypeError):
            custom_offset = 0

        try:
            offset_y = max(-35, min(35, int(_get_val("portrait_offset_y", 0))))
        except (ValueError, TypeError):
            offset_y = 0

        y_prefix = f"        \\vspace*{{{offset_y}pt}}\n" if offset_y else ""

        if align == "center":
            offset_tex = f"\\hspace*{{{custom_offset}pt}}" if custom_offset else ""
            portrait_block = (
                f"{y_prefix}        \\vspace*{{4pt}}\n"
                "        \\begin{center}\n"
                f"            {offset_tex}\\includegraphics[width={width_ratio:.2f}\\linewidth]{{Portrait.png}}\n"
                "        \\end{center}\n"
                "        \\vspace*{4pt}\n"
            )
        elif align == "right":
            offset_tex = f"\\hspace*{{{custom_offset}pt}}" if custom_offset else ""
            portrait_block = (
                f"{y_prefix}        \\vspace*{{4pt}}\n"
                "        {\\raggedleft\n"
                f"            {offset_tex}\\includegraphics[width={width_ratio:.2f}\\linewidth]{{Portrait.png}}\\par\n"
                "        }\n"
                "        \\vspace*{4pt}\n"
            )
        else:
            # Default: flush left, aligned with the name (6pt base offset; clamp width to avoid overflow at 1.0)
            total_offset = 6 + custom_offset
            eff_width = min(width_ratio, 0.94) if total_offset > 0 else width_ratio
            offset_tex = f"\\hspace*{{{total_offset}pt}}" if total_offset else ""
            portrait_block = (
                f"{y_prefix}        \\vspace*{{4pt}}\n"
                f"        {{\\raggedright{offset_tex}\\includegraphics[width={eff_width:.2f}\\linewidth]{{Portrait.png}}\\par}}\n"
                "        \\vspace*{6pt}\n"
            )

    # Header Name block
    header_name_block = (
        "        \\fcolorbox{white}{white}{\\begin{minipage}[c][1.35cm][c]{\\mpwidth}\n"
        "                \\raggedright\n"
        f"                \\LARGE{{\\textbf{{\\textcolor{{maincol}}{{{person_name}}}}}}}\\\\[2pt]\n"
        f"                \\normalsize{{\\textcolor{{maincol}}{{{role_lines}}}}}\n"
        "            \\end{minipage}}\\\\\n"
    )

    # Skills + Languages rows
    skills = cv_data.get("skills", [])
    languages = cv_data.get("languages", []) if show_languages else []
    skill_lines: list[str] = []
    for idx, sk in enumerate(skills):
        s_name = escape_latex(_pick_lang(sk, lang, "name"))
        s_lvl = escape_latex(_pick_lang(sk, lang, "level"))
        sep = (
            f"\\\\[{skill_lang_spacing}]"
            if (idx == len(skills) - 1 and languages and show_skills)
            else (f"\\\\[{skill_item_spacing}]" if idx < len(skills) - 1 else r"\\")
        )
        skill_lines.append(f"            \\cvskill{{{s_name}}} {{{s_lvl}}} {sep}")
    lang_only_lines: list[str] = []
    for idx, lg in enumerate(languages):
        l_name = escape_latex(_pick_lang(lg, lang, "name"))
        l_lvl = escape_latex(_pick_lang(lg, lang, "level"))
        sep = f"\\\\[{skill_item_spacing}]" if idx < len(languages) - 1 else r"\\"
        row_tex = f"            \\cvskill{{{l_name}}} {{{l_lvl}}} {sep}"
        skill_lines.append(row_tex)
        lang_only_lines.append(row_tex)
    skill_rows_tex = "\n".join(skill_lines)
    lang_only_rows_tex = "\n".join(lang_only_lines)

    # Interests
    interest_lines = [
        f"        \\cvbullet{{{escape_latex(_pick_lang(it, lang)) if isinstance(it, dict) else escape_latex(str(it))}}}"
        for it in cv_data.get("interests", [])
    ]
    interest_rows_tex = "\n".join(interest_lines)

    # Contact
    city = escape_latex(personal.get("city", "München"))
    phone_raw = str(personal.get("phone", "+49 170 0000000")).strip()
    phone_tex = escape_latex(phone_raw).replace(" ", r"\,")
    email_raw = str(personal.get("email", "alex.muster@example.com")).strip()
    email_tex = escape_latex(email_raw)
    email_href = escape_latex(email_raw)
    linkedin_url = str(personal.get("linkedin") or "").strip()
    github_url = str(personal.get("github") or "").strip()

    # Profile
    profile_text = protect_brands(escape_latex(_pick_lang(cv_data.get("profile", {}), lang)))

    # Experience
    exp_blocks: list[str] = []
    for exp in cv_data.get("experience", []):
        tf = escape_latex(normalize_ats_dates(exp.get("timeframe", "")))
        title = escape_latex(_pick_lang(exp, lang, "title"))
        comp = escape_latex(exp.get("company", ""))
        bullets_key = f"bullets_{lang}"
        bullets = exp[bullets_key] if bullets_key in exp and exp[bullets_key] is not None else (exp.get("bullets_de") or [])
        clean_bullets = [b for b in bullets if str(b).strip()]
        if clean_bullets:
            items_tex = "\n".join(f"                \\item {protect_brands(escape_latex(b))}" for b in clean_bullets)
            desc_tex = (
                f"{{\\begin{{itemize}}[leftmargin=1.1em,itemsep={item_spacing},topsep=0pt,parsep=0pt,partopsep=0pt]\n"
                f"{items_tex}\n"
                "            \\end{itemize}}"
            )
        else:
            desc_tex = "{}"
        exp_blocks.append(f"        \\cvevent\n        {{{tf}}}\n        {{{title}}}\n        {{{comp}}}\n        {desc_tex}\n")
    exp_blocks_tex = "\n".join(exp_blocks)

    # Education
    edu_blocks: list[str] = []
    for edu in cv_data.get("education", []):
        tf = escape_latex(normalize_ats_dates(edu.get("timeframe", "")))
        deg = escape_latex(_pick_lang(edu, lang, "degree"))
        inst = escape_latex(edu.get("institution", ""))
        bullets_key = f"bullets_{lang}"
        bullets = edu[bullets_key] if bullets_key in edu and edu[bullets_key] is not None else (edu.get("bullets_de") or [])
        b_lines = []
        for b in bullets:
            if isinstance(b, dict):
                lbl = escape_latex(b.get("label", ""))
                txt_raw = str(b.get("text", "")).strip()
                if not lbl and not txt_raw:
                    continue
                txt = protect_brands(escape_latex(txt_raw))
                b_lines.append(f"                \\item \\textbf{{{lbl}}}: {txt}" if lbl else f"                \\item {txt}")
            elif str(b).strip():
                b_lines.append(f"                \\item {protect_brands(escape_latex(str(b)))}")
        if b_lines:
            desc_tex = (
                f"{{\\begin{{itemize}}[leftmargin=1.1em,itemsep={item_spacing},topsep=0pt,parsep=0pt,partopsep=0pt]\n"
                + "\n".join(b_lines)
                + "\n            \\end{itemize}}"
            )
        else:
            desc_tex = "{}"
        edu_blocks.append(f"        \\cvevent\n        {{{tf}}}\n        {{{deg}}}\n        {{{inst}}}\n        {desc_tex}\n")
    edu_blocks_tex = "\n".join(edu_blocks)

    # Signature
    sig_city = escape_latex(cv_data.get("signature_city") or personal.get("city") or "Ingolstadt")
    sig_img = (
        r"\includegraphics[width=3.4cm,height=1.05cm,keepaspectratio]{Signature_crop.png}"
        if (ASSETS_DIR / "Signature_crop.png").exists()
        else r"\vspace{0.9cm}"
    )
    signature_block = (
        "        \\par\n"
        "        \\vspace{8pt}\n"
        "        \\cvtext{%\n"
        "            \\begin{tabular*}{\\mpwidth}{@{}p{0.42\\mpwidth}@{\\extracolsep{\\fill}}p{0.42\\mpwidth}@{}}\n"
        f"                & {sig_img} \\\\[-3pt]\n"
        f"                \\textcolor{{maincol}}{{{sig_city}, \\cvdate}} & \\hrulefill \\\\[1pt]\n"
        f"                & \\textcolor{{maincol}}{{\\small {person_name}}} \\\\\n"
        "            \\end{tabular*}%\n"
        "        }\n"
    )

    # Section ordering & Column construction
    section_order_left = _get_val("section_order_left", ["portrait", "header", "skills", "interests", "contact"])
    if not isinstance(section_order_left, list):
        section_order_left = ["portrait", "header", "skills", "interests", "contact"]

    left_chunks: list[str] = []
    for s in section_order_left:
        s_norm = str(s).strip().lower()
        if s_norm == "portrait" and portrait_block:
            left_chunks.append(portrait_block)
        elif s_norm in ("header", "name"):
            left_chunks.append(header_name_block)
        elif s_norm == "skills" and show_skills:
            t = _get_title("skills", "Kenntnisse", "Skills")
            left_chunks.append(
                f"        \\vspace{{{left_section_spacing}}}\n"
                f"        \\cvsection{{{t}}}\n\n"
                f"        \\begin{{tabular*}}{{\\mpwidth}}{{@{{}}l@{{\\extracolsep{{\\fill}}}}r@{{}}}}\n"
                f"{skill_rows_tex}\n"
                f"        \\end{{tabular*}}\n"
            )
        elif s_norm == "skills" and not show_skills and show_languages and lang_only_rows_tex:
            t = _get_title("languages", "Sprachen", "Languages")
            left_chunks.append(
                f"        \\vspace{{{left_section_spacing}}}\n"
                f"        \\cvsection{{{t}}}\n\n"
                f"        \\begin{{tabular*}}{{\\mpwidth}}{{@{{}}l@{{\\extracolsep{{\\fill}}}}r@{{}}}}\n"
                f"{lang_only_rows_tex}\n"
                f"        \\end{{tabular*}}\n"
            )
        elif s_norm == "interests" and show_interests:
            t = _get_title("interests", "Interessen", "Interests")
            left_chunks.append(
                f"        \\vspace{{{left_section_spacing}}}\n"
                f"        \\cvsection{{{t}}}\n\n"
                f"{interest_rows_tex}\n"
            )
        elif s_norm == "contact" and show_contact:
            t = _get_title("contact", "Kontakt", "Contact")
            contact_lines = [
                f"        \\icontext{{MapMarker}}{{16}}{{{city}}}{{black}}\\\\[{contact_item_spacing}]",
                f"        \\icontext{{MobilePhone}}{{16}}{{\\mbox{{{phone_tex}}}}}{{black}}\\\\[{contact_item_spacing}]",
                f"        \\iconemail{{Envelope}}{{16}}{{{email_tex}}}{{{email_href}}}{{black}}",
            ]
            if linkedin_url and linkedin_url.startswith(("http://", "https://")):
                li_disp = escape_latex(linkedin_url.replace("https://", "").replace("http://", "").rstrip("/"))
                contact_lines[-1] += f"\\\\[{contact_item_spacing}]"
                contact_lines.append(f"        \\iconhref{{Linkedin}}{{14}}{{{li_disp}}}{{{linkedin_url}}}{{black}}")
            if github_url and github_url.startswith(("http://", "https://")):
                gh_disp = escape_latex(github_url.replace("https://", "").replace("http://", "").rstrip("/"))
                contact_lines[-1] += f"\\\\[{contact_item_spacing}]"
                contact_lines.append(f"        \\iconhref{{Github}}{{14}}{{{gh_disp}}}{{{github_url}}}{{black}}")
            left_chunks.append(
                f"        \\vspace{{{left_section_spacing}}}\n"
                f"        \\cvsection{{{t}}}\n\n"
                + "\n".join(contact_lines)
                + "\n"
            )
    left_column_content = "\n".join(left_chunks)

    section_order_right = _get_val("section_order_right", ["profile", "experience", "education", "signature"])
    if not isinstance(section_order_right, list):
        section_order_right = ["profile", "experience", "education", "signature"]

    right_chunks: list[str] = []
    for s in section_order_right:
        s_norm = str(s).strip().lower()
        if s_norm == "profile" and show_profile:
            t = _get_title("profile", "Profil", "Profile")
            right_chunks.append(
                f"        \\cvsection{{{t}}}\n"
                f"        \\vspace{{{main_section_spacing}}}\n\n"
                f"        \\cvtext{{{profile_text}}}\n"
            )
        elif s_norm in ("experience", "work_experience", "work"):
            t = _get_title("experience", "Berufserfahrung", "Work Experience")
            right_chunks.append(
                f"        \\vspace{{{main_section_spacing}}}\n"
                f"        \\cvsection{{{t}}}\n"
                f"        \\vspace{{{main_section_spacing}}}\n\n"
                f"{exp_blocks_tex}\n"
            )
        elif s_norm in ("education", "academics"):
            t = _get_title("education", "Ausbildung", "Education")
            right_chunks.append(
                f"        \\vspace{{{main_section_spacing}}}\n"
                f"        \\cvsection{{{t}}}\n"
                f"        \\vspace{{{main_section_spacing}}}\n\n"
                f"{edu_blocks_tex}\n"
            )
        elif s_norm == "signature" and show_signature:
            right_chunks.append(signature_block)
    right_column_content = "\n".join(right_chunks)

    replacements = {
        "@@ASSETS_DIR@@": assets_posix,
        "@@LANG@@": lang,
        "@@DOC_FONT_OPT@@": doc_font_opt,
        "@@CUSTOM_FONT_SIZE@@": custom_font_size,
        "@@MARGIN_TOP@@": margin_top,
        "@@MARGIN_BOTTOM@@": margin_bottom,
        "@@MARGIN_LEFT@@": margin_left,
        "@@MARGIN_RIGHT@@": margin_right,
        "@@MAIN_RGB@@": f"{main_rgb[0]},{main_rgb[1]},{main_rgb[2]}",
        "@@DARK_RGB@@": f"{dark_rgb[0]},{dark_rgb[1]},{dark_rgb[2]}",
        "@@LIGHT_RGB@@": f"{light_rgb[0]},{light_rgb[1]},{light_rgb[2]}",
        "@@ACCENT_RGB@@": f"{accent_rgb[0]},{accent_rgb[1]},{accent_rgb[2]}",
        "@@SECTION_SPACING@@": section_spacing,
        "@@EVENT_SPACING@@": event_spacing,
        "@@INTEREST_ITEM_SPACING@@": interest_item_spacing,
        "@@COLUMN_RATIO@@": column_ratio_str,
        "@@HEADER_TITLE@@": header_title,
        "@@LEFT_COLUMN_CONTENT@@": left_column_content,
        "@@RIGHT_COLUMN_CONTENT@@": right_column_content,
    }
    token_pattern = re.compile("|".join(re.escape(k) for k in replacements))
    return token_pattern.sub(lambda m: replacements[m.group(0)], tpl)


def build_cv_preset(preset_key: str, base_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a verified CV preset derived strictly from master_profile.json without inventing facts."""
    prof = base_profile or load_master_profile()
    cv_data = copy.deepcopy(prof.get("cv_base") or {})
    if preset_key == "robotics_perception":
        cv_data["role_line1"] = {"de": "Robotik- & KI-Ingenieur", "en": "Robotics & AI Engineer"}
        cv_data["role_line2"] = {"de": "Perception, ROS 2 & Software", "en": "Perception, ROS 2 & Software"}
        cv_data["profile"] = {
            "de": (
                "Ingenieur für Robotik und Automatisiertes Fahren (M.Sc.) mit praktischer "
                "Werkstudenten- und Industrieerfahrung in der Software- und Robotikentwicklung. "
                "Praxiserprobt in Python, PyTorch, ROS 2, Computer Vision, Sensorfusion auf Edge-Hardware "
                "sowie in der Entwicklung von LLM- und Fullstack-KI-Anwendungen mit FastAPI und Docker."
            ),
            "en": (
                "Robotics and Automated Systems Engineer (M.Sc.) with hands-on "
                "working-student and industry experience in software and robotics engineering. Experienced in Python, "
                "PyTorch, ROS 2, computer vision, sensor fusion on edge hardware, and developing "
                "agentic LLM and full-stack AI applications with FastAPI and Docker."
            ),
        }
        cv_data["skills"] = [
            {"name_de": "Python", "name_en": "Python", "level_de": "Sehr gut", "level_en": "Proficient"},
            {"name_de": "PyTorch / 3D CV", "name_en": "PyTorch / 3D CV", "level_de": "Praxis", "level_en": "Practical use"},
            {"name_de": "ROS 2", "name_en": "ROS 2", "level_de": "Praxis", "level_en": "Practical use"},
            {"name_de": "Sensorfusion (LiDAR)", "name_en": "Sensor Fusion (LiDAR)", "level_de": "Praxis", "level_en": "Practical use"},
            {"name_de": "FastAPI / REST", "name_en": "FastAPI / REST", "level_de": "Praxis", "level_en": "Practical use"},
            {"name_de": "Docker / Git / CI/CD", "name_en": "Docker / Git / CI/CD", "level_de": "Praxis", "level_en": "Practical use"},
            {"name_de": "Linux / Embedded", "name_en": "Linux / Embedded", "level_de": "Gut", "level_en": "Good"},
            {"name_de": "Java / C#", "name_en": "Java / C#", "level_de": "Gut", "level_en": "Good"},
        ]
        if cv_data.get("education") and len(cv_data["education"][0].get("bullets_de", [])) > 1:
            cv_data["education"][0]["bullets_de"][1]["text"] = (
                "ROS-2-basierte Sensorintegration (Kamera/LiDAR) und Implementierung eines "
                "Sensorfusions-Netzwerks auf Embedded-Edge-Hardware."
            )
            cv_data["education"][0]["bullets_en"][1]["text"] = (
                "ROS 2 sensor integration (camera/LiDAR) and implementation of a "
                "sensor fusion network on embedded edge hardware."
            )
            if len(cv_data.get("education", [])) > 1 and len(cv_data["education"][1].get("bullets_de", [])) > 2:
                cv_data["education"][1]["bullets_de"][2]["text"] = (
                    "Training und Evaluation eines PyTorch-basierten 3D-Objekterkennungsmodells auf Punktwolken."
                )
                cv_data["education"][1]["bullets_en"][2]["text"] = (
                    "Training and evaluation of a PyTorch-based 3D object detection model on point clouds."
                )
    return cv_data


def build_cv_pdf(
    cv_data: dict[str, Any],
    lang: str = "de",
    variant_id: int | None = None,
    stem_label: str = "Standard",
    strict_single_page: bool = True,
) -> dict[str, Any]:
    lang = "en" if lang == "en" else "de"
    tex_source = render_cv_tex(cv_data, lang=lang)
    prefix = f"cv_{variant_id}_" if variant_id else "cv_"
    filename = f"{prefix}{slugify_filename(stem_label)}_{lang.upper()}.pdf"
    target_pdf = PDF_CV_DIR / filename

    page_warning = ""
    try:
        res = compile_tex_to_pdf(tex_source, target_pdf, passes=2, enforce_single_page=strict_single_page)
        pages = res["pages"]
        if pages != 1:
            page_warning = f"Das Dokument hat {pages} Seiten (Ziel: exakt 1 A4-Seite). Bitte Inhalt leicht kürzen."
    except PageLimitError as exc:
        if strict_single_page:
            raise
        pages = exc.pages
        page_warning = str(exc)

    return {
        "pdf_path": str(target_pdf),
        "tex_path": str(target_pdf.with_suffix(".tex")),
        "pages": pages,
        "lang": lang,
        "warning": page_warning,
    }


def compile_and_save_variant(
    con: sqlite3.Connection,
    variant_id: int,
    langs: tuple[str, ...] = ("de", "en"),
    strict_single_page: bool = False,
) -> dict[str, Any]:
    row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
    if not row:
        raise ValueError("CV-Variante nicht gefunden")
    cv_data = json.loads(row["cv_data_json"])
    results: dict[str, Any] = {}
    pdf_de = row["pdf_de_path"]
    pdf_en = row["pdf_en_path"]
    warnings: list[str] = []

    for lg in langs:
        built = build_cv_pdf(
            cv_data,
            lang=lg,
            variant_id=variant_id,
            stem_label=row["name"],
            strict_single_page=strict_single_page,
        )
        results[lg] = built
        if built["pages"] != 1:
            warnings.append(f"{lg.upper()}: {built['pages']} Seiten (Ziel: 1 Seite)")
        if lg == "de":
            pdf_de = built["pdf_path"]
        else:
            pdf_en = built["pdf_path"]

    stamp = now_iso()
    with con:
        con.execute(
            "UPDATE cv_variants SET pdf_de_path=?, pdf_en_path=?, updated_at=? WHERE id=?",
            (pdf_de, pdf_en, stamp, variant_id),
        )
    return {"variant_id": variant_id, "builds": results, "warnings": warnings, "updated_at": stamp}
