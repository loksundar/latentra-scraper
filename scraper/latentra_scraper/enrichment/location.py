"""
Location normalization — parses raw location strings into structured fields.
No external geocoding — pure regex + dictionary matching.
"""

import re

# US state abbreviations and full names → standard 2-letter code
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
    "d.c.": "DC", "dc": "DC", "washington dc": "DC", "washington d.c.": "DC",
    "washington, d.c.": "DC",
}

# Reverse lookup: abbreviation → abbreviation
_STATE_ABBREVS = {v: v for v in US_STATES.values()}
_STATE_ABBREVS.update({k: v for k, v in US_STATES.items()})

# Two-letter codes that are also ordinary words / other abbreviations. They
# only count as a state when the ATS wrote them in capitals ("Portland, OR"),
# never when lower-cased ("San Francisco or Palo Alto").
_AMBIGUOUS_STATE_CODES = {"IN", "OR", "ME", "OK", "HI", "DE", "LA", "PA", "ID", "MA", "CO", "WA", "MT", "AL", "AR", "MS", "MO", "MD", "NE", "OH"}

# Country aliases → standard name
COUNTRY_MAP = {
    "us": "United States", "usa": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "united states": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "united kingdom": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    "canada": "Canada", "ca": None,  # CA is ambiguous (California vs Canada)
    "germany": "Germany", "deutschland": "Germany",
    "france": "France", "japan": "Japan", "india": "India",
    "australia": "Australia", "singapore": "Singapore",
    "ireland": "Ireland", "netherlands": "Netherlands",
    "sweden": "Sweden", "switzerland": "Switzerland",
    "spain": "Spain", "italy": "Italy", "brazil": "Brazil",
    "south korea": "South Korea", "korea": "South Korea",
    "israel": "Israel", "poland": "Poland", "mexico": "Mexico",
    "portugal": "Portugal", "denmark": "Denmark", "norway": "Norway",
    "finland": "Finland", "belgium": "Belgium", "austria": "Austria",
    "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "new zealand": "New Zealand", "taiwan": "Taiwan",
    "hong kong": "Hong Kong", "china": "China",
    "philippines": "Philippines", "indonesia": "Indonesia",
    "malaysia": "Malaysia", "thailand": "Thailand", "vietnam": "Vietnam",
    "romania": "Romania", "ukraine": "Ukraine", "argentina": "Argentina",
    "colombia": "Colombia", "chile": "Chile", "costa rica": "Costa Rica",
    "uae": "United Arab Emirates", "united arab emirates": "United Arab Emirates",
    "dubai": "United Arab Emirates", "qatar": "Qatar",
    "saudi arabia": "Saudi Arabia", "egypt": "Egypt",
    "south africa": "South Africa", "nigeria": "Nigeria", "kenya": "Kenya",
    "greece": "Greece", "hungary": "Hungary", "turkey": "Turkey",
}

# Well-known US cities → state
US_CITY_STATE = {
    "new york": "NY", "new york city": "NY", "nyc": "NY", "manhattan": "NY", "brooklyn": "NY",
    "san francisco": "CA", "sf": "CA", "san francisco bay area": "CA", "bay area": "CA",
    "south san francisco": "CA", "los angeles": "CA", "la": "CA",
    "san jose": "CA", "san diego": "CA", "palo alto": "CA", "mountain view": "CA",
    "sunnyvale": "CA", "cupertino": "CA", "menlo park": "CA", "santa clara": "CA",
    "redwood city": "CA", "oakland": "CA", "berkeley": "CA", "irvine": "CA",
    "seattle": "WA", "bellevue": "WA", "redmond": "WA",
    "austin": "TX", "dallas": "TX", "houston": "TX", "san antonio": "TX", "plano": "TX",
    "chicago": "IL", "boston": "MA", "cambridge": "MA",
    "denver": "CO", "boulder": "CO",
    "atlanta": "GA", "miami": "FL", "tampa": "FL", "orlando": "FL",
    "phoenix": "AZ", "scottsdale": "AZ",
    "portland": "OR", "nashville": "TN", "charlotte": "NC", "raleigh": "NC",
    "durham": "NC", "pittsburgh": "PA", "philadelphia": "PA",
    "detroit": "MI", "ann arbor": "MI", "minneapolis": "MN",
    "salt lake city": "UT", "washington": "DC", "arlington": "VA",
    "mclean": "VA", "reston": "VA", "herndon": "VA",
    # Workday / Oracle tenants often send a bare city ("Baltimore", "SAN JOSE").
    "baltimore": "MD", "bethesda": "MD", "columbia": "MD", "fort meade": "MD",
    "alexandria": "VA", "chantilly": "VA", "fairfax": "VA", "tysons": "VA", "richmond": "VA",
    "columbus": "OH", "cleveland": "OH", "cincinnati": "OH", "indianapolis": "IN",
    "kansas city": "MO", "st. louis": "MO", "st louis": "MO", "milwaukee": "WI", "madison": "WI",
    "las vegas": "NV", "reno": "NV", "sacramento": "CA", "long beach": "CA", "fremont": "CA",
    "santa monica": "CA", "san mateo": "CA", "foster city": "CA", "pasadena": "CA", "emeryville": "CA",
    "jacksonville": "FL", "fort lauderdale": "FL", "memphis": "TN", "louisville": "KY",
    "oklahoma city": "OK", "omaha": "NE", "honolulu": "HI", "hartford": "CT", "providence": "RI",
    "buffalo": "NY", "rochester": "NY", "albany": "NY", "boise": "ID", "tucson": "AZ", "tempe": "AZ",
    "chandler": "AZ", "mesa": "AZ", "albuquerque": "NM", "el paso": "TX", "fort worth": "TX",
    "huntsville": "AL", "birmingham": "AL", "new orleans": "LA", "charleston": "SC", "greenville": "SC",
    "alpharetta": "GA", "savannah": "GA", "chapel hill": "NC", "cary": "NC", "lehi": "UT", "provo": "UT",
    "colorado springs": "CO", "fort collins": "CO", "spokane": "WA", "tacoma": "WA", "kirkland": "WA",
    "grand rapids": "MI", "des moines": "IA", "little rock": "AR", "anchorage": "AK",
}

# Tokens that describe a region / work arrangement, never a city.
_NOT_A_CITY = {
    "remote", "hybrid", "onsite", "on-site", "on site", "in office", "in-office",
    "flexible", "amer", "emea", "apac", "latam", "na", "north america", "americas",
    "global", "worldwide", "n/a", "tbd", "various", "multiple", "multiple locations",
    "other", "anywhere", "distributed", "hq", "headquarters", "office", "central",
    "east", "west", "east coast", "west coast", "midwest", "northeast", "southeast",
    "pacific", "eastern", "central time", "pacific time", "us", "usa", "u.s.",
}

# Remote keywords — in a LOCATION string any of these means remote work.
REMOTE_KEYWORDS = ["remote", "work from home", "wfh", "anywhere", "distributed"]

# In a TITLE only the explicit word counts ("Backend Engineer (Remote)").
_TITLE_REMOTE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.I)

# In the DESCRIPTION the bare word "remote" (or "distributed", "anywhere") is
# noise: "distributed systems", "remote teams", "may work remotely two days a
# week", "this is not a remote role". Only a sentence that states the role
# itself is remote counts, and not when it is negated just before.
_DESC_REMOTE = re.compile(
    r"\b(?:fully|100\s*%|completely|entirely|permanently)[\s-]*remote\b"
    r"|\bremote[\s-]*first\b"
    r"|\bthis (?:role|position|job|opportunity) (?:is|will be) (?:a )?(?:fully |100% )?remote\b"
    r"|\b(?:is|are) (?:a )?(?:fully |100% )?remote (?:role|position|job|opportunity)\b"
    r"|\bremote (?:role|position|job|opportunity)\b"
    r"|\bwork from anywhere\b"
    r"|\bwork[\s-]*from[\s-]*home\b",
    re.I,
)
_NEGATION_BEFORE = re.compile(r"\b(?:not|no|isn't|is not|cannot|can't|unable|never|non)\W{0,3}\w*\W{0,3}$", re.I)

_SPLIT = re.compile(r"\s*[,;/|()&–—-]\s*|\s+(?:and|or)\s+", re.I)

# Work-arrangement words glued to a place without a separator: "Remote
# Washington", "Remote in Texas", "Hybrid Seattle" (Workday tenants such as
# Adobe). Removing them leaves the place name for the state / city lookup.
_MODE_WORDS = re.compile(
    r"\b(?:remote|hybrid|on-?site|work from home|wfh|anywhere|distributed|in|from|based|only|within)\b",
    re.I,
)


def _is_state(part_raw: str):
    """2-letter code for a part that names a US state, else None."""
    clean = part_raw.strip("() .").lower() if len(part_raw.strip("() ")) > 2 else part_raw.strip("() ")
    if len(clean) == 2:
        code = clean.upper()
        if code not in _STATE_ABBREVS.values():
            return None
        if code in _AMBIGUOUS_STATE_CODES and clean != code:
            return None   # lower-case "in"/"or"/"me" is a word, not a state
        return code
    clean = clean.lower()
    return US_STATES.get(clean) or US_STATES.get(clean + ".") if clean else None


# ISO-3166 alpha-2 codes ATSs put after a city ("Dublin, IE", "Sydney, AU").
# Only codes that are NOT also a US state code, and only when upper-cased.
ISO2_COUNTRY = {
    "IE": "Ireland", "AU": "Australia", "GB": "United Kingdom", "FR": "France",
    "NL": "Netherlands", "ES": "Spain", "JP": "Japan", "SG": "Singapore",
    "BR": "Brazil", "MX": "Mexico", "CH": "Switzerland", "PL": "Poland",
    "PT": "Portugal", "NZ": "New Zealand", "ZA": "South Africa", "AE": "United Arab Emirates",
    "KR": "South Korea", "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "BE": "Belgium",
    "DK": "Denmark", "FI": "Finland", "CZ": "Czech Republic",
    "RO": "Romania", "EG": "Egypt",
    "NG": "Nigeria", "KE": "Kenya", "PH": "Philippines", "VN": "Vietnam", "TH": "Thailand", "UA": "Ukraine", "UK": "United Kingdom",
}


def _is_country(part_raw: str):
    clean = part_raw.strip("() ").lower()
    if clean in COUNTRY_MAP:
        return COUNTRY_MAP[clean] or "ambiguous"
    code = part_raw.strip("() ")
    if len(code) == 2 and code.isupper() and code in ISO2_COUNTRY:
        return ISO2_COUNTRY[code]
    return None


def _description_says_remote(text: str) -> bool:
    for m in _DESC_REMOTE.finditer(text):
        if not _NEGATION_BEFORE.search(text[max(0, m.start() - 30):m.start()]):
            return True
    return False


def normalize_location(location_raw: str, title: str = "", description_text: str = "") -> dict:
    """
    Parse a raw location string into structured fields.

    Returns:
        {
            "location_city": str|None,
            "location_state": str|None,  (2-letter code for US)
            "location_country": str|None,
            "is_remote_friendly": bool,
            "remote_source": "location" | "title" | "description" | None,
        }

    `remote_source` says where the remote signal came from so callers can
    treat a location/title that says "Remote" differently from a description
    sentence (only the former should flip remote_type).
    """
    result = {
        "location_city": None,
        "location_state": None,
        "location_country": None,
        "is_remote_friendly": False,
        "remote_source": None,
    }

    raw = (location_raw or "").strip()
    raw_lower = raw.lower()

    # Remote signal, strongest source first.
    if raw and any(kw in raw_lower for kw in REMOTE_KEYWORDS):
        result["is_remote_friendly"] = True
        result["remote_source"] = "location"
    elif title and _TITLE_REMOTE.search(title):
        result["is_remote_friendly"] = True
        result["remote_source"] = "title"
    elif description_text and _description_says_remote(description_text):
        result["is_remote_friendly"] = True
        result["remote_source"] = "description"

    if not raw:
        return result

    # Split "San Francisco, CA; Chicago, IL & New York, NY (HQ)" into tokens.
    parts = [p.strip() for p in _SPLIT.split(raw) if p and p.strip(" .")]
    if not parts:
        return result

    # One token that is a place only once the work-mode words are removed:
    # "Remote Washington" → WA. Seen live (Adobe/Workday): the validator
    # accepted the row as US, but it reached the site with no state or country.
    # A state NAME (or an upper-case code) is taken as the state — "Remote
    # Washington" means the state, not the district; a known city or a country
    # goes through the normal lookup below.
    if len(parts) == 1 and _is_state(parts[0]) is None and _is_country(parts[0]) is None:
        bare = re.sub(r"\s+", " ", _MODE_WORDS.sub(" ", parts[0])).strip(" -–—/,().")
        if bare and bare.lower() != parts[0].strip("() .").lower():
            state = _is_state(bare) if (len(bare) > 2 or bare.isupper()) else None
            if state:
                result["location_state"] = state
                result["location_country"] = "United States"
                return result
            if bare.lower() in US_CITY_STATE or _is_country(bare):
                parts = [bare]

    states = [_is_state(p) for p in parts]
    countries = [_is_country(p) for p in parts]

    # Country: any US evidence wins (multi-location postings like
    # "Seattle; New York; Remote in the US; Canada" are US jobs); otherwise the
    # first foreign country named.
    us_evidence = any(s for s in states) or any(c == "United States" for c in countries) \
        or any(p.lower().strip("() .") in US_CITY_STATE for p in parts) \
        or re.search(r"\b(?:us|usa|u\.s\.a?\.?|united states)\b", raw_lower) is not None
    if us_evidence:
        result["location_country"] = "United States"
    else:
        for c in countries:
            if c and c != "ambiguous":
                result["location_country"] = c
                break

    # City + state: prefer the first "City, ST" pair, then a known US city,
    # then the first standalone state.
    def city_ok(p: str) -> bool:
        low = p.lower().strip("() .")
        return bool(low) and low not in _NOT_A_CITY and _is_country(p) is None \
            and not any(kw in low for kw in REMOTE_KEYWORDS) and len(low) > 1 \
            and not re.fullmatch(r"[\d\W]+", low)

    for i in range(1, len(parts)):
        if not states[i]:
            continue
        prev = parts[i - 1]
        prev_low = prev.lower().strip("() .")
        if states[i - 1] is None and city_ok(prev):
            # "Seattle, San Francisco, New York, Chicago": a state NAME right
            # after a known city of another state is itself a city in a list.
            if len(parts[i].strip("() .")) > 2 and prev_low in US_CITY_STATE \
                    and US_CITY_STATE[prev_low] != states[i]:
                continue
            result["location_city"] = prev.strip("() ")
            result["location_state"] = states[i]
            break
        if i == 1 and states[0] and len(prev_low) > 2 and prev_low in US_CITY_STATE:
            # "New York, NY" / "Washington, DC": the first token is a state
            # NAME that is also a city name; the code after it decides.
            result["location_city"] = prev.strip("() ")
            result["location_state"] = states[i]
            break

    if not result["location_state"]:
        for i, p in enumerate(parts):
            low = p.lower().strip("() .")
            if low in US_CITY_STATE and (low not in US_STATES or len(parts) == 1):
                result["location_city"] = p.strip("() ")
                result["location_state"] = US_CITY_STATE[low]
                break
        else:
            for s in states:
                if s:
                    result["location_state"] = s
                    break

    if not result["location_city"]:
        for i, p in enumerate(parts):
            if states[i] or not city_ok(p):
                continue
            low = p.lower().strip("() .")
            if low in US_STATES:
                continue
            result["location_city"] = p.strip("() ")
            break

    if result["location_state"] and not result["location_country"]:
        result["location_country"] = "United States"
    if result["location_city"] and len(result["location_city"]) > 100:
        result["location_city"] = result["location_city"][:100].rstrip()

    return result
