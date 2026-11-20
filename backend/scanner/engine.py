"""Parallel multi-portal scan engine with feed-health guards, batch SQLite commits, and live progress."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import difflib
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urlparse

from backend.config import load_sources_config, now_iso
from backend.db import ACTIVE_APPLICATIONS, connect
from backend.scanner.geo_scoring import (
    canonical_company,
    canonical_url,
    content_fingerprint,
    hard_exclusion,
    location_tier,
    relevance,
    strip_html,
)
from backend.scanner.parsers import (
    ashby_jobs,
    enrich_complete_listing,
    greenhouse_jobs,
    hensoldt_list_jobs,
    is_complete_source,
    join_jobs,
    lever_jobs,
    mbda_list_jobs,
    personio_jobs,
    portal_search_jobs,
    recruitee_jobs,
    schema_jobs,
    smartrecruiters_jobs,
    source_key,
    tech_portal_jobs,
)


def _validate_snapshot(jobs: list[dict]) -> list[dict]:
    if not isinstance(jobs, list):
        raise ValueError("Stellenliste ist keine gültige Liste")
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for item in jobs:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(k), str) or not item[k].strip() for k in ("company", "title", "url")
        ):
            raise ValueError("Pflichtfelder (Unternehmen, Titel, URL) fehlen")
        try:
            u = canonical_url(item["url"])
        except ValueError:
            continue
        if u in seen_urls:
            continue
        seen_urls.add(u)
        deduped.append(item)
    jobs[:] = deduped
    return deduped


def _allow_closures(
    con: sqlite3.Connection,
    src: str,
    company: str,
    previous_ids: list[int],
    seen_ids: list[int],
    stamp: str,
) -> tuple[bool, str]:
    prev_set = set(previous_ids)
    seen_set = set(seen_ids)
    missing = prev_set - seen_set
    row = con.execute("SELECT * FROM feed_health WHERE source = ?", (src,)).fetchone()
    last_good = (row["last_good_count"] or 0) if row else 0
    baseline = max(len(prev_set), last_good)
    total_empty_with_history = (not seen_set) and (baseline >= 5 or last_good >= 5)
    suspicious = (baseline >= 10 and len(seen_set) < baseline * 0.5 and len(missing) >= 5) or (
        not seen_set and bool(missing)
    )
    digest = hashlib.sha256(json.dumps(sorted(missing)).encode()).hexdigest()
    confirmed = bool(
        not total_empty_with_history
        and row
        and row["pending_missing"] == digest
        and row["pending_since"]
        and datetime.fromisoformat(stamp) - datetime.fromisoformat(row["pending_since"]) >= timedelta(hours=6)
    )
    warning = ""
    if suspicious and not confirmed:
        if total_empty_with_history:
            warning = (
                f"Feed lieferte 0 Stellen bei {baseline} erwarteten ({len(missing)} aktiv). "
                "Automatische Deaktivierung ausgesetzt."
            )
        else:
            warning = (
                f"Auffälliger Rückgang ({baseline} → {len(seen_set)}). "
                f"{len(missing)} mögliche Schließungen sicherheitshalber zurückgestellt."
            )
    pending_since = (row["pending_since"] if row and row["pending_missing"] == digest else stamp) if warning else ""
    checks = ((row["pending_checks"] if row and row["pending_missing"] == digest else 0) + 1) if warning else 0
    con.execute(
        """
        INSERT INTO feed_health(source, company, last_check, last_good_count, current_count, warning, pending_missing, pending_since, pending_checks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
          company=excluded.company, last_check=excluded.last_check, last_good_count=excluded.last_good_count,
          current_count=excluded.current_count, warning=excluded.warning, pending_missing=excluded.pending_missing,
          pending_since=excluded.pending_since, pending_checks=excluded.pending_checks
        """,
        (
            src,
            company or "",
            stamp,
            baseline if warning else len(seen_set),
            len(seen_set),
            warning,
            digest if warning else "",
            pending_since,
            checks,
        ),
    )
    return not warning, warning


def _reconcile_availability(
    con: sqlite3.Connection,
    job_id: int,
    stamp: str,
    src: str,
    reason: str,
    run_id: int | None = None,
) -> str | None:
    """Returns 'unlisted' | 'relisted' | None if availability changed."""
    row = con.execute("SELECT active FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    available = (
        con.execute(
            "SELECT 1 FROM job_sources WHERE job_id=? AND available=1 "
            "AND (source NOT IN ('manual','codex:web','openai:web_search') OR source=?) LIMIT 1",
            (job_id, src),
        ).fetchone()
        is not None
    )
    if available == bool(row["active"]):
        return None
    ev = "relisted" if available else "unlisted"
    con.execute(
        "INSERT INTO availability_events(job_id, event, observed_at, source, reason, run_id) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, ev, stamp, src, reason, run_id),
    )
    con.execute(
        "UPDATE jobs SET active=?, unavailable_since=?, unavailable_reason=?, availability_checked_at=? WHERE id=?",
        (int(available), "" if available else stamp, "" if available else reason, stamp, job_id),
    )
    return ev


def _upsert_job_in_tx(
    con: sqlite3.Connection,
    item: dict,
    prefs: dict[str, str],
    run_id: int | None,
) -> tuple[int, bool, bool, bool]:
    """Upsert one job inside an active transaction. Returns (job_id, is_new, is_changed, is_relisted)."""
    company = canonical_company(str(item["company"]))
    title = str(item["title"]).strip()
    if not company or not title:
        raise ValueError("Unternehmen und Titel fehlen")
    url = canonical_url(str(item["url"]))
    src = str(item.get("source") or "manual")
    location = str(item.get("location") or "").strip()
    description = strip_html(str(item.get("description") or ""))[:30000]

    keys = [("url", url)]
    if item.get("source_id"):
        keys.append(("source_id", f"{src}:{item['source_id']}"))

    job_id = None
    for kind, key in keys:
        r = con.execute("SELECT job_id FROM identities WHERE kind=? AND key=?", (kind, key)).fetchone()
        if r:
            job_id = r[0]
            break

    if job_id is None:
        _city_norm_map = {
            "münchen": "munich",
            "nürnberg": "nuremberg",
            "köln": "cologne",
            "regensburg": "regensburg",
            "augsburg": "augsburg",
            "ingolstadt": "ingolstadt",
            "erlangen": "erlangen",
            "würzburg": "wuerzburg",
            "wuerzburg": "wuerzburg",
        }

        def _city_token(loc: str) -> str:
            head = (loc or "").split(",")[0].split("/")[0].split("(")[0].strip().casefold()
            if not head:
                return ""
            return _city_norm_map.get(head, head)

        target_city = _city_token(location)
        candidates = con.execute(
            "SELECT id, description, url, source, source_id, location FROM jobs WHERE company=? COLLATE NOCASE AND title=? COLLATE NOCASE",
            (company, title),
        ).fetchall()
        for cand in candidates:
            if item.get("source_id") and cand["source"] == src and cand["source_id"] and cand["source_id"] != str(item.get("source_id")):
                continue
            cand_loc = cand["location"] or ""
            cand_city = _city_token(cand_loc)
            if location and cand_loc and location.casefold() != cand_loc.casefold():
                if not target_city or not cand_city or (
                    target_city != cand_city
                    and target_city not in cand_loc.casefold()
                    and cand_city not in location.casefold()
                ):
                    continue
            cand_desc = cand["description"] or ""
            if len(description) >= 300 and len(cand_desc) >= 300:
                if difflib.SequenceMatcher(None, description[:4000], cand_desc[:4000]).ratio() >= 0.88:
                    job_id = cand["id"]
                    break
            elif description.strip() and description.strip() == cand_desc.strip():
                job_id = cand["id"]
                break

    previous = None
    if job_id is not None:
        previous = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        description = description or previous["description"]
        if src != previous["source"] and len(description) < len(previous["description"]):
            description = previous["description"]
        location = location or previous["location"]

    pref = prefs.get(company.casefold(), "neutral")
    excl = hard_exclusion(title, location)
    if excl:
        score, rsn = 0, excl
    else:
        score, rsn = relevance(title, description, pref == "preferred")

    con.execute("INSERT OR IGNORE INTO companies(name, preference) VALUES (?, 'neutral')", (company,))
    tier = location_tier(location)
    seen = item.get("_observed_at") or now_iso()
    new_fp = content_fingerprint(company, title, location, description)

    is_new = False
    is_changed = False
    if job_id is None:
        cur = con.execute(
            """
            INSERT INTO jobs(company, title, location, description, url, source, source_id, tier, score, reason,
                             verification, active, first_seen, last_seen, content_hash, hard_exclusion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                company, title, location, description, url, src, item.get("source_id"),
                tier, score, rsn, item.get("verification", "feed"), seen, seen, new_fp, excl,
            ),
        )
        job_id = cur.lastrowid
        is_new = True
    else:
        old_fp = content_fingerprint(previous["company"], previous["title"], previous["location"], previous["description"])
        if old_fp != new_fp:
            is_changed = True
            if previous["bookmarked"] or previous["status"] in ACTIVE_APPLICATIONS:
                snap = {k: previous[k] for k in ("company", "title", "location", "description", "url")}
                con.execute(
                    "INSERT INTO job_snapshots(job_id, content, content_hash, reason, created) VALUES (?, ?, ?, 'vor Anzeigenänderung', ?)",
                    (job_id, json.dumps(snap, ensure_ascii=False), previous["content_hash"], seen),
                )
        incoming_sid = str(item.get("source_id") or "").strip()
        next_source = previous["source"]
        next_source_id = previous["source_id"] or incoming_sid
        if previous["source"] in ("codex:web", "portal:helsing", "manual") and src not in (
            "codex:web",
            "portal:helsing",
            "manual",
        ):
            next_source = src
            if incoming_sid:
                next_source_id = incoming_sid
        con.execute(
            """
            UPDATE jobs SET company=?, title=?, location=?, description=?, url=COALESCE(NULLIF(?, ''), url),
                            source=?, source_id=?,
                            tier=?, score=?, reason=?, last_seen=MAX(last_seen, ?), content_hash=?,
                            verification=?, hard_exclusion=?, changed_at=CASE WHEN ? THEN ? ELSE changed_at END
            WHERE id=?
            """,
            (
                company,
                title,
                location,
                description,
                url,
                next_source,
                next_source_id,
                tier,
                score,
                rsn,
                seen,
                new_fp,
                item.get("verification", previous["verification"]),
                excl,
                int(is_changed),
                seen,
                job_id,
            ),
        )

    for kind, key in keys:
        con.execute("INSERT OR IGNORE INTO identities(kind, key, job_id) VALUES (?, ?, ?)", (kind, key, job_id))

    # Observe source availability
    prev_src = con.execute("SELECT last_checked FROM job_sources WHERE job_id=? AND source=?", (job_id, src)).fetchone()
    is_relisted = False
    if not prev_src or prev_src[0] <= seen:
        con.execute(
            "INSERT INTO job_sources(job_id, source, url, last_seen, last_checked, available, reason) "
            "VALUES (?, ?, ?, ?, ?, 1, '') ON CONFLICT(job_id, source) DO UPDATE SET "
            "url=excluded.url, last_seen=excluded.last_seen, last_checked=excluded.last_checked, available=1, reason=''",
            (job_id, src, url, seen, seen),
        )
        con.execute("UPDATE jobs SET availability_checked_at=MAX(availability_checked_at, ?) WHERE id=?", (seen, job_id))
        ev = _reconcile_availability(con, job_id, seen, src, "seen_again", run_id)
        is_relisted = ev == "relisted"

    return job_id, is_new, is_changed, is_relisted


def _fetch_single_feed(con: sqlite3.Connection, db_lock: threading.Lock, feed: dict) -> tuple[str, str, list[dict], list[str]]:
    """Executes HTTP I/O for a single feed. Returns (source_name, company, jobs, closed_urls)."""
    src = source_key(feed)
    company = feed["company"]
    kind = feed["type"]
    if kind == "greenhouse":
        return src, company, greenhouse_jobs(feed), []
    if kind == "lever":
        return src, company, lever_jobs(feed), []
    if kind == "ashby":
        return src, company, ashby_jobs(feed), []
    if kind == "personio":
        return src, company, personio_jobs(feed), []
    if kind == "recruitee":
        return src, company, recruitee_jobs(feed), []
    if kind == "smartrecruiters":
        return src, company, smartrecruiters_jobs(feed), []
    if kind == "join":
        return src, company, join_jobs(feed), []
    if kind == "schema":
        return src, company, schema_jobs(feed), []
    if kind == "mbda":
        return src, company, enrich_complete_listing(con, db_lock, mbda_list_jobs(), src), []
    if kind == "hensoldt":
        return src, company, enrich_complete_listing(con, db_lock, hensoldt_list_jobs(), src), []
    if kind in ("avature", "workday", "html_portal"):
        jobs, closed = portal_search_jobs(con, db_lock, feed)
        return src, company, jobs, closed
    if kind == "tech_portal":
        jobs, closed = tech_portal_jobs(feed)
        return src, company, jobs, closed
    raise ValueError(f"Unbekannter Feed-Typ: {kind}")


class ScanManager:
    """Singleton-style background scan coordinator with live progress polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False,
            "started_at": "",
            "finished_at": "",
            "sources_total": 0,
            "sources_done": 0,
            "sources_ok": 0,
            "sources_failed": 0,
            "current_source": "",
            "found_count": 0,
            "new_count": 0,
            "closed_count": 0,
            "relisted_count": 0,
            "changed_count": 0,
            "errors": [],
            "scan_run_id": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state, errors=list(self._state["errors"]))

    def start_background(self, only_companies: list[str] | None = None, max_workers: int = 10) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state.update(
                {
                    "running": True,
                    "started_at": now_iso(),
                    "finished_at": "",
                    "sources_total": 0,
                    "sources_done": 0,
                    "sources_ok": 0,
                    "sources_failed": 0,
                    "current_source": "Initialisiere Scan …",
                    "found_count": 0,
                    "new_count": 0,
                    "closed_count": 0,
                    "relisted_count": 0,
                    "changed_count": 0,
                    "errors": [],
                    "scan_run_id": None,
                }
            )
        t = threading.Thread(
            target=self.run_sync,
            kwargs={"only_companies": only_companies, "max_workers": max_workers, "_already_marked": True},
            daemon=True,
        )
        t.start()
        return True

    def run_sync(
        self,
        only_companies: list[str] | None = None,
        max_workers: int = 10,
        _already_marked: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if not _already_marked:
                if self._state["running"]:
                    raise RuntimeError("Ein Scan läuft bereits")
                self._state["running"] = True

        t0 = time.perf_counter()
        started = now_iso()
        con = connect()
        db_lock = threading.Lock()

        try:
            config = load_sources_config()
            feeds_raw = config.get("feeds", [])
            # Include dynamic feeds if any exist in company_search
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_search'").fetchone():
                for r in con.execute("SELECT source_json FROM company_search WHERE coverage='feed' AND dynamic=1 AND source_json!=''"):
                    try:
                        feeds_raw.append(json.loads(r["source_json"]))
                    except ValueError:
                        pass

            prefs = {
                r["name"].casefold(): r["preference"]
                for r in con.execute("SELECT name, preference FROM companies")
            }
            only_set = {c.casefold() for c in only_companies} if only_companies else None

            dedup_feeds = {}
            for f in feeds_raw:
                if not f.get("enabled", True):
                    continue
                comp = canonical_company(f.get("company", ""))
                if prefs.get(comp.casefold()) == "excluded":
                    continue
                if only_set and comp.casefold() not in only_set and f.get("company", "").casefold() not in only_set:
                    continue
                dedup_feeds[source_key(f)] = f
            feeds = list(dedup_feeds.values())

            with db_lock, con:
                legacy_cur = con.execute("INSERT INTO runs(started, kind) VALUES (?, 'feed')", (started,))
                legacy_run_id = legacy_cur.lastrowid
                sr_cur = con.execute(
                    "INSERT INTO scan_runs(started_at, sources_total) VALUES (?, ?)",
                    (started, len(feeds)),
                )
                scan_run_id = sr_cur.lastrowid

            with self._lock:
                self._state.update(
                    {
                        "running": True,
                        "started_at": started,
                        "finished_at": "",
                        "sources_total": len(feeds),
                        "sources_done": 0,
                        "sources_ok": 0,
                        "sources_failed": 0,
                        "current_source": "Starte parallelen Abruf …",
                        "found_count": 0,
                        "new_count": 0,
                        "closed_count": 0,
                        "relisted_count": 0,
                        "changed_count": 0,
                        "errors": [],
                        "scan_run_id": scan_run_id,
                    }
                )

            new_ids: list[int] = []
            closed_ids: list[int] = []
            relisted_ids: list[int] = []
            errors: list[str] = []
            found_total = 0
            changed_total = 0
            sources_ok = 0
            sources_failed = 0

            with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(feeds) or 1))) as pool:
                future_map = {
                    pool.submit(_fetch_single_feed, con, db_lock, feed): feed
                    for feed in feeds
                }
                for fut in as_completed(future_map):
                    feed = future_map[fut]
                    comp_label = feed.get("company", "?")
                    src_name = source_key(feed)
                    try:
                        src_name, comp_label, jobs, closed_urls = fut.result()
                        if is_complete_source(src_name):
                            _validate_snapshot(jobs)
                    except Exception as exc:
                        sources_failed += 1
                        err_msg = f"{comp_label} ({src_name}): {exc}"
                        errors.append(err_msg)
                        fail_stamp = now_iso()
                        with db_lock, con:
                            con.execute(
                                """
                                INSERT INTO feed_health(source, company, last_check, last_good_count, current_count, warning, pending_missing, pending_since, pending_checks)
                                VALUES (?, ?, ?, 0, 0, ?, '', '', 0)
                                ON CONFLICT(source) DO UPDATE SET
                                  company=excluded.company, last_check=excluded.last_check,
                                  current_count=0, warning=excluded.warning
                                """,
                                (src_name, comp_label, fail_stamp, str(exc)[:300]),
                            )
                        with self._lock:
                            self._state["sources_done"] += 1
                            self._state["sources_failed"] = sources_failed
                            self._state["current_source"] = f"Fehler bei {comp_label}"
                            self._state["errors"] = errors[-25:]
                        continue

                    stamp = now_iso()
                    feed_errors = 0
                    seen_ids: list[int] = []
                    with db_lock, con:
                        previous_ids = [
                            r[0]
                            for r in con.execute(
                                "SELECT job_id FROM job_sources WHERE source=? AND available=1",
                                (src_name,),
                            )
                        ]
                        for job in jobs:
                            found_total += 1
                            try:
                                jid, is_new, is_chg, is_rel = _upsert_job_in_tx(con, job, prefs, legacy_run_id)
                                seen_ids.append(jid)
                                if is_new:
                                    new_ids.append(jid)
                                if is_chg:
                                    changed_total += 1
                                if is_rel:
                                    relisted_ids.append(jid)
                            except Exception as exc:
                                feed_errors += 1
                                errors.append(f"{comp_label}: {exc}")

                        # Handle explicit 404/410 closures from portal detail checks
                        for c_url in closed_urls:
                            try:
                                target = canonical_url(c_url)
                            except ValueError:
                                continue
                            for r_js in con.execute("SELECT job_id, url FROM job_sources WHERE source=?", (src_name,)):
                                try:
                                    js_canon = canonical_url(r_js[1])
                                except ValueError:
                                    continue
                                if js_canon == target:
                                    con.execute(
                                        "UPDATE job_sources SET available=0, last_checked=?, reason='original_closed' WHERE job_id=? AND source=?",
                                        (stamp, r_js[0], src_name),
                                    )
                                    ev = _reconcile_availability(con, r_js[0], stamp, src_name, "original_closed", legacy_run_id)
                                    if ev == "unlisted":
                                        closed_ids.append(r_js[0])

                        seen_ids = list(dict.fromkeys(seen_ids))
                        # Handle complete-feed absence closures with feed_health guard
                        if feed_errors == 0 and is_complete_source(src_name):
                            allowed, guard_warn = _allow_closures(con, src_name, comp_label, previous_ids, seen_ids, stamp)
                            if allowed:
                                seen_set = set(seen_ids)
                                for prev_jid in previous_ids:
                                    if prev_jid not in seen_set:
                                        con.execute(
                                            "UPDATE job_sources SET available=0, last_checked=?, reason='absent_complete_feed' "
                                            "WHERE job_id=? AND source=?",
                                            (stamp, prev_jid, src_name),
                                        )
                                        ev = _reconcile_availability(
                                            con, prev_jid, stamp, src_name, "absent_complete_feed", legacy_run_id
                                        )
                                        if ev == "unlisted":
                                            closed_ids.append(prev_jid)
                            elif guard_warn:
                                errors.append(f"{comp_label}: {guard_warn}")
                        elif feed_errors == 0:
                            con.execute(
                                """
                                INSERT INTO feed_health(source, company, last_check, last_good_count, current_count, warning, pending_missing, pending_since, pending_checks)
                                VALUES (?, ?, ?, NULL, ?, '', '', '', 0)
                                ON CONFLICT(source) DO UPDATE SET
                                  company=excluded.company, last_check=excluded.last_check,
                                  current_count=excluded.current_count, warning=''
                                """,
                                (src_name, comp_label, stamp, len(seen_ids)),
                            )

                    sources_ok += 1
                    with self._lock:
                        self._state["sources_done"] += 1
                        self._state["sources_ok"] = sources_ok
                        self._state["current_source"] = comp_label
                        self._state["found_count"] = found_total
                        self._state["new_count"] = len(set(new_ids))
                        self._state["closed_count"] = len(set(closed_ids))
                        self._state["relisted_count"] = len(set(relisted_ids))
                        self._state["changed_count"] = changed_total
                        self._state["errors"] = errors[-25:]

            finished = now_iso()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            uniq_new = sorted(set(new_ids))
            uniq_closed = sorted(set(closed_ids))
            uniq_relisted = sorted(set(relisted_ids))

            with db_lock, con:
                con.execute(
                    "UPDATE runs SET finished=?, found=?, added=?, error=? WHERE id=?",
                    (finished, found_total, len(uniq_new), "\n".join(errors)[:4000], legacy_run_id),
                )
                con.execute(
                    """
                    UPDATE scan_runs SET
                      finished_at=?, duration_ms=?, sources_ok=?, sources_failed=?,
                      found_count=?, new_count=?, closed_count=?, relisted_count=?, changed_count=?,
                      new_job_ids_json=?, closed_job_ids_json=?, relisted_job_ids_json=?, errors_json=?
                    WHERE id=?
                    """,
                    (
                        finished, duration_ms, sources_ok, sources_failed,
                        found_total, len(uniq_new), len(uniq_closed), len(uniq_relisted), changed_total,
                        json.dumps(uniq_new), json.dumps(uniq_closed), json.dumps(uniq_relisted),
                        json.dumps(errors[:100], ensure_ascii=False),
                        scan_run_id,
                    ),
                )

            with self._lock:
                self._state.update(
                    {
                        "running": False,
                        "finished_at": finished,
                        "current_source": "Abgeschlossen",
                    }
                )
                return dict(self._state, duration_ms=duration_ms)
        except Exception as exc:
            with self._lock:
                self._state["running"] = False
                self._state["finished_at"] = now_iso()
                self._state["errors"].append(str(exc))
            raise
        finally:
            con.close()


SCAN_MANAGER = ScanManager()
