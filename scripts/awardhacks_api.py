#!/usr/bin/env python3
"""
Direkte integrasjon mot awardhacks.se sitt faktiske søke-endepunkt
(POST /Home/ListResult), oppdaget via et Playwright-utforskningsscript.

Dette løser to begrensninger vi hadde med å bare skrape forsiden:
1. Kan velge kabinklasse (Business/Plus/Economy/Any) - ikke bare Business
2. Kan søke spesifikt på enveis (Return=false) - fanger opp Tokyo-seter
   som bare vises som enveis i dagens SAS-rutenett

Gjenbrukes av både check_awardhacks.py (daglig sjekk) og telegram_bot.py
(spør-kommandoer).
"""

import re
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://awardhacks.se/"
SEARCH_URL = "https://awardhacks.se/Home/ListResult"

CABIN_VALUES = {
    "business": "3",
    "plus": "2",
    "economy": "1",
    "go": "1",
    "any": "0",
    "mixed": "0",
}

# Flyplasskode -> verdien awardhacks.se faktisk bruker i From/To-feltene.
# Noen byer (Tokyo, New York) er kun tilgjengelig som by-nivå, ikke per flyplass.
AIRPORT_TO_SITE_CODE = {
    "CPH": "CPH", "ARN": "ARN", "OSL": "OSL",
    "HND": "TYO", "NRT": "TYO",
    "ORD": "ORD", "IAD": "IAD", "ATL": "ATL", "BOS": "BOS",
    "LAX": "LAX", "SFO": "SFO", "SEA": "SEA", "MIA": "MIA",
    "JFK": "NYC", "EWR": "NYC",
    "YYZ": "YYZ", "ICN": "ICN", "BKK": "BKK", "DXB": "DXB",
    "HKT": "HKT", "KBV": "KBV", "BOM": "BOM",
    "ALL": "All",
}

# GOT (Göteborg) og AAL (Aalborg) støttes ikke av awardhacks.se sitt skjema.
UNSUPPORTED_CODES = {"GOT", "AAL"}

SCANDI_CODES = {"CPH", "ARN", "OSL"}

ROUTE_RE = re.compile(r"([A-Z]{3})\s*-\s*([A-Z]{3})")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
SEATS_RE = re.compile(r"(\d+/\d+/\d+)")
DURATION_RE = re.compile(r"duration:\s*(\d+d)")
SEEN_AGO_RE = re.compile(r"seats\s+(\d+\w*\s*ago)")


def get_session_and_token():
    """Henter en ny nettleser-sesjon (cookies) og gjeldende sikkerhets-token
    fra forsiden. Må gjøres før hvert søk siden token er sesjonsbundet."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    resp = session.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if not token_input or not token_input.get("value"):
        raise RuntimeError("Fant ikke __RequestVerificationToken på forsiden.")
    return session, token_input["value"]


def resolve_site_codes(codes):
    """Slår sammen en liste flyplasskoder til de faktiske verdiene
    awardhacks.se bruker, og fjerner duplikater (f.eks HND+NRT -> kun TYO
    én gang). Returnerer en liste med (site_code, original_codes_matched)."""
    seen = {}
    unsupported = []
    for code in codes:
        code = code.upper()
        if code in UNSUPPORTED_CODES:
            unsupported.append(code)
            continue
        site_code = AIRPORT_TO_SITE_CODE.get(code, code)
        seen.setdefault(site_code, []).append(code)
    return list(seen.items()), unsupported


def search_one(session, token, from_code, to_code, cabin="any",
                return_trip=True, min_days=10, max_days=21):
    """Gjør ett søk mot awardhacks.se for en spesifikk from/to-kombinasjon.
    Returnerer en liste med rå-rader (samme format som før: dict med
    'texts' og 'tds')."""
    data = {
        "MinDays": str(min_days),
        "__Invariant": "MinDays",
        "MaxDays": str(max_days),
        "Passengers": "1",
        "CabinClass": CABIN_VALUES.get(cabin.lower(), "3"),
        "Equipment": "",
        "OutMin": "",
        "OutMax": "",
        "InMin": "",
        "InMax": "",
        "From": from_code,
        "To": to_code,
        "__RequestVerificationToken": token,
        "OpenJaw": "false",
        "Return": "true" if return_trip else "false",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = session.post(
        SEARCH_URL,
        data=data,
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            rows.append({
                "texts": [td.get_text(" ", strip=True) for td in tds],
                "tds": tds,
            })
    return rows


def parse_route_codes(text):
    m = ROUTE_RE.search(text)
    if not m:
        return None
    return m.group(1), m.group(2)


def extract_date(text):
    from datetime import date
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def extract_seats(text):
    m = SEATS_RE.search(text)
    return m.group(1) if m else "?"


def extract_duration(text):
    m = DURATION_RE.search(text)
    return m.group(1) if m else ""


def extract_seen_ago(text):
    m = SEEN_AGO_RE.search(text)
    return m.group(1) if m else ""


def date_in_range(d, date_from, date_to):
    from datetime import date
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()
    if not date_from and not date_to:
        return True
    if d is None:
        return True
    if date_from:
        y, mo, day = (int(x) for x in date_from.split("-"))
        if d < date(y, mo, day):
            return False
    if date_to:
        y, mo, day = (int(x) for x in date_to.split("-"))
        if d > date(y, mo, day):
            return False
    return True


def build_sas_flex_url(from_code, to_code, out_date):
    """Bygger en lenke til SAS sin egen fleksible-datoer-kalendervisning for
    en gitt rute/dato (samme URL-mønster SAS selv genererer, uten å binde
    til et spesifikt flightnummer). Åpner rett i nettleseren, viser priser
    for nærliggende dager automatisk."""
    if not out_date:
        return None
    date_str = out_date.strftime("%Y%m%d")
    return (
        f"https://www.sas.se/en/book/flights?"
        f"search=OW_{from_code}-{to_code}-{date_str}_a1c0i0y0&bookingFlow=points"
    )


def build_match(row, group_label):
    """Bygger en ferdig match-dict fra en rå-rad, uavhengig av om det er
    rundtur (5 kolonner) eller enveis (3 kolonner)."""
    texts = row["texts"]
    tds = row["tds"]
    if len(texts) < 3:
        return None
    out_codes = parse_route_codes(texts[0])
    if not out_codes:
        return None
    is_round_trip = len(texts) >= 5

    ret_codes = parse_route_codes(texts[2]) if is_round_trip else None
    last_text = texts[-1] if texts else ""
    last_td = tds[-1] if tds else None
    anchor = last_td.find("a") if last_td else None
    out_date = extract_date(texts[1])

    return {
        "group_label": group_label,
        "out_route": f"{out_codes[0]} - {out_codes[1]}",
        "out_date": out_date,
        "out_departure": texts[1] if len(texts) > 1 else "",
        "ret_route": (f"{ret_codes[0]} - {ret_codes[1]}" if ret_codes
                      else ("" if is_round_trip else "enveis")),
        "ret_departure": texts[3] if (is_round_trip and len(texts) > 3) else "",
        "seats": extract_seats(texts[0]),
        "seen_ago": extract_seen_ago(texts[0]),
        "duration": extract_duration(last_text),
        "book_href": anchor["href"] if anchor and anchor.has_attr("href") else None,
        "open_jaw": "Open jaw" in last_text,
        "sas_flex_url": build_sas_flex_url(out_codes[0], out_codes[1], out_date),
    }


def search_group_matches(group_label, from_codes, to_codes, date_from="", date_to="",
                          cabin="any", min_days=10, max_days=21):
    """Høynivå-funksjon: søker en gruppe og returnerer ferdige match-dicts,
    filtrert på dato lokalt (siden vi ikke stoler blindt på serverens eget
    datofilter-format)."""
    rows = search_group(from_codes, to_codes, cabin=cabin, min_days=min_days, max_days=max_days)
    matches = []
    for row in rows:
        m = build_match(row, group_label)
        if m is None:
            continue
        if not date_in_range(m["out_date"], date_from, date_to):
            continue
        matches.append(m)
    return matches
def search_group(from_codes, to_codes, cabin="any", min_days=10, max_days=21):
    """Søker alle relevante from/to-kombinasjoner for en søkegruppe, både
    rundtur og enveis, og returnerer alle rå-rader samlet (med duplikater
    fjernet på tvers av kombinasjoner)."""
    session, token = get_session_and_token()

    from_pairs, from_unsupported = resolve_site_codes(from_codes)
    to_pairs, to_unsupported = resolve_site_codes(to_codes)

    if from_unsupported or to_unsupported:
        print(
            f"Advarsel: awardhacks.se støtter ikke kodene "
            f"{sorted(set(from_unsupported + to_unsupported))} - hoppes over."
        )

    all_rows = []
    seen_row_texts = set()

    for from_site, _ in from_pairs:
        for to_site, _ in to_pairs:
            for return_trip in (True, False):
                rows = search_one(
                    session, token, from_site, to_site,
                    cabin=cabin, return_trip=return_trip,
                    min_days=min_days, max_days=max_days,
                )
                for row in rows:
                    key = tuple(row["texts"])
                    if key in seen_row_texts:
                        continue
                    seen_row_texts.add(key)
                    all_rows.append(row)

    return all_rows
   
