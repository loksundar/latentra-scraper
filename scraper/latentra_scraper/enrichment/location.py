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
}

# Reverse lookup: abbreviation → abbreviation
_STATE_ABBREVS = {v: v for v in US_STATES.values()}
_STATE_ABBREVS.update({k: v for k, v in US_STATES.items()})

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
}

# Well-known US cities → state
US_CITY_STATE = {
    "new york": "NY", "nyc": "NY", "manhattan": "NY", "brooklyn": "NY",
    "san francisco": "CA", "sf": "CA", "los angeles": "CA", "la": "CA",
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
}

# Remote keywords
REMOTE_KEYWORDS = ["remote", "work from home", "wfh", "anywhere", "distributed"]


def normalize_location(location_raw: str, title: str = "", description_text: str = "") -> dict:
    """
    Parse a raw location string into structured fields.

    Returns:
        {
            "location_city": str|None,
            "location_state": str|None,  (2-letter code for US)
            "location_country": str|None,
            "is_remote_friendly": bool,
        }
    """
    result = {
        "location_city": None,
        "location_state": None,
        "location_country": None,
        "is_remote_friendly": False,
    }

    if not location_raw:
        # Check title/description for remote signal
        combined = f"{title} {description_text}".lower()
        for kw in REMOTE_KEYWORDS:
            if kw in combined:
                result["is_remote_friendly"] = True
                break
        return result

    raw = location_raw.strip()
    raw_lower = raw.lower()

    # Check for remote
    for kw in REMOTE_KEYWORDS:
        if kw in raw_lower:
            result["is_remote_friendly"] = True
            break

    # Also check title + description for remote
    if not result["is_remote_friendly"]:
        combined = f"{title} {description_text}".lower()
        for kw in REMOTE_KEYWORDS:
            if kw in combined:
                result["is_remote_friendly"] = True
                break

    # Split by common separators: ", " " - " " / " " | "
    parts = re.split(r'\s*[,/|–—-]\s*|\s+(?:and)\s+', raw)
    parts = [p.strip() for p in parts if p.strip()]
    parts_lower = [p.lower() for p in parts]

    # Try to find country
    for i, part in enumerate(parts_lower):
        clean = part.strip("() ")
        if clean in COUNTRY_MAP and COUNTRY_MAP[clean]:
            result["location_country"] = COUNTRY_MAP[clean]
            break

    # Try to find US state
    for part in parts_lower:
        clean = part.strip("() ")
        # Check 2-letter abbreviation
        if len(clean) == 2 and clean.upper() in _STATE_ABBREVS.values():
            result["location_state"] = clean.upper()
            if not result["location_country"]:
                result["location_country"] = "United States"
            break
        # Check full state name
        if clean in US_STATES:
            result["location_state"] = US_STATES[clean]
            if not result["location_country"]:
                result["location_country"] = "United States"
            break

    # Try to find city
    for part in parts_lower:
        clean = part.strip("() ")
        # Skip if this was already matched as state/country
        if clean in US_STATES or (len(clean) == 2 and clean.upper() in _STATE_ABBREVS.values()):
            continue
        if clean in COUNTRY_MAP:
            continue
        # Check known US cities
        if clean in US_CITY_STATE:
            result["location_city"] = parts[parts_lower.index(part)].strip("() ")
            if not result["location_state"]:
                result["location_state"] = US_CITY_STATE[clean]
            if not result["location_country"]:
                result["location_country"] = "United States"
            break

    # If no city found from known list, use first non-state non-country part
    if not result["location_city"] and parts:
        for part in parts:
            clean_low = part.lower().strip("() ")
            if clean_low in US_STATES or clean_low in COUNTRY_MAP:
                continue
            if len(clean_low) == 2 and clean_low.upper() in _STATE_ABBREVS.values():
                continue
            if any(kw in clean_low for kw in REMOTE_KEYWORDS):
                continue
            if clean_low:
                result["location_city"] = part.strip("() ")
                break

    return result
