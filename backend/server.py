"""FastAPI local server powering the Bewerbungs-Manager desktop & browser workbench."""
from __future__ import annotations

from contextlib import closing
from functools import lru_cache
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader, PdfWriter
import pypdfium2 as pdfium

from backend.ai.agy_client import (
    AgyError,
    adapt_letter_paragraphs_with_ai,
    adjust_cv_data_with_ai,
    assess_single_job,
    check_agy_status,
    generate_cover_letter_with_ai,
    generate_interview_prep_with_ai,
    rank_prioritized_jobs_with_ai,
    recommend_letter_paragraphs_with_ai,
)
from backend.config import (
    ALLGEMEINE_UNTERLAGEN_DIR,
    DATA_DIR,
    FESTANSTELLUNG_BEWERBUNG_DIR,
    FRONTEND_DIST_DIR,
    PDF_DIR,
    load_master_profile,
    now_iso,
    save_master_profile,
    today_de,
)
from backend.db import (
    adapt_paragraph_for_target,
    connect,
    delete_letter_paragraph,
    get_companies_overview,
    get_company_application_history,
    get_interview_prep,
    get_job_detail,
    get_pipeline_sankey_data,
    get_prioritized_ai_ranking,
    get_prioritized_jobs_for_ai_ranking,
    get_skill_gap_heatmap,
    ingest_paragraphs_from_body,
    lifecycle_summary,
    list_letter_paragraphs,
    query_jobs,
    reorder_prioritized_jobs,
    resolve_application_folder,
    save_interview_prep,
    save_prioritized_ai_ranking,
    undo_job_event,
    update_job_workflow,
    upsert_letter_paragraph,
)
from backend.documents.cv_builder import build_cv_preset, compile_and_save_variant
from backend.documents.latex_common import LaTeXCompileError, slugify_filename
from backend.documents.letter_builder import (
    save_and_compile_letter,
    save_letter_draft_record,
    today_for_lang,
)
from backend.scanner.address_resolver import resolve_letter_address
from backend.scanner.engine import SCAN_MANAGER
from backend.scanner.geo_scoring import (
    MIN_AI_RULE_SCORE,
    canonical_company,
    hard_exclusion,
    location_tier,
    relevance,
)
from backend.documents.pdf_verifier import run_letter_preflight, verify_pdf_quality

app = FastAPI(title="Bewerbungs-Manager API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cached agy status (refresh every 5 minutes or on demand)
_agy_cache_lock = threading.Lock()
_agy_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _get_agy_status(force: bool = False) -> dict[str, Any]:
    now_s = time.monotonic()
    with _agy_cache_lock:
        if not force and _agy_cache["data"] is not None and (now_s - _agy_cache["ts"]) <= 300:
            return _agy_cache["data"]
    fresh = check_agy_status()
    with _agy_cache_lock:
        _agy_cache["data"] = fresh
        _agy_cache["ts"] = time.monotonic()
        return _agy_cache["data"]


# Background AI batch runner state & locks
_ai_batch_lock = threading.Lock()
_ai_batch_cancel = threading.Event()
_pdfium_render_lock = threading.Lock()
_compile_guard_lock = threading.Lock()

# Bavaria-first prioritization for Hydrogen batch assessment:
# Tier 1 (Bavaria) pays 0 cost; outside-Zone-1 postings incur moderate negative costs
# so very strong Tier 2/3/4 roles still qualify without overtaking stronger Bavarian fits.
HYDROGEN_TIER_COSTS = {1: 0, 2: -12, 3: -18, 4: -24}

_ai_batch_state: dict[str, Any] = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_job": "",
    "model": "hydrogen",
    "errors": [],
    "results": [],
}


# --- Pydantic Request Schemas ---
class JobWorkflowPatch(BaseModel):
    changes: dict[str, Any]
    confirmed_sent: bool = False


class PrioritizedAiRankRequest(BaseModel):
    focus: str = "balanced"
    instruction: str = ""
    model: str = "hydrogen"
    apply_order: bool = True


class CompanyPrefRequest(BaseModel):
    company: str
    preference: str = Field(pattern="^(preferred|neutral|excluded)$")


class ScanStartRequest(BaseModel):
    only_companies: list[str] | None = None
    max_workers: int = Field(default=10, ge=1, le=20)


class AssessJobRequest(BaseModel):
    model: str = "hydrogen"
    deep: bool = False


class BatchAssessRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    model: str = "hydrogen"
    tier: str = "all"


class CreateCvVariantRequest(BaseModel):
    name: str
    job_id: int | None = None
    base_variant_id: int = 1
    lang: str = "de"
    preset: str = ""


class RenameCvVariantRequest(BaseModel):
    name: str


class SaveCvDataRequest(BaseModel):
    name: str | None = None
    lang: str = "de"
    cv_data: dict[str, Any]
    compile_pdf: bool = True


class AdjustCvRequest(BaseModel):
    prompt: str
    model: str = "hydrogen"
    include_job_context: bool = True
    job_id: int | None = None


class PrefillLetterRequest(BaseModel):
    job_id: int | None = None
    lang: str = "auto"


class GenerateLetterRequest(BaseModel):
    job_id: int | None = None
    lang: str = "de"
    model: str = "hydrogen"
    instruction: str = ""
    current_draft: dict[str, Any] = Field(default_factory=dict)
    proofread_only: bool = False
    humanize_only: bool = False


class SaveLetterDraftRequest(BaseModel):
    letter_id: int | None = None
    job_id: int | None = None
    name: str = ""
    lang: str = "de"
    company: str = ""
    contact_person: str = ""
    street: str = ""
    zip_city: str = ""
    job_ref: str = ""
    place: str = "München"
    date_str: str = ""
    subject: str = ""
    salutation: str = ""
    body_text: str = ""
    closing: str = ""
    include_signature: bool = True
    signature_width_cm: float = 3.7
    address_source: dict[str, Any] | None = None


class CompileLetterRequest(SaveLetterDraftRequest):
    pass


class LetterFromTemplateRequest(BaseModel):
    source_letter_id: int
    job_id: int


class AdaptBlocksRequest(BaseModel):
    job_id: int | None = None
    paragraphs: list[str]
    lang: str = "de"
    model: str = "hydrogen"


class AutofillAddressRequest(BaseModel):
    job_id: int | None = None
    company: str = ""
    location: str = ""
    title: str = ""
    lang: str = "de"


def _parse_address_source(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(str(raw))
        return val if isinstance(val, dict) else None
    except Exception:
        return None


class GenerateInterviewPrepRequest(BaseModel):
    lang: str = "de"
    model: str = "hydrogen"


class ReorderPrioritizedRequest(BaseModel):
    job_ids: list[int]


class CreateLetterParagraphRequest(BaseModel):
    text: str
    lang: str = ""
    category: str = ""
    label: str = ""
    company: str = ""
    job_title: str = ""
    job_id: int | None = None
    source_kind: str = "manual"


class RecommendParagraphsRequest(BaseModel):
    job_id: int
    lang: str = ""
    model: str = "hydrogen"


def _safe_contained_pdf(path_str: str | None) -> Path:
    if not path_str:
        raise HTTPException(status_code=404, detail="PDF-Pfad fehlt")
    p = Path(path_str).resolve()
    allowed_roots = (PDF_DIR.resolve(), DATA_DIR.resolve(), FESTANSTELLUNG_BEWERBUNG_DIR.resolve())
    if not any(p == root or root in p.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Dateipfad außerhalb der erlaubten Verzeichnisse")
    if not p.exists():
        raise HTTPException(status_code=404, detail="PDF-Datei nicht gefunden")
    return p


# --- Endpoints ---
@app.get("/api/health")
def api_health() -> dict[str, str]:
    return {"status": "ok"}
@app.get("/api/overview")
def api_overview(refresh_ai: bool = False) -> dict[str, Any]:
    with closing(connect()) as con:
        summary = lifecycle_summary(con)
    profile = load_master_profile()
    with _ai_batch_lock:
        batch_st = dict(
            _ai_batch_state,
            errors=list(_ai_batch_state["errors"]),
            results=list(_ai_batch_state.get("results", [])),
        )
    return {
        "lifecycle": summary,
        "scan": SCAN_MANAGER.status(),
        "ai_status": _get_agy_status(force=refresh_ai),
        "ai_batch": batch_st,
        "personal": profile["personal"],
    }


@app.get("/api/profile")
def api_get_profile() -> dict[str, Any]:
    return load_master_profile()


@app.put("/api/profile")
def api_put_profile(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Ungültiges Profil-Format")
    current = load_master_profile()
    for key in ("personal", "cv_base", "letter_defaults", "ranking_profile"):
        if key in payload and isinstance(payload[key], dict):
            if isinstance(current.get(key), dict):
                merged = dict(current[key])
                merged.update(payload[key])
                current[key] = merged
            else:
                current[key] = payload[key]
    save_master_profile(current)
    return {"ok": True, "profile": current}


@app.get("/api/jobs")
def api_list_jobs(
    lifecycle: str = "relevant_all",
    q: str = "",
    tier: str = "",
    city_area: str = "",
    status: str = "",
    min_score: int = 1,
    include_excluded: bool = False,
    hide_prioritized: bool = False,
    hide_applied: bool = False,
    sort_by: str = "score",
    limit: int = Query(default=450, ge=1, le=2000),
) -> dict[str, Any]:
    with closing(connect()) as con:
        return query_jobs(
            con,
            lifecycle=lifecycle,
            q=q,
            tier=tier,
            city_area=city_area,
            status=status,
            min_score=min_score,
            include_excluded=include_excluded,
            hide_prioritized=hide_prioritized,
            hide_applied=hide_applied,
            sort_by=sort_by,
            limit=limit,
        )


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: int) -> dict[str, Any]:
    with closing(connect()) as con:
        detail = get_job_detail(con, job_id, mark_viewed=True)
    if not detail:
        raise HTTPException(status_code=404, detail="Stelle nicht gefunden")
    return detail


@app.patch("/api/jobs/{job_id}")
def api_patch_job(job_id: int, body: JobWorkflowPatch) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            res = update_job_workflow(con, job_id, body.changes, confirmed_sent=body.confirmed_sent)
            detail = get_job_detail(con, job_id, mark_viewed=False)
        return {**res, "job": detail}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs/undo/{event_id}")
def api_undo_job(event_id: int) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            return undo_job_event(con, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs/prioritized/reorder")
def api_reorder_prioritized(body: ReorderPrioritizedRequest) -> dict[str, Any]:
    with closing(connect()) as con:
        ordered = reorder_prioritized_jobs(con, body.job_ids)
    return {"ordered_ids": ordered}


@app.get("/api/jobs/prioritized/ranking")
def api_get_prioritized_ranking() -> dict[str, Any]:
    with closing(connect()) as con:
        ranking = get_prioritized_ai_ranking(con)
        company_history = get_company_application_history(con)
    return {"ranking": ranking, "company_history": company_history}


@app.post("/api/jobs/prioritized/ai-rank")
def api_ai_rank_prioritized(body: PrioritizedAiRankRequest) -> dict[str, Any]:
    focus = body.focus if body.focus in ("balanced", "chance", "location", "urgency") else "balanced"
    try:
        with closing(connect()) as con:
            active_jobs = get_prioritized_jobs_for_ai_ranking(con)
            if not active_jobs:
                raise HTTPException(status_code=400, detail="Keine aktiven priorisierten Stellen vorhanden.")
            company_history = get_company_application_history(con)
            previous_order = [int(j["id"]) for j in active_jobs]
            result = rank_prioritized_jobs_with_ai(
                active_jobs,
                company_history,
                focus=focus,
                instruction=body.instruction.strip()[:500],
                model_key=body.model or "hydrogen",
            )
            result["previous_order"] = previous_order
            result["evaluated_count"] = len(active_jobs)
            if body.apply_order:
                reorder_prioritized_jobs(con, result["ranked_ids"])
            save_prioritized_ai_ranking(con, result)
        return {
            "ranking": result,
            "company_history": company_history,
            "applied": body.apply_order,
        }
    except (AgyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/companies")
def api_companies_overview() -> dict[str, Any]:
    with closing(connect()) as con:
        return get_companies_overview(con)


@app.post("/api/companies/preference")
def api_company_pref(body: CompanyPrefRequest) -> dict[str, Any]:
    comp = canonical_company(body.company)
    with closing(connect()) as con, con:
        con.execute(
            "INSERT INTO companies(name, preference) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET preference=excluded.preference",
            (comp, body.preference),
        )
        # Immediately recalculate rule scores so +7 bonus/exclusion takes effect without waiting for next scan
        rows = con.execute(
            "SELECT id, company, title, location, description, hard_exclusion FROM jobs WHERE active = 1"
        ).fetchall()
        updates = []
        is_pref = body.preference == "preferred"
        for r in rows:
            if canonical_company(r["company"]).casefold() != comp.casefold():
                continue
            excl = r["hard_exclusion"] or hard_exclusion(r["title"], r["location"])
            if excl:
                continue
            sc, rsn = relevance(r["title"], r["description"], is_pref)
            updates.append((sc, rsn, r["id"]))
        if updates:
            con.executemany("UPDATE jobs SET score=?, reason=? WHERE id=?", updates)
    return {"company": comp, "preference": body.preference}


# --- Scanner Endpoints ---
@app.post("/api/scan/start")
def api_scan_start(body: ScanStartRequest) -> dict[str, Any]:
    started = SCAN_MANAGER.start_background(only_companies=body.only_companies, max_workers=body.max_workers)
    return {"started": started, "status": SCAN_MANAGER.status()}


@app.get("/api/scan/status")
def api_scan_status() -> dict[str, Any]:
    return SCAN_MANAGER.status()


@app.get("/api/scan/runs")
def api_scan_runs(limit: int = Query(default=15, ge=1, le=100)) -> list[dict[str, Any]]:
    with closing(connect()) as con:
        rows = con.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [
        {
            "id": r["id"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "duration_ms": r["duration_ms"],
            "sources_total": r["sources_total"],
            "sources_ok": r["sources_ok"],
            "sources_failed": r["sources_failed"],
            "found_count": r["found_count"],
            "new_count": r["new_count"],
            "closed_count": r["closed_count"],
            "relisted_count": r["relisted_count"],
            "changed_count": r["changed_count"],
            "errors": json.loads(r["errors_json"] or "[]"),
        }
        for r in rows
    ]


# --- AI Assessment Endpoints ---
@app.post("/api/jobs/{job_id}/assess")
def api_assess_job(job_id: int, body: AssessJobRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            res = assess_single_job(con, job_id, model_key=body.model or "hydrogen", with_deep_review=body.deep)
            detail = get_job_detail(con, job_id, mark_viewed=False)
        return {**res, "job": detail}
    except (AgyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/ai/batch-assess")
def api_batch_assess(body: BatchAssessRequest) -> dict[str, Any]:
    with _ai_batch_lock:
        if _ai_batch_state["running"]:
            raise HTTPException(status_code=409, detail="Eine KI-Stapelbewertung läuft bereits.")
        _ai_batch_cancel.clear()
        _ai_batch_state.update(
            {
                "running": True,
                "total": 0,
                "done": 0,
                "current_job": "Wähle regelqualifizierte Kandidaten (Bayern zuerst) …",
                "model": body.model or "hydrogen",
                "errors": [],
                "results": [],
            }
        )

    def _worker() -> None:
        con = connect()
        try:
            sql = (
                "SELECT jobs.id, jobs.company, jobs.title, jobs.location, jobs.tier, jobs.score, "
                "COALESCE(companies.preference, 'neutral') AS preference "
                "FROM jobs "
                "LEFT JOIN companies ON jobs.company = companies.name COLLATE NOCASE "
                "WHERE jobs.active = 1 "
                "AND jobs.status NOT IN ('uninteressant', 'ignoriert') "
                "AND jobs.hard_exclusion = '' "
                "AND COALESCE(companies.preference, 'neutral') != 'excluded' "
                "AND jobs.score >= ? "
                "AND LENGTH(COALESCE(jobs.description, '')) >= 120 "
                "AND (jobs.ai_score IS NULL OR jobs.ai_version < 3) "
            )
            args: list[Any] = [MIN_AI_RULE_SCORE]
            if body.tier in ("1", "2", "3", "4"):
                sql += "AND jobs.tier = ? "
                args.append(int(body.tier))
            sql += (
                "ORDER BY "
                "CASE WHEN jobs.bookmarked = 1 OR jobs.priority > 0 THEN 1 ELSE 0 END DESC, "
                "(jobs.score + CASE jobs.tier "
                f"WHEN 1 THEN {HYDROGEN_TIER_COSTS[1]} "
                f"WHEN 2 THEN {HYDROGEN_TIER_COSTS[2]} "
                f"WHEN 3 THEN {HYDROGEN_TIER_COSTS[3]} "
                f"ELSE {HYDROGEN_TIER_COSTS[4]} END) DESC, "
                "jobs.tier ASC, "
                "jobs.score DESC, "
                "jobs.first_seen DESC LIMIT ?"
            )
            args.append(body.limit)
            targets = con.execute(sql, args).fetchall()
            with _ai_batch_lock:
                _ai_batch_state["total"] = len(targets)

            for r in targets:
                if _ai_batch_cancel.is_set():
                    with _ai_batch_lock:
                        _ai_batch_state["errors"].append("Stapelbewertung durch Nutzer gestoppt.")
                    break
                with _ai_batch_lock:
                    _ai_batch_state["current_job"] = f"{r['company']} — {r['title']}"
                try:
                    res = assess_single_job(con, r["id"], model_key=body.model or "hydrogen", with_deep_review=False)
                    assessment = res.get("assessment") or {}
                    verdict = assessment.get("reason") or ""
                    if len(verdict) > 140:
                        verdict = verdict[:137] + "…"
                    raw_ai = int(res["ai_score"])
                    pref_bonus = 7 if r["preference"] == "preferred" else 0
                    tier_val = int(r["tier"] or 4)
                    item = {
                        "id": r["id"],
                        "company": r["company"],
                        "title": r["title"],
                        "location": r["location"] or "",
                        "tier": tier_val,
                        "zone_cost": HYDROGEN_TIER_COSTS.get(tier_val, -24),
                        "ai_score": raw_ai,
                        "rank_score": min(100, raw_ai + pref_bonus),
                        "rule_score": int(r["score"] or 0),
                        "eligible": bool(assessment.get("eligible", True)),
                        "summary": verdict,
                    }
                    with _ai_batch_lock:
                        _ai_batch_state["results"].append(item)
                except Exception as exc:
                    with _ai_batch_lock:
                        _ai_batch_state["errors"].append(f"{r['company']}: {exc}")
                with _ai_batch_lock:
                    _ai_batch_state["done"] += 1
        except Exception as exc:
            with _ai_batch_lock:
                _ai_batch_state["errors"].append(f"Batch-Initialisierung fehlgeschlagen: {exc}")
        finally:
            con.close()
            with _ai_batch_lock:
                _ai_batch_state["results"].sort(
                    key=lambda x: (x.get("rank_score", x["ai_score"]) + x.get("zone_cost", 0), x["ai_score"]),
                    reverse=True,
                )
                _ai_batch_state["running"] = False
                _ai_batch_state["current_job"] = "Abgebrochen" if _ai_batch_cancel.is_set() else "Abgeschlossen"

    threading.Thread(target=_worker, daemon=True).start()
    with _ai_batch_lock:
        return dict(
            _ai_batch_state,
            errors=list(_ai_batch_state["errors"]),
            results=list(_ai_batch_state.get("results", [])),
        )


@app.post("/api/ai/batch-cancel")
def api_batch_cancel() -> dict[str, Any]:
    _ai_batch_cancel.set()
    with _ai_batch_lock:
        return {"cancelling": True, "running": _ai_batch_state["running"]}


@app.get("/api/ai/batch-status")
def api_batch_status() -> dict[str, Any]:
    with _ai_batch_lock:
        return dict(
            _ai_batch_state,
            errors=list(_ai_batch_state["errors"]),
            results=list(_ai_batch_state.get("results", [])),
        )


# --- CV Studio Endpoints ---
def _pdf_pages(path_str: str) -> int:
    if not path_str or not Path(path_str).exists():
        return 0
    try:
        return len(PdfReader(path_str).pages)
    except Exception:
        return 0


NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@lru_cache(maxsize=32)
def _render_pdf_page_png(path_str: str, mtime_ns: int, page: int = 0) -> bytes:
    with _pdfium_render_lock:
        doc = pdfium.PdfDocument(path_str)
        try:
            page_count = len(doc)
            if page_count <= 0:
                raise ValueError("PDF enthält 0 Seiten")
            idx = max(0, min(page, page_count - 1))
            pil_img = doc[idx].render(scale=2.4).to_pil()
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue()
        finally:
            doc.close()


def clear_pdf_render_cache() -> None:
    """Clear memory cache for rendered PDF PNGs so freshly compiled files appear immediately."""
    _render_pdf_page_png.cache_clear()


def _serialize_cv_variant(row: Any, include_data: bool = False) -> dict[str, Any]:
    de_exists = bool(row["pdf_de_path"] and Path(row["pdf_de_path"]).exists())
    en_exists = bool(row["pdf_en_path"] and Path(row["pdf_en_path"]).exists())
    out = {
        "id": row["id"],
        "job_id": row["job_id"],
        "name": row["name"],
        "lang": row["lang"],
        "pdf_de_ready": de_exists,
        "pdf_en_ready": en_exists,
        "pages_de": _pdf_pages(row["pdf_de_path"]) if de_exists else 0,
        "pages_en": _pdf_pages(row["pdf_en_path"]) if en_exists else 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_data:
        out["cv_data"] = json.loads(row["cv_data_json"])
        out["history"] = json.loads(row["history_json"] or "[]")
        pdf_de = Path(row["pdf_de_path"]) if row["pdf_de_path"] else None
        pdf_en = Path(row["pdf_en_path"]) if row["pdf_en_path"] else None
        job = None
        if row["job_id"]:
            with closing(connect()) as c:
                job = get_job_detail(c, row["job_id"], mark_viewed=False)
        out["verification_de"] = verify_pdf_quality(pdf_de, doc_type="cv", job=job) if (pdf_de and pdf_de.exists()) else None
        out["verification_en"] = verify_pdf_quality(pdf_en, doc_type="cv", job=job) if (pdf_en and pdf_en.exists()) else None
        out["verification"] = out["verification_en"] if row["lang"] == "en" else out["verification_de"]
    return out


@app.get("/api/cv/variants")
def api_list_cv_variants() -> list[dict[str, Any]]:
    with closing(connect()) as con:
        rows = con.execute("SELECT * FROM cv_variants ORDER BY CASE WHEN job_id IS NULL THEN 0 ELSE 1 END, updated_at DESC").fetchall()
    return [_serialize_cv_variant(r, include_data=False) for r in rows]


@app.get("/api/cv/variants/{variant_id}")
def api_get_cv_variant(variant_id: int) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
            # Ensure PDFs are compiled if missing
            missing_de = not (row["pdf_de_path"] and Path(row["pdf_de_path"]).exists())
            missing_en = not (row["pdf_en_path"] and Path(row["pdf_en_path"]).exists())
            if missing_de or missing_en:
                compile_and_save_variant(con, variant_id, langs=("de", "en"), strict_single_page=False)
                row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
        return _serialize_cv_variant(row, include_data=True)
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/cv/variants")
def api_create_cv_variant(body: CreateCvVariantRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            if body.job_id:
                jrow = con.execute("SELECT id, title, description FROM jobs WHERE id = ?", (body.job_id,)).fetchone()
                if not jrow:
                    raise HTTPException(status_code=404, detail="Ziel-Stelle nicht gefunden")
                existing = con.execute("SELECT id FROM cv_variants WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1", (body.job_id,)).fetchone()
                if existing:
                    row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (existing["id"],)).fetchone()
                    return _serialize_cv_variant(row, include_data=True)
                lang = body.lang if body.lang in ("de", "en") else detect_job_language(jrow["title"], jrow["description"])
            else:
                lang = "en" if body.lang == "en" else "de"

            if body.preset in ("ai_backend", "robotics_perception"):
                cv_json = json.dumps(build_cv_preset(body.preset), ensure_ascii=False)
            else:
                base = con.execute("SELECT cv_data_json FROM cv_variants WHERE id = ?", (body.base_variant_id,)).fetchone()
                cv_json = base["cv_data_json"] if base else json.dumps(load_master_profile()["cv_base"], ensure_ascii=False)
            stamp = now_iso()
            with con:
                cur = con.execute(
                    "INSERT INTO cv_variants(job_id, name, lang, cv_data_json, history_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, '[]', ?, ?)",
                    (body.job_id, body.name.strip() or "Lebenslauf-Variante", lang, cv_json, stamp, stamp),
                )
                vid = cur.lastrowid
            with _compile_guard_lock:
                compile_and_save_variant(con, vid, langs=("de", "en"), strict_single_page=False)
                clear_pdf_render_cache()
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (vid,)).fetchone()
        return _serialize_cv_variant(row, include_data=True)
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/cv/variants/{variant_id}/rename")
def api_rename_cv_variant(variant_id: int, body: RenameCvVariantRequest) -> dict[str, Any]:
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    with closing(connect()) as con:
        row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
        with con:
            con.execute("UPDATE cv_variants SET name = ?, updated_at = ? WHERE id = ?", (new_name, now_iso(), variant_id))
        updated = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
    return _serialize_cv_variant(updated, include_data=False)


@app.delete("/api/cv/variants/{variant_id}")
def api_delete_cv_variant(variant_id: int) -> dict[str, Any]:
    with closing(connect()) as con:
        row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
        if row["job_id"] is None:
            raise HTTPException(status_code=400, detail="Der Master-Lebenslauf kann nicht gelöscht werden.")
        for col in ("pdf_de_path", "pdf_en_path"):
            p_str = row[col]
            if p_str:
                for p in (Path(p_str), Path(p_str).with_suffix(".tex")):
                    if p.exists():
                        p.unlink(missing_ok=True)
        with con:
            con.execute("DELETE FROM cv_variants WHERE id = ?", (variant_id,))
    clear_pdf_render_cache()
    return {"deleted": True}


@app.put("/api/cv/variants/{variant_id}")
def api_save_cv_variant(variant_id: int, body: SaveCvDataRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
            history = json.loads(row["history_json"] or "[]")
            # Only append undo history on explicit compiles/saves to prevent 1.5s auto-save from evicting AI snapshots
            if body.compile_pdf:
                history.append({"ts": now_iso(), "label": "Manuelle Bearbeitung", "cv_data": json.loads(row["cv_data_json"])})
                history = history[-15:]
            with con:
                con.execute(
                    "UPDATE cv_variants SET name=COALESCE(?, name), lang=?, cv_data_json=?, history_json=?, updated_at=? WHERE id=?",
                    (
                        body.name.strip() if body.name else None,
                        body.lang,
                        json.dumps(body.cv_data, ensure_ascii=False),
                        json.dumps(history, ensure_ascii=False),
                        now_iso(),
                        variant_id,
                    ),
                )
            if body.compile_pdf:
                build_info = compile_and_save_variant(con, variant_id, langs=("de", "en"), strict_single_page=False)
                clear_pdf_render_cache()
                warnings = build_info["warnings"]
            else:
                warnings = []
            updated = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
        return {**_serialize_cv_variant(updated, include_data=True), "warnings": warnings}
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/cv/variants/{variant_id}/adjust")
def api_adjust_cv_variant(variant_id: int, body: AdjustCvRequest) -> dict[str, Any]:
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Bitte gib einen Anpassungswunsch ein.")
    try:
        with closing(connect()) as con:
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
            current_cv = json.loads(row["cv_data_json"])

            job_ctx = None
            target_job_id = body.job_id or row["job_id"]
            if body.include_job_context and target_job_id:
                jrow = con.execute("SELECT company, title, location, description FROM jobs WHERE id = ?", (target_job_id,)).fetchone()
                if jrow:
                    job_ctx = dict(jrow)

            ai_res = adjust_cv_data_with_ai(current_cv, body.prompt, job_context=job_ctx, model_key=body.model or "hydrogen")
            history = json.loads(row["history_json"] or "[]")
            history.append({"ts": now_iso(), "label": body.prompt.strip()[:80], "cv_data": current_cv})
            history = history[-15:]

            with con:
                con.execute(
                    "UPDATE cv_variants SET cv_data_json=?, history_json=?, updated_at=? WHERE id=?",
                    (json.dumps(ai_res["cv_data"], ensure_ascii=False), json.dumps(history, ensure_ascii=False), now_iso(), variant_id),
                )
            build_info = compile_and_save_variant(con, variant_id, langs=("de", "en"), strict_single_page=False)
            clear_pdf_render_cache()
            updated = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()

        return {
            "summary": ai_res["summary"],
            "model": ai_res["model"],
            "warnings": build_info["warnings"],
            "variant": _serialize_cv_variant(updated, include_data=True),
        }
    except (AgyError, LaTeXCompileError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/cv/variants/{variant_id}/undo")
def api_undo_cv_variant(variant_id: int) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
            history = json.loads(row["history_json"] or "[]")
            if not history:
                raise HTTPException(status_code=400, detail="Keine frühere Version zum Wiederherstellen vorhanden.")
            prev = history.pop()
            with con:
                con.execute(
                    "UPDATE cv_variants SET cv_data_json=?, history_json=?, updated_at=? WHERE id=?",
                    (json.dumps(prev["cv_data"], ensure_ascii=False), json.dumps(history, ensure_ascii=False), now_iso(), variant_id),
                )
            build_info = compile_and_save_variant(con, variant_id, langs=("de", "en"), strict_single_page=False)
            clear_pdf_render_cache()
            updated = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
        return {**_serialize_cv_variant(updated, include_data=True), "warnings": build_info["warnings"]}
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _candidate_file_slug() -> str:
    try:
        name = str((load_master_profile().get("personal") or {}).get("name") or "Candidate")
    except Exception:
        name = "Candidate"
    return slugify_filename(name, fallback="Candidate")


@app.get("/api/cv/variants/{variant_id}/pdf/{lang}")
def api_get_cv_pdf(variant_id: int, lang: str, download: bool = False) -> FileResponse:
    lg = "en" if lang.lower() == "en" else "de"
    try:
        with closing(connect()) as con:
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
            col = "pdf_en_path" if lg == "en" else "pdf_de_path"
            if not row[col] or not Path(row[col]).exists():
                compile_and_save_variant(con, variant_id, langs=(lg,), strict_single_page=False)
                clear_pdf_render_cache()
                row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pdf_path = _safe_contained_pdf(row[col])
    cand_slug = _candidate_file_slug()
    dl_name = f"{'CV' if lg == 'en' else 'Lebenslauf'}_{cand_slug}_{slugify_filename(row['name'])}.pdf"
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=dl_name if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/api/cv/variants/{variant_id}/preview/{lang}")
def api_get_cv_preview(variant_id: int, lang: str, page: int = 0) -> Response:
    lg = "en" if lang.lower() == "en" else "de"
    try:
        with closing(connect()) as con:
            row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="CV-Variante nicht gefunden")
            col = "pdf_en_path" if lg == "en" else "pdf_de_path"
            if not row[col] or not Path(row[col]).exists():
                with _compile_guard_lock:
                    compile_and_save_variant(con, variant_id, langs=(lg,), strict_single_page=False)
                    clear_pdf_render_cache()
                row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (variant_id,)).fetchone()
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pdf_path = _safe_contained_pdf(row[col])
    png = _render_pdf_page_png(str(pdf_path), pdf_path.stat().st_mtime_ns, page)
    return Response(content=png, media_type="image/png", headers=NO_CACHE_HEADERS)


# --- Cover Letter Endpoints ---
@app.get("/api/letters")
def api_list_letters() -> list[dict[str, Any]]:
    with closing(connect()) as con:
        rows = con.execute("SELECT * FROM cover_letters ORDER BY updated_at DESC").fetchall()
    return [
        {
            **dict(r),
            "include_signature": bool(r["include_signature"]),
            "pdf_ready": bool(r["pdf_path"] and Path(r["pdf_path"]).exists()),
            "pages": _pdf_pages(r["pdf_path"]) if r["pdf_path"] else 0,
            "address_source": _parse_address_source(r["address_source"]) if "address_source" in r.keys() else None,
        }
        for r in rows
    ]


@app.get("/api/letters/paragraphs")
def api_list_letter_paragraphs(lang: str = "") -> list[dict[str, Any]]:
    with closing(connect()) as con:
        return list_letter_paragraphs(con, lang=lang)


@app.post("/api/letters/paragraphs")
def api_create_letter_paragraph(body: CreateLetterParagraphRequest) -> dict[str, Any]:
    with closing(connect()) as con:
        saved = upsert_letter_paragraph(
            con,
            text=body.text,
            lang=body.lang,
            category=body.category,
            label=body.label,
            company=body.company,
            job_title=body.job_title,
            job_id=body.job_id,
            source_kind=body.source_kind,
            commit=True,
        )
        if not saved:
            raise HTTPException(status_code=400, detail="Absatz ist zu kurz (mindestens 35 Zeichen).")
        items = list_letter_paragraphs(con)
    return {"item": saved, "items": items}


@app.delete("/api/letters/paragraphs/{paragraph_id}")
def api_delete_letter_paragraph(paragraph_id: int) -> dict[str, Any]:
    with closing(connect()) as con:
        deleted = delete_letter_paragraph(con, paragraph_id)
    return {"deleted": deleted}


@app.post("/api/letters/paragraphs/recommend")
def api_recommend_letter_paragraphs(body: RecommendParagraphsRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            job = get_job_detail(con, body.job_id, mark_viewed=False)
            if not job:
                raise HTTPException(status_code=404, detail="Stelle nicht gefunden")
            paragraphs = list_letter_paragraphs(con, lang=body.lang)
        return recommend_letter_paragraphs_with_ai(
            job, paragraphs, model_key=body.model or "hydrogen", target_lang=body.lang
        )
    except (AgyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/letters/adapt-blocks")
def api_adapt_letter_blocks(body: AdaptBlocksRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            job = get_job_detail(con, body.job_id, mark_viewed=False) if body.job_id else None
        target_company = (job.get("company") if job else "") or ""
        target_title = (job.get("title") if job else "") or ""
        pre_cleaned: list[str] = []
        det_notes: list[str] = []
        for p in body.paragraphs:
            ad = adapt_paragraph_for_target(
                None, p, target_company=target_company, target_title=target_title, lang=body.lang
            )
            pre_cleaned.append(ad["text"])
            det_notes.extend(ad["replacements"])
        ai_res = adapt_letter_paragraphs_with_ai(
            job, pre_cleaned, lang="en" if body.lang == "en" else "de", model_key=body.model or "hydrogen"
        )
        all_changes = list(dict.fromkeys(det_notes + list(ai_res.get("changes") or [])))
        return {**ai_res, "changes": all_changes}
    except (AgyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/letters/from-template")
def api_letter_from_template(body: LetterFromTemplateRequest) -> dict[str, Any]:
    defaults = load_master_profile()["letter_defaults"]
    with closing(connect()) as con:
        src = con.execute("SELECT * FROM cover_letters WHERE id = ?", (body.source_letter_id,)).fetchone()
        if not src:
            raise HTTPException(status_code=404, detail="Vorlage-Anschreiben nicht gefunden")
        jrow = con.execute("SELECT * FROM jobs WHERE id = ?", (body.job_id,)).fetchone()
        if not jrow:
            raise HTTPException(status_code=404, detail="Ziel-Stelle nicht gefunden")
        job_lang = detect_job_language(jrow["title"], jrow["description"])
        addr_info = resolve_letter_address(con=con, job_id=jrow["id"], lang=job_lang)
        adapted_paras: list[str] = []
        replacements: list[str] = []
        raw_paras = [p.strip() for p in re.split(r"\n\s*\n", (src["body_text"] or "").replace("\r\n", "\n")) if p.strip()]
        for p in raw_paras:
            ad = adapt_paragraph_for_target(
                con,
                p,
                target_company=addr_info["company"],
                target_title=jrow["title"],
                target_city=addr_info["zip_city"],
                lang=src["lang"] or job_lang,
            )
            adapted_paras.append(ad["text"])
            replacements.extend(ad["replacements"])
        eff_lang = src["lang"] if src["lang"] in ("de", "en") else job_lang
        payload = {
            "job_id": jrow["id"],
            "name": f"{addr_info['company']} — {jrow['title'][:40]}",
            "lang": eff_lang,
            "company": addr_info["company"],
            "contact_person": addr_info["contact_person"],
            "street": addr_info["street"],
            "zip_city": addr_info["zip_city"],
            "job_ref": addr_info["job_ref"],
            "address_source": addr_info["source"],
            "place": defaults.get("place", "München"),
            "date_str": today_for_lang(eff_lang),
            "subject": addr_info["subject"] or (f"Application for {jrow['title']}" if eff_lang == "en" else f"Bewerbung als {jrow['title']}"),
            "salutation": defaults["salutation_en"] if eff_lang == "en" else defaults["salutation_de"],
            "body_text": "\n\n".join(adapted_paras),
            "closing": defaults["closing_en"] if eff_lang == "en" else defaults["closing_de"],
            "include_signature": bool(defaults.get("include_signature", True)),
            "signature_width_cm": float(defaults.get("signature_width_cm", 3.7)),
        }
        preflight = run_letter_preflight(payload, job_row=dict(jrow), con=con)
        return {
            **payload,
            "id": None,
            "pdf_ready": False,
            "pages": 0,
            "preflight": preflight,
            "template_replacements": list(dict.fromkeys(replacements)),
        }


@app.get("/api/letters/{letter_id}")
def api_get_letter(letter_id: int) -> dict[str, Any]:
    with closing(connect()) as con:
        row = con.execute("SELECT * FROM cover_letters WHERE id = ?", (letter_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Anschreiben nicht gefunden")
        pdf_path = Path(row["pdf_path"]) if row["pdf_path"] else None
        job = get_job_detail(con, row["job_id"], mark_viewed=False) if row["job_id"] else None
        pages = _pdf_pages(row["pdf_path"]) if row["pdf_path"] else 0
        verification = verify_pdf_quality(pdf_path, doc_type="letter", job=job) if (pdf_path and pdf_path.exists()) else None
        preflight = run_letter_preflight(dict(row), job_row=job, con=con, pdf_pages=pages or None)
    return {
        **dict(row),
        "include_signature": bool(row["include_signature"]),
        "pdf_ready": bool(row["pdf_path"] and Path(row["pdf_path"]).exists()),
        "pdf_stale": _is_pdf_stale(row["pdf_path"], row["updated_at"]),
        "pages": pages,
        "verification": verification,
        "preflight": preflight,
        "address_source": _parse_address_source(row["address_source"]) if "address_source" in row.keys() else None,
    }


@app.post("/api/letters/autofill-address")
def api_autofill_letter_address(body: AutofillAddressRequest) -> dict[str, Any]:
    with closing(connect()) as con:
        return resolve_letter_address(
            con=con,
            job_id=body.job_id,
            company=body.company,
            location=body.location,
            title=body.title,
            lang=body.lang,
        )


@app.post("/api/letters/prefill")
def api_prefill_letter(body: PrefillLetterRequest) -> dict[str, Any]:
    defaults = load_master_profile()["letter_defaults"]
    with closing(connect()) as con:
        if body.job_id:
            existing = con.execute("SELECT * FROM cover_letters WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1", (body.job_id,)).fetchone()
            if existing:
                pdf_path = Path(existing["pdf_path"]) if existing["pdf_path"] else None
                job = get_job_detail(con, existing["job_id"], mark_viewed=False) if existing["job_id"] else None
                pages = _pdf_pages(existing["pdf_path"]) if existing["pdf_path"] else 0
                verification = verify_pdf_quality(pdf_path, doc_type="letter", job=job) if (pdf_path and pdf_path.exists()) else None
                preflight = run_letter_preflight(dict(existing), job_row=job, con=con, pdf_pages=pages or None)
                return {
                    **dict(existing),
                    "include_signature": bool(existing["include_signature"]),
                    "pdf_ready": bool(existing["pdf_path"] and Path(existing["pdf_path"]).exists()),
                    "pdf_stale": _is_pdf_stale(existing["pdf_path"], existing["updated_at"]),
                    "pages": pages,
                    "verification": verification,
                    "preflight": preflight,
                    "address_source": _parse_address_source(existing["address_source"]) if "address_source" in existing.keys() else None,
                }
            jrow = con.execute("SELECT * FROM jobs WHERE id = ?", (body.job_id,)).fetchone()
            if jrow:
                lang = body.lang if body.lang in ("de", "en") else detect_job_language(jrow["title"], jrow["description"])
                addr_info = resolve_letter_address(con=con, job_id=jrow["id"], lang=lang)
                preflight = run_letter_preflight({"company": addr_info["company"], "zip_city": addr_info["zip_city"], "job_ref": addr_info["job_ref"], "subject": addr_info["subject"], "body_text": "", "lang": lang}, job_row=dict(jrow), con=con)
                return {
                    "id": None,
                    "job_id": jrow["id"],
                    "name": f"{addr_info['company']} — {jrow['title'][:40]}",
                    "lang": lang,
                    "company": addr_info["company"],
                    "contact_person": addr_info["contact_person"],
                    "street": addr_info["street"],
                    "zip_city": addr_info["zip_city"],
                    "job_ref": addr_info["job_ref"],
                    "address_source": addr_info["source"],
                    "place": defaults.get("place", "München"),
                    "date_str": today_for_lang(lang),
                    "subject": addr_info["subject"] or (f"Application for {jrow['title']}" if lang == "en" else f"Bewerbung als {jrow['title']}"),
                    "salutation": defaults["salutation_en"] if lang == "en" else defaults["salutation_de"],
                    "body_text": "",
                    "closing": defaults["closing_en"] if lang == "en" else defaults["closing_de"],
                    "include_signature": bool(defaults.get("include_signature", True)),
                    "signature_width_cm": float(defaults.get("signature_width_cm", 3.7)),
                    "pdf_ready": False,
                    "pages": 0,
                    "preflight": preflight,
                }

    lang = "en" if body.lang == "en" else "de"
    return {
        "id": None,
        "job_id": None,
        "name": "Neues Anschreiben",
        "lang": lang,
        "company": "",
        "contact_person": "",
        "street": "",
        "zip_city": "",
        "job_ref": "",
        "place": defaults.get("place", "München"),
        "date_str": today_for_lang(lang),
        "subject": "",
        "salutation": defaults["salutation_en"] if lang == "en" else defaults["salutation_de"],
        "body_text": "",
        "closing": defaults["closing_en"] if lang == "en" else defaults["closing_de"],
        "include_signature": bool(defaults.get("include_signature", True)),
        "signature_width_cm": float(defaults.get("signature_width_cm", 3.7)),
        "pdf_ready": False,
        "pages": 0,
    }


@app.post("/api/letters/generate")
def api_generate_letter(body: GenerateLetterRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            job_context = get_job_detail(con, body.job_id, mark_viewed=False) if body.job_id else None
        draft_payload = dict(body.current_draft)
        draft_payload["lang"] = "en" if body.lang == "en" else "de"
        res = generate_cover_letter_with_ai(
            draft_payload,
            instruction=body.instruction,
            job_context=job_context,
            model_key=body.model,
            proofread_only=body.proofread_only,
            humanize_only=body.humanize_only,
        )
        return res
    except (AgyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/letters/draft")
def api_save_letter_draft(body: SaveLetterDraftRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            if body.job_id and not con.execute("SELECT 1 FROM jobs WHERE id = ?", (body.job_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Ziel-Stelle nicht gefunden")
            res = save_letter_draft_record(
                con,
                body.model_dump(),
                letter_id=body.letter_id,
            )
            row = con.execute("SELECT * FROM cover_letters WHERE id = ?", (res["letter_id"],)).fetchone()
            pdf_path = Path(row["pdf_path"]) if row["pdf_path"] else None
            job = get_job_detail(con, row["job_id"], mark_viewed=False) if row["job_id"] else None
            pages = _pdf_pages(row["pdf_path"]) if row["pdf_path"] else 0
            verification = verify_pdf_quality(pdf_path, doc_type="letter", job=job) if (pdf_path and pdf_path.exists()) else None
            preflight = run_letter_preflight(dict(row), job_row=job, con=con, pdf_pages=pages or None)
        return {
            **dict(row),
            "include_signature": bool(row["include_signature"]),
            "pdf_ready": bool(row["pdf_path"] and Path(row["pdf_path"]).exists()),
            "pdf_stale": _is_pdf_stale(row["pdf_path"], row["updated_at"]),
            "pages": pages,
            "warning": None,
            "verification": verification,
            "preflight": preflight,
            "address_source": _parse_address_source(row["address_source"]) if "address_source" in row.keys() else None,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/letters/compile")
def api_compile_letter(body: CompileLetterRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            if body.job_id and not con.execute("SELECT 1 FROM jobs WHERE id = ?", (body.job_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Ziel-Stelle nicht gefunden")
            with _compile_guard_lock:
                res = save_and_compile_letter(
                    con,
                    body.model_dump(),
                    letter_id=body.letter_id,
                    strict_single_page=False,
                )
                clear_pdf_render_cache()
            row = con.execute("SELECT * FROM cover_letters WHERE id = ?", (res["letter_id"],)).fetchone()
            pdf_path = Path(row["pdf_path"]) if row["pdf_path"] else None
            job = get_job_detail(con, row["job_id"], mark_viewed=False) if row["job_id"] else None
            if (row["body_text"] or "").strip():
                is_sent_status = bool(job and job.get("status") in ("beworben", "eingeladen", "gespraech", "angebot", "angenommen"))
                ingest_paragraphs_from_body(
                    con,
                    row["body_text"],
                    lang=row["lang"] or "de",
                    company=row["company"] or (job["company"] if job else ""),
                    job_title=(job["title"] if job else row["subject"]) or "",
                    job_id=row["job_id"],
                    source_kind="sent" if is_sent_status else "saved",
                    increment_usage=True,
                    commit=True,
                )
            verification = verify_pdf_quality(pdf_path, doc_type="letter", job=job) if (pdf_path and pdf_path.exists()) else None
            preflight = run_letter_preflight(dict(row), job_row=job, con=con, pdf_pages=res["pages"])
        return {
            **dict(row),
            "include_signature": bool(row["include_signature"]),
            "pdf_ready": True,
            "pdf_stale": False,
            "pages": res["pages"],
            "warning": res["warning"],
            "verification": verification,
            "preflight": preflight,
            "address_source": _parse_address_source(row["address_source"]) if "address_source" in row.keys() else None,
        }
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/letters/{letter_id}")
def api_delete_letter(letter_id: int) -> dict[str, Any]:
    with closing(connect()) as con, con:
        row = con.execute("SELECT pdf_path FROM cover_letters WHERE id = ?", (letter_id,)).fetchone()
        if row and row["pdf_path"]:
            for p in (Path(row["pdf_path"]), Path(row["pdf_path"]).with_suffix(".tex")):
                if p.exists():
                    p.unlink(missing_ok=True)
        con.execute("DELETE FROM cover_letters WHERE id = ?", (letter_id,))
    clear_pdf_render_cache()
    return {"deleted": True}


@app.get("/api/letters/{letter_id}/pdf")
def api_get_letter_pdf(letter_id: int, download: bool = False) -> FileResponse:
    with closing(connect()) as con:
        row = con.execute("SELECT * FROM cover_letters WHERE id = ?", (letter_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Anschreiben nicht gefunden")
    pdf_path = _safe_contained_pdf(row["pdf_path"])
    prefix = "Cover_Letter" if row["lang"] == "en" else "Anschreiben"
    dl_name = f"{prefix}_{_candidate_file_slug()}_{slugify_filename(row['company'] or row['name'])}.pdf"
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=dl_name if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/api/letters/{letter_id}/preview")
def api_get_letter_preview(letter_id: int, page: int = 0) -> Response:
    with closing(connect()) as con:
        row = con.execute("SELECT * FROM cover_letters WHERE id = ?", (letter_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Anschreiben nicht gefunden")
    pdf_path = _safe_contained_pdf(row["pdf_path"])
    png = _render_pdf_page_png(str(pdf_path), pdf_path.stat().st_mtime_ns, page)
    return Response(content=png, media_type="image/png", headers=NO_CACHE_HEADERS)


def detect_job_language(title: str = "", description: str = "") -> str:
    """Detect whether a job posting is in German ('de') or English ('en')."""
    text = f"{title or ''} {description or ''}".lower()
    if not text.strip():
        return "de"
    de_matches = len(
        re.findall(
            r"\b(und|der|die|das|mit|wir|sie|für|oder|im|auf|nach|ihr|profil|aufgaben|anforderungen|studium|abschluss|bewerbung|erfahrung|kenntnisse|standort|bieten|voraussetzungen)\b",
            text,
        )
    )
    en_matches = len(
        re.findall(
            r"\b(and|the|with|we|you|for|or|in|on|requirements|responsibilities|qualifications|experience|degree|skills|apply|about|role|looking|joining|offer|benefits)\b",
            text,
        )
    )
    return "en" if en_matches > de_matches else "de"


# Standard certificate filenames searched in ALLGEMEINE_UNTERLAGEN_DIR when exporting bundles
_DE_CERTIFICATES = (
    ("Master_Urkunde.pdf", "Master_Urkunde.pdf"),
    ("Master_Pruefungszeugnis.pdf", "Master_Pruefungszeugnis.pdf"),
    ("Bachelorurkunde.pdf", "Bachelorurkunde.pdf"),
    ("Bachelorpruefungszeugnis.pdf", "Bachelorpruefungszeugnis.pdf"),
    ("Arbeitszeugnisse.pdf", "Arbeitszeugnisse.pdf"),
)

_EN_CERTIFICATES = (
    ("Master_Certificate.pdf", "Master_Certificate.pdf"),
    ("Master_Transcript.pdf", "Master_Transcript.pdf"),
    ("Bachelor_Certificate.pdf", "Bachelor_Certificate.pdf"),
    ("Bachelor_Transcript.pdf", "Bachelor_Transcript.pdf"),
    ("Work_References.pdf", "Work_References.pdf"),
)

_LANG_EXCLUSIVE_FILES = {
    "de": {
        "Master_Certificate.pdf",
        "Master_Transcript.pdf",
        "Bachelor_Certificate.pdf",
        "Bachelor_Transcript.pdf",
        "Work_References.pdf",
    },
    "en": {
        "Master_Urkunde.pdf",
        "Master_Pruefungszeugnis.pdf",
        "Bachelorurkunde.pdf",
        "Bachelorpruefungszeugnis.pdf",
        "Arbeitszeugnisse.pdf",
    },
}


def _cleanup_target_folder(target_dir: Path, lang: str) -> None:
    """Remove files belonging to the opposite language BEFORE copying new files."""
    for excl in _LANG_EXCLUSIVE_FILES.get(lang, set()):
        excl_path = target_dir / excl
        if excl_path.exists():
            try:
                excl_path.unlink()
            except OSError:
                pass


def _merge_pdf_files(paths_with_labels: list[tuple[Path, str]], out_path: Path) -> Path | None:
    writer = PdfWriter()
    added_any = False
    for src, title in paths_with_labels:
        if not src.exists():
            continue
        try:
            reader = PdfReader(str(src))
            start_page = len(writer.pages)
            for page in reader.pages:
                writer.add_page(page)
            if len(reader.pages) > 0 and title:
                writer.add_outline_item(title, start_page)
            added_any = True
        except Exception:
            continue
    if not added_any:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        writer.write(f)
    return out_path


def _copy_standard_certificates(
    target_dir: Path,
    lang: str,
    warnings: list[str] | None = None,
) -> tuple[list[str], list[tuple[Path, str]]]:
    copied: list[str] = []
    cert_entries_for_bundle: list[tuple[Path, str]] = []
    if not ALLGEMEINE_UNTERLAGEN_DIR.exists():
        if warnings is not None:
            warnings.append(f"Zeugnis-Ordner nicht gefunden: {ALLGEMEINE_UNTERLAGEN_DIR}")
        return copied, cert_entries_for_bundle

    cert_list = _EN_CERTIFICATES if lang == "en" else _DE_CERTIFICATES
    for src_name, dst_name in cert_list:
        src_path = ALLGEMEINE_UNTERLAGEN_DIR / src_name
        dst_path = target_dir / dst_name
        if src_path.exists():
            shutil.copy2(src_path, dst_path)
            copied.append(str(dst_path))
            cert_entries_for_bundle.append((dst_path, dst_name.replace(".pdf", "").replace("_", " ")))
    return copied, cert_entries_for_bundle


def _open_in_windows_explorer(folder: Path) -> None:
    try:
        subprocess.Popen(["explorer.exe", str(folder)])
    except Exception:
        pass


def _is_pdf_stale(pdf_path_str: str | None, updated_at: str | None) -> bool:
    if not pdf_path_str:
        return True
    p = Path(pdf_path_str)
    if not p.exists():
        return True
    if not updated_at:
        return False
    try:
        from datetime import datetime
        upd_ts = datetime.fromisoformat(updated_at).timestamp()
        return p.stat().st_mtime < (upd_ts - 1.0)
    except Exception:
        return False


@app.post("/api/dossier/{job_id}/export")
def api_export_dossier(
    job_id: int,
    lang: str = "auto",
    include_attachments: bool = True,
    open_explorer: bool = False,
    create_bundle: bool = True,
) -> dict[str, Any]:
    """Export tailored CV, Cover Letter, and optional attachments into the configured export directory."""
    cand_slug = _candidate_file_slug()
    warnings: list[str] = []
    preflight: dict[str, Any] | None = None
    bundle_size_mb: float | None = None

    try:
        with closing(connect()) as con:
            jrow = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not jrow:
                raise HTTPException(status_code=404, detail="Stelle nicht gefunden")

            let_row = con.execute(
                "SELECT * FROM cover_letters WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1", (job_id,)
            ).fetchone()
            cv_row = (
                con.execute("SELECT * FROM cv_variants WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1", (job_id,)).fetchone()
                or con.execute("SELECT * FROM cv_variants WHERE job_id IS NULL ORDER BY id ASC LIMIT 1").fetchone()
            )

            if lang in ("de", "en"):
                eff_lang = lang
            elif let_row and let_row["lang"] in ("de", "en"):
                eff_lang = let_row["lang"]
            else:
                eff_lang = detect_job_language(jrow["title"], jrow["description"])

            target_dir = resolve_application_folder(jrow["company"], jrow["title"], jrow["document_links"] or "")
            target_dir.mkdir(parents=True, exist_ok=True)

            # Clean obsolete & opposite-language files BEFORE copying new artifacts (prevents cover letter self-deletion!)
            _cleanup_target_folder(target_dir, eff_lang)

            exported_files: list[str] = []
            dest_cv: Path | None = None
            dest_let: Path | None = None

            if cv_row:
                col = "pdf_en_path" if eff_lang == "en" else "pdf_de_path"
                if _is_pdf_stale(cv_row[col], cv_row["updated_at"]):
                    with _compile_guard_lock:
                        compile_and_save_variant(con, cv_row["id"], langs=(eff_lang,), strict_single_page=False)
                        clear_pdf_render_cache()
                    cv_row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (cv_row["id"],)).fetchone()
                if cv_row[col] and Path(cv_row[col]).exists():
                    cv_pages = _pdf_pages(cv_row[col])
                    if cv_pages > 1:
                        warnings.append(f"Lebenslauf hat {cv_pages} Seiten statt 1 Seite!")
                    dest_cv = target_dir / f"{'CV' if eff_lang == 'en' else 'Lebenslauf'}_{cand_slug}.pdf"
                    shutil.copy2(cv_row[col], dest_cv)
                    exported_files.append(str(dest_cv))

            if let_row:
                if _is_pdf_stale(let_row["pdf_path"], let_row["updated_at"]) and (let_row["body_text"] or "").strip():
                    with _compile_guard_lock:
                        save_and_compile_letter(
                            con,
                            {
                                **dict(let_row),
                                "include_signature": bool(let_row["include_signature"]),
                                "address_source": _parse_address_source(let_row["address_source"]) if "address_source" in let_row.keys() else None,
                            },
                            letter_id=let_row["id"],
                            strict_single_page=False,
                        )
                        clear_pdf_render_cache()
                    let_row = con.execute("SELECT * FROM cover_letters WHERE id = ?", (let_row["id"],)).fetchone()
                let_pages = _pdf_pages(let_row["pdf_path"]) if let_row["pdf_path"] else 0
                preflight = run_letter_preflight(dict(let_row), job_row=dict(jrow), con=con, pdf_pages=let_pages or None)
                warnings.extend(preflight.get("blockers") or [])
                warnings.extend(preflight.get("warnings") or [])
                if let_row["lang"] in ("de", "en") and let_row["lang"] != eff_lang:
                    warnings.append(f"Hinweis: Anschreiben ist auf {let_row['lang'].upper()}, Dossier wird auf {eff_lang.upper()} exportiert.")
                if let_row["pdf_path"] and Path(let_row["pdf_path"]).exists():
                    dest_let = target_dir / f"{'Cover_Letter' if eff_lang == 'en' else 'Anschreiben'}_{cand_slug}.pdf"
                    shutil.copy2(let_row["pdf_path"], dest_let)
                    exported_files.append(str(dest_let))
                if (let_row["body_text"] or "").strip():
                    is_sent_status = jrow["status"] in ("beworben", "eingeladen", "gespraech", "angebot", "angenommen")
                    ingest_paragraphs_from_body(
                        con,
                        let_row["body_text"],
                        lang=let_row["lang"] or eff_lang,
                        company=let_row["company"] or jrow["company"],
                        job_title=jrow["title"] or let_row["subject"] or "",
                        job_id=job_id,
                        source_kind="sent" if is_sent_status else "saved",
                        commit=False,
                    )

            cert_entries: list[tuple[Path, str]] = []
            if include_attachments:
                cert_files, cert_entries = _copy_standard_certificates(target_dir, eff_lang, warnings=warnings)
                exported_files.extend(cert_files)
                if create_bundle and cert_entries:
                    cert_bundle_name = f"{'Certificates' if eff_lang == 'en' else 'Zeugnisse'}_{cand_slug}.pdf"
                    cert_bundle = _merge_pdf_files(cert_entries, target_dir / cert_bundle_name)
                    if cert_bundle:
                        exported_files.append(str(cert_bundle))

            if create_bundle and (dest_let or dest_cv):
                full_parts: list[tuple[Path, str]] = []
                if dest_let:
                    full_parts.append((dest_let, "Cover Letter" if eff_lang == "en" else "Anschreiben"))
                if dest_cv:
                    full_parts.append((dest_cv, "CV" if eff_lang == "en" else "Lebenslauf"))
                full_parts.extend(cert_entries)
                comp_slug = slugify_filename(jrow["company"] or "Bewerbung")
                bundle_prefix = "Application" if eff_lang == "en" else "Bewerbung"
                full_bundle = _merge_pdf_files(full_parts, target_dir / f"{bundle_prefix}_{cand_slug}_{comp_slug}.pdf")
                if full_bundle and full_bundle.exists():
                    exported_files.append(str(full_bundle))
                    bundle_size_mb = round(full_bundle.stat().st_size / (1024 * 1024), 2)
                    if bundle_size_mb > 4.8:
                        warnings.append(
                            f"Gesamt-PDF ({bundle_prefix}_{cand_slug}_{comp_slug}.pdf) ist {bundle_size_mb} MB groß (manche Portale begrenzen Uploads auf 5 MB — dann Einzeldateien hochladen)."
                        )

            with con:
                con.execute("UPDATE jobs SET document_links = ? WHERE id = ?", (str(target_dir), job_id))
    except LaTeXCompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Dateisystemfehler beim Export: {exc}")

    if open_explorer:
        _open_in_windows_explorer(target_dir)

    return {
        "folder": str(target_dir),
        "lang": eff_lang,
        "files": exported_files,
        "file_count": len(exported_files),
        "bundle_size_mb": bundle_size_mb,
        "warnings": list(dict.fromkeys(w for w in warnings if w)),
        "preflight": preflight,
    }


@app.post("/api/dossier/{job_id}/open-folder")
def api_open_dossier_folder(job_id: int, lang: str = "auto") -> dict[str, Any]:
    """Open the company application folder in Windows Explorer (creating & populating it first if not present)."""
    with closing(connect()) as con:
        jrow = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not jrow:
            raise HTTPException(status_code=404, detail="Stelle nicht gefunden")
        let_row = con.execute(
            "SELECT lang FROM cover_letters WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1", (job_id,)
        ).fetchone()
        target_dir = resolve_application_folder(jrow["company"], jrow["title"], jrow["document_links"] or "")

    if not target_dir.exists() or not any(target_dir.iterdir()):
        return api_export_dossier(job_id, lang=lang, include_attachments=True, open_explorer=True)

    # Refresh certificates and any updated tailored PDFs if stale
    return api_export_dossier(
        job_id,
        lang=lang if lang in ("de", "en") else (let_row["lang"] if let_row and let_row["lang"] in ("de", "en") else "auto"),
        include_attachments=True,
        open_explorer=True,
    )


# --- Analytics & Interview-Prep Endpoints ---
@app.get("/api/pipeline/sankey")
def api_pipeline_sankey() -> dict[str, Any]:
    with closing(connect()) as con:
        return get_pipeline_sankey_data(con)


@app.get("/api/analytics/skill-gaps")
def api_skill_gaps(min_score: int = 55) -> dict[str, Any]:
    with closing(connect()) as con:
        return get_skill_gap_heatmap(con, min_score=min_score)


@app.get("/api/interview-prep/{job_id}")
def api_get_interview_prep(job_id: int) -> dict[str, Any]:
    with closing(connect()) as con:
        prep_record = get_interview_prep(con, job_id)
        if not prep_record:
            return {"job_id": job_id, "model": "", "updated_at": "", "prep": None}
        return prep_record


@app.post("/api/interview-prep/{job_id}")
def api_generate_interview_prep(job_id: int, body: GenerateInterviewPrepRequest) -> dict[str, Any]:
    try:
        with closing(connect()) as con:
            job = get_job_detail(con, job_id, mark_viewed=False)
            if not job:
                raise HTTPException(status_code=404, detail="Stelle nicht gefunden")
            prep_data = generate_interview_prep_with_ai(
                job,
                lang=body.lang,
                model_key=body.model,
            )
            saved = save_interview_prep(con, job_id, prep_data, model=prep_data.get("model", ""))

            # Exportiere zusätzlich Interview_Prep.md im Firmenordner, falls dieser bereits existiert
            company = str(job.get("company") or "").strip()
            job_title = str(job.get("title") or "").strip()
            target_folder = resolve_application_folder(
                company, job_title, job.get("export_folder") or job.get("document_links") or ""
            )
            if target_folder and target_folder.exists():
                prep_md_file = target_folder / f"Interview_Prep_{slugify_filename(company)}.md"
                md_lines = [
                    f"# Interview Vorbereitung: {company} — {job.get('title')}",
                    f"*Erstellt am: {today_de()} | Modell: {prep_data.get('model')}*",
                    "",
                    "## 60-Sekunden Pitch",
                    prep_data.get("pitch_60s", ""),
                    "",
                    "## STAR Stories",
                ]
                for idx, star in enumerate(prep_data.get("star_stories", []), 1):
                    md_lines.extend([
                        f"### {idx}. {star.get('question')} ({star.get('project')})",
                        f"- **Situation & Aufgabe:** {star.get('situation_task')}",
                        f"- **Aktion:** {star.get('action')}",
                        f"- **Ergebnis:** {star.get('result')}",
                        "",
                    ])
                md_lines.append("## Ehrliche Gap-Bridges (Lücken & Seniorität)")
                for idx, gb in enumerate(prep_data.get("gap_bridges", []), 1):
                    md_lines.extend([
                        f"### {idx}. {gb.get('gap')}",
                        f"> {gb.get('honest_bridge')}",
                        "",
                    ])
                md_lines.append("## Fragen an das Team")
                for q in prep_data.get("smart_questions", []):
                    md_lines.append(f"- {q}")
                try:
                    prep_md_file.write_text("\n".join(md_lines), encoding="utf-8")
                except OSError:
                    pass

        return saved
    except (AgyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# Serve built frontend SPA when available
if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
