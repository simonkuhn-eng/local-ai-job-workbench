"""Console CLI for testing and operating Bewerbungs-Manager."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sys

from backend.ai.agy_client import AgyError, adjust_cv_data_with_ai, assess_single_job, check_agy_status
from backend.config import now_iso
from backend.db import connect, lifecycle_summary, query_jobs
from backend.documents.cv_builder import compile_and_save_variant
from backend.documents.latex_common import LaTeXCompileError
from backend.documents.letter_builder import save_and_compile_letter
from backend.scanner.engine import SCAN_MANAGER


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Bewerbungs-Manager Konsole")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="Datenbank-, Lebenszyklus- und agy-KI-Status anzeigen")

    p_jobs = sub.add_parser("jobs", help="Stellen nach Lebenszyklus/Region filtern")
    p_jobs.add_argument("--lifecycle", default="relevant_all", choices=["relevant_all", "new", "active", "closed", "relisted", "bookmarked", "pipeline"])
    p_jobs.add_argument("--tier", default="")
    p_jobs.add_argument("--q", default="")
    p_jobs.add_argument("--limit", type=int, default=15)

    p_scan = sub.add_parser("scan", help="Parallelen Multi-Portal-Scan starten")
    p_scan.add_argument("--company", action="append", help="Nur bestimmte Arbeitgeber scannen (mehrfach nutzbar)")
    p_scan.add_argument("--workers", type=int, default=10)

    p_rank = sub.add_parser("rank", help="Regelqualifizierte Stelle per Google Antigravity Abo (Gemini Hydrogen) bewerten")
    p_rank.add_argument("--job-id", type=int, required=True)
    p_rank.add_argument(
        "--model",
        default="hydrogen",
        choices=["hydrogen", "hydrogen-low", "hydrogen-medium", "hydrogen-high", "hydrogen-xhigh", "flash", "flash-high"],
    )
    p_rank.add_argument("--deep", action="store_true", help="Tiefenprüfung (Anforderungsmatrix) mit ausführen")

    p_cv = sub.add_parser("cv-build", help="Standard-MBDA-Lebenslauf (DE + EN) kompilieren")
    p_cv.add_argument("--variant-id", type=int, default=1)

    p_cva = sub.add_parser("cv-adjust", help="MBDA-Lebenslauf per natürlicher Sprache mit Gemini Hydrogen/Argon anpassen und PDF bauen")
    p_cva.add_argument("--variant-id", type=int, default=1)
    p_cva.add_argument("--prompt", required=True)
    p_cva.add_argument("--job-id", type=int, default=None)
    p_cva.add_argument(
        "--model",
        default="hydrogen",
        choices=["hydrogen", "hydrogen-low", "hydrogen-medium", "hydrogen-high", "hydrogen-xhigh", "argon", "argon-medium", "argon-high", "flash", "flash-high"],
    )

    p_let = sub.add_parser("letter-build", help="DIN-Anschreiben aus getrennten Feldern als 1-Seiten-PDF bauen")
    p_let.add_argument("--company", required=True)
    p_let.add_argument("--contact", default="")
    p_let.add_argument("--street", default="")
    p_let.add_argument("--zip-city", default="")
    p_let.add_argument("--job-ref", default="")
    p_let.add_argument("--subject", required=True)
    p_let.add_argument("--body", required=True)
    p_let.add_argument("--lang", default="de", choices=["de", "en"])

    p_srv = sub.add_parser("serve", help="FastAPI-Server starten")
    p_srv.add_argument("--port", type=int, default=8780)

    args = parser.parse_args()

    if args.cmd == "stats":
        with closing(connect()) as con:
            summary = lifecycle_summary(con)
        agy = check_agy_status()
        print(json.dumps({"lifecycle": summary, "agy_ai": agy}, ensure_ascii=False, indent=2))

    elif args.cmd == "jobs":
        with closing(connect()) as con:
            res = query_jobs(con, lifecycle=args.lifecycle, tier=args.tier, q=args.q, limit=args.limit)
        print(f"Treffer gesamt: {res['total_matching']} (zeige {len(res['items'])})")
        for it in res["items"]:
            print(
                f"[{it['id']:>5}] Tier {it['tier']} | {it['rank_score']:>3} ({it['score_source']}) | "
                f"{it['tenure_badge']:<22} | {it['company']} — {it['title']} ({it['location']})"
            )

    elif args.cmd == "scan":
        res = SCAN_MANAGER.run_sync(only_companies=args.company, max_workers=args.workers)
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.cmd == "rank":
        try:
            with closing(connect()) as con:
                res = assess_single_job(con, args.job_id, model_key=args.model, with_deep_review=args.deep)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except (AgyError, ValueError) as exc:
            raise SystemExit(f"Fehler bei KI-Bewertung: {exc}")

    elif args.cmd == "cv-build":
        try:
            with closing(connect()) as con:
                res = compile_and_save_variant(con, args.variant_id, langs=("de", "en"), strict_single_page=True)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except (LaTeXCompileError, ValueError) as exc:
            raise SystemExit(f"Fehler bei CV-Kompilierung: {exc}")

    elif args.cmd == "cv-adjust":
        try:
            with closing(connect()) as con:
                row = con.execute("SELECT * FROM cv_variants WHERE id = ?", (args.variant_id,)).fetchone()
                if not row:
                    raise SystemExit("CV-Variante nicht gefunden")
                cv_data = json.loads(row["cv_data_json"])
                history = json.loads(row["history_json"] or "[]")
                job_ctx = None
                if args.job_id:
                    jrow = con.execute("SELECT company, title, location, description FROM jobs WHERE id=?", (args.job_id,)).fetchone()
                    if jrow:
                        job_ctx = dict(jrow)
                adjusted = adjust_cv_data_with_ai(cv_data, args.prompt, job_context=job_ctx, model_key=args.model)
                history.append({"ts": now_iso(), "label": f"CLI: {args.prompt[:60]}", "cv_data": cv_data})
                with con:
                    con.execute(
                        "UPDATE cv_variants SET cv_data_json=?, history_json=? WHERE id=?",
                        (json.dumps(adjusted["cv_data"], ensure_ascii=False), json.dumps(history[-15:], ensure_ascii=False), args.variant_id),
                    )
                built = compile_and_save_variant(con, args.variant_id, langs=("de", "en"), strict_single_page=False)
            print(json.dumps({"summary": adjusted["summary"], "model": adjusted["model"], "build": built}, ensure_ascii=False, indent=2))
        except (AgyError, LaTeXCompileError, ValueError) as exc:
            raise SystemExit(f"Fehler bei CV-Anpassung: {exc}")

    elif args.cmd == "letter-build":
        try:
            with closing(connect()) as con:
                res = save_and_compile_letter(
                    con,
                    {
                        "company": args.company,
                        "contact_person": args.contact,
                        "street": args.street,
                        "zip_city": args.zip_city,
                        "job_ref": args.job_ref,
                        "subject": args.subject,
                        "body_text": args.body,
                        "lang": args.lang,
                    },
                    strict_single_page=True,
                )
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except (LaTeXCompileError, ValueError) as exc:
            raise SystemExit(f"Fehler bei Anschreiben-Bau: {exc}")

    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("backend.server:app", host="127.0.0.1", port=args.port, reload=False)


if __name__ == "__main__":
    main()
