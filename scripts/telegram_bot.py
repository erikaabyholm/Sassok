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

# Gjenbruker matching-logikken fra check_awardhacks.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_awardhacks import (
    fetch_rows, find_matches, format_match, send_telegram, URL as AWARDHACKS_URL
)

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
}

SCANDI_CODES = {"CPH", "ARN", "OSL", "GOT", "AAL"}
DEFAULT_FROM = ["CPH", "ARN", "OSL"]

SEASON_MONTHS = {
    "høst": (9, 11), "host": (9, 11), "vår": (3, 5), "var": (3, 5),
    "sommer": (6, 8), "vinter": (12, 2),
}

TRIGGER_WORDS = ("sjekk", "søk", "sok")


def parse_command(text):
    """Tolker en fritekst-melding og returnerer en søkegruppe, eller None
    hvis meldingen ikke inneholder et gjenkjennelig sjekk-kommando."""
    lower = text.lower().strip()
    if not any(w in lower for w in TRIGGER_WORDS):
        return None

    to_codes = set()
    from_codes = set()
    for city, codes in CITY_TO_CODES.items():
        if city in lower:
            if set(codes) & SCANDI_CODES:
                from_codes.update(codes)
            else:
                to_codes.update(codes)

    if not to_codes:
        return {"error": "Fant ingen kjent destinasjon i meldingen. Prøv f.eks. 'sjekk Tokyo 2026'."}

    if not from_codes:
        from_codes = set(DEFAULT_FROM)

    year_match = re.search(r"(20\d{2})", lower)
    year = int(year_match.group(1)) if year_match else None

    season = None
    for key in SEASON_MONTHS:
        if key in lower:
            season = key
            break

    date_from = date_to = ""
    if year and season:
        start_m, end_m = SEASON_MONTHS[season]
        if season in ("vinter",):
            date_from = f"{year}-12-01"
            date_to = f"{year + 1}-02-28"
        else:
            date_from = f"{year}-{start_m:02d}-01"
            end_year = year
            date_to = f"{end_year}-{end_m:02d}-28"
    elif year:
        date_from = f"{year}-01-01"
        date_to = f"{year}-12-31"

    return {
        "label": f"Telegram-sjekk: {text.strip()}",
        "from": sorted(from_codes),
        "to": sorted(to_codes),
        "date_from": date_from,
        "date_to": date_to,
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

        rows = fetch_rows()
        matches = find_matches(rows, [group])

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
