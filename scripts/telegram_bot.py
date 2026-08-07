#!/usr/bin/env python3
"""
Enkel Telegram-bot: sjekker for nye meldinger (via polling, ikke webhook),
tolker kommandoer som "sjekk billetter Tokyo 2026" eller "sjekk Dubai høst 2026",
kjører et søk mot awardhacks.se, og svarer med resultatet i samme Telegram-chat.

Krever samme secrets som check_awardhacks.py:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Kjøres jevnlig (f.eks hvert 5. minutt) av .github/workflows/telegram-bot.yml.
Holder styr på hvilke meldinger som er behandlet via offset-filen
scripts/telegram_offset.txt, som committes tilbake til repoet av workflowen.
"""

import os
import re
import sys
import json
import requests

# Gjenbruker det direkte awardhacks.se-API-et
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awardhacks_api import search_group_matches
from check_awardhacks import format_match, send_telegram

OFFSET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_offset.txt")

# By -> flyplasskoder. Utvid gjerne listen ved behov.
CITY_TO_CODES = {
    "tokyo": ["HND", "NRT"], "haneda": ["HND"], "narita": ["NRT"],
    "chicago": ["ORD"], "washington": ["IAD"], "atlanta": ["ATL"],
    "boston": ["BOS"], "los angeles": ["LAX"], "san francisco": ["SFO"],
    "seattle": ["SEA"], "miami": ["MIA"], "new york": ["JFK", "EWR"],
    "toronto": ["YYZ"], "seoul": ["ICN"], "mumbai": ["BOM"],
    "bangkok": ["BKK"], "dubai": ["DXB"], "phuket": ["HKT"], "krabi": ["KBV"],
    "stockholm": ["ARN"], "oslo": ["OSL"], "københavn": ["CPH"],
    "kobenhavn": ["CPH"], "copenhagen": ["CPH"], "göteborg": ["GOT"],
    "goteborg": ["GOT"], "gothenburg": ["GOT"], "aalborg": ["AAL"],
    "skandinavia": ["CPH", "ARN", "OSL"], "scandinavia": ["CPH", "ARN", "OSL"],
}

REVERSE_PHRASES = ("motsatt vei", "motsatt", "andre veien", "hjem", "retur vei")

SCANDI_CODES = {"CPH", "ARN", "OSL", "GOT", "AAL"}
DEFAULT_FROM = ["CPH", "ARN", "OSL"]

SEASON_MONTHS = {
    "høst": (9, 11), "host": (9, 11), "vår": (3, 5), "var": (3, 5),
    "sommer": (6, 8), "vinter": (12, 2),
}

TRIGGER_WORDS = ("sjekk", "søk", "sok")


def find_city_codes_in_text(fragment):
    """Finner alle kjente by-koder nevnt i et tekstfragment."""
    codes = set()
    for city, city_codes in CITY_TO_CODES.items():
        if city in fragment:
            codes.update(city_codes)
    return codes


def parse_explicit_direction(lower):
    """Prøver å tolke eksplisitt 'fra X til Y' (eller 'til Y fra X') i meldingen.
    Returnerer (from_codes, to_codes) som sets, eller (None, None) hvis ingen
    tydelig fra/til-frasering ble funnet."""
    m_fra = re.search(r"\bfra\s+(.+?)(?:\s+til\b|$)", lower)
    m_til = re.search(r"\btil\s+(.+?)(?:\s+fra\b|$)", lower)

    from_codes = find_city_codes_in_text(m_fra.group(1)) if m_fra else set()
    to_codes = find_city_codes_in_text(m_til.group(1)) if m_til else set()

    if from_codes and to_codes:
        return from_codes, to_codes
    return None, None


def parse_command(text):
    """Tolker en fritekst-melding og returnerer en søkegruppe, eller None
    hvis meldingen ikke inneholder et gjenkjennelig sjekk-kommando."""
    lower = text.lower().strip()
    if not any(w in lower for w in TRIGGER_WORDS):
        return None

    explicit_from, explicit_to = parse_explicit_direction(lower)

    if explicit_from and explicit_to:
        from_codes, to_codes = explicit_from, explicit_to
    else:
        to_codes = set()
        from_codes = set()
        for city, codes in CITY_TO_CODES.items():
            if city in lower:
                if set(codes) & SCANDI_CODES:
                    from_codes.update(codes)
                else:
                    to_codes.update(codes)

        if not to_codes:
            return {"error": "Fant ingen kjent destinasjon i meldingen. Prøv f.eks. 'sjekk Tokyo 2026' eller 'sjekk fra Tokyo til Oslo 2026'."}

        if not from_codes:
            from_codes = set(DEFAULT_FROM)

        if any(phrase in lower for phrase in REVERSE_PHRASES):
            from_codes, to_codes = to_codes, from_codes

    year_match = re.search(r"(20\d{2})", lower)
    year = int(year_match.group(1)) if year_match else None

    matched_seasons = []
    for key in SEASON_MONTHS:
        if key in lower and SEASON_MONTHS[key] not in [SEASON_MONTHS[s] for s in matched_seasons]:
            matched_seasons.append(key)

    date_from = date_to = ""
    if year and matched_seasons:
        from datetime import date as _date
        spans = []
        for season in matched_seasons:
            start_m, end_m = SEASON_MONTHS[season]
            if season in ("vinter",):
                spans.append((_date(year, 12, 1), _date(year + 1, 2, 28)))
            else:
                spans.append((_date(year, start_m, 1), _date(year, end_m, 28)))
        overall_start = min(s for s, _ in spans)
        overall_end = max(e for _, e in spans)
        date_from = overall_start.strftime("%Y-%m-%d")
        date_to = overall_end.strftime("%Y-%m-%d")
    elif year:
        date_from = f"{year}-01-01"
        date_to = f"{year}-12-31"

    cabin = "business"
    if "economy" in lower or "økonomi" in lower or "okonomi" in lower:
        cabin = "economy"
    elif "plus" in lower:
        cabin = "plus"
    elif "any" in lower or "mixed" in lower or "alle klasser" in lower:
        cabin = "any"

    return {
        "label": f"Telegram-sjekk: {text.strip()}",
        "from": sorted(from_codes),
        "to": sorted(to_codes),
        "date_from": date_from,
        "date_to": date_to,
        "cabin": cabin,
    }


def load_offset():
    try:
        with open(OFFSET_PATH, "r") as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def save_offset(update_id):
    with open(OFFSET_PATH, "w") as f:
        f.write(str(update_id))


def get_updates(token, offset):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, params={"offset": offset + 1, "timeout": 0}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Mangler TELEGRAM_BOT_TOKEN eller TELEGRAM_CHAT_ID.", file=sys.stderr)
        return

    offset = load_offset()
    updates = get_updates(token, offset)

    if not updates:
        print("Ingen nye meldinger.")
        return

    highest_id = offset
    for update in updates:
        highest_id = max(highest_id, update.get("update_id", highest_id))
        message = update.get("message") or {}
        text = message.get("text", "")
        sender_chat_id = str(message.get("chat", {}).get("id", ""))

        if sender_chat_id != str(chat_id):
            continue  # ignorer meldinger fra andre enn deg selv

        if not text:
            continue

        group = parse_command(text)
        if group is None:
            continue  # ikke en sjekk-kommando, ignorer stille

        if "error" in group:
            send_telegram(f"⚠️ {group['error']}")
            continue

        try:
            matches = search_group_matches(
                group["label"], group["from"], group["to"],
                date_from=group["date_from"], date_to=group["date_to"],
                cabin=group.get("cabin", "business"),
            )
        except Exception as e:
            send_telegram(f"⚠️ Noe gikk galt under søket: {e}")
            continue

        if matches:
            lines = [f"✈️ {len(matches)} treff for \"{text.strip()}\":\n"]
            for m in matches:
                lines.append(format_match(m))
                lines.append("")
            send_telegram("\n".join(lines).strip())
        else:
            date_desc = f" ({group['date_from']} til {group['date_to']})" if group['date_from'] else ""
            send_telegram(
                f"✈️ Ingen ledige seter funnet for \"{text.strip()}\"{date_desc} akkurat nå."
            )

    save_offset(highest_id)


if __name__ == "__main__":
    main()
