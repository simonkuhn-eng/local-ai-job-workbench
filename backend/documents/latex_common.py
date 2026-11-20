"""Shared MiKTeX pdflatex runner, LaTeX escaping, and 1-page verification."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
import uuid
from pypdf import PdfReader

from backend.config import BUILD_TEMP_DIR, ensure_dirs

_TRANSLIT = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}


class LaTeXCompileError(RuntimeError):
    pass


class PageLimitError(LaTeXCompileError):
    def __init__(self, message: str, pages: int, pdf_path: Path) -> None:
        super().__init__(message)
        self.pages = pages
        self.pdf_path = pdf_path


def find_pdflatex() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    candidates = [
        "pdflatex",
        str(Path(local_app_data) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "pdflatex.exe"),
        r"C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe",
    ]
    for cand in candidates:
        found = shutil.which(cand)
        if found:
            return found
        if os.path.isfile(cand):
            return cand
    raise LaTeXCompileError("pdflatex wurde nicht gefunden. Bitte MiKTeX prüfen.")


_UNICODE_LATEX_SAFE = {
    "\u00a0": " ",
    "\u202f": " ",
    "\u00ad": "",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": ",",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2026": "...",
    "\u2022": "-",
    "\u2192": "->",
    "\u20ac": "EUR",
    "\u00b0": r"\textdegree{}",
    "\u00b1": "+/-",
    "\u00d7": "x",
    "\u00b5": "u",
    "\u00b2": "^2",
    "\u00b3": "^3",
    "\u2264": "<=",
    "\u2265": ">=",
    "\u2248": "~=",
    "\u2713": "[OK]",
    "\u2714": "[OK]",
    "\u25ba": ">",
    "\u2122": "TM",
    "\u00ae": "(R)",
    "\u00a9": "(C)",
}

_NON_BMP_RE = re.compile(r"[\U00010000-\U0010ffff]")


def escape_latex(value: str | None, keep_linebreaks: bool = False) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFC", str(value))
    s = _NON_BMP_RE.sub("", s)
    for uc, repl in _UNICODE_LATEX_SAFE.items():
        s = s.replace(uc, repl)
    s = s.replace("\\", "\x00")
    for ch, rep in (
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ):
        s = s.replace(ch, rep)
    s = s.replace("\x00", r"\textbackslash{}")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if keep_linebreaks:
        lines = [ln for ln in s.split("\n") if ln.strip()]
        if lines:
            rebuilt = [lines[0]]
            for nxt in lines[1:]:
                nxt_s = nxt.lstrip()
                sep = r"\\{}" if nxt_s[0] in ("[", "*") else r"\\"
                rebuilt[-1] += sep
                rebuilt.append(nxt)
            s = "\n".join(rebuilt)
        else:
            s = ""
    else:
        s = re.sub(r"\s*\n\s*", " ", s)
    return s


def normalize_ats_dates(period_str: str | None) -> str:
    """Ersetzt '--', '–' (U+2013) und '—' (U+2014) in Datumsbereichen durch ' - ' für Workday-ATS-Kompatibilität."""
    if not period_str:
        return ""
    cleaned = re.sub(r"\s*(?:--|[\u2013\u2014])\s*", " - ", str(period_str).strip())
    return cleaned


def protect_brands(escaped_tex: str) -> str:
    """Wrap brand/tech tokens that hyphenate poorly in narrow LaTeX columns."""
    for token in ("Docker", "FastAPI", "PyTorch"):
        escaped_tex = re.sub(rf"(?<!\\mbox\{{)\b{token}\b", rf"\\mbox{{{token}}}", escaped_tex)
    return escaped_tex


def slugify_filename(value: str, fallback: str = "dokument") -> str:
    s = str(value or "")
    for k, v in _TRANSLIT.items():
        s = s.replace(k, v)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:60] or fallback


def compile_tex_to_pdf(
    tex_source: str,
    target_pdf_path: Path,
    passes: int = 2,
    enforce_single_page: bool = True,
) -> dict:
    """Compile tex_source in an isolated temp folder (2 passes) and move clean PDF to target_pdf_path."""
    ensure_dirs()
    pdflatex = find_pdflatex()
    job_dir = BUILD_TEMP_DIR / f"run_{uuid.uuid4().hex[:10]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    tex_file = job_dir / "document.tex"
    tex_file.write_text(tex_source, encoding="utf-8")

    log_output = ""
    try:
        for _ in range(passes):
            try:
                proc = subprocess.run(
                    [
                        pdflatex,
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        "-output-directory",
                        str(job_dir),
                        str(tex_file),
                    ],
                    cwd=str(job_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise LaTeXCompileError(
                    "pdflatex Zeitüberschreitung (>120s) — fehlendes Paket oder hängende Eingabe prüfen."
                ) from exc
            log_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode != 0:
                log_file = job_dir / "document.log"
                full_log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else log_output
                lines = full_log.splitlines()
                err_idx = next((i for i, ln in enumerate(lines) if ln.startswith("!")), None)
                if err_idx is not None:
                    snippet = "\n".join(lines[max(0, err_idx - 3): min(len(lines), err_idx + 12)])
                else:
                    snippet = "\n".join(lines[-30:])
                raise LaTeXCompileError(f"LaTeX-Kompilierung fehlgeschlagen:\n{snippet}")

        built_pdf = job_dir / "document.pdf"
        if not built_pdf.exists():
            tail = "\n".join(log_output.splitlines()[-35:])
            raise LaTeXCompileError(f"Keine PDF erzeugt:\n{tail}")

        pages = len(PdfReader(str(built_pdf)).pages)
        if enforce_single_page and pages != 1:
            raise PageLimitError(
                f"Das Dokument hat {pages} Seiten (Ziel: exakt 1 A4-Seite). Bitte Inhalt leicht kürzen.",
                pages=pages,
                pdf_path=target_pdf_path,
            )

        target_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target_pdf_path.with_name(f".{target_pdf_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            shutil.copyfile(built_pdf, tmp_target)
            os.replace(tmp_target, target_pdf_path)
        except PermissionError as exc:
            tmp_target.unlink(missing_ok=True)
            raise LaTeXCompileError(
                f"Zieldatei '{target_pdf_path.name}' ist gesperrt (evtl. noch im PDF-Viewer geöffnet). Bitte schließen und erneut kompilieren."
            ) from exc
        except OSError:
            tmp_target.unlink(missing_ok=True)
            raise
        try:
            os.utime(target_pdf_path, None)
        except OSError:
            pass

        # Save .tex atomically next to the PDF for full reproducibility
        target_tex_path = target_pdf_path.with_suffix(".tex")
        tmp_tex = target_tex_path.with_name(f".{target_tex_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp_tex.write_text(tex_source, encoding="utf-8")
            os.replace(tmp_tex, target_tex_path)
        except OSError:
            tmp_tex.unlink(missing_ok=True)
            raise

        return {
            "pdf_path": str(target_pdf_path),
            "tex_path": str(target_tex_path),
            "pages": pages,
        }
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
