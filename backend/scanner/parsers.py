"""All 13 verified feed and career-portal parsers (thread-safe, zero external side effects)."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import html
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
import xml.etree.ElementTree as ET

from backend.config import USER_AGENT, now_iso
from backend.scanner.geo_scoring import (
    SENIOR,
    TECHNICAL,
    canonical_url,
    excluded_employment,
    feed_title,
    hard_exclusion,
    location_tier,
    norm,
    outside_region,
    relevance,
    slug,
    strip_html,
)
from backend.scanner.network import HttpStatusError, fetch_html, fetch_json, portal_request, tls_context

TARGETED_TYPES = ("avature", "workday", "html_portal", "tech_portal")
COMPLETE_PREFIXES = ("greenhouse:", "recruitee:", "lever:", "ashby:", "personio:", "smartrecruiters:", "schema:", "join:")


def source_key(feed: dict) -> str:
    kind = feed.get("type", "unknown")
    if kind in TARGETED_TYPES:
        return "portal:" + slug(feed["company"])
    if kind == "lever":
        host = "api.eu.lever.co" if feed.get("region") == "eu" else "api.lever.co"
        return f"lever:{host}:{feed['board']}"
    if kind in ("greenhouse", "ashby", "join"):
        return f"{kind}:{feed.get('board', '?')}"
    if kind == "smartrecruiters":
        return f"smartrecruiters:{feed.get('board', '?')}"
    if kind == "schema":
        return f"schema:{urlparse(feed.get('url', '')).hostname}"
    if kind in ("recruitee", "personio"):
        return f"{kind}:{urlparse(feed.get('url', '')).hostname}"
    return {"mbda": "mbda:de", "hensoldt": "hensoldt:all"}.get(kind, kind)


def is_complete_source(src: str) -> bool:
    return src.startswith(COMPLETE_PREFIXES) or src in ("mbda:de", "hensoldt:all")


# --- Lightweight DOM tree for career portals ---
_VOID = {"br", "hr", "img", "input", "meta", "link", "wbr", "source", "area", "base", "embed", "param", "track", "col"}


class _Node:
    def __init__(self, tag: str = "", attrs=(), parent: _Node | None = None) -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[_Node | str] = []

    def find(self, predicate) -> list[_Node]:
        if predicate(self):
            return [self]
        out: list[_Node] = []
        for c in self.children:
            if isinstance(c, _Node):
                out.extend(c.find(predicate))
        return out

    def text(self) -> str:
        if self.tag in ("script", "style"):
            return ""
        return re.sub(
            r"\s+",
            " ",
            " ".join(c.text() if isinstance(c, _Node) else c for c in self.children),
        ).strip()

    def has(self, cls: str) -> bool:
        return cls in (self.attrs.get("class") or "").split()


class Tree(HTMLParser):
    def __init__(self, raw_html: str) -> None:
        super().__init__()
        self.root = _Node()
        self.current = self.root
        self.feed(raw_html)

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.current.children.append(_Node(tag, attrs, self.current))

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node.parent and node.tag != tag:
            node = node.parent
        if node.parent:
            self.current = node.parent

    def handle_data(self, text: str) -> None:
        self.current.children.append(text)


_TOKEN = re.compile(
    r"""\s*(?:('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|([A-Za-z_$][\w$]*)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}\[\]:,]))""",
    re.S,
)
_KEY_END = re.compile(r"\s*:")


def _data_literal(value: str):
    if not isinstance(value, str) or len(value) > 2_000_000:
        raise ValueError("Eingebettete Stellenliste überschreitet Abrufgrenze")
    tokens: list[str] = []
    pos = 0
    while pos < len(value):
        match = _TOKEN.match(value, pos)
        if not match:
            if not value[pos:].strip():
                break
            raise ValueError("Öffentliche Stellenliste enthält keine reine Datenstruktur")
        string, identifier, number, punctuation = match.groups()
        if string:
            token = json.dumps(ast.literal_eval(string))
        elif identifier:
            token = (
                identifier
                if identifier in ("true", "false", "null")
                else json.dumps(identifier)
                if _KEY_END.match(value, match.end())
                else None
            )
            if token is None:
                raise ValueError("Ausführbare Ausdrücke sind keine Stellendaten")
        else:
            token = number or punctuation
        tokens.append(token)
        pos = match.end()
    return json.loads("".join(tokens))


# --- Standard ATS Feed Parsers ---
def greenhouse_jobs(feed: dict) -> list[dict]:
    board = feed["board"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise ValueError("Ungültiger Greenhouse-Board-Name")
    payload = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise RuntimeError("Greenhouse: jobs fehlt")
    out = []
    for job in payload["jobs"]:
        emp = next(
            (
                str(m.get("value") or "")
                for m in (job.get("metadata") or [])
                if isinstance(m, dict) and "employ" in str(m.get("name") or "").lower()
            ),
            "",
        )
        out.append(
            {
                "company": feed["company"],
                "title": feed_title(job.get("title", ""), emp),
                "location": (job.get("location") or {}).get("name", ""),
                "description": job.get("content", ""),
                "url": job.get("absolute_url", ""),
                "source": f"greenhouse:{board}",
                "source_id": str(job.get("id", "")),
                "verification": "feed",
            }
        )
    return out


def lever_jobs(feed: dict) -> list[dict]:
    board = feed["board"]
    host = "api.eu.lever.co" if feed.get("region") == "eu" else "api.lever.co"
    jobs: dict[str, dict] = {}
    for offset in range(0, 10000, 100):
        page = fetch_json(f"https://{host}/v0/postings/{board}?mode=json&skip={offset}&limit=100")
        if not isinstance(page, list):
            raise RuntimeError("Lever: Stellenliste fehlt")
        if page and all(str(item["id"]) in jobs for item in page):
            raise RuntimeError("Lever: Seitennavigation wiederholt sich")
        jobs.update({str(item["id"]): item for item in page})
        if len(page) < 100:
            break
    else:
        raise RuntimeError("Lever: Seitenlimit erreicht")
    result = []
    for job_id, job in jobs.items():
        categories = job.get("categories") or {}
        primary = categories.get("location") or ""
        places = [primary + (", " + job["country"] if job.get("country") and primary else "")]
        places.extend(x for x in categories.get("allLocations", []) if x != primary)
        description = [job.get("description") or job.get("descriptionPlain") or job.get("descriptionBodyPlain") or ""]
        description.extend((item.get("text") or "") + "\n" + (item.get("content") or "") for item in job.get("lists", []))
        description += [
            job.get("additional") or job.get("additionalPlain") or "",
            "Beschäftigung: " + (categories.get("commitment") or "unbekannt"),
        ]
        result.append(
            {
                "company": feed["company"],
                "title": feed_title(job["text"], categories.get("commitment", "")),
                "location": "; ".join(x for x in places if x),
                "description": "\n".join(description),
                "url": job["hostedUrl"],
                "source": f"lever:{host}:{board}",
                "source_id": job_id,
                "verification": "feed",
            }
        )
    return result


def ashby_jobs(feed: dict) -> list[dict]:
    board = feed["board"]
    payload = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise RuntimeError("Ashby: jobs fehlt")
    result = []
    for job in payload["jobs"]:
        if job.get("isListed") is False:
            continue
        places = []
        for place in [job, *(job.get("secondaryLocations") or [])]:
            address = place.get("address") or {}
            address = address.get("postalAddress") or address
            location = place.get("location") or address.get("addressLocality") or ""
            country = address.get("addressCountry") or ""
            if country and country.casefold() not in location.casefold():
                location = f"{location}, {country}" if location else country
            if location:
                places.append(location)
        employment = job.get("employmentType") or ""
        result.append(
            {
                "company": feed["company"],
                "title": feed_title(job["title"], employment),
                "location": "; ".join(dict.fromkeys(places)),
                "description": (job.get("descriptionHtml") or job.get("descriptionPlain") or "")
                + "\nBeschäftigung: "
                + (employment or "unbekannt"),
                "url": job["jobUrl"],
                "source": "ashby:" + board,
                "source_id": job["id"],
                "verification": "feed",
            }
        )
    return result


def _parse_personio_xml(page: str, feed: dict, language: str = "de") -> list[dict]:
    root = ET.fromstring(page)
    if root.tag != "workzag-jobs":
        raise RuntimeError("Personio: XML-Stellenliste fehlt")
    base = canonical_url(feed["url"]).rstrip("/")
    result = []
    for job in root.findall("position"):
        job_id, title = job.findtext("id"), job.findtext("name")
        if not job_id or not title:
            raise RuntimeError("Personio: Unvollständiger Eintrag")
        description = "\n".join(
            (x.findtext("name") or "") + "\n" + (x.findtext("value") or "")
            for x in job.findall("jobDescriptions/jobDescription")
        )
        metadata = "\n".join(
            f"{key}: {job.findtext(key)}"
            for key in ("schedule", "employmentType", "seniority", "yearsOfExperience")
            if job.findtext(key)
        )
        employment = job.findtext("employmentType") or ""
        places = [job.findtext("office") or ""] + [x.text or "" for x in job.findall("additionalOffices/office")]
        result.append(
            {
                "company": feed["company"],
                "title": feed_title(feed_title(title, employment), job.findtext("schedule") or ""),
                "location": "; ".join(dict.fromkeys(x for x in places if x)),
                "description": description + "\n" + metadata,
                "url": base + f"/job/{job_id}?language={language}",
                "source": "personio:" + (urlparse(base).hostname or ""),
                "source_id": job_id,
                "verification": "feed",
            }
        )
    return result


def _parse_personio_html(feed: dict, primary: str = "de") -> list[dict]:
    base = canonical_url(feed["url"]).rstrip("/")
    page = fetch_html(f"{base}/?language={primary}")
    root = Tree(page).root
    cards = root.find(lambda n: n.tag == "a" and n.has("job-box"))
    src = "personio:" + (urlparse(base).hostname or "")
    entries: list[dict] = []
    seen_ids: set[str] = set()
    for card in cards:
        href = card.attrs.get("href", "")
        m = re.search(r"/job/(\d+)", href)
        if not m:
            continue
        job_id = m[1]
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        t_nodes = card.find(lambda n: n.has("jb-title"))
        raw_title = t_nodes[0].text() if t_nodes else card.text()
        d_nodes = card.find(lambda n: n.has("jb-description"))
        meta_parts = (
            [c.text() for c in d_nodes[0].children if isinstance(c, _Node) and c.text()]
            if d_nodes
            else []
        )
        location = meta_parts[0] if meta_parts else ""
        emp_meta = [x for x in meta_parts[1:] if x]
        title = raw_title
        for part in emp_meta:
            title = feed_title(title, part)
        entries.append(
            {
                "job_id": job_id,
                "title": title,
                "location": location,
                "meta": "\n".join(emp_meta),
                "url": f"{base}/job/{job_id}?language={primary}",
            }
        )

    def _detail(entry: dict) -> dict:
        desc = ""
        try:
            raw = fetch_html(entry["url"])
            for attrs, txt in re.findall(r"<script([^>]*)>(.*?)</script>", raw, re.S | re.I):
                if "ld+json" in attrs.lower():
                    try:
                        val = json.loads(txt)
                    except Exception:
                        continue
                    for p in (val if isinstance(val, list) else [val]):
                        if isinstance(p, dict) and p.get("@type") == "JobPosting" and p.get("description"):
                            desc = str(p["description"])
                            break
                if desc:
                    break
            if not desc:
                droot = Tree(raw).root
                mains = droot.find(lambda n: n.tag == "main")
                desc = mains[0].text() if mains else droot.text()
        except Exception:
            desc = ""
        # Keep empty string when detail fetch fails so engine.py preserves existing DB description!
        if desc and entry["meta"]:
            desc = (desc + "\n" + entry["meta"]).strip()
        return {
            "company": feed["company"],
            "title": entry["title"],
            "location": entry["location"],
            "description": desc,
            "url": entry["url"],
            "source": src,
            "source_id": entry["job_id"],
            "verification": "feed",
        }

    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(_detail, entries))


def personio_jobs(feed: dict) -> list[dict]:
    base = canonical_url(feed["url"]).rstrip("/")
    primary = feed.get("language", "de")
    other = "en" if primary == "de" else "de"
    try:
        xml_pri = fetch_html(f"{base}/xml?language={primary}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return _parse_personio_html(feed, primary)
        raise
    with ThreadPoolExecutor(max_workers=1) as pool:
        f_alt = pool.submit(fetch_html, f"{base}/xml?language={other}")
        jobs = _parse_personio_xml(xml_pri, feed, primary)
        try:
            alternate = {j["source_id"]: j for j in _parse_personio_xml(f_alt.result(), feed, other)}
        except Exception:
            alternate = {}
    seen_ids = set()
    for job in jobs:
        seen_ids.add(job["source_id"])
        alt = alternate.get(job["source_id"])
        if alt and len(alt["description"]) > len(job["description"]) and len(job["description"]) < 500:
            job["description"], job["url"] = alt["description"], alt["url"]
    # Also include postings published exclusively in the alternate language
    for alt_id, alt_job in alternate.items():
        if alt_id not in seen_ids:
            jobs.append(alt_job)
    return jobs


def recruitee_jobs(feed: dict) -> list[dict]:
    base = canonical_url(feed["url"]).rstrip("/")
    payload = fetch_json(base + "/api/offers")
    if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
        raise RuntimeError("Recruitee: offers fehlt")
    result = []
    for job in payload["offers"]:
        locations = [
            ", ".join(str(place.get(k) or "") for k in ("city", "state_name", "country") if place.get(k))
            for place in (job.get("locations") or [])
        ]
        location = "; ".join(x for x in locations if x) or str(job.get("location") or job.get("city") or "")
        if job.get("country") and job["country"] not in location:
            location = f"{location}, {job['country']}" if location else str(job["country"])
        emp = str(job.get("employment_type_code") or job.get("employment_type") or "")
        result.append(
            {
                "company": feed["company"],
                "title": feed_title(job.get("title", ""), emp),
                "location": location,
                "description": (job.get("description") or "") + "\n" + (job.get("requirements") or ""),
                "url": job.get("careers_url") or base + "/o/" + job["slug"],
                "source": "recruitee:" + (urlparse(base).hostname or ""),
                "source_id": str(job["id"]),
                "verification": "feed",
            }
        )
    return result


def smartrecruiters_jobs(feed: dict) -> list[dict]:
    board = feed.get("board", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", board):
        raise ValueError("Ungültige SmartRecruiters-Kennung")
    base = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
    rows: list[dict] = []
    total = None
    offset = 0
    country = feed.get("country")
    for _ in range(10):
        qparams: dict[str, object] = {"limit": 100, "offset": offset}
        if country:
            qparams["country"] = country
        payload = fetch_json(base + "?" + urlencode(qparams))
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list) or type(payload.get("totalFound")) is not int:
            raise ValueError("SmartRecruiters-Snapshot unvollständig")
        if total is None:
            total = payload["totalFound"]
        if total != payload["totalFound"]:
            raise ValueError("SmartRecruiters-Trefferzahl während Paginierung geändert")
        page = payload["content"]
        rows.extend(page)
        if len(rows) >= total:
            break
        if not page:
            raise ValueError("SmartRecruiters-Paginierung vorzeitig beendet")
        offset += len(page)
    if len(rows) != total or len({r.get("id") for r in rows}) != total:
        raise ValueError("SmartRecruiters-Snapshot unvollständig/doppelt")

    def _detail(row: dict) -> dict | None:
        identity = row.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9]{1,30}", identity):
            return None
        try:
            posting = fetch_json(f"{base}/{identity}")
        except Exception:
            # Keep listing stub on transient network error so complete-feed scan doesn't trigger false closures
            pl = row.get("location") or {}
            stub_loc = pl.get("fullLocation") or ", ".join(str(pl[k]) for k in ("city", "region", "country") if pl.get(k))
            ref = row.get("ref") or f"https://jobs.smartrecruiters.com/{board}/{identity}"
            return {
                "company": feed["company"],
                "title": row.get("name", ""),
                "location": stub_loc,
                "description": "",
                "url": canonical_url(ref),
                "source": "smartrecruiters:" + board,
                "source_id": identity,
                "verification": "feed",
            }
        if not isinstance(posting, dict) or posting.get("id") != identity or posting.get("active") is not True:
            return None
        sections = (posting.get("jobAd") or {}).get("sections") or {}
        desc = "\n".join((sections.get(k) or {}).get("text", "") for k in ("jobDescription", "qualifications", "additionalInformation"))
        if len(strip_html(desc)) < 80:
            return None
        place = posting.get("location") or {}
        location = place.get("fullLocation") or ", ".join(str(place[k]) for k in ("city", "region", "country") if place.get(k))
        if place.get("country") == "de" and place.get("region") == "BY":
            location = ", ".join(x for x in (place.get("city"), "Bayern", "Germany") if x)
        employment = (posting.get("typeOfEmployment") or {}).get("label", "")
        url = canonical_url(posting.get("postingUrl", ""))
        return {
            "company": feed["company"],
            "title": feed_title(posting["name"], employment),
            "location": location,
            "description": desc + "\nEmployment: " + employment,
            "url": url,
            "source": "smartrecruiters:" + board,
            "source_id": identity,
            "verification": "feed",
        }

    with ThreadPoolExecutor(max_workers=5) as pool:
        return [item for item in pool.map(_detail, rows) if item is not None]


def _extract_next_data(raw_html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw_html, re.S)
    if not m:
        raise ValueError("JOIN: __NEXT_DATA__ fehlt")
    return json.loads(m.group(1))


def join_jobs(feed: dict) -> list[dict]:
    board = feed.get("board", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", board):
        raise ValueError("Ungültige JOIN-Kennung")
    base = f"https://join.com/companies/{board}"
    items: list[dict] = []
    seen_ids: set[str] = set()
    page = 1
    page_count = 1
    while page <= min(page_count, 10):
        u = base if page == 1 else f"{base}?page={page}"
        data = _extract_next_data(fetch_html(u))
        jobs_data = ((data.get("props") or {}).get("pageProps") or {}).get("pageData", {}).get("jobs") or {}
        page_count = int((jobs_data.get("pagination") or {}).get("pageCount") or 1)
        page_items = jobs_data.get("items") or []
        for it in page_items:
            jid = str(it.get("id") or "")
            if jid and jid not in seen_ids and it.get("idParam"):
                seen_ids.add(jid)
                items.append(it)
        if not page_items:
            break
        page += 1

    def _detail(it: dict) -> dict:
        id_param = it["idParam"]
        url = f"{base}/{id_param}"
        desc = ""
        detail_job = it
        try:
            ddata = _extract_next_data(fetch_html(url))
            j = ((ddata.get("props") or {}).get("pageProps") or {}).get("pageData", {}).get("job")
            if isinstance(j, dict) and j.get("description"):
                detail_job = j
                desc = str(j["description"])
        except Exception:
            desc = ""
        city_obj = detail_job.get("city") or {}
        country_obj = detail_job.get("country") or {}
        city_name = city_obj.get("cityName") or ""
        country_name = city_obj.get("countryName") or country_obj.get("name") or ""
        location = ", ".join(x for x in (city_name, country_name) if x)
        if detail_job.get("workplaceType") == "REMOTE" and "remote" not in location.casefold():
            location = (location + " (Remote)").strip()
        emp = (detail_job.get("employmentType") or {}).get("name") or ""
        meta = "\n".join(
            x
            for x in (
                f"Employment: {emp}" if emp else "",
                f"Workplace: {detail_job.get('workplaceType')}" if detail_job.get("workplaceType") else "",
            )
            if x
        )
        return {
            "company": feed["company"],
            "title": feed_title(detail_job.get("title") or it.get("title") or "", emp),
            "location": location,
            "description": (desc + "\n" + meta).strip(),
            "url": url,
            "source": "join:" + board,
            "source_id": str(detail_job.get("id") or it["id"]),
            "verification": "feed",
        }

    if not items:
        return []
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(_detail, items))


def schema_jobs(feed: dict) -> list[dict]:
    url = canonical_url(feed["url"])
    host = urlparse(url).hostname or ""
    payload = fetch_json(url)
    if not isinstance(payload, dict) or payload.get("@type") != "DataFeed" or type(payload.get("numberOfItems")) is not int:
        raise ValueError("JobPosting DataFeed fehlt")
    elements = payload.get("dataFeedElement")
    if not isinstance(elements, list) or len(elements) != payload["numberOfItems"]:
        raise ValueError("JobPosting Snapshot unvollständig")
    result = []
    seen_ids: set[str] = set()
    for element in elements:
        item = (element or {}).get("item") or {}
        identity = str((item.get("identifier") or {}).get("value", ""))
        if not identity.isdigit() or identity in seen_ids:
            raise ValueError("Ungültige oder doppelte JobPosting-ID")
        seen_ids.add(identity)
        posting_url = canonical_url(item.get("url", ""))
        desc = "\n".join(strip_html(item.get(k, "")) for k in ("description", "responsibilities", "qualifications"))
        places = item.get("jobLocation")
        places = [places] if isinstance(places, dict) else (places or [])
        locations = []
        for place in places:
            addr = place.get("address") or {}
            country = addr.get("addressCountry", "")
            if isinstance(country, dict):
                country = country.get("name", "")
            region = addr.get("addressRegion", "")
            if region in ("BY", "DE-BY") and str(country).lower() in ("de", "germany", "deutschland"):
                region = "Bayern"
            city = addr.get("addressLocality", "")
            if city:
                locations.append(", ".join(str(v) for v in (city, region, country) if v))
        employment = item.get("employmentType", "")
        if isinstance(employment, list):
            employment = " / ".join(employment)
        if not item.get("title") or len(desc.strip()) < 100 or not locations:
            raise ValueError("JobPosting unvollständig")
        result.append(
            {
                "company": feed["company"],
                "title": feed_title(item["title"], employment),
                "location": "; ".join(locations),
                "description": desc + "\nEmployment: " + employment,
                "url": posting_url,
                "source": f"schema:{host}",
                "source_id": identity,
                "verification": "feed",
            }
        )
    return result


# --- MBDA & HENSOLDT Complete Feeds + Bounded Detail Enrichment ---
class _MbdaJobParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jobs: list[dict] = []
        self.in_row = False
        self.cell = -1
        self.field = ""
        self.current: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        attr = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.cell = -1
            self.current = {}
        elif self.in_row and tag == "td":
            self.cell += 1
            self.field = "location" if self.cell == 1 else ""
        elif self.in_row and self.cell == 0 and tag == "a" and "job" in (attr.get("class") or "").split():
            self.current["url"] = attr.get("href") or ""
            self.field = "title"
        elif self.in_row and self.cell == 0 and tag == "span":
            cls = attr.get("class") or ""
            if cls in ("focus", "reference-id"):
                self.field = "focus" if cls == "focus" else "source_id"

    def handle_data(self, data: str) -> None:
        if self.in_row and self.field:
            self.current[self.field] = self.current.get(self.field, "") + data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "span", "td"):
            self.field = ""
        if tag == "tr" and self.in_row:
            title = re.sub(r"\s+", " ", self.current.get("title", "")).strip()
            focus = re.sub(r"\s+", " ", self.current.get("focus", "")).strip()
            location = re.sub(r"\s+", " ", self.current.get("location", "")).strip()
            url = self.current.get("url", "")
            if title and url.startswith("https://www.mbda-careers.de/stellen/mbda/"):
                self.jobs.append(
                    {
                        "company": "MBDA Deutschland",
                        "title": f"{title} – {focus}" if focus else title,
                        "location": location,
                        "description": "",
                        "url": url,
                        "source": "mbda:de",
                        "source_id": self.current.get("source_id", "").strip(),
                        "verification": "Karriereportal",
                    }
                )
            self.in_row = False


def mbda_list_jobs() -> list[dict]:
    parser = _MbdaJobParser()
    parser.feed(fetch_html("https://www.mbda-careers.de/stellen/mbda/"))
    if not parser.jobs:
        raise RuntimeError("MBDA: Keine Stellen gefunden")
    unique = {canonical_url(j["url"]): j for j in parser.jobs}
    return list(unique.values())


def hensoldt_list_jobs() -> list[dict]:
    jobs: dict[str, dict] = {}
    offset = 0
    for _ in range(80):
        page = fetch_html(f"https://jobs.hensoldt.net/search/?q=&startrow={offset}")
        total_match = re.search(r"of\s+([\d,]+)\s+Jobs", page)
        if not total_match:
            raise RuntimeError("HENSOLDT: Trefferzahl fehlt")
        total = int(total_match[1].replace(",", ""))
        places = dict(re.findall(r'id="job-(\d+)-desktop-section-multilocation-value"[^>]*>(.*?)</div>', page, re.S))
        page_items: dict[str, dict] = {}
        for path, label in re.findall(r'<a\b[^>]*href="(/job/[^\"]+)"[^>]*>(.*?)</a>', page, re.S):
            m = re.search(r"/(\d+)/?$", path)
            if not m:
                continue
            jid = m[1]
            raw_loc = strip_html(places.get(jid, ""))
            dedup_loc = " • ".join(dict.fromkeys(p.strip() for p in raw_loc.split("•") if p.strip()))
            page_items[jid] = {
                "company": "HENSOLDT",
                "title": strip_html(label),
                "location": dedup_loc,
                "description": "",
                "url": urljoin("https://jobs.hensoldt.net", html.unescape(path)),
                "source": "hensoldt:all",
                "source_id": jid,
                "verification": "Karriereportal",
            }
        before = len(jobs)
        jobs.update(page_items)
        if len(jobs) >= total:
            return list(jobs.values())
        if len(jobs) == before:
            raise RuntimeError("HENSOLDT: Seitennavigation unvollständig")
        offset += len(page_items)
    raise RuntimeError("HENSOLDT: Seitenlimit erreicht")


def _listing_detail_parse(raw_html: str, item: dict, source: str) -> dict:
    root = Tree(raw_html).root
    headings = root.find(lambda n: n.tag == "h1")
    if not headings:
        raise ValueError("Originaltitel fehlt")
    if source == "hensoldt:all":
        shells = root.find(lambda n: n.has("jobDisplayShell"))
        bodies = root.find(lambda n: n.has("jobdescription"))
        places = shells[0].find(lambda n: n.tag == "meta" and n.attrs.get("itemprop") == "streetAddress") if shells else []
        if not bodies or not places:
            raise ValueError("HENSOLDT-Stellenbereich unvollständig")
        title = headings[0].text()
        body = "\n".join(n.text() if hasattr(n, "text") else str(n) for n in bodies[0].children)
        location = "; ".join(dict.fromkeys(n.attrs.get("content", "").strip() for n in places if n.attrs.get("content", "").strip()))
    else:
        fields = {
            n.attrs["name"]: n.attrs.get("content", "")
            for n in root.find(lambda n: n.tag == "meta" and n.attrs.get("name", "").startswith("job_"))
        }
        sections = root.find(lambda n: n.tag == "section" and n.has("content"))
        section_texts = [n.text() for n in sections if not re.match(r"IHR NÄCHSTER SCHRITT|DEINE BEWERBUNG|IHRE BEWERBUNG", n.text(), re.I)]
        mains = root.find(lambda n: n.has("grid-cell--main"))
        intro = [n.text() for n in mains[0].children if hasattr(n, "tag") and n.tag == "p"] if mains else []
        body = "\n".join(intro + section_texts) + "\nZielgruppe: " + fields.get("job_target", "")
        title = fields.get("job_titlemain", item["title"]) + (
            " – " + fields["job_titlesub"] if fields.get("job_titlesub") else ""
        )
        employment = fields.get("job_target", "")
        header_probe = " ".join(
            fields.get(k, "") for k in ("job_teaser", "job_titlemain", "job_titlesub", "job_target")
        )
        if re.search(r"DEIN AUSBILDUNGSBERUF|Ausbildung zum|Ausbildungsstart", header_probe, re.I):
            employment = "Ausbildung"
        if excluded_employment(employment):
            title += " – " + employment
        state_val = fields.get("job_state") or fields.get("job_staate") or ""
        location = ", ".join(v for v in (fields.get("job_location") or fields.get("job_city"), state_val) if v)
    if len(body.strip()) < 200 or not location:
        raise ValueError("Originaltext oder Standort unvollständig")
    return {
        **item,
        "title": title,
        "location": location,
        "description": body[:30000],
        "verification": "Originalanzeige direkt gelesen",
    }


def enrich_complete_listing(con: sqlite3.Connection, db_lock: threading.Lock, jobs: list[dict], source: str, limit: int = 35) -> list[dict]:
    stamp = now_iso()
    with db_lock:
        cache = {
            r["url"]: dict(r)
            for r in con.execute("SELECT * FROM portal_job_cache WHERE source = ?", (source,))
        }
    candidates = []
    cache_upserts = []
    for item in jobs:
        url = canonical_url(item["url"])
        item = dict(item, url=url)
        cached = cache.get(url)
        payload = json.loads(cached["payload"]) if cached and cached["payload"] else {}
        eff_title = payload.get("title") or item["title"]
        eff_loc = payload.get("location") or item["location"]
        if not hard_exclusion(eff_title, eff_loc) and (TECHNICAL.search(eff_title) or relevance(eff_title, "")[0] >= 6):
            cache_upserts.append((source, url, json.dumps(item, ensure_ascii=False), stamp))
            if not cached or cached["next_check"] <= stamp:
                is_sen = bool(SENIOR.search(eff_title))
                candidates.append((bool(payload), is_sen, cached["checked"] if cached else "", item))

    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    pending = [t[3] for t in candidates[:limit]]

    def _fetch(it: dict):
        try:
            return it, _listing_detail_parse(portal_request(it["url"]), it, source), ""
        except Exception as exc:
            return it, None, str(exc)[:300]

    fetched = []
    if pending:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fetched = list(pool.map(_fetch, pending))

    with db_lock, con:
        for src, url, meta, ls in cache_upserts:
            con.execute(
                "INSERT INTO portal_job_cache(source, url, metadata, last_seen) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source, url) DO UPDATE SET metadata=excluded.metadata, last_seen=excluded.last_seen",
                (src, url, meta, ls),
            )
        for it, payload, err in fetched:
            nxt = (datetime.fromisoformat(stamp) + timedelta(days=1 if err else 7)).isoformat(timespec="seconds")
            if err:
                con.execute("UPDATE portal_job_cache SET error=?, next_check=? WHERE source=? AND url=?", (err, nxt, source, it["url"]))
            else:
                con.execute(
                    "UPDATE portal_job_cache SET payload=?, state='open', checked=?, next_check=?, error='' WHERE source=? AND url=?",
                    (json.dumps(payload, ensure_ascii=False), stamp, nxt, source, it["url"]),
                )
        fresh_cache = {
            r["url"]: r["payload"]
            for r in con.execute("SELECT url, payload FROM portal_job_cache WHERE source=? AND payload!=''", (source,))
        }

    out = []
    for item in jobs:
        raw_p = fresh_cache.get(canonical_url(item["url"]))
        if raw_p:
            p = json.loads(raw_p)
            item = {**item, "title": p["title"], "location": p["location"], "description": p["description"], "verification": p["verification"]}
        out.append(item)
    return out


# --- Employer HTML Portals, Avature, Workday, Tech Portals ---
EMPLOYER_HOSTS = {
    "successfactors": "jobs.knds.de",
    "sf_kuka": "jobs.kuka.com",
    "dlr": "jobs.dlr.de",
    "rexx": "join.asap.de",
    "helsing": "helsing.ai",
    "blackned": "jobs.blackned.de",
    "softgarden": "iisfraunhofer.softgarden.io",
    "waymo": "careers.withwaymo.com",
    "pinpoint": "careers.osapiens.com",
    "neura": "jobs.neura-robotics.com",
    "swm": "www.swm.de",
    "rohde": "www.rohde-schwarz.com",
    "tq": "www.tq-group.com",
    "breezy": "eigenblue.breezy.hr",
    "arqus": "www.arqus.space",
    "orora": "ororatech.com",
    "diehl": "www.diehl.com",
    "blickfeld": "www.blickfeld.com",
    "deepscenario": "www.deepscenario.com",
    "iabg": "jobboerse.iabg.de",
    "festo": "jobs.festo.com",
    "beckhoff": "www.beckhoff.com",
    "grob": "karriere.grobgroup.com",
    "kaercher": "careers.kaercher.com",
}


def _beckhoff_list_url(offset: int = 0) -> str:
    return "https://webapp-api.beckhoff-web-services.com/listing/listing.proxy.php?" + urlencode(
        {
            "action": "itemList",
            "cmsLanguage": "DE-DE",
            "cmsNativeLanguage": "DE-DE",
            "template": "jobs",
            "listLimit": 100,
            "listOffset": offset,
            "index": 0,
            "searchValueList[cms_templatename][]": "DataAssets.stellenangebot",
        }
    )


def _swm_jobs(root: _Node, feed: dict) -> list[dict]:
    components = root.find(lambda n: n.tag == "jobboerse-filter" and ":job-items" in n.attrs)
    if len(components) != 1:
        raise ValueError("SWM-Stellenliste nicht erkannt")
    records = _data_literal(components[0].attrs[":job-items"])
    jobs = []
    for record in records:
        if record.get("status") != "Published":
            continue
        opening = record.get("opening", {})
        if opening.get("generalApplication"):
            continue
        places = [
            f.get("valueTranslations", {}).get("de", f.get("value", ""))
            for f in record.get("lovs", [])
            if f.get("nameTranslations", {}).get("de", f.get("name")) == "Ort"
        ]
        location = "; ".join(places)
        if not location:
            continue
        description = "\n".join(
            f.get("label", "") + "\n" + strip_html(f.get("value", ""))
            for f in record.get("customFields", [])
            if f.get("label") not in ("Video/Podcast", "Kontakt")
        )
        employment = opening.get("scheduleTypeDetails", {}).get("translations", {}).get("de", "")
        title = record["jobAdTitle"] + (" – " + employment if excluded_employment(employment) else "")
        description += "\nBeschäftigungsart: " + employment
        if len(description) >= 100:
            jobs.append(
                {
                    "company": feed["company"],
                    "title": title,
                    "location": location + ", Deutschland",
                    "description": description,
                    "url": feed["url"].split("?")[0] + "?id=" + str(int(record["id"])),
                    "verification": "SWM-Stellenliste direkt gelesen",
                }
            )
    return jobs


def _link_title(link: _Node) -> str:
    headings = link.find(lambda n: n.tag in ("h1", "h2", "h3", "h4"))
    if headings:
        return headings[0].text()
    if link.text() and link.text() not in ("Apply", "Zum Angebot", "Learn more"):
        return link.text()
    parent = link.parent
    for _ in range(4):
        if not parent:
            break
        headings = parent.find(lambda n: n.tag in ("h1", "h2", "h3", "h4", "h5", "h6"))
        if len(headings) == 1:
            return headings[0].text()
        parent = parent.parent
    return ""


def _employer_search_page(raw_html: str, feed: dict, offset: int = 0) -> tuple[list[dict], int]:
    parser = feed["parser"]
    feed_host = urlparse(feed.get("url", "")).hostname
    host = feed_host or EMPLOYER_HOSTS[parser]
    root = Tree(raw_html).root
    jobs: list[dict] = []
    next_offset = 0

    if parser == "beckhoff":
        val = json.loads(raw_html)
        for chunk in re.split(r"<tr\b[^>]*>", val.get("data", ""), flags=re.I)[1:]:
            rnode = Tree(chunk.split("</tr>")[0]).root
            cells = rnode.find(lambda n: n.tag == "td")
            if not cells:
                continue
            links = rnode.find(lambda n: n.tag == "a" and "/stellenangebote/" in n.attrs.get("href", ""))
            if len(cells) >= 4 and len(links) == 1:
                url = canonical_url(urljoin(feed["url"], links[0].attrs["href"]))
                jobs.append({"title": cells[0].text(), "location": cells[2].text(), "url": url})
        total = int(val.get("total", 0))
        next_offset = offset + len(jobs) if jobs and offset + len(jobs) < total else 0
    elif parser == "grob":
        for row in root.find(lambda n: n.has("gr-jobs-element") and n.attrs.get("data-jobid", "").isdigit()):
            links = row.find(lambda n: n.tag == "a" and re.fullmatch(r"/stellenangebote-deutschland/job/\d+", n.attrs.get("href", "")))
            titles = row.find(lambda n: n.has("gr-jobs-titel"))
            places = row.find(lambda n: n.has("location"))
            if len(links) == 1 and len(titles) == 1 and len(places) == 1:
                jobs.append({"title": titles[0].text(), "location": places[0].text() + ", Deutschland", "url": urljoin(feed["url"], links[0].attrs["href"])})
    elif parser in ("festo", "kaercher"):
        empty = root.find(lambda n: n.attrs.get("id") == "noresults")
        if empty and re.search(r"no .*?(?:jobs|positions|results)|keine .*?(?:Stellen|Ergebnisse)|keine passenden", empty[0].text(), re.I):
            return [], 0
        labels = root.find(lambda n: n.has("pagination"))
        match = re.search(r"\b(?:of|von)\s+([\d.,]+)\b", labels[0].text(), re.I) if labels else None
        total = int(match[1].replace(",", "").replace(".", "")) if match else 0
        for row in root.find(lambda n: n.tag == "tr" and n.has("data-row")):
            links = row.find(lambda n: n.tag == "a" and n.has("jobTitle-link"))
            places = row.find(lambda n: n.has("colLocation"))
            if links and places:
                jobs.append({"title": links[0].text(), "location": places[0].text(), "url": urljoin(feed["url"], links[0].attrs["href"])})
        next_offset = offset + len(jobs) if jobs and offset + len(jobs) < total else 0
    elif parser == "iabg":
        grouped: dict[str, list[str]] = {}
        for link in root.find(lambda n: n.tag == "a" and "showJobOfferDetail.do?" in n.attrs.get("href", "")):
            u = urljoin(feed["url"], link.attrs["href"])
            grouped.setdefault(u, []).append(link.text())
        for u, fields in grouped.items():
            if len(fields) >= 3:
                jobs.append({"title": fields[0], "location": fields[2] + ", Deutschland", "url": u})
    elif parser == "swm":
        jobs = [{**j, "_embedded_job": j} for j in _swm_jobs(root, feed)]
    elif parser in ("rohde", "tq", "breezy", "arqus", "orora", "diehl", "blickfeld", "deepscenario", "innok", "dallmeier"):
        patterns = {
            "rohde": r"/de/karriere/stellenangebote/[^/]+_251563-\d+\.html",
            "tq": r"/de/unternehmen/karriere/stellenangebot/[^/]+/",
            "breezy": r"/p/[^/]+",
            "arqus": r"/jobs-[a-z0-9-]+",
            "orora": r"/company/careers/\d+",
            "diehl": r"/career/de/jobs-bewerbung/stellenboerse/vacancies/[^/]+",
            "blickfeld": r"/careers/[^/]+/",
            "deepscenario": r"/careers/[^/]+",
            "innok": r"/karriere/job/[^/]+/?",
            "dallmeier": r"/de/ueber-uns/karriere/.+",
        }
        for link in root.find(lambda n: n.tag == "a" and n.attrs.get("href")):
            u = urljoin(feed["url"], link.attrs["href"])
            parsed = urlparse(u)
            if parsed.hostname != host or not re.fullmatch(patterns[parser], parsed.path):
                continue
            if parser == "arqus" and re.search("unsolicited|internship", parsed.path):
                continue
            t = _link_title(link) or (parsed.path.lstrip("/").replace("-", " ") if parser == "arqus" else "")
            if t:
                jobs.append({"title": t, "location": "", "url": u})
        if parser == "diehl":
            # Extract city directly from each Diehl vacancy card
            for li in root.find(lambda n: n.tag == "li" and any(c.startswith("is-") for c in (n.attrs.get("class") or "").split())):
                a_nodes = li.find(lambda n: n.tag == "a" and n.attrs.get("href"))
                if not a_nodes:
                    continue
                u_card = canonical_url(urljoin(feed["url"], a_nodes[0].attrs["href"]))
                city_nodes = li.find(lambda n: n.has("city"))
                city_txt = city_nodes[0].text() if city_nodes else ""
                if city_txt:
                    for item in jobs:
                        if canonical_url(item["url"]) == u_card:
                            item["location"] = city_txt
            pages = []
            for node in root.find(lambda n: n.tag == "a" and n.has("pagination-link") and n.attrs.get("hx-get")):
                p = parse_qs(urlparse(node.attrs["hx-get"]).query).get("p", ["0"])[0]
                if p.isdigit() and int(p) > max(offset, 1):
                    pages.append(int(p))
            next_offset = min(pages) if pages else 0
    elif parser == "pinpoint":
        val = json.loads(raw_html)
        for rec in val.get("data", []):
            u = rec.get("url", "")
            if urlparse(u).hostname == host:
                jobs.append({"title": feed_title(rec["title"], rec.get("employment_type_text", "")), "location": "", "url": u})
    elif parser == "neura":
        for link in root.find(lambda n: n.tag == "a" and "/offer/" in n.attrs.get("href", "")):
            u = urljoin(feed["url"], link.attrs["href"])
            if urlparse(u).hostname == host and re.fullmatch(r"/offer/[^/]+/[a-f0-9-]{36}", urlparse(u).path):
                jobs.append({"title": link.text().split("Neura Robotics")[0].strip(), "location": "", "url": u})
    elif parser in ("successfactors", "sf_kuka"):
        empty = root.find(lambda n: n.attrs.get("id") == "noresults")
        if empty and re.search(r"keine passenden Stellen offen|no .*?(?:jobs|positions|results)|keine .*?(?:Stellen|Ergebnisse)", empty[0].text(), re.I):
            return [], 0
        tot_m = re.search(r"\bvon\s+([\d.]+)\s+Stellen|\bof\s+([\d,]+)\s+(?:Jobs|results)|\b(\d+)\s+Stellenangebot\s+wird angezeigt", raw_html, re.I)
        total = int(next(v for v in tot_m.groups() if v).replace(".", "").replace(",", "")) if tot_m else 0
        tiles = root.find(lambda n: n.tag == "li" and n.has("job-tile"))
        for node in tiles:
            links = node.find(lambda n: n.tag == "a" and n.has("jobTitle-link"))
            if links:
                jobs.append({"title": links[0].text(), "location": "", "url": urljoin(feed["url"], links[0].attrs["href"])})
        if not tiles:
            seen_u: set[str] = set()
            for row in root.find(lambda n: n.tag == "tr" and n.has("data-row")):
                links = row.find(lambda n: n.tag == "a" and n.has("jobTitle-link"))
                places = row.find(lambda n: n.has("colLocation") or n.has("jobLocation"))
                if links:
                    u = urljoin(feed["url"], links[0].attrs["href"])
                    if u not in seen_u:
                        seen_u.add(u)
                        jobs.append({"title": links[0].text(), "location": places[0].text() if places else "", "url": u})
        next_offset = offset + len(jobs) if jobs and offset + len(jobs) < total else 0
    elif parser == "dlr":
        for link in root.find(lambda n: n.tag == "a" and "/job/" in n.attrs.get("href", "") and n.text()):
            jobs.append({"title": link.text(), "location": "", "url": urljoin(feed["url"], link.attrs["href"])})
    elif parser == "waymo":
        for link in root.find(lambda n: n.tag == "a" and n.attrs.get("id", "").startswith("link_job_title")):
            u = urljoin(feed["url"], link.attrs.get("href", ""))
            if urlparse(u).hostname == host and urlparse(u).path.startswith("/jobs/") and urlparse(u).path != "/jobs/search":
                jobs.append({"title": link.text(), "location": "", "url": u})
    elif parser == "rexx":
        for node in root.find(lambda n: n.has("joboffer_inner")):
            links = node.find(lambda n: n.tag == "a" and re.search(r"-j\d+\.html$", n.attrs.get("href", "")))
            locs = node.find(lambda n: n.has("joboffer_informations"))
            if links:
                jobs.append({"title": links[0].text(), "location": locs[0].text() if locs else "", "url": urljoin(feed["url"], links[0].attrs["href"])})
    elif parser == "helsing":
        for link in root.find(lambda n: n.tag == "a" and re.fullmatch(r"/jobs/\d+", n.attrs.get("href", ""))):
            fields = {n.attrs["data-label"]: n.text() for n in link.find(lambda n: "data-label" in n.attrs)}
            if fields.get("Position"):
                jobs.append({"title": feed_title(fields["Position"], fields.get("Time", "")), "location": fields.get("Location", ""), "url": urljoin(feed["url"], link.attrs["href"])})
    elif parser == "softgarden":
        for node in root.find(lambda n: n.has("matchElement") and re.fullmatch(r"job_id_\d+", n.attrs.get("id", ""))):
            links = node.find(lambda n: n.tag == "a" and re.match(r"(?:\.\./|/)?job/\d+/", n.attrs.get("href", "")))
            places = node.find(lambda n: n.has("location"))
            if links:
                jobs.append({"title": links[0].text(), "location": places[0].text() if places else "", "url": urljoin(feed["url"], links[0].attrs["href"])})
    elif parser == "blackned":
        for link in root.find(lambda n: n.tag == "a" and n.attrs.get("data-guide-id") == "joblist-card"):
            headings = link.find(lambda n: n.tag == "h2")
            if headings and re.fullmatch(r"/de\?id=[a-f0-9]+", link.attrs.get("href", "")):
                jobs.append({"title": headings[0].text(), "location": "", "description": link.text(), "url": urljoin(feed["url"], link.attrs["href"])})
    elif parser == "krones":
        seen_k: set[str] = set()
        for link in root.find(lambda n: n.tag == "a" and re.fullmatch(r"/de/karriere/stellenausschreibung/\?job=\d+", n.attrs.get("href", ""))):
            u = urljoin(feed["url"], link.attrs["href"])
            if u not in seen_k:
                seen_k.add(u)
                jobs.append({"title": link.text() or f"Krones {u.split('=')[-1]}", "location": "", "url": u})
    elif parser == "reinhausen":
        for row in root.find(lambda n: n.tag == "tr" and n.attrs.get("data-href", "").startswith("https://career.reinhausen.com/")):
            tds = row.find(lambda n: n.tag == "td")
            t_node = next((td for td in tds if "data-title" in td.attrs), None)
            c_node = next((td for td in tds if "data-country" in td.attrs), None)
            p_node = next((td for td in tds if "data-place" in td.attrs), None)
            country = (c_node.attrs.get("data-country") or c_node.text()) if c_node else ""
            if country and country not in ("DE", "Germany", "Deutschland"):
                continue
            title = html.unescape((t_node.text() if t_node else "").strip())
            place = html.unescape((p_node.attrs.get("data-place") or p_node.text()) if p_node else "") or "Regensburg, Deutschland"
            if (
                title
                and not excluded_employment(title)
                and re.search(r"software|entwickl|engineer|ingenieur|embedded|robot|ai\b|\bki\b|data|sensor|automati|control|system|test|informatik|digital|cloud|devops|cyber", title, re.I)
            ):
                jobs.append({"title": title, "location": place, "url": row.attrs["data-href"]})
    elif parser == "bmw":
        for link in root.find(lambda n: n.tag == "a" and n.has("grp-jobfinder__link-jobdescription")):
            refs = link.find(lambda n: n.has("grp-jobfinder-cell-refno"))
            if not refs:
                continue
            attrs = refs[0].attrs
            title = html.unescape(attrs.get("data-job-title") or "").strip()
            loc = html.unescape(attrs.get("data-job-location") or "").strip()
            jtype = attrs.get("data-job-type") or ""
            if not title or excluded_employment(title) or re.search(r"praktikum|abschlussarbeit|werkstud|ausbildung|schüler", jtype, re.I):
                continue
            if location_tier(loc) != 1 and not re.search(r"München|Munich|Unterschleißheim|Regensburg|Ingolstadt|Landshut|Dingolfing", loc, re.I):
                continue
            if not re.search(r"software|robot|\bai\b|\bki\b|machine|learning|vision|perception|llm|rag|agent|backend|fullstack|java|c#|autonomous|python|embedded|sensor|cloud|entwickl|data|simulation|system|engineer|ingenieur|\bit\b|test", title, re.I):
                continue
            jobs.append({"title": title, "location": loc, "url": urljoin(feed["url"], link.attrs.get("href", ""))})

    allowed_hosts = {host, "career.reinhausen.com"} if parser == "reinhausen" else {host}
    return [
        {**item, "url": canonical_url(item["url"]), "company": feed["company"]}
        for item in jobs
        if urlparse(item["url"]).hostname in allowed_hosts
    ], next_offset


def _employer_detail(raw_html: str, metadata: dict, parser: str) -> dict | None:
    root = Tree(raw_html).root
    if parser in ("dlr", "successfactors", "sf_kuka", "festo", "kaercher"):
        for shell in root.find(lambda n: n.has("jobDisplayShell")):
            notices = shell.find(
                lambda n: n.tag == "p"
                and re.fullmatch(
                    r"(?:Sorry, this position has been filled\.|Sie können diese Stelle nicht anzeigen, da sie derzeit nicht verfügbar ist\.)",
                    n.text(),
                    re.I,
                )
            )
            if notices and not shell.find(lambda n: n.has("jobdescription") and n.text()):
                return None

    if parser == "diehl":
        h1_texts = " ".join(n.text() for n in root.find(lambda n: n.tag == "h1"))
        if re.search(r"Inaktive Stellenanzeige|nicht mehr aktiv", h1_texts, re.I):
            return None

    if parser in ("successfactors", "sf_kuka", "festo", "kaercher"):
        bodies = root.find(lambda n: n.has("jobdescription"))
        headings = root.find(lambda n: n.tag == "h1")
        places = root.find(lambda n: n.tag == "meta" and n.attrs.get("itemprop") == "streetAddress")
        inline = root.find(
            lambda n: n.attrs.get("id") == "job-location"
            or n.has("jobGeoLocation")
            or n.attrs.get("data-careersite-propertyid") == "location"
        )
        if not bodies or not headings or not (places or inline):
            raise ValueError("SuccessFactors-Anzeige unvollständig")
        loc = places[0].attrs.get("content", "") if places else inline[0].text()
        title_txt = next((h.text() for h in reversed(headings) if h.text()), headings[0].text())
        return {**metadata, "title": title_txt, "description": bodies[0].text(), "location": loc, "verification": "SuccessFactors-Originalanzeige gelesen"}

    if parser == "dlr":
        tokens = root.find(lambda n: n.has("joblayouttoken"))
        if not tokens:
            raise ValueError("DLR-Originalanzeige fehlt")
        fields = {}
        for tok in tokens:
            lbls = tok.find(lambda n: n.has("joblayouttoken-label"))
            if lbls:
                fields[lbls[0].text().rstrip(":").strip()] = tok.text()[len(lbls[0].text()):].strip()
        loc = fields.get("Arbeitsort", "")
        desc = "\n".join(n.text() for n in tokens)
        if not loc or len(desc) < 200:
            raise ValueError("DLR-Standort/Text unvollständig")
        return {**metadata, "title": tokens[0].text(), "location": loc + ", Deutschland", "description": desc, "verification": "DLR-Originalanzeige gelesen"}

    if parser == "grob":
        secs = root.find(lambda n: n.has("gr-job-detail"))
        if len(secs) != 1:
            raise ValueError("GROB-Detail fehlt")
        sec = secs[0]
        titles = sec.find(lambda n: n.tag == "h1" and n.has("job-title"))
        icons = sec.find(lambda n: n.has("gr-job-icons"))
        fields = [n.text() for n in icons[0].children if hasattr(n, "tag") and n.tag == "div"] if len(icons) == 1 else []
        body = sec.text().split("Ihr Ansprechpartner")[0]
        if not titles or len(fields) < 2:
            raise ValueError("GROB-Felder fehlen")
        return {**metadata, "title": feed_title(titles[0].text(), fields[0]), "location": fields[1] + ", Deutschland", "description": body, "verification": "GROB-Originalanzeige gelesen"}

    if parser == "iabg":
        tables = root.find(lambda n: n.tag == "table" and n.has("mainTable"))
        if len(tables) != 1:
            raise ValueError("IABG-Detail fehlt")
        body = tables[0].text()
        m = re.search(r"Standort\s*:\s*(.*?)\s*Beschreibung der Stelle\s*:", body)
        if not m or len(body) < 200:
            raise ValueError("IABG-Text unvollständig")
        return {**metadata, "location": m[1] + ", Deutschland", "description": body.split("Tweet")[0], "verification": "IABG-Originalanzeige gelesen"}

    if parser == "deepscenario":
        secs = root.find(lambda n: n.tag == "section" and n.has("career"))
        if len(secs) != 1:
            raise ValueError("DeepScenario-Detail fehlt")
        sec = secs[0]
        titles = sec.find(lambda n: n.tag == "h1" and n.has("career__title"))
        info = sec.find(lambda n: n.has("career__info"))
        body = sec.text().split("Interested?")[0]
        if not titles or not info or len(body) < 200:
            raise ValueError("DeepScenario unvollständig")
        place = re.split(r"Working Student|Full.?time|Part.?time|Internship", info[0].text(), flags=re.I)[0].strip()
        emp = re.search(r"Working Student|Full.?time|Part.?time|Internship", info[0].text(), re.I)
        return {**metadata, "title": feed_title(titles[0].text(), emp[0] if emp else ""), "location": place, "description": body, "verification": "DeepScenario-Originalanzeige gelesen"}

    if parser == "softgarden":
        titles = root.find(lambda n: n.tag == "h1" and n.attrs.get("id") == "main-title")
        places = root.find(lambda n: n.tag == "li" and n.has("job-location"))
        contracts = root.find(lambda n: n.tag == "li" and n.has("job-contract-type"))
        secs = root.find(lambda n: n.tag == "section" and n.has("section-content"))
        body = "\n".join(n.text() for n in secs if not n.find(lambda c: c.tag == "form"))
        if titles and places and len(body) >= 250:
            emp = contracts[0].text() if contracts else ""
            return {**metadata, "title": feed_title(titles[0].text(), emp), "location": places[0].text(), "description": body, "verification": "Softgarden-Originalanzeige gelesen"}

    if parser == "breezy":
        bodies = root.find(lambda n: n.has("description"))
        places = root.find(lambda n: n.has("location"))
        titles = root.find(lambda n: n.tag == "h1")
        if not bodies or not places or not titles:
            raise ValueError("Breezy-Detail unvollständig")
        return {**metadata, "title": titles[0].text(), "location": places[0].text(), "description": bodies[0].text(), "verification": "Breezy-Originalanzeige gelesen"}

    # Try JSON-LD JobPosting
    for attrs, raw in re.findall(r"<script\b([^>]*)>(.*?)</script>", raw_html, re.S | re.I):
        if "application/ld+json" not in attrs.lower():
            continue
        try:
            val = json.loads(raw)
        except ValueError:
            if parser == "tq":
                field = re.search(r'"description"\s*:\s*"(.*?)"\s*,\s*"identifier"', raw, re.S)
                if not field:
                    continue
                try:
                    val = json.loads(raw[: field.start(1) - 1] + json.dumps(field[1]) + raw[field.end(1) + 1 :])
                except ValueError:
                    continue
            else:
                continue
        records = val if isinstance(val, list) else (val.get("@graph", [val]) if isinstance(val, dict) else [])
        for posting in records:
            if not isinstance(posting, dict) or posting.get("@type") != "JobPosting":
                continue
            desc = " ".join(strip_html(posting.get(k, "")) for k in ("description", "responsibilities", "qualifications"))
            places = posting.get("jobLocation", [])
            if isinstance(places, dict):
                places = [places]
            locs = []
            for pl in places:
                addr = (pl or {}).get("address") or {}
                country = addr.get("addressCountry", "")
                if isinstance(country, dict):
                    country = country.get("name", "")
                region = addr.get("addressRegion", "")
                if region in ("DE-BY", "BY"):
                    region = "Bayern"
                locs.append(", ".join(str(v) for v in (addr.get("addressLocality"), region, country) if v))
            loc = "; ".join(v for v in locs if v) or metadata.get("location", "")
            if not loc and posting.get("jobLocationType") == "TELECOMMUTE":
                loc = "Remote"
            emp = posting.get("employmentType", "")
            if isinstance(emp, list):
                emp = " / ".join(emp)
            if posting.get("title") and len(desc.strip()) >= 100 and loc:
                return {**metadata, "title": feed_title(posting["title"].strip(), emp), "location": loc, "description": desc + " Employment: " + str(emp), "verification": "JSON-LD JobPosting gelesen"}

    # Fallback main section extraction for arqus/orora/tq/diehl/blickfeld/innok/dallmeier/bmw
    if parser in ("arqus", "orora", "tq", "diehl", "blickfeld", "innok", "dallmeier", "bmw"):
        mains = root.find(lambda n: n.tag == "main")
        body = mains[0].text() if mains else root.text()
        scope = mains[0] if mains else root
        headings = [
            h.text()
            for tag in ("h1", "h2")
            for h in scope.find(lambda n, t=tag: n.tag == t)
            if h.text() and not re.search(r"Europe's guardian in orbit|Join OroraTech|Karriere|Stellenbörse|Lust auf Team", h.text(), re.I)
        ]
        title = headings[0] if headings else metadata.get("title", "")
        if parser == "arqus" and not headings and metadata.get("title") and not re.search(r"guardian in orbit", metadata["title"], re.I):
            title = metadata["title"]
        body = re.split(r"Job Application|Join OroraTech|Cookie Consent|Manage your consent", body, 1)[0]
        loc = metadata.get("location", "")
        if parser == "diehl":
            # Extract structured Land/Standort item from sidebar <ul class="info">
            for ul in root.find(lambda n: n.tag == "ul" and n.has("info")):
                for li in ul.find(lambda n: n.tag == "li"):
                    strongs = li.find(lambda n: n.tag == "strong")
                    if strongs and "standort" in strongs[0].text().lower():
                        raw_l = li.text()[len(strongs[0].text()):].strip(" :,-")
                        if raw_l and len(raw_l) <= 80:
                            loc = raw_l
                            break
        if not loc:
            for pat in (r"(?:Location|Standort)\s*[:|]\s*([^\n|]{3,80})",):
                m = re.search(pat, body)
                if m:
                    cand_loc = re.split(r"Funktionsbereich|Funktion\b|Unternehmensbereich|Einstiegslevel|Arbeitszeit|Vertragsart|Your ", m[1])[0].strip()
                    if 2 <= len(cand_loc) <= 60 and len(cand_loc.split()) <= 6 and not re.search(r"\b(?:wir|we|you|du|team|für|for|with|mit|are|is|und|and)\b", cand_loc, re.I):
                        loc = cand_loc
                        break
        if parser == "arqus" and not loc:
            loc = "München, Deutschland"
        elif parser == "innok" and not loc:
            loc = "Regenstauf, Bayern, Deutschland"
        elif parser == "dallmeier" and not loc:
            m_city = re.search(r"\b(Regensburg|München|Hamburg|Gladbeck)\b", title + "\n" + body)
            loc = f"{m_city[1]}, Deutschland" if m_city else "Regensburg, Bayern, Deutschland"
        company = metadata.get("company", "")
        if title and len(body) >= 200 and loc:
            return {**metadata, "company": company, "title": title, "location": loc, "description": body[:30000], "verification": "Originalanzeige gelesen"}

    raise ValueError("JobPosting konnte nicht vollständig gelesen werden")


def _iabg_search(url: str, query: str) -> str:
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()), HTTPSHandler(context=tls_context()))

    def _read(req: Request) -> str:
        with opener.open(req, timeout=18) as resp:
            return resp.read(3_000_000).decode("utf-8", errors="replace")

    initial = _read(Request(url, headers={"User-Agent": USER_AGENT}))
    root = Tree(initial).root
    fields = {
        n.attrs["name"]: n.attrs.get("value", "")
        for n in root.find(lambda n: n.tag == "input" and n.attrs.get("name") and n.attrs.get("type") == "hidden")
    }
    fields["freeText"] = query
    return _read(
        Request(
            url,
            data=urlencode(fields).encode(),
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
    )


def _avature_page(raw_html: str, feed: dict, offset: int) -> tuple[list[dict], int]:
    tree = Tree(raw_html).root
    host = urlparse(feed["url"]).hostname
    jobs = []
    for article in tree.find(lambda n: n.tag == "article" and n.has("article--result")):
        headers = article.find(lambda n: n.tag == "h3")
        links = headers[0].find(lambda n: n.tag == "a" and n.attrs.get("href")) if headers else []
        if not links:
            continue
        u = urljoin(feed["url"], links[0].attrs["href"])
        if urlparse(u).hostname != host or not re.search(r"/(JobDetail|FolderDetail)/", urlparse(u).path):
            continue
        locs = article.find(lambda n: n.has("list-item-location"))
        jobs.append({"title": links[0].text(), "location": locs[0].text() if locs else "", "url": canonical_url(u), "company": feed["company"]})
    next_offsets = []
    for link in tree.find(lambda n: n.tag == "a" and n.attrs.get("href")):
        u = urlparse(urljoin(feed["url"], link.attrs["href"]))
        if u.hostname == host:
            vals = parse_qs(u.query).get("folderOffset", [])
            if vals and vals[0].isdigit() and int(vals[0]) > offset:
                next_offsets.append(int(vals[0]))
    return jobs, min(next_offsets) if next_offsets else 0


def _avature_detail(raw_html: str, metadata: dict) -> dict:
    root = Tree(raw_html).root
    mains = root.find(lambda n: n.tag == "main")
    if not mains:
        raise ValueError("Avature main fehlt")
    main = mains[0]
    fields = {}
    loc_vals = []
    for label in main.find(lambda n: n.has("article__content__view__field__label")):
        vals = label.parent.find(lambda n: n.has("article__content__view__field__value")) if label.parent else []
        if vals:
            k = re.sub(r"\s*/\s*", "/", label.text().lower())
            fields[k] = vals[0].text()
            if k == "location(s)":
                entries = vals[0].find(lambda n: n.tag == "li") or vals
                loc_vals = [re.sub(r"\s+-\s+", ", ", n.text()) for n in entries]
    desc = main.text()
    posting_locs = main.find(lambda n: n.has("posting-location"))
    loc = (
        "; ".join(loc_vals)
        or ", ".join(n.text() for n in posting_locs if n.text() and n.text() != "Location")
        or ", ".join(fields[k] for k in ("city", "state/province", "country/region", "country") if fields.get(k))
        or metadata.get("location", "")
    )
    if len(desc) < 200 or not loc:
        raise ValueError("Avature-Anzeige unvollständig")
    return {**metadata, "location": loc, "description": desc[:30000], "verification": "Avature-Originalanzeige gelesen"}


_HEALTHINEERS_SITE_MAP = {
    "ERL": "Erlangen, Bayern, Germany",
    "FOR": "Forchheim, Bayern, Germany",
    "KEM": "Kemnath, Bayern, Germany",
    "MUC": "München, Bayern, Germany",
    "TAR": "Tarrytown, NY, United States",
    "MLV": "Malvern, PA, United States",
    "WAP": "Walpole, MA, United States",
    "ATL": "Atlanta, GA, United States",
    "PAO": "Palo Alto, CA, United States",
    "MWA": "Milwaukee, WI, United States",
    "MSP": "Minneapolis, MN, United States",
    "DWL": "Newark, DE, United States",
    "YOW": "Ottawa, Canada",
    "PUN": "Pune, India",
    "NDE": "New Delhi, India",
    "HEL": "Helsinki, Finland",
    "NTN": "Nottingham, United Kingdom",
}


def _resolve_healthineers_location(raw_loc: str, country: str) -> str:
    clean = re.sub(r"^Standort prüfen\s*:\s*", "", raw_loc or "", flags=re.I).strip()
    if clean:
        prefix = clean.split()[0].upper()
        if prefix in _HEALTHINEERS_SITE_MAP:
            return _HEALTHINEERS_SITE_MAP[prefix]
    if country and country.casefold() not in clean.casefold():
        return f"{clean}, {country}" if clean else country
    return clean


def _workday_detail(value: dict, metadata: dict) -> dict | None:
    info = value.get("jobPostingInfo", {})
    if info.get("canApply") is not True:
        return None
    if not info.get("jobDescription") or not info.get("title"):
        raise ValueError("Workday-Anzeige unvollständig")
    country = (info.get("country") or {}).get("descriptor", "")
    loc = metadata.get("location") or info.get("location", "")
    if metadata.get("use_workday_city"):
        places = [info.get("location", ""), *(info.get("additionalLocations") or [])]
        loc = "; ".join(", ".join(v for v in (pl, country) if v) for pl in places if isinstance(pl, str) and pl) or loc
    elif not metadata.get("location"):
        loc = _resolve_healthineers_location(loc, country)
    elif loc.startswith("Standort prüfen") or re.fullmatch(r"[A-Z]{2,4}(?:\s+[A-Z0-9]{1,5}){0,3}", loc.strip()):
        loc = _resolve_healthineers_location(loc, country)
    return {
        **metadata,
        "title": feed_title(info["title"], str(info.get("timeType", ""))),
        "location": loc,
        "description": strip_html(info["jobDescription"]) + " Employment: " + str(info.get("timeType", "")),
        "verification": "Workday-Originalanzeige gelesen",
    }


def portal_search_jobs(con: sqlite3.Connection, db_lock: threading.Lock, feed: dict) -> tuple[list[dict], list[str]]:
    """Returns (open_jobs, closed_urls_404)."""
    src = source_key(feed)
    kind = feed["type"]
    host = urlparse(feed["url"]).hostname or ""
    stamp = now_iso()

    # Load Healthineers location cache if applicable
    locations_map: dict[str, str] = {}
    if kind == "workday" and feed.get("tenant") == "onehealthineers":
        with db_lock:
            cached = con.execute("SELECT value FROM settings WHERE key='healthineers_locations'").fetchone()
        if cached:
            try:
                locations_map = json.loads(cached[0]).get("locations", {})
            except ValueError:
                pass

    searches = feed.get("searches") or [{"text": text} for text in feed.get("keywords", [""])]
    with db_lock:
        saved_cursors = {
            r["query"]: r["offset"]
            for r in con.execute("SELECT query, offset FROM portal_cursors WHERE source = ?", (src,))
        }

    metadata: list[dict] = []
    new_cursors: list[tuple[str, str, int]] = []

    for search in searches:
        query = search["text"]
        cursor_key = search.get("key", query)
        offset = saved_cursors.get(cursor_key, 0)
        # For Avature feeds with deep cursors, always fetch page 0 (newest postings!) first
        offsets_to_fetch: list[int] = []
        if kind == "avature" and offset > 0:
            offsets_to_fetch.append(0)
        for p_idx in range(feed.get("pages_per_query", 1)):
            offsets_to_fetch.append(offset if p_idx == 0 else -1)

        seen_search_urls: set[str] = set()
        for step_offset in offsets_to_fetch:
            cur_off = offset if step_offset == -1 else step_offset
            if kind == "avature":
                u = feed["url"].rstrip("/") + "/" + quote(query, safe="") + "/?" + urlencode({"folderOffset": cur_off})
                jobs, next_offset = _avature_page(portal_request(u), feed, cur_off)
                if not jobs and cur_off > 0:
                    u0 = feed["url"].rstrip("/") + "/" + quote(query, safe="") + "/?" + urlencode({"folderOffset": 0})
                    jobs, next_offset = _avature_page(portal_request(u0), feed, 0)
            elif kind == "html_portal":
                parser = feed["parser"]
                params: dict[str, Any] = {}
                if parser == "iabg":
                    jobs, next_offset = _employer_search_page(_iabg_search(feed["url"], query), feed, cur_off)
                    metadata += [j for j in jobs if j["url"] not in seen_search_urls and not seen_search_urls.add(j["url"])]
                    offset = next_offset
                    break
                if parser == "beckhoff":
                    jobs, next_offset = _employer_search_page(portal_request(_beckhoff_list_url(cur_off)), feed, cur_off)
                    metadata += [j for j in jobs if j["url"] not in seen_search_urls and not seen_search_urls.add(j["url"])]
                    offset = next_offset
                    if not next_offset:
                        break
                    continue
                if parser == "neura":
                    base = search.get("url", feed["url"]).rstrip("/")
                    u = base + ("/" + str(cur_off) if cur_off > 1 else "")
                else:
                    params = (
                        {"q": query, "startrow": cur_off, "locationsearch": feed.get("location_search", "")}
                        if parser in ("successfactors", "sf_kuka", "festo", "kaercher")
                        else {"query": query, "page": cur_off or 1}
                        if parser == "waymo"
                        else {"term": query}
                        if parser == "rohde"
                        else {"q": query, "p": cur_off or 1}
                        if parser == "diehl"
                        else {}
                    )
                    sep = "&" if "?" in feed["url"] else "?"
                    u = feed["url"] + (sep + urlencode(params) if params else "")
                try:
                    jobs, next_offset = _employer_search_page(portal_request(u), feed, cur_off)
                except HttpStatusError as exc:
                    if cur_off > 0 and exc.code == 404:
                        jobs, next_offset = [], 0
                    else:
                        raise
                except RuntimeError as exc:
                    if cur_off > 0 and "HTTP 404" in str(exc):
                        jobs, next_offset = [], 0
                    else:
                        raise
                if not jobs and cur_off > 0:
                    if parser == "neura":
                        u0 = search.get("url", feed["url"]).rstrip("/")
                    else:
                        params0 = dict(params, startrow=0) if "startrow" in params else dict(params, page=1) if "page" in params else dict(params, p=1) if "p" in params else params
                        u0 = feed["url"] + (("&" if "?" in feed["url"] else "?") + urlencode(params0) if params0 else "")
                    jobs, next_offset = _employer_search_page(portal_request(u0), feed, 0)
            else:  # workday
                base = f"https://{host}/wday/cxs/{feed['tenant']}/{feed['board']}"
                resp = json.loads(
                    portal_request(
                        base + "/jobs",
                        {"appliedFacets": search.get("facets", {}), "limit": 20, "offset": cur_off, "searchText": query},
                    )
                )
                if cur_off and cur_off >= resp.get("total", 0):
                    resp = json.loads(
                        portal_request(
                            base + "/jobs",
                            {"appliedFacets": search.get("facets", {}), "limit": 20, "offset": 0, "searchText": query},
                        )
                    )
                    cur_off = 0
                jobs = []
                for post in resp.get("jobPostings", []):
                    path = post.get("externalPath", "")
                    if not path.startswith("/job/"):
                        continue
                    req_id = post.get("bulletFields", [""])[0] if post.get("bulletFields") else ""
                    raw_loc = locations_map.get(req_id, "") if feed.get("tenant") == "onehealthineers" else post.get("locationsText", "")
                    if feed.get("tenant") == "onehealthineers" and not raw_loc:
                        raw_loc = post.get("locationsText", "")
                    jobs.append(
                        {
                            "company": feed["company"],
                            "title": post["title"],
                            "location": raw_loc,
                            "url": f"https://{host}/en-US/{feed['board']}{path}",
                            "detail_url": base + path,
                            "use_workday_city": feed.get("tenant") != "onehealthineers",
                        }
                    )
                next_offset = cur_off + len(jobs) if jobs and cur_off + len(jobs) < resp.get("total", 0) else 0
            metadata += [j for j in jobs if j["url"] not in seen_search_urls and not seen_search_urls.add(j["url"])]
            if step_offset == 0 and kind == "avature" and len(offsets_to_fetch) > 1:
                continue
            offset = next_offset
            if not next_offset:
                break
        new_cursors.append((src, cursor_key, offset))

    for seed in feed.get("seed_urls", []):
        metadata.append({**seed, "company": feed["company"], "location": ""})

    eligible_meta = []
    min_sc = feed.get("min_score", 10)
    for item in metadata:
        if not item.get("title") or excluded_employment(item["title"]) or outside_region(item.get("location", "")):
            continue
        if feed.get("title_pattern") and not re.search(feed["title_pattern"], item["title"], re.I):
            continue
        has_desc = len((item.get("description") or "").strip()) >= 100
        eff_min = min_sc if has_desc else min(min_sc, 5)
        if relevance(item["title"], item.get("description", ""))[0] < eff_min and not TECHNICAL.search(item["title"]):
            continue
        eligible_meta.append(item)

    with db_lock, con:
        for s_src, s_q, s_off in new_cursors:
            con.execute(
                "INSERT INTO portal_cursors VALUES (?, ?, ?) ON CONFLICT(source, query) DO UPDATE SET offset=excluded.offset",
                (s_src, s_q, s_off),
            )
        for item in eligible_meta:
            con.execute(
                "INSERT INTO portal_job_cache(source, url, metadata, last_seen) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source, url) DO UPDATE SET metadata=excluded.metadata, last_seen=excluded.last_seen, "
                "state=CASE WHEN state='closed' THEN 'pending' ELSE state END, "
                "next_check=CASE WHEN state='closed' THEN '' ELSE next_check END",
                (src, item["url"], json.dumps(item, ensure_ascii=False), stamp),
            )
        pending = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM portal_job_cache WHERE source=? AND state!='closed' AND next_check<=? "
                "ORDER BY CASE WHEN state='pending' AND error='' THEN 0 WHEN error='' THEN 1 ELSE 2 END, checked LIMIT ?",
                (src, stamp, min(feed.get("detail_limit", 45), 120)),
            )
        ]

    def _fetch_detail(row: dict):
        item = json.loads(row["metadata"])
        try:
            if kind == "avature":
                job = _avature_detail(portal_request(item["url"]), item)
            elif kind == "html_portal":
                embedded = item.get("_embedded_job") if feed["parser"] == "swm" else None
                job = embedded or _employer_detail(portal_request(item["url"]), item, feed["parser"])
            else:
                job = _workday_detail(json.loads(portal_request(item["detail_url"])), item)
            return row["url"], job, "", bool(row.get("error"))
        except (HttpStatusError, HTTPError) as exc:
            code = getattr(exc, "code", None)
            if code in (404, 410):
                return row["url"], None, "", bool(row.get("error"))
            return row["url"], False, str(exc), bool(row.get("error"))
        except Exception as exc:
            if re.search(r"\bHTTP\s+(?:404|410)\b", str(exc)):
                return row["url"], None, "", bool(row.get("error"))
            return row["url"], False, str(exc), bool(row.get("error"))

    detail_results = []
    if pending:
        with ThreadPoolExecutor(max_workers=4) as pool:
            detail_results = list(pool.map(_fetch_detail, pending))

    closed_urls: list[str] = []
    with db_lock, con:
        for url, job, err, had_prev_err in detail_results:
            backoff_days = (3 if had_prev_err else 1) if err else 7
            nxt = (datetime.fromisoformat(stamp) + timedelta(days=backoff_days)).isoformat(timespec="seconds")
            if err:
                con.execute(
                    "UPDATE portal_job_cache SET checked=?, next_check=?, error=? WHERE source=? AND url=?",
                    (stamp, nxt, err[:300], src, url),
                )
            elif job is None:
                con.execute("UPDATE portal_job_cache SET state='closed', checked=?, error='' WHERE source=? AND url=?", (stamp, src, url))
                closed_urls.append(url)
            else:
                con.execute(
                    "UPDATE portal_job_cache SET payload=?, state='open', checked=?, next_check=?, error='' WHERE source=? AND url=?",
                    (json.dumps(job, ensure_ascii=False), stamp, nxt, src, url),
                )
        open_rows = con.execute(
            "SELECT url, payload, checked FROM portal_job_cache WHERE source=? AND state='open' AND payload!=''",
            (src,),
        ).fetchall()

    out_jobs = []
    for r in open_rows:
        u = urlparse(r["url"])
        q_params = parse_qs(u.query)
        identity = (
            q_params.get("jobOfferId", [None])[0]
            or q_params.get("id", [None])[0]
            or q_params.get("job", [None])[0]
            or u.path.strip("/")
        )
        out_jobs.append({**json.loads(r["payload"]), "source": src, "source_id": identity, "_observed_at": r["checked"]})
    return out_jobs, closed_urls


def tech_portal_jobs(feed: dict) -> tuple[list[dict], list[str]]:
    company = feed["company"]
    src = source_key(feed)
    metadata: list[dict] = []

    if company == "Apple":
        for page in range(1, 4):
            raw = fetch_html(feed["url"] + f"&page={page}")
            m = re.search(r"window.__staticRouterHydrationData\s*=\s*JSON.parse\(", raw)
            if not m:
                continue
            encoded, _ = json.JSONDecoder().raw_decode(raw[m.end() :])
            data = json.loads(encoded).get("loaderData", {})

            def _walk(v):
                if isinstance(v, dict):
                    if "postingTitle" in v and v.get("reqId") and v.get("transformedPostingTitle"):
                        metadata.append(
                            {
                                "company": "Apple",
                                "title": v["postingTitle"],
                                "location": "; ".join(loc.get("name", "") + ", " + loc.get("countryName", "") for loc in v.get("locations", [])),
                                "url": f"https://jobs.apple.com/en-us/details/{v['reqId']}/{v['transformedPostingTitle']}",
                                "source_id": v["reqId"],
                            }
                        )
                    for ch in v.values():
                        _walk(ch)
                elif isinstance(v, list):
                    for ch in v:
                        _walk(ch)

            _walk(data)
    elif company in ("NVIDIA", "Qualcomm", "Infineon", "Infineon Technologies"):
        sites = {
            "NVIDIA": ("jobs.nvidia.com", "nvidia.com"),
            "Qualcomm": ("careers.qualcomm.com", "qualcomm.com"),
            "Infineon": ("jobs.infineon.com", "infineon.com"),
            "Infineon Technologies": ("jobs.infineon.com", "infineon.com"),
        }
        host, domain = sites[company]
        seen_urls: set[str] = set()
        for loc_q in feed.get("locations", ["Munich, Germany"]):
            for start in range(0, 80, 10):
                u = f"https://{host}/api/pcsx/search?" + urlencode(
                    {"domain": domain, "query": "", "location": loc_q, "start": start, "sort_by": "relevance"}
                )
                val = fetch_json(u)
                positions = (val.get("data") or {}).get("positions") or []
                for row in positions:
                    path = row.get("positionUrl", "")
                    full_u = f"https://{host}{path}"
                    if re.fullmatch(r"/careers/job/\d+", path) and row.get("name") and full_u not in seen_urls:
                        seen_urls.add(full_u)
                        metadata.append(
                            {
                                "company": company,
                                "title": row["name"],
                                "location": "; ".join(row.get("locations") or []),
                                "url": full_u,
                                "source_id": str(row["id"]),
                            }
                        )
                if start + len(positions) >= (val.get("data") or {}).get("count", 0) or not positions:
                    break

    filtered = list(
        {
            r["url"]: r
            for r in metadata
            if re.search(r"Munich|München|Ingolstadt|Regensburg|Augsburg|Nürnberg|Nuremberg|Erlangen|Neubiberg", r["location"], re.I)
            and (
                re.search(r"engineer|developer|entwickl|ingenieur|scientist|robot|vision|learning|prototyp|architect|python|\bml\b|\bki\b|perception", r["title"], re.I)
                or re.search(r"\bAI\b", r["title"])
            )
            and not re.search(r"\b(?:intern|internship|student|working student|werkstudent|sales|account|director)\b", r["title"], re.I)
        }.values()
    )[:60]

    def _fetch(item: dict):
        try:
            raw = fetch_html(item["url"])
            if company == "Apple":
                m = re.search(r"window.__staticRouterHydrationData\s*=\s*JSON.parse\(", raw)
                if not m:
                    raise ValueError("Apple Hydration JSON fehlt")
                enc, _ = json.JSONDecoder().raw_decode(raw[m.end() :])
                val = json.loads(enc).get("loaderData", {}).get("jobDetails", {}).get("jobsData", {})
                text = "\n".join(
                    f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                    for k in ("jobSummary", "description", "responsibilities", "minimumQualifications", "preferredQualifications")
                    if (v := val.get(k))
                )
                return {**item, "description": text, "source": src, "verification": "Apple-Originalanzeige gelesen", "_observed_at": now_iso()}, None
            else:
                for attrs, txt in re.findall(r"<script([^>]*)>(.*?)</script>", raw, re.S | re.I):
                    if "ld+json" in attrs.lower():
                        v = json.loads(txt)
                        for p in (v if isinstance(v, list) else [v]):
                            if isinstance(p, dict) and p.get("@type") == "JobPosting":
                                return {**item, "description": p.get("description", ""), "source": src, "verification": "Eightfold-Originalanzeige gelesen", "_observed_at": now_iso()}, None
                raise ValueError("JSON-LD fehlt")
        except (HttpStatusError, HTTPError) as exc:
            code = getattr(exc, "code", None)
            if code in (404, 410):
                return None, item["url"]
            return None, None
        except Exception as exc:
            if re.search(r"\bHTTP\s+(?:404|410)\b", str(exc)):
                return None, item["url"]
            return None, None

    jobs = []
    closed = []
    if filtered:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for j_item, c_url in pool.map(_fetch, filtered):
                if j_item:
                    jobs.append(j_item)
                if c_url:
                    closed.append(c_url)
    return jobs, closed
