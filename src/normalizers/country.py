"""
normalizers/country.py
======================
Normalize country strings to ISO-3166 alpha-2 codes.
Uses a lookup table — no external library required.
"""
from typing import Optional

_COUNTRY_TABLE: dict[str, str] = {
    # Alpha-2 pass-through (will be upper-cased)
    # Common names & variants
    "united states": "US", "united states of america": "US",
    "usa": "US", "u.s.a": "US", "u.s": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "england": "GB", "britain": "GB", "scotland": "GB", "wales": "GB",
    "india": "IN", "bharat": "IN",
    "australia": "AU", "oz": "AU",
    "canada": "CA",
    "germany": "DE", "deutschland": "DE",
    "france": "FR",
    "singapore": "SG",
    "netherlands": "NL", "holland": "NL",
    "japan": "JP",
    "china": "CN", "people's republic of china": "CN",
    "brazil": "BR", "brasil": "BR",
    "mexico": "MX", "méxico": "MX",
    "south africa": "ZA",
    "uae": "AE", "united arab emirates": "AE",
    "new zealand": "NZ", "aotearoa": "NZ",
    "sweden": "SE", "sverige": "SE",
    "norway": "NO", "norge": "NO",
    "denmark": "DK", "danmark": "DK",
    "finland": "FI", "suomi": "FI",
    "switzerland": "CH", "schweiz": "CH",
    "austria": "AT", "österreich": "AT",
    "spain": "ES", "españa": "ES",
    "italy": "IT", "italia": "IT",
    "portugal": "PT",
    "ireland": "IE", "éire": "IE",
    "israel": "IL",
    "poland": "PL", "polska": "PL",
    "hong kong": "HK",
    "malaysia": "MY",
    "indonesia": "ID",
    "philippines": "PH",
    "pakistan": "PK",
    "bangladesh": "BD",
    "russia": "RU", "russian federation": "RU",
    "ukraine": "UA",
    "turkey": "TR", "türkiye": "TR",
    "egypt": "EG",
    "nigeria": "NG",
    "kenya": "KE",
    "ghana": "GH",
    "argentina": "AR",
    "colombia": "CO",
    "chile": "CL",
    "peru": "PE",
    "thailand": "TH",
    "vietnam": "VN", "viet nam": "VN",
    "south korea": "KR", "korea": "KR",
    "taiwan": "TW",
    "sri lanka": "LK",
    "nepal": "NP",
    "cambodia": "KH",
    "myanmar": "MM", "burma": "MM",
    "ethiopia": "ET",
    "tanzania": "TZ",
    "uganda": "UG",
}

import re as _re
_ALPHA2_RE = _re.compile(r"^[A-Za-z]{2}$")


def normalize_country(raw: str) -> Optional[str]:
    """
    Returns ISO-3166 alpha-2 code (e.g. 'IN', 'US') or None.

    Parameters
    ----------
    raw : country name, code, or variant (case-insensitive)
    """
    if not raw or not isinstance(raw, str):
        return None

    cleaned = raw.strip()
    lookup_key = cleaned.lower().replace(".", "").strip()

    # Check the alias table FIRST: some 2-letter strings (e.g. "UK") are
    # common informal aliases rather than valid ISO-3166 codes, and must
    # resolve to the correct code (GB) rather than passing through as-is.
    if lookup_key in _COUNTRY_TABLE:
        return _COUNTRY_TABLE[lookup_key]

    # Otherwise, if it already looks like a genuine alpha-2 code, accept it.
    if _ALPHA2_RE.match(cleaned):
        return cleaned.upper()

    return None


# ── Major city → country lookup ───────────────────────────────────────────────
# Used by free-text extractors (notes, resumes) to infer a country from
# informal phrasing like "Based in Hyderabad" that lacks an explicit
# "Country:" label. Intentionally limited to major/unambiguous cities.
_CITY_TO_COUNTRY: dict[str, str] = {
    # India
    "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN", "delhi": "IN",
    "new delhi": "IN", "hyderabad": "IN", "pune": "IN", "chennai": "IN",
    "kolkata": "IN", "ahmedabad": "IN", "gurgaon": "IN", "gurugram": "IN",
    "noida": "IN", "jaipur": "IN", "kochi": "IN", "chandigarh": "IN",
    "lucknow": "IN", "indore": "IN", "nagpur": "IN", "coimbatore": "IN",
    # US
    "new york": "US", "san francisco": "US", "los angeles": "US",
    "chicago": "US", "seattle": "US", "austin": "US", "boston": "US",
    "denver": "US", "atlanta": "US", "dallas": "US", "houston": "US",
    "miami": "US", "san jose": "US", "san diego": "US", "phoenix": "US",
    # UK
    "london": "GB", "manchester": "GB", "birmingham": "GB", "edinburgh": "GB",
    # Other major tech hubs
    "toronto": "CA", "vancouver": "CA", "berlin": "DE", "munich": "DE",
    "paris": "FR", "amsterdam": "NL", "singapore": "SG", "tokyo": "JP",
    "sydney": "AU", "melbourne": "AU", "dublin": "IE", "tel aviv": "IL",
    "dubai": "AE", "shanghai": "CN", "beijing": "CN", "hong kong": "HK",
}


def infer_country_from_city_mention(text: str) -> Optional[str]:
    """
    Scan free text for a known major city name and return its country code.
    Used as a fallback when no explicit country/location label is present
    (e.g. "Based in Hyderabad" in informal recruiter notes).
    """
    if not text:
        return None
    text_lower = text.lower()
    for city, country in _CITY_TO_COUNTRY.items():
        if _re.search(r"\b" + _re.escape(city) + r"\b", text_lower):
            return country
    return None