#!/usr/bin/env python3
"""
Søker awardhacks.se sitt faktiske søke-API direkte (POST /Home/ListResult)
for hver søkegruppe i config.json, og sender en daglig oppsummering via
Telegram. Fanger opp både rundtur OG enveis-seter, i valgt kabinklasse.

Miljøvariabler (settes som GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN   - token fra @BotFather
  TELEGRAM_CHAT_ID     - din chat-id (se README for hvordan finne den)

Søkegrupper (fra-flyplasser, til-flyplasser, datointervall, kabinklasse)
redigeres enklest i editor.html, eller direkte i config.json.
"""

import os
import re
import sys
import json
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awardhacks_api import search_group_matches

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def load_groups():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        groups = data.get("groups", [])
        if not groups:
            print("Advarsel: config.json inneholder ingen søkegrupper.", file=sys.stderr)
        return groups
    except FileNotFoundError:
        print(f"Fant ikke {CONFIG_PATH} - bruker ingen søkegrupper.", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"config.json er ikke gyldig JSON: {e}", file=sys.stderr)
        return []


def resolve_group_codes(g):
    def codes(lst, custom):
        out = list(dict.fromkeys(lst or []))  # bevar rekkefølge, fjern duplikater
        for c in (custom or "").split(","):
            c = c.strip().upper()
            if c and c not in out:
                out.append(c)
        return out

    return codes(g.get("from"), g.get("custom_from")), codes(g.get("to"), g.get("custom_to"))


MATCH_SEPARATOR = "\n" + "─" * 18 + "\n"


def format_seen_ago_no(seen_ago):
    """Gjør om '19h ago' til '19 timer siden' e.l."""
    m = re.match(r"(\d+)\s*(\w+)", seen_ago or "")
    if not m:
        return "Ukjent tidspunkt"
    value, unit = m.group(1), m.group(2)
    if unit.startswith("h"):
        enhet = "time" if value == "1" else "timer"
    elif unit.startswith("m"):
        enhet = "minutt" if value == "1" else "minutter"
    elif unit.startswith("d"):
        enhet = "dag" if value == "1" else "dager"
    else:
        enhet = unit
    return f"{value} {enhet} siden"


def reorder_seats(seats):
    """Snur rekkefølgen på de tre seter-tallene. Merk: awardhacks.se oppgir
    ikke offisielt hva hver posisjon betyr - dette er en ren snuoperasjon,
    ikke en garantert Business/Plus/Economy-sortering."""
    parts = (seats or "").split("/")
    return "/".join(reversed(parts)) if len(parts) == 3 else (seats or "")


def html_escape_url(url):
    return (url or "").replace("&", "&amp;")


def format_match(m):
    is_round_trip = bool(m["ret_route"]) and m["ret_route"] != "enveis"

    lines = [format_seen_ago_no(m.get("seen_ago"))]

    if is_round_trip:
        lines.append(f"{m['out_route']}  ↔  {m['ret_route']}")
        lines.append(f"{m['out_departure']}  →  {m['ret_departure']}")
    else:
        lines.append(m["out_route"])
        lines.append(m["out_departure"])

    lines.append(f"Seter: {reorder_seats(m['seats'])}")

    if m["duration"]:
        lines.append(m["duration"])

    if m["book_href"]:
        lines.append(f'<a href="{html_escape_url(m["book_href"])}">Bestill</a>')
    elif m["open_jaw"]:
        lines.append("(open jaw - book manuelt på sas.no)")

    if m.get("sas_flex_url"):
        lines.append(f'<a href="{html_escape_url(m["sas_flex_url"])}">Se nærliggende datoer</a>')

    return "\n".join(lines)


def send_telegram(message, parse_mode=None, chat_id=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Mangler TELEGRAM_BOT_TOKEN eller chat_id - hopper over varsling.")
        print(message)
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    max_len = 3800
    chunks = [message[i:i + max_len] for i in range(0, len(message), max_len)] or [message]

    for chunk in chunks:
        data = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        r = requests.post(api_url, data=data, timeout=30)
        if r.status_code != 200:
            print(f"Feil ved sending til Telegram: {r.status_code} {r.text}", file=sys.stderr)


def main():
    groups_cfg = load_groups()
    if not groups_cfg:
        send_telegram("⚠️ Ingen søkegrupper konfigurert i config.json.")
        return

    all_matches = []
    labels = []
    for g in groups_cfg:
        from_codes, to_codes = resolve_group_codes(g)
        if not from_codes or not to_codes:
            continue
        label = g.get("label") or f"{'/'.join(from_codes)}→{'/'.join(to_codes)}"
        labels.append(label)
        cabin = g.get("cabin", "any")
        try:
            matches = search_group_matches(
                label, from_codes, to_codes,
                date_from=g.get("date_from", ""), date_to=g.get("date_to", ""),
                cabin=cabin,
            )
            all_matches.extend(matches)
        except Exception as e:
            print(f"Feil ved søk for gruppe '{label}': {e}", file=sys.stderr)

    today = os.popen("date +%Y-%m-%d").read().strip()

    if all_matches:
        header = f"✈️ Awardhacks-sjekk {today} - {len(all_matches)} treff:\n"
        body = MATCH_SEPARATOR.join(format_match(m) for m in all_matches)
        message = header + "\n" + body
        send_telegram(message, parse_mode="HTML")
    else:
        message = f"✈️ Awardhacks-sjekk {today}: ingen ledige bonusseter for søkegruppene ({', '.join(labels) or 'ingen'}) akkurat nå."
        send_telegram(message)

    print(message)


if __name__ == "__main__":
    main()
