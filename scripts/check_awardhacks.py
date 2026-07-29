#!/usr/bin/env python3
"""
Henter ledige SAS-bonusbilletter fra awardhacks.se, matcher mot søkegrupper
(fra config.json) og sender en daglig oppsummering via Telegram.

Miljøvariabler (settes som GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN   - token fra @BotFather
  TELEGRAM_CHAT_ID     - din chat-id (se README for hvordan finne den)

Søkegrupper (fra-flyplasser, til-flyplasser, datointervall) redigeres enklest
i editor.html, eller direkte i config.json. Se README for format.
"""

import os
import re
import sys
import json
import requests
from datetime import date
from bs4 import BeautifulSoup

URL = "https://awardhacks.se/"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

ROUTE_RE = re.compile(r"([A-Z]{3})\s*-\s*([A-Z]{3})")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
SEATS_RE = re.compile(r"(\d+/\d+/\d+)")
DURATION_RE = re.compile(r"duration:\s*(\d+d)")


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


def fetch_rows():
    resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
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


def date_in_range(d, date_from, date_to):
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


def resolve_group(g):
    """Slår sammen 'from'/'to'-lister med evt. custom_from/custom_to (samme
    format som editor.html bruker før nedlasting)."""
    def codes(lst, custom):
        out = set(lst or [])
        for c in (custom or "").split(","):
            c = c.strip().upper()
            if c:
                out.add(c)
        return out

    return {
        "label": g.get("label") or "",
        "from": codes(g.get("from"), g.get("custom_from")),
        "to": codes(g.get("to"), g.get("custom_to")),
        "date_from": g.get("date_from") or "",
        "date_to": g.get("date_to") or "",
    }


def find_matches(rows, groups_cfg):
    resolved = [resolve_group(g) for g in groups_cfg]
    resolved = [g for g in resolved if g["from"] and g["to"]]

    matches = []
    for row in rows:
        texts = row["texts"]
        if len(texts) < 3:
            continue
        out_codes = parse_route_codes(texts[0])
        if not out_codes:
            continue
        out_from, out_to = out_codes
        out_date = extract_date(texts[1])
        is_round_trip = len(texts) >= 5

        for g in resolved:
            if out_from not in g["from"] or out_to not in g["to"]:
                continue
            if not date_in_range(out_date, g["date_from"], g["date_to"]):
                continue

            ret_codes = parse_route_codes(texts[2]) if is_round_trip else None
            last_text = texts[-1] if texts else ""
            last_td = row["tds"][-1] if row["tds"] else None
            anchor = last_td.find("a") if last_td else None

            matches.append({
                "group_label": g["label"] or f"{out_from}→{out_to}",
                "out_route": f"{out_from} - {out_to}",
                "out_departure": texts[1] if len(texts) > 1 else "",
                "ret_route": (f"{ret_codes[0]} - {ret_codes[1]}" if ret_codes
                              else ("" if is_round_trip else "enveis")),
                "ret_departure": texts[3] if (is_round_trip and len(texts) > 3) else "",
                "seats": extract_seats(texts[0]),
                "duration": extract_duration(last_text),
                "book_href": anchor["href"] if anchor and anchor.has_attr("href") else None,
                "open_jaw": "Open jaw" in last_text,
            })
            break

    return matches


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
    rows = fetch_rows()
    if not rows:
        send_telegram("⚠️ Klarte ikke å lese tabellen fra awardhacks.se i dag. Sjekk siden manuelt.")
        return

    matches = find_matches(rows, groups_cfg)
    today = os.popen("date +%Y-%m-%d").read().strip()

    if matches:
        lines = [f"✈️ Awardhacks-sjekk {today} - {len(matches)} treff:\n"]
        for m in matches:
            lines.append(format_match(m))
            lines.append("")
        message = "\n".join(lines).strip()
    else:
        labels = ", ".join((g.get("label") or "uten navn") for g in groups_cfg) or "ingen grupper konfigurert"
        message = f"✈️ Awardhacks-sjekk {today}: ingen ledige bonusseter for søkegruppene ({labels}) akkurat nå."

    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
