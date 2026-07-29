#!/usr/bin/env python3
"""
Engangs-verktøy: bruker Playwright (en ekte, usynlig nettleser) til å besøke
awardhacks.se og skrive ut den faktiske skjema-strukturen - hvilke <select>,
avkrysningsbokser og knapper som finnes, med deres navn/id/verdier.

Formålet er å finne de riktige "krokene" vi trenger for å bygge en robot som
kan sette Tokyo, Business/Economy-klasse osv. automatisk, uten å gjette.

Skriver alt til vanlig konsoll (synlig i GitHub Actions-loggen).
"""

from playwright.sync_api import sync_playwright

URL = "https://awardhacks.se/"


def describe_page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")

        print("=" * 60)
        print("TITTEL:", page.title())
        print("=" * 60)

        print("\n--- <select>-elementer ---")
        selects = page.locator("select").all()
        for i, sel in enumerate(selects):
            name = sel.get_attribute("name")
            id_ = sel.get_attribute("id")
            options = sel.locator("option").all_inner_texts()
            print(f"[{i}] name={name!r} id={id_!r}")
            print(f"    options: {options}")

        print("\n--- checkbox/radio-input ---")
        inputs = page.locator("input[type=checkbox], input[type=radio]").all()
        for i, inp in enumerate(inputs):
            name = inp.get_attribute("name")
            id_ = inp.get_attribute("id")
            value = inp.get_attribute("value")
            checked = inp.is_checked()
            # Prøv å finne tilhørende <label>
            label_text = ""
            if id_:
                label = page.locator(f'label[for="{id_}"]')
                if label.count() > 0:
                    label_text = label.first.inner_text()
            print(f"[{i}] name={name!r} id={id_!r} value={value!r} checked={checked} label={label_text!r}")

        print("\n--- Andre <input>-felt (tekst/dato/tall) ---")
        text_inputs = page.locator(
            "input[type=text], input[type=date], input[type=number], input:not([type])"
        ).all()
        for i, inp in enumerate(text_inputs):
            name = inp.get_attribute("name")
            id_ = inp.get_attribute("id")
            placeholder = inp.get_attribute("placeholder")
            print(f"[{i}] name={name!r} id={id_!r} placeholder={placeholder!r}")

        print("\n--- <form>-elementer (action/metode) ---")
        forms = page.locator("form").all()
        for i, f in enumerate(forms):
            action = f.get_attribute("action")
            method = f.get_attribute("method")
            print(f"[{i}] action={action!r} method={method!r}")

        print("\n--- Knapper ---")
        buttons = page.locator("button, input[type=submit]").all()
        for i, b in enumerate(buttons):
            text = b.inner_text() if b.evaluate("el => el.tagName") == "BUTTON" else b.get_attribute("value")
            print(f"[{i}] text/value={text!r}")

        browser.close()


if __name__ == "__main__":
    describe_page()
