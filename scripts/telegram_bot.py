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
import html
import requests

# Gjenbruker det direkte awardhacks.se-API-et
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awardhacks_api import search_group_matches
from check_awardhacks import format_match, send_telegram, MATCH_SEPARATOR

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

MONTH_NAMES = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
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


ALL_WORDS = ("alle", "alt", "hvor som helst")
ALL_AIRPORTS_PHRASES = ("alle flyplasser", "alle ruter", "alle destinasjoner", "alt", "hvor som helst")

RUN_WORKFLOW_TRIGGERS = (
    "kjør workflow", "kjør sjekk", "kjør daglig sjekk", "start sjekk",
    "trigger sjekk", "kjør den daglige", "start workflow",
)


def trigger_award_check_workflow():
    """Starter award-check.yml-workflowen på nytt via GitHub sitt API, med
    tokenet GitHub selv gir jobben (ingen egen PAT nødvendig)."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False, "Mangler GITHUB_TOKEN eller GITHUB_REPOSITORY i miljøet til denne jobben."

    url = f"https://api.github.com/repos/{repo}/actions/workflows/award-check.yml/dispatches"
    try:
        resp = requests.post(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
            json={"ref": "main"},
            timeout=30,
        )
    except requests.RequestException as e:
        return False, f"Nettverksfeil: {e}"

    if resp.status_code == 204:
        return True, "▶️ Daglig sjekk startet! Svar kommer i egen melding om ca. 30-60 sekunder."
    return False, f"Klarte ikke starte workflowen (HTTP {resp.status_code}): {resp.text[:200]}"


HELP_TRIGGERS = (
    "hjelp", "hvilke klasser", "hvilke byer", "hvilke ord", "hvordan spør",
    "hvordan søker", "kommandoer", "hva kan jeg skrive", "hva kan jeg spørre",
    "instruksjoner", "help",
)

HELP_TEXT = """✈️ Slik spør du boten:

Meldingen må inneholde "sjekk" eller "søk".

📍 STEDER (bruk disse ordene):
Skandinavia: Oslo, København, Stockholm, Göteborg, Aalborg
Andre: Tokyo, Chicago, Washington, Atlanta, Boston, Los Angeles, \
San Francisco, Seattle, Miami, New York, Toronto, Seoul, Mumbai, \
Bangkok, Dubai, Phuket, Krabi

🔀 RETNING:
"sjekk Tokyo 2026" → Skandinavia (alle 3) til Tokyo
"sjekk fra Tokyo til Oslo 2026" → eksplisitt retning
"sjekk alle fra Oslo 2026" → Oslo til alle destinasjoner
"sjekk alle til Tokyo 2026" → alle avreisesteder til Tokyo
"sjekk alle flyplasser 2026" → helt bredt søk

📅 DATO (valgfritt, kombiner fritt):
År: "2026"
Sesong: vår, sommer, høst, vinter
Måned: januar-desember
Eksakt dato: "29. september 2026"
Periode: "fra 29. september til 12. oktober 2026"

💺 KABINKLASSE (valgfritt, standard er "any"):
business, plus, economy, any

📋 EKSEMPLER:
sjekk Tokyo høst 2026 economy
sjekk fra Oslo til Dubai 2026 business
sjekk alle fra København november 2026"""


def parse_command(text):
    """Tolker en fritekst-melding og returnerer en søkegruppe, eller None
    hvis meldingen ikke inneholder et gjenkjennelig sjekk-kommando."""
    lower = text.lower().strip()

    if any(phrase in lower for phrase in RUN_WORKFLOW_TRIGGERS):
        return {"run_workflow": True}

    if any(phrase in lower for phrase in HELP_TRIGGERS):
        return {"help": True}

    if not any(w in lower for w in TRIGGER_WORDS):
        # Mangler utløser-ordet - men gi tilbakemelding hvis meldingen
        # tydelig ser ut som et forsøk på en kommando (nevner et kjent sted,
        # et årstall, eller "fra"/"til"), slik at vanlig prat fortsatt ignoreres.
        looks_like_attempt = (
            any(city in lower for city in CITY_TO_CODES)
            or re.search(r"20\d{2}", lower)
            or re.search(r"\bfra\b|\btil\b", lower)
        )
        if looks_like_attempt:
            return {"error": "Jeg reagerer bare på meldinger som inneholder \"sjekk\" eller \"søk\". Prøv f.eks. \"sjekk Tokyo 2026\"."}
        return None

    m_fra = re.search(r"\bfra\s+(.+?)(?:\s+til\b|$)", lower)
    m_til = re.search(r"\btil\s+(.+?)(?:\s+fra\b|$)", lower)
    from_found = find_city_codes_in_text(m_fra.group(1)) if m_fra else set()
    to_found = find_city_codes_in_text(m_til.group(1)) if m_til else set()
    has_all_word = any(w in lower for w in ALL_WORDS)

    if m_fra and not from_found and not has_all_word:
        return {"error": "Kjente ikke igjen stedet etter \"fra\" i meldingen din. Prøv et kjent sted (f.eks. Oslo, København, Stockholm, Tokyo, Dubai...), eller skriv \"alle\"."}

    if m_til and not to_found and not has_all_word:
        return {"error": "Kjente ikke igjen stedet etter \"til\" i meldingen din. Prøv et kjent sted (f.eks. Oslo, København, Stockholm, Tokyo, Dubai...), eller skriv \"alle\"."}

    if from_found and to_found:
        # Eksplisitt "fra X til Y" med begge sider kjent
        from_codes, to_codes = from_found, to_found
    elif from_found and has_all_word:
        # F.eks. "sjekk alle fra Oslo" - Oslo som avreise, alle destinasjoner
        from_codes, to_codes = from_found, {"ALL"}
    elif to_found and has_all_word:
        # F.eks. "sjekk alle til Tokyo" - Tokyo som destinasjon, alle avreisesteder
        from_codes, to_codes = {"ALL"}, to_found
    elif any(phrase in lower for phrase in ALL_AIRPORTS_PHRASES) and not from_found and not to_found:
        # Ingen spesifikk by nevnt i det hele tatt - bredt søk begge veier
        from_codes, to_codes = {"ALL"}, {"ALL"}
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
            return {"error": "Fant ingen kjent destinasjon i meldingen. Prøv f.eks. 'sjekk Tokyo 2026', 'sjekk fra Tokyo til Oslo 2026', eller 'sjekk alle flyplasser 2026' for et bredt søk."}

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
    if year:
        from datetime import date as _date
        import calendar as _calendar

        # Eksplisitte datoer, f.eks. "29. september" eller "12 oktober".
        # (?<!\d) hindrer at siste sifre i årstallet (2026) feiltolkes som dag.
        month_alt = "|".join(sorted(MONTH_NAMES.keys(), key=len, reverse=True))
        explicit_dates = []
        for m in re.finditer(rf"(?<!\d)(\d{{1,2}})\.?\s*({month_alt})", lower):
            day, month_name = int(m.group(1)), m.group(2)
            month = MONTH_NAMES[month_name]
            max_day = _calendar.monthrange(year, month)[1]
            if 1 <= day <= max_day:
                explicit_dates.append(_date(year, month, day))

        spans = []
        for season in matched_seasons:
            start_m, end_m = SEASON_MONTHS[season]
            if season in ("vinter",):
                spans.append((_date(year, 12, 1), _date(year + 1, 2, 28)))
            else:
                spans.append((_date(year, start_m, 1), _date(year, end_m, 28)))

        if explicit_dates:
            # Eksplisitte datoer gitt - bruk disse presist, ikke hele måneder
            # (selv om månedsnavnene også nevnes andre steder i meldingen).
            spans.extend((d, d) for d in explicit_dates)
        else:
            for month_name in MONTH_NAMES:
                if month_name in lower:
                    m = MONTH_NAMES[month_name]
                    last_day = _calendar.monthrange(year, m)[1]
                    spans.append((_date(year, m, 1), _date(year, m, last_day)))

        if spans:
            overall_start = min(s for s, _ in spans)
            overall_end = max(e for _, e in spans)
            date_from = overall_start.strftime("%Y-%m-%d")
            date_to = overall_end.strftime("%Y-%m-%d")
        else:
            date_from = f"{year}-01-01"
            date_to = f"{year}-12-31"

    cabin = "any"
    if "economy" in lower or "økonomi" in lower or "okonomi" in lower:
        cabin = "economy"
    elif "plus" in lower:
        cabin = "plus"
    elif "business" in lower:
        cabin = "business"
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
    owner_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not owner_chat_id:
        print("Mangler TELEGRAM_BOT_TOKEN eller TELEGRAM_CHAT_ID.", file=sys.stderr)
        return

    # Ekstra chat-ID-er som får lov til å spørre boten (komma-separert),
    # i tillegg til eieren. Kun for engangs-spørringer - den daglige
    # sjekken (config.json) sendes uansett kun til eieren (TELEGRAM_CHAT_ID).
    allowed_extra = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_chat_ids = {str(owner_chat_id)} | {
        c.strip() for c in allowed_extra.split(",") if c.strip()
    }

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

        if sender_chat_id not in allowed_chat_ids:
            continue  # ikke på tillatelseslisten, ignorer stille

        if not text:
            continue

        group = parse_command(text)
        if group is None:
            continue  # ikke en sjekk-kommando, ignorer stille

        if "error" in group:
            send_telegram(f"⚠️ {group['error']}", chat_id=sender_chat_id)
            continue

        if group.get("help"):
            send_telegram(HELP_TEXT, chat_id=sender_chat_id)
            continue

        if group.get("run_workflow"):
            success, msg = trigger_award_check_workflow()
            send_telegram(("✅ " if success else "⚠️ ") + msg, chat_id=sender_chat_id)
            continue

        try:
            matches = search_group_matches(
                group["label"], group["from"], group["to"],
                date_from=group["date_from"], date_to=group["date_to"],
                cabin=group.get("cabin", "any"),
            )
        except Exception as e:
            send_telegram(f"⚠️ Noe gikk galt under søket: {e}", chat_id=sender_chat_id)
            continue

        if matches:
            header = f"✈️ {len(matches)} treff for \"{html.escape(text.strip())}\":\n"
            body = MATCH_SEPARATOR.join(format_match(m) for m in matches)
            send_telegram(header + "\n" + body, parse_mode="HTML", chat_id=sender_chat_id)
        else:
            date_desc = f" ({group['date_from']} til {group['date_to']})" if group['date_from'] else ""
            send_telegram(
                f"✈️ Ingen ledige seter funnet for \"{text.strip()}\"{date_desc} akkurat nå.",
                chat_id=sender_chat_id,
            )

    save_offset(highest_id)


if __name__ == "__main__":
    main()
  
