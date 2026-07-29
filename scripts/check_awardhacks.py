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


def format_match(m):
    parts = [
        f"[{m['group_label']}]",
        m["out_route"],
        m["out_departure"],
    ]
    if m["ret_route"]:
        parts.append("→ retur " + m["ret_route"])
        parts.append(m["ret_departure"])
    parts.append(f"seter:{m['seats']}")
    if m["duration"]:
        parts.append(m["duration"])
    line = " | ".join(p for p in parts if p)
    if m["book_href"]:
        line += f"\n  {m['book_href']}"
    elif m["open_jaw"]:
        line += "\n  (open jaw - book manuelt på sas.no)"
    return line


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Mangler TELEGRAM_BOT_TOKEN eller TELEGRAM_CHAT_ID - hopper over varsling.")
        print(message)
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    max_len = 3800
    chunks = [message[i:i + max_len] for i in range(0, len(message), max_len)] or [message]

    for chunk in chunks:
        r = requests.post(api_url, data={
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }, timeout=30)
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
        cabin = g.get("cabin", "business")
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
        lines = [f"✈️ Awardhacks-sjekk {today} - {len(all_matches)} treff:\n"]
        for m in all_matches:
            lines.append(format_match(m))
            lines.append("")
        message = "\n".join(lines).strip()
    else:
        message = f"✈️ Awardhacks-sjekk {today}: ingen ledige bonusseter for søkegruppene ({', '.join(labels) or 'ingen'}) akkurat nå."

    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
