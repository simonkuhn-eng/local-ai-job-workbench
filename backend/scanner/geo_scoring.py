"""Geographic tier rules, canonicalization, and profile-aligned heuristic scoring."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

COMPANY_NAMES = (
    "Siemens Healthineers", "Siemens Energy", "MBDA Deutschland", "Airbus Defence and Space",
    "Diehl Defence", "Rohde & Schwarz", "ARX Robotics", "Quantum Systems", "Everllence",
    "Agile Robots", "Alpine Eagle", "NavVis", "HENSOLDT", "Siemens", "KUKA", "AUDI", "Helsing",
    "Apex.AI", "FERNRIDE", "DeepScenario", "Blickfeld", "CARIAD", "Bertrandt", "Cognizant Mobility",
    "RobCo", "Roboception", "idealworks", "STABL Energy", "Sereact", "Torc Robotics", "Magazino", "Korial",
    "SE3 Labs", "AUMOVIO", "Infineon Technologies", "AVL Software and Functions", "MVTec Software",
    "Innok Robotics", "FRAMOS", "ProGlove", "Kinexon", "DeepDrive", "TWAICE", "Filics", "PartSpace",
    "planqc", "Hive Robotics", "robominds", "softgate", "Robotise", "Nexis", "microagi",
    "Munich Electrification", "sensmore", "Schaeffler", "ZF",
    "BMW Group", "Maschinenfabrik Reinhausen", "Dallmeier electronic",
    "Krones", "TECVIA", "HiQ", "OPTITOOL", "goodBytz",
    "Wayve", "Waymo", "Mobileye", "Applied Intuition", "Aurora Innovation", "Zoox", "Waabi", "Oxa", "Embotech",
    "ANYbotics", "NEURA Robotics", "Dexory", "Nuro", "Intrinsic", "Wandelbots", "Figure AI", "1X", "Skild AI",
)

TIER_LABELS = {
    1: "Bayern",
    2: "Restliches Deutschland & England",
    3: "USA & restliches Europa",
    4: "Außerhalb / Unbekannt",
}


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def slug(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "",
        norm(value).replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"),
    )


def canonical_company(value: str) -> str:
    value = (value or "").strip()
    aliases = {
        "aurora": "Aurora Innovation",
        "aurora operations, inc.": "Aurora Innovation",
        "aurora operations inc.": "Aurora Innovation",
        "figure": "Figure AI",
        "knds": "KNDS Deutschland",
        "kndms": "KNDS Deutschland",
        "kmw": "KNDS Deutschland",
        "krauss-maffei wegmann": "KNDS Deutschland",
        "asap gruppe": "ASAP",
        "asap group": "ASAP",
        "eigenblue": "Motius",
        "eigenblue gmbh": "Motius",
        "machine learning reply": "Machine Learning Reply GmbH",
        "machine learning reply gmbh": "Machine Learning Reply GmbH",
        "scalable": "Scalable Capital",
        "scalable capital gmbh": "Scalable Capital",
        "quantco inc.": "QuantCo",
        "quantco gmbh": "QuantCo",
        "aleph alpha gmbh": "Aleph Alpha",
        "driveblocks gmbh": "driveblocks",
        "blackshark": "Blackshark.ai",
        "blackshark ai": "Blackshark.ai",
        "parloa gmbh": "Parloa",
        "tng": "TNG Technology Consulting",
        "tng technology consulting gmbh": "TNG Technology Consulting",
        "maiborn wolff": "MaibornWolff",
        "maibornwolff gmbh": "MaibornWolff",
        "qaware gmbh": "QAware",
        "deepl se": "DeepL",
        "deepl gmbh": "DeepL",
        "se3": "SE3 Labs",
        "se3 labs": "SE3 Labs",
        "aumovio": "AUMOVIO",
        "aumovio se": "AUMOVIO",
        "infineon": "Infineon Technologies",
        "infineon technologies ag": "Infineon Technologies",
        "mvtec": "MVTec Software",
        "mvtec software gmbh": "MVTec Software",
        "avl software and functions gmbh": "AVL Software and Functions",
        "innok robotics gmbh": "Innok Robotics",
        "framos gmbh": "FRAMOS",
        "kinexon": "Kinexon",
        "kinexon gmbh": "Kinexon",
        "proglove": "ProGlove",
        "workaround gmbh": "ProGlove",
        "deepdrive gmbh": "DeepDrive",
        "twaice technologies gmbh": "TWAICE",
        "filics gmbh": "Filics",
        "partspace gmbh": "PartSpace",
        "planqc gmbh": "planqc",
        "hive robotics gmbh": "Hive Robotics",
        "robominds gmbh": "robominds",
        "softgate gmbh": "softgate",
        "robotise gmbh": "Robotise",
        "nexis gmbh": "Nexis",
        "microagi": "microagi",
        "munich electrification gmbh": "Munich Electrification",
        "sensmore gmbh": "sensmore",
        "schaeffler ag": "Schaeffler",
        "schaeffler technologies ag & co. kg": "Schaeffler",
        "zf friedrichshafen ag": "ZF",
        "audi ag": "AUDI",
        "bmw": "BMW Group",
        "bmw ag": "BMW Group",
        "bayerische motoren werke ag": "BMW Group",
        "reinhausen": "Maschinenfabrik Reinhausen",
        "maschinenfabrik reinhausen gmbh": "Maschinenfabrik Reinhausen",
        "dallmeier": "Dallmeier electronic",
        "dallmeier electronic gmbh & co.kg": "Dallmeier electronic",
        "krones ag": "Krones",
        "krones.digital": "Krones",
        "krones / syskron": "Krones",
        "tecvia gmbh": "TECVIA",
        "hiq gmbh": "HiQ",
        "optitool gmbh": "OPTITOOL",
        "goodbytz gmbh": "goodBytz",
        "everllence": "Everllence",
        "everllence se": "Everllence",
        "man energy solutions": "Everllence",
        "man energy solutions se": "Everllence",
        "airbus defence and space gmbh": "Airbus Defence and Space",
        "diehl defence gmbh": "Diehl Defence",
        "diehl defence gmbh & co. kg": "Diehl Defence",
        "quantum-systems": "Quantum Systems",
        "quantum-systems gmbh": "Quantum Systems",
        "quantum systems gmbh": "Quantum Systems",
        "rohde & schwarz gmbh & co. kg": "Rohde & Schwarz",
        "apple gmbh": "Apple",
    }
    n = norm(value)
    if n in aliases:
        return aliases[n]
    if n in ("energy robotics", "energy robotics gmbh", "energy robotics gmbh / korial"):
        return "Korial"
    for name in COMPANY_NAMES:
        if n == norm(name) or n.startswith(norm(name) + " "):
            return name
    if n in ("arx", "arx robotics gmbh"):
        return "ARX Robotics"
    return value


def canonical_url(url: str) -> str:
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Eine gültige http(s)-URL ist erforderlich")
    if p.username or p.password:
        raise ValueError("URLs mit Zugangsdaten sind nicht erlaubt")
    scheme = p.scheme.casefold()
    netloc = p.hostname.casefold()
    if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        netloc = f"{netloc}:{p.port}"
    path = re.sub(r"/+", "/", p.path).rstrip("/") or "/"
    query = parse_qs(p.query, keep_blank_values=False)
    meaningful = {
        k: query[k]
        for k in sorted(query)
        if not k.casefold().startswith("utm_")
        and k.casefold() not in {"gclid", "fbclid", "msclkid", "ref", "referrer", "source", "trk", "gh_jid", "gh_src"}
    }
    return urlunparse((scheme, netloc, path, "", urlencode(meaningful, doseq=True), ""))


class _PlainText(HTMLParser):
    BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}
    SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "br":
            self.parts.append("\n")
        elif t == "li":
            self.parts.append("\n• ")
        elif t in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if t in self.BLOCK_TAGS or t == "li":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def strip_html(value: str) -> str:
    s = html.unescape(value or "")
    # Only unescape a second time if literal escaped HTML tags remain (preserves List<String> / vector<int>!)
    if re.search(r"&lt;/?(?:p|div|br|li|ul|ol|span|strong|b|i|h[1-6]|a|table|tr|td)\b", s, re.I):
        s = html.unescape(s)
    parser = _PlainText()
    parser.feed(s)
    raw = "".join(parser.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def content_fingerprint(company: str, title: str, location: str, description: str) -> str:
    payload = {"company": company, "title": title, "location": location, "description": description}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def excluded_employment(title: str) -> bool:
    text = norm(title)
    training = bool(
        re.search(
            r"\b(?:working\s+students?|werkstudent\w*|werkstudierend\w*|internships?|interns?\b|"
            r"praktik\w*|apprentice\w*|azubi\w*|student\s+worker\w*|strategic\s+student\s+program|"
            r"(?:pflicht|schüler|schueler|vor|berufs|freiwilligen|freiwilliges)praktik\w*|praxissemester|"
            r"ausbildung\w*|auszubildend\w*|duale[rsm]?\s+stud(?:ium|ent\w*|ierende\w*)|studium\s+mit\s+vertiefter\s+praxis|bachelor\s+of|"
            r"masterstudium|(?:bachelor|master)student\w*|studienarbeit\w*|thesis|abschlussarbeit|(?:bachelor|master|diplom)arbeit|bachelorand\w*|masterand\w*|diplomand\w*|"
            r"co[- ]op|stagiaire|alternance|apprentissage|doktorand\w*|promotionsstelle|"
            r"phd\s+(?:student|position|candidate)|studentische\s+hilfskraft|hiwi|ferienjob\w*|minijob\w*)\b|"
            r"\bstage\s+(?:20\d{2}|[-–:])",
            text,
        )
    )
    part_time = bool(re.search(r"\b(?:part[- ]time|teilzeit)\b", text))
    full_time = bool(re.search(r"\b(?:full[- ]time|vollzeit)\b", text))
    return training or (part_time and not full_time)


def feed_title(title: str, employment: str) -> str:
    if excluded_employment(title):
        return title
    e_norm = re.sub(r"[^a-z]", "", norm(employment))
    labels = {
        "parttime": "Teilzeit",
        "teilzeit": "Teilzeit",
        "intern": "Praktikum",
        "internship": "Praktikum",
        "praktikum": "Praktikum",
        "workingstudent": "Werkstudent",
        "werkstudent": "Werkstudent",
        "werkstudierende": "Werkstudent",
        "student": "Werkstudent",
        "apprenticeship": "Ausbildung",
        "ausbildung": "Ausbildung",
    }
    label = labels.get(e_norm)
    if not label:
        emp_low = norm(employment)
        if re.search(r"\b(?:part[- ]?time|teilzeit)\b", emp_low) and not re.search(r"\b(?:full[- ]?time|vollzeit)\b", emp_low):
            label = "Teilzeit"
        elif re.search(r"\b(?:intern(?:ship)?|praktik\w*)\b", emp_low):
            label = "Praktikum"
        elif re.search(r"\b(?:working\s+student|werkstudent\w*)\b", emp_low):
            label = "Werkstudent"
        elif re.search(r"\b(?:apprentice\w*|ausbildung)\b", emp_low):
            label = "Ausbildung"
    return f"{title} – {label}" if label else title


MUNICH_AREA = {
    "munich", "münchen", "muenchen", "garching", "ottobrunn", "unterföhring", "unterfoehring",
    "unterhaching", "neubiberg", "taufkirchen", "aschheim", "ismaning", "feldkirchen", "haar",
    "dachau", "unterschleißheim", "unterschleissheim", "oberschleißheim", "oberschleissheim",
    "gilching", "germering", "puchheim", "starnberg", "weßling", "wessling", "oberpfaffenhofen",
    "fürstenfeldbruck", "fuerstenfeldbruck", "freising", "hallbergmoos", "eching", "poing",
    "grünwald", "gruenwald", "gräfelfing", "graefelfing", "planegg", "pullach",
    "gröbenzell", "groebenzell",
}

INGOLSTADT_AREA = {
    "ingolstadt", "gaimersheim", "manching", "neuburg", "neuburg an der donau",
    "schrobenhausen", "kösching", "koesching", "lentings", "lenting", "reichertshofen",
    "pfaffenhofen", "pfaffenhofen an der ilm", "eichstätt", "eichstaett",
}

AUGSBURG_AREA = {
    "augsburg", "friedberg", "königsbrunn", "koenigsbrunn", "gersthofen", "neusäß", "neusaess",
    "stadtbergen", "mering", "schwabmünchen", "donauwörth", "donauwoerth",
}

CITY_AREAS = {
    "ingolstadt": INGOLSTADT_AREA,
    "munich": MUNICH_AREA,
    "augsburg": AUGSBURG_AREA,
    "nuremberg": {
        "nürnberg", "nuernberg", "nuremberg", "fürth", "fuerth", "zirndorf", "oberasbach",
        "schwabach", "feucht", "lauf an der pegnitz", "röthenbach an der pegnitz",
        "roethenbach an der pegnitz", "röthenbach", "roethenbach",
    },
    "erlangen": {"erlangen", "bubenreuth", "baiersdorf", "herzogenaurach", "eckental", "uttenreuth"},
    "regensburg": {
        "regensburg", "neutraubling", "barbing", "regenstauf", "pentling",
        "obertraubling", "lappersdorf", "tegernheim", "sinzing",
    },
    "wuerzburg": {
        "würzburg", "wuerzburg", "wurzburg", "höchberg", "hoechberg", "veitshöchheim",
        "veitshoechheim", "estenfeld", "randersacker", "gerbrunn", "rottenbauer",
        "kist", "giebelstadt", "röttingen", "roettingen", "ochsenfurt", "marktheidenfeld", "lohr am main",
    },
}

BAVARIA = {
    "bayern", "bavaria", "landshut", "rosenheim", "passau", "kempten", "bayreuth", "amberg",
    "schweinfurt", "coburg", "straubing", "ansbach", "mindelheim", "aschau am inn", "aschau am lnn",
    "freilassing", "kaufbeuren", "schongau", "teisnach", "durach", "seefeld", "seefeld/delling",
    "deggendorf", "plattling", "osterhofen", "heimertingen", "markt schwaben", "kötz", "koetz",
    "marktoberdorf", "schwandorf", "sulzbach-rosenberg",
    "memmingen", "forchheim", "putzbrunn", "aschaffenburg", "bamberg", "dingolfing",
    "neu-ulm", "traunstein", "weiden", "hof", "neumarkt", "weilheim", "penzberg", "erding", "holzkirchen",
} | set().union(*CITY_AREAS.values())

GERMANY = {
    "deutschland", "deutschlandweit", "germany", "berlin", "hamburg", "frankfurt", "stuttgart", "koeln", "köln",
    "cologne", "duesseldorf", "düsseldorf", "dortmund", "bremen", "hannover", "hanover", "dresden",
    "leipzig", "potsdam", "karlsruhe", "bochum", "wolfsburg", "essen", "bonn", "duisburg", "luebeck",
    "lübeck", "ulm", "konstanz", "baden-württemberg", "baden-wuerttemberg", "baden-wurttemberg",
    "darmstadt", "mannheim", "heidelberg", "aachen", "kiel", "braunschweig", "friedrichshafen",
    "überlingen", "ueberlingen", "laupheim", "markdorf", "heilbronn", "nonnweiler", "wangen",
    "wangen im allgäu", "wangen im allgaeu",
    "nrw", "nordrhein-westfalen", "hessen", "sachsen", "niedersachsen", "rheinland-pfalz",
    "schleswig-holstein", "thüringen", "saarland", "rostock", "teltow", "wiesbaden", "mainz",
    "freiburg", "münster", "muenster", "oberkochen", "metzingen", "verl", "pforzheim", "bielefeld",
    "kassel", "magdeburg", "erfurt", "jena", "göttingen", "goettingen",
}

ENGLAND = {
    "england", "london", "cambridge", "oxford", "bristol", "manchester", "birmingham", "liverpool",
    "leeds", "sheffield", "nottingham", "southampton", "reading", "newcastle", "brighton", "coventry",
    "enfield", "plymouth", "andover", "derby", "york", "portsmouth", "milton keynes", "stevenage",
    "bolton", "wallingford", "oxfordshire", "basingstoke", "guildford",
}

USA_CITIES = {
    "san francisco", "pittsburgh", "dallas", "sunnyvale", "mountain view", "san carlos",
    "hayward", "menlo park", "foster city", "detroit", "boston", "seattle", "austin",
    "santa clara", "san jose", "palo alto", "cupertino", "los angeles", "san diego",
    "irvine", "cambridge, ma", "chicago", "atlanta", "denver", "phoenix", "portland",
    "philadelphia", "miami", "raleigh", "huntsville", "ann arbor", "blacksburg", "fort worth",
    "poway", "newton", "malvern", "walpole", "tarrytown", "cary", "troy", "el paso", "reno",
    "martinsburg", "columbus", "reston", "fremont", "new york city", "sacramento", "oakland",
    "pasadena", "waltham", "lexington", "indianapolis", "boise", "newark, de", "wilmington",
}
USA = {
    "usa", "us", "united states", "united states of america", "u.s", "u.s.", "u.s.a.",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
} | USA_CITIES

US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "dc",
}
US_NON_COLLIDING_STATE_CODES = US_STATE_CODES - {
    "al", "ar", "ca", "de", "id", "il", "in", "ma", "md", "me", "mt",
}

EUROPE_CITIES = {
    "paris", "amsterdam", "rotterdam", "vienna", "wien", "zurich", "zürich", "geneva", "genf",
    "prague", "prag", "praha", "brno", "ostrava", "stockholm", "oslo", "copenhagen", "kopenhagen",
    "helsinki", "brussels", "brüssel", "bruxelles", "madrid", "barcelona", "rome", "rom",
    "milan", "mailand", "warsaw", "warschau", "wroclaw", "wrocław", "dublin", "lisbon", "lissabon",
    "kyiv", "kiev", "zagreb", "belgrade", "beograd", "cakovec", "namysłów", "debrecen",
}
EUROPE = {
    "europe", "europa", "france", "frankreich", "italy", "italien", "spain", "spanien",
    "netherlands", "niederlande", "austria", "österreich", "oesterreich", "switzerland", "schweiz",
    "poland", "polen", "sweden", "schweden", "norway", "norwegen", "denmark", "dänemark",
    "daenemark", "finland", "finnland", "belgium", "belgien", "ireland", "irland", "portugal",
    "czech", "czechia", "tschechien", "ukraine", "scotland", "wales", "united kingdom", "uk", "romania",
    "rumänien", "hungary", "ungarn", "croatia", "kroatien", "greece", "griechenland", "estonia",
    "estland", "latvia", "lettland", "lithuania", "litauen", "slovakia", "slowakei", "slovenia",
    "slowenien", "bulgaria", "bulgarien", "iceland", "island", "luxembourg", "luxemburg", "malta",
    "cyprus", "zypern", "serbia", "serbien", "albania", "albanien", "montenegro", "moldova",
    "moldau", "north macedonia", "nordmazedonien", "bosnia", "bosnien", "kosovo", "belarus", "weißrussland",
}
OUTSIDE_REGION = {
    "australia", "australien", "new zealand", "neuseeland", "canada", "kanada", "china", "india",
    "indien", "japan", "singapore", "singapur", "israel", "brazil", "brasilien", "mexico", "mexiko",
    "chile", "argentina", "argentinien", "south africa", "südafrika", "united arab emirates", "uae",
    "saudi arabia", "saudi-arabien", "ksa", "south korea", "südkorea", "republic of korea",
    "taiwan", "thailand", "vietnam", "indonesia", "indonesien", "malaysia", "philippines",
    "philippinen", "pakistan", "egypt", "ägypten", "morocco", "marokko",
    "hong kong", "hongkong", "turkey", "türkei", "tuerkei", "türkiye", "turkiye",
    "armenia", "armenien", "bangladesh", "colombia", "kolumbien", "kazakhstan", "kasachstan", "peru",
}
EUROPE_CODES = {
    "eu", "fr", "at", "ch", "it", "es", "nl", "be", "pl", "cz", "sk", "se", "no", "dk", "fi",
    "ie", "pt", "ro", "hu", "gr", "hr", "si", "ee", "lv", "lt", "bg", "is", "lu", "mt", "cy",
    "ua", "rs", "al", "me", "md", "mk", "ba", "by",
}
OUTSIDE_CODES = {
    "za", "au", "nz", "ca", "cn", "in", "jp", "sg", "il", "br", "mx", "cl", "ar", "ae", "sa",
    "kr", "tw", "th", "vn", "id", "my", "ph", "pk", "eg", "ma", "hk", "tr", "am", "bd", "co", "kz", "pe",
}


def contains_place(text: str, places: set[str]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(place) + r"(?!\w)", text) for place in places)


def _plz_tier_hint(text: str) -> int | None:
    """Infer German Tier 1 (Bavaria) or Tier 2 (Germany) from explicit 5-digit German PLZ."""
    if contains_place(text, USA):
        return None
    for m in re.finditer(r"\b(\d{5})\b", text):
        plz = int(m.group(1))
        if (80000 <= plz <= 87999) or (90000 <= plz <= 97999):
            return 1
        if 1067 <= plz <= 99998 and contains_place(text, GERMANY | {"de"}):
            return 2
    return None


def matches_city_area(location: str, area_key: str) -> bool:
    places = CITY_AREAS.get(area_key)
    if not places:
        return False
    return contains_place(norm(location), places)


def country_code(text: str) -> str:
    text = re.sub(r"\bkorea\s*[;,]\s*republic\s+of\b", "south korea", norm(text))
    if contains_place(text, {"germany", "deutschland", "deutschlandweit"}):
        return "de"
    if contains_place(text, {"united states", "united states of america", "usa", "u.s.a."}):
        return "us"
    if contains_place(text, {"england", "united kingdom", "vereinigtes königreich", "great britain", "uk"}):
        return "gb"
    if contains_place(text, OUTSIDE_REGION):
        return "outside"
    if "tbilisi" in text:
        return "eu"
    if contains_place(text, USA):
        return "us"
    matches = re.findall(r"(?<=,)\s*([a-z]{2})(?=\s*(?:,|$))", text)
    for code in reversed(matches):
        if code in US_NON_COLLIDING_STATE_CODES:
            return "us"
        if code in EUROPE_CODES | OUTSIDE_CODES | {"de", "us", "gb", "uk"}:
            return "gb" if code == "uk" else code
    if contains_place(text, EUROPE - {"uk"}):
        return "eu"
    return ""


def location_tier(location: str) -> int:
    text = re.sub(r"\bkorea\s*[;,]\s*republic\s+of\b", "south korea", norm(location))
    if not text:
        return 4
    segments = re.split(r"\s*[;|•·]\s*|\s+-\s+", text)
    if len(segments) > 1:
        tiers = [location_tier(part) for part in segments if part.strip()]
        return min(tiers) if tiers else 4
    code = country_code(text)
    if code == "outside" or code in OUTSIDE_CODES:
        return 4
    if code == "us":
        return 3
    if code == "de":
        if contains_place(text, BAVARIA) or _plz_tier_hint(text) == 1:
            return 1
        return 2
    if code in EUROPE_CODES:
        return 3
    if code in ("gb", "uk"):
        return 2 if contains_place(text, ENGLAND) else 3
    if contains_place(text, OUTSIDE_REGION):
        return 4
    if contains_place(text, USA):
        return 3
    if contains_place(text, BAVARIA) or _plz_tier_hint(text) == 1:
        return 1
    if contains_place(text, GERMANY) or contains_place(text, ENGLAND) or _plz_tier_hint(text) == 2:
        return 2
    if contains_place(text, EUROPE) or contains_place(text, EUROPE_CITIES):
        return 3
    return 4


def outside_region(location: str) -> bool:
    norm_loc = re.sub(r"\bkorea\s*[;,]\s*republic\s+of\b", "south korea", norm(location))
    if not norm_loc:
        return False
    # Split multi-location listings cleanly without breaking 'KSA - RYD G, Saudi Arabia' into empty/code chunks
    segments = [part.strip() for part in re.split(r"\s*[;|•·]\s*", norm_loc) if part.strip()]
    concrete = [
        part for part in segments
        if part not in {"remote", "hybrid", "onsite", "on-site", "homeoffice", "home office"}
    ]
    segments = concrete or segments
    return bool(segments) and all(
        (cc := country_code(part)) not in EUROPE_CODES | {"de", "us", "gb", "uk"}
        and (contains_place(part, OUTSIDE_REGION) or cc == "outside" or cc in OUTSIDE_CODES)
        for part in segments
    )


def hard_exclusion(title: str, location: str) -> str:
    if re.search(
        r"initiativbewerbung|unsolicited|speculative application|general application|talent pool|talent community|create your own dream job",
        norm(title),
    ):
        return "Initiativbewerbung oder Talent-Pool; keine konkrete Vakanz"
    if excluded_employment(title):
        return "Keine passende Vollzeitstelle"
    if outside_region(location):
        return "Standort außerhalb der erlaubten Suchregionen"
    return ""


# Domain terms for rule-based relevance scoring (AI/LLM/RAG, Python/FastAPI, Robotics/Perception/ADAS)
TERMS = (
    (r"\b(?:llm\w*|rag|agentic|ai[- ]agents?|ki[- ]agents?|intelligent[- ]agents?|multi[- ]agent\w*|gen\s?ai|generativ\w*\s+(?:ai|ki)|prompt[- ]engineer\w*|tool[- ]calling|vlm\w*|vision[- ]language)\b", 19, "LLM / Agentic AI / RAG"),
    (r"\b(?:robot\w*|ros\s?2?|moveit\w*|gazebo|isaac\s*sim|mujoco)\b", 17, "Robotik / ROS 2"),
    (r"\b(?:autonom(?:ous|e[nsmr]?)?\s+(?:driving|fahren|fahrzeuge?|vehicles?|systems?|systeme|mobile|robots?|navigation|flight|fliegen|uav|ugv)|automatisiert\w*\s+fahren\w*|automated\s+driving|adas|fahrerassist\w*|fahrzeugsicherheit|motion\s+planning|path\s+planning|trajectory|trajektorien\w*|slam|localization|lokalisierung)\b", 17, "Autonomes Fahren / ADAS"),
    (r"\b(?:perception|computer\s+vision|bildverarbeitung|object\s+detection|objekterkenn\w*|3d[- ]detection|point\s*cloud\w*|punktwolke\w*|opencv|openpcdet|yolo\w*|(?:image|semantic|instance|3d|panoptic)[- ]segmentation|segmentierung)\b", 16, "Perception / Computer Vision"),
    (r"\b(?:sensor\s*fusion|sensorfusion\w*|multi[- ]sensor\w*|lidar|radar[- ]?(?:signal|processing|sensor\w*|tracking)|bev(?:fusion)?|kalman|ekf|ukf|(?:object|target|sensor|motion|multi[- ]object|vehicle)[- ]tracking)\b", 17, "Sensorfusion / BEV"),
    (r"\b(?:machine\s+learning|deep\s+learning|ai|ki|künstlich\w*\s+intelligenz|artificial\s+intelligence|pytorch|tensorflow|transformer\w*|neural\s+network\w*|foundation\s+models?|reinforcement\s+learning|mlops|data\s+scien\w*)\b", 15, "KI / Machine Learning"),
    (r"\b(?:python|fastapi|fullstack|full[- ]stack|backend|api[- ]integration|rest[- ]apis?|embedded[- ]linux|microservices?|vue\.?js|typescript|c#|(?<![\w.])\.net\b|java\b)\b", 13, "Python / Fullstack / Backend"),
    (r"\b(?:software\s*engineer\w*|software[- ]?entwick\w*|software\s*developer\w*|software[- ]ingenieur\w*|(?:ki|ai|ml|backend|frontend|fullstack|full[- ]stack|anwendungs|robotik)[- ]?entwick\w*|solutions?[- ]engineer\w*|ai[- ]engineer\w*|ml[- ]engineer\w*|applikationsentwick\w*|application[- ]engineer\w*|fachinformatik\w*)\b", 13, "Softwareentwicklung"),
    (r"\b(?:regelungstechnik\w*|regelungs\w*|control\s+(?:systems?|engineer\w*|theory|algorithms?)|motion\s+control|systemdynamik|flugführung\w*|gnc|guidance,?\s*(?:navigation|and\s+control))\b", 12, "Regelungstechnik / Control"),
    (r"\b(?:automation|automatisierung\w*|testautomat\w*|workflow[- ]automation|systemintegration|ci/cd|hil[- ]?test\w*|hardware[- ]in[- ]the[- ]loop)\b", 10, "Automatisierung / Integration"),
    (r"\b(?:defen[cs]e(?!\s+trade\s+controls)|verteidigung\w*|rüstung(?:s\w*)?|ruestung(?:s\w*)?|military(?!\s+(?:leave|spouse|veteran|service|discount|status))|lenkflugkörper\w*|flugkörper\w*|drohnen?|drones?|uavs?|uas|counter[- ]uas|c[- ]uas|interceptors?|unbemannt\w*|ugv|avionik|avionics)\b", 10, "Defence & Drones"),
    (r"\b(?:medizintechnik|medtech|medical\s+(?:devices?|imaging|robotics|ai))\b", 8, "Medizintechnik"),
    (r"\b(?:simulation\w*|simulink|matlab|modellier\w*|digital\s+twin)\b", 10, "Simulation / MATLAB / Simulink"),
    (r"\b(?:systems?\s+engineer\w*|systemingenieur\w*|systemarchitektur\w*|functional\s+safety|funktionale\s+sicherheit|iso\s*26262)\b", 8, "Systems Engineering"),
    (r"\b(?:power\s+grid|stromnetz\w*|stadtwerke|erneuerbare\s+energien?|renewable\s+energy|smart\s+grid|energiesystem\w*|bess|batteriespeicher\w*)\b", 6, "Energie"),
    (r"\b(?:automotive|fahrzeug(?:entwicklung|bau|technik|systeme|daten)\w*|autonomous\s+vehicle\w*|electric\s+vehicle\w*)\b", 3, "Automotive"),
)

# C++ deliberately excluded from bonus skills (Basics only); exact word boundaries prevent false hits (e.g. 'rag' in 'Vertrag')!
SKILL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bpython\b", re.I), "Python"),
    (re.compile(r"\bfastapi\b", re.I), "FastAPI"),
    (re.compile(r"\brag\b|retrieval[- ]augmented", re.I), "RAG"),
    (re.compile(r"\bpytorch\b", re.I), "PyTorch"),
    (re.compile(r"(?<!\w)c#(?!\w)", re.I), "C#"),
    (re.compile(r"(?<![\w.])\.net\b", re.I), ".NET"),
    (re.compile(r"\bjava\b", re.I), "Java"),
    (re.compile(r"\bvue\.?js\b", re.I), "Vue.js"),
    (re.compile(r"\breact\b", re.I), "React"),
    (re.compile(r"\btypescript\b", re.I), "TypeScript"),
    (re.compile(r"\bmatlab\b", re.I), "MATLAB"),
    (re.compile(r"\bsimulink\b", re.I), "Simulink"),
    (re.compile(r"\b(?:can[- ]?bus|can\s*fd)\b", re.I), "CAN-Bus"),
    (re.compile(r"\bros\s?2\b", re.I), "ROS 2"),
    (re.compile(r"\bdocker\b", re.I), "Docker"),
    (re.compile(r"\b(?:kubernetes|k8s)\b", re.I), "Kubernetes"),
    (re.compile(r"\bgithub\s+actions\b", re.I), "GitHub Actions"),
    (re.compile(r"\blinux\b", re.I), "Linux"),
    (re.compile(r"\blangchain\b", re.I), "LangChain"),
    (re.compile(r"\bazure\b", re.I), "Azure"),
    (re.compile(r"\b(?:vector\s+search|vektorsuche)\b", re.I), "Vektorsuche"),
    (re.compile(r"\bbevfusion\b", re.I), "BEVFusion"),
    (re.compile(r"\blidar\b", re.I), "LiDAR"),
    (re.compile(r"\b(?:(?:ai|ki|llm|autonomous|intelligent)[- ]+agents?|multi[- ]agent\w*|agentic)\b", re.I), "Agenten"),
    (re.compile(r"\b(?:prompt\s+engineering|prompting|tool[- ]calling)\b", re.I), "Prompting"),
    (re.compile(r"\b(?:rest[- ]?apis?|graphql|openapi)\b", re.I), "API"),
    (re.compile(r"\bgit(?:lab|hub)?\b", re.I), "Git"),
    (re.compile(r"\b(?:sql|postgresql|sqlite)\b", re.I), "SQL / DB"),
    (re.compile(r"\bworkflow[- ]automation\b", re.I), "Automation"),
)

MIN_AI_RULE_SCORE = 8


def rule_eligibility(score: int, hard_excl: str, tier: int, preference: str = "neutral") -> tuple[bool, str]:
    """Return (eligible, reason_if_ineligible) for whether a job qualifies rule-based for Gemini 4 Hydrogen rating."""
    if preference == "excluded":
        return False, "Unternehmen ist auf der Ausschlussliste"
    if hard_excl:
        return False, hard_excl
    if score < MIN_AI_RULE_SCORE:
        return False, f"Regel-Score zu niedrig ({score} < {MIN_AI_RULE_SCORE} Pkt. — fachlich oder wegen Seniorität ausgesiebt)"
    return True, ""


NON_TECH_ROLE = re.compile(
    r"\b(?:hr|human\s+resources|recruiter\w*|recruiting|talent\s+acquisition|people\s+(?:&|and)\s+culture|people\s+analytics|"
    r"marketing|copywriter|pr\s+manager|communications?\s+manager|"
    r"account\s+executive|business\s+development\s+representative|\bbdr\b|\bsdr\b|key\s+account|"
    r"sales(?:\s+engineer|\s+manager|\s+representative)?|vertrieb\w*|presales|pre[- ]sales|customer\s+success|"
    r"product\s+manager|produktmanager\w*|product\s+owner|"
    r"buchhalt\w*|accountant|accounting|finanzbuchhalt\w*|payroll|lohnbuchhalt\w*|finance|controlling|controller\b|"
    r"supply\s+chain|einkäufer\w*|buyer|procurement|"
    r"legal\s+counsel|syndikus\w*|volljurist\w*|paralegal|"
    r"office\s+manager\w*|empfangs\w*|receptionist|executive\s+assistant|team\s+assistant|"
    r"facility\s+manag\w*|hausmeister\w*|lagerist\w*|logistikmitarbeiter\w*|technician\w*|techniker\w*|maintenance|monteur\w*|bauingenieur\w*)\b",
    re.I,
)

# Roles focused on mechanical/PCB/electrical/RF hardware or technician work without clear software/AI title
HARDWARE_ONLY_ROLE = re.compile(
    r"\b(?:mechanical|maschinenbau|konstrukteur\w*|cad\b|pcb\b|schaltungsdesign|circuit\s+design|rf\b|hochfrequenz|"
    r"thermal\b|thermisch\w*|antriebs(?:strang|technik)|propulsion|neutronics|manufacturing\s+technician|production\s+technician|"
    r"cnc\b|fräser\w*|elektroniker\w*|mechatroniker\w*(?!.*(?:software|entwickl))|hardware\s+(?:engineer|developer|design)|electronics?\s+engineer|electrical\s+engineer|elektrotechnik\w*)\b",
    re.I,
)
SOFTWARE_AI_TITLE = re.compile(
    r"\b(?:software\w*|entwickl\w*|developer\w*|python|ai\b|\bki\b|ml\b|machine\s+learning|deep\s+learning|llm|rag|agent\w*|"
    r"perception|vision|algo\w*|fullstack|full[- ]stack|backend|frontend|cloud|devops|mlops|informatik\w*|data\s+(?:scien|engin|analy))\b",
    re.I,
)

TECHNICAL = re.compile(
    r"\b(?:engineer\w*|ingenieur\w*|entwickl\w*|developer\w*|software\w*|robot\w*|autonom\w*|perception|vision|"
    r"machine\s+learning|deep\s+learning|ki|ai|llm|rag|agentic|fullstack|full[- ]stack|backend|frontend|"
    r"simulation\w*|system\w*|test\w*|valid\w*|automat\w*|data|integration|application|applikations\w*|"
    r"scientist\w*|research\w*|wissenschaftl\w*|informatik\w*|physik\w*|mathematik\w*|fachinformatik\w*|"
    r"regelung\w*|control|navigation|slam|sensor\w*|radar|lidar|avionik|avionics|embedded|firmware|"
    r"devops|mlops|cloud|platform|architect\w*|architekt\w*|algorithm\w*|cyber|security|technical\s+staff|specialist)\b",
    re.I,
)
SENIOR = re.compile(
    r"\b(?:senior|sr\.?(?!\w)|staff|principal|chief|lead|head|director|vp|vice\s+president|"
    r"manager\w*|teamlead(?:er)?|teamleiter\w*|gruppenleiter\w*|abteilungsleiter\w*|bereichsleiter\w*|"
    r"entwicklungsleiter\w*|projektleiter\w*|teilprojektleiter\w*|produktmanager\w*|leiter(?:in)?|"
    r"expert\w*|architect\w*|architekt\w*)\b",
    re.I,
)
ENTRY = re.compile(
    r"\b(?:junior|early[- ]career|entry[- ]level|trainee|berufseinsteig\w*|berufseinstieg|absolvent\w*|young\s+professional\w*|new\s+grad)\b",
    re.I,
)
EXPERIENCE = re.compile(
    r"\b(\d{1,2})(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*(?:years?|jahre\w*)\s+(?:of\s+)?"
    r"(?:\S+\s+){0,4}?(?:berufs\s*)?(?:experience|erfahrung\w*)|"
    r"\b(\d{1,2})\s*[-–]?\s*jährig\w*\s+(?:\S+\s+){0,2}?(?:berufs)?erfahrung|"
    r"(?:mehrjährig\w*\s+berufserfahrung)|"
    r"(?:berufs)?erfahrung\s+(?:von\s+)?(?:mindestens\s+|über\s+|ca\.\s+)?(\d{1,2})\s*\+?\s*(?:jahren?|years?)",
    re.I,
)
# Only exempt TRUE OR alternatives (never "C++ and Python" or "C++ sowie Python"!)
LANGUAGE_ALTERNATIVE = re.compile(
    r"(?:mindestens\s+(?:einer|eine)|at\s+least\s+one|one\s+of|entweder)[^.!?\n]{0,140}(?:Python[^.!?\n]{0,70}C\+\+|C\+\+[^.!?\n]{0,70}Python)|"
    r"C\+\+\s*(?:und/oder|and/or|oder|or|/)\s*Python|Python\s*(?:und/oder|and/or|oder|or|/)\s*C\+\+|"
    r"(?:such\s+as|wie|beispielsweise|e\.g\.|z\.b\.)[^.!?\n]{0,60}(?:C\+\+[^.!?\n]{0,30}\b(?:or|oder)\b[^.!?\n]{0,20}Python|Python[^.!?\n]{0,30}\b(?:or|oder)\b[^.!?\n]{0,20}C\+\+)",
    re.I,
)


def extract_signals(title: str, text: str) -> dict:
    experience: list[int] = []
    if EXPERIENCE.search(text):
        for raw_sentence in re.split(r"[.!?\n]+(?=\s|$)", text):
            sentence = re.sub(r"^[\s•\-\*]+", "", raw_sentence).strip()
            if not sentence:
                continue
            matches = list(EXPERIENCE.finditer(sentence))
            if not matches:
                continue
            if (
                re.search(r"\b(?:unternehmen|company|we|wir)\b", sentence, re.I)
                and re.search(r"\b(?:mit\s+(?:über|mehr als)|with\s+(?:over|more than))\s+\d{1,2}\s+(?:jahren|years)", sentence, re.I)
                and not re.search(r"\b(?:you|your|du|dich|euch|dein\w*|bringst|bringen|bewerber\w*|candidate\w*|require\w*)\b", sentence, re.I)
            ):
                continue
            if re.search(r"preferred|ideally|nice.to.have|wünschenswert|von vorteil|idealerweise", sentence, re.I):
                continue
            if (
                re.search(r"you|your|require|qualification|must|bring|minimum|mindestens|erfahrung|profil|verfüg|\bhave\b|\bdegree\b|\bmaster\b|at\s+least", sentence, re.I)
                or re.match(r"^\s*\d{1,2}\s*\+?\s*(?:years?|jahre)", sentence, re.I)
            ):
                for m in matches:
                    val = next((int(g) for g in m.groups() if g is not None), None)
                    if val is None and "mehrjährig" in m.group(0).lower():
                        val = 3
                    if val is not None and val <= 20:
                        experience.append(val)
    years = max(experience, default=0)
    alternatives = list(LANGUAGE_ALTERNATIVE.finditer(text))
    strong_cpp = any(
        not any(a.start() <= m.start() + m.group(0).lower().find("c++") < a.end() for a in alternatives)
        for m in re.finditer(
            r"(?:strong|excellent|proficien\w*|expert\w*|advanced|deep|mandatory|required|fundiert\w*|sehr gut\w*|sicher\w*|tiefgehend\w*|modern\s+c\+\+)[^.\n]{0,65}C\+\+|"
            r"C\+\+[^.\n]{0,65}(?:strong|proficien\w*|expert\w*|advanced|mandatory|required|fundiert\w*|kenntnisse\s+erforderlich|zwingend)",
            text,
            re.I,
        )
    )
    senior_tit = bool(SENIOR.search(title))
    text_entry_clean = re.sub(r"\b(?:mentor\w*\s+(?:more\s+)?junior|graduate\s+degree)\b", "", text, flags=re.I)
    entry = (bool(ENTRY.search(title)) or (bool(ENTRY.search(text_entry_clean)) and years < 3)) and not senior_tit
    phd_required = bool(
        re.search(
            r"(?:phd|doctorate|promotion|doktortitel)\s+(?:required|mandatory|erforderlich|vorausgesetzt)|"
            r"(?:abgeschlossene[ns]?\s+promotion|erfolgreich\s+abgeschlossene\s+promotion)",
            text,
            re.I,
        )
    )
    high_travel = bool(re.search(r"\b(?:[4-9]\d|100)\s*%[^\n.]{0,30}(?:travel|reise|reisetätigkeit|mobilität)", text, re.I))
    return {
        "minimum_years": years,
        "strong_cpp": strong_cpp,
        "entry_signal": entry,
        "phd_required": phd_required,
        "high_travel": high_travel,
        "technical": bool(TECHNICAL.search(title)),
        "senior_title": senior_tit,
    }


def relevance(title: str, description: str, preferred: bool = False) -> tuple[int, str]:
    title_text = norm(title)
    desc_raw = description or ""
    if len(desc_raw) > 12000:
        desc_norm_slice = norm(desc_raw[:8500] + "\n" + desc_raw[-3500:])
    else:
        desc_norm_slice = norm(desc_raw)
    full_text = title_text + " " + desc_norm_slice
    reasons: list[str] = []
    title_score = 0
    description_score = 0

    for pattern, weight, label in TERMS:
        if re.search(pattern, title_text):
            title_score += weight
            if label not in reasons:
                reasons.append(label)
        elif re.search(pattern, desc_norm_slice):
            description_score += max(2, weight // 2)
            if len(reasons) < 3 and label not in reasons:
                reasons.append(label)

    score = min(title_score, 40) + min(description_score, 24)
    matched_skills = [label for pat, label in SKILL_PATTERNS if pat.search(full_text)]
    score += min(len(matched_skills) * 3, 18)
    if matched_skills:
        reasons.append("Skills: " + ", ".join(matched_skills[:4]))

    has_sw_ai_title = bool(SOFTWARE_AI_TITLE.search(title_text))
    if TECHNICAL.search(title_text):
        score += 6
    else:
        if description_score < 14 and len(matched_skills) < 2:
            score = min(score, 8)
        else:
            score = max(0, score - 8)

    # Downrank pure hardware/mechanical/electrical/technician roles lacking software/AI in title
    if HARDWARE_ONLY_ROLE.search(title_text) and not has_sw_ai_title:
        score = max(0, score - 22)
        reasons.append("Hardware-/Mechanik-/Techniker-Fokus")

    # Clamp commercial/PM/Sales/HR/Recruiting/Technicians even if 'AI'/'Software' appears in title
    if NON_TECH_ROLE.search(title_text):
        score = min(score, 12)
        reasons.append("Nicht-SW-Zielrolle")

    sig = extract_signals(title, description or "")
    if sig["entry_signal"]:
        score += 12
        reasons.append("Berufseinstieg")
    if sig["strong_cpp"]:
        score -= 2 if sig["entry_signal"] else 5
        reasons.append("C++-Einarbeitung")
    if sig["phd_required"]:
        score -= 12
        reasons.append("Promotion vorausgesetzt")
    if sig["high_travel"]:
        score -= 10
        reasons.append("Hoher Reiseanteil (>30%)")

    # Pure requirements engineering without coding in title
    if re.search(r"\brequirements?\s+engineer\w*|anforderungsmanager\w*", title_text) and not has_sw_ai_title:
        score -= 12

    seniority_penalty = 0
    if sig["minimum_years"] >= 5:
        seniority_penalty += 10
        reasons.append(f"{sig['minimum_years']}+ J. Erfahrung")
    elif sig["minimum_years"] >= 3:
        seniority_penalty += 5

    if re.search(r"\b(?:director|head\b|vice\s+president|\bvp\b|chief|abteilungsleiter\w*|bereichsleiter\w*|entwicklungsleiter\w*)", title_text):
        seniority_penalty += 20
        reasons.append("Führungsposition")
    elif re.search(r"\b(?:staff|principal|architect\w*|architekt\w*)", title_text):
        seniority_penalty += 14
        reasons.append("hohe Seniorität")
    elif re.search(r"\b(?:lead|manager\w*|teamleiter\w*|teamlead\w*|gruppenleiter\w*|projektleiter\w*|teilprojektleiter\w*|produktmanager\w*)", title_text):
        seniority_penalty += 12
        reasons.append("Lead-Rolle")
    elif re.search(r"\b(?:senior|sr\.?(?!\w)|expert\w*)", title_text):
        seniority_penalty += 9
        reasons.append("Senior-Rolle prüfen")

    score -= min(seniority_penalty, 22)

    if excluded_employment(title):
        score = 0
        reasons = ["keine Vollzeitstelle"]
    elif preferred and score >= 8:
        score += 7
        reasons.append("bevorzugtes Unternehmen")

    return max(0, min(100, score)), "; ".join(reasons[:5]) or "Keine klaren Profiltreffer"
