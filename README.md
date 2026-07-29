# Awardhacks-overvåking

Sjekker awardhacks.se daglig for ledige SAS-bonusseter på valgte ruter, og sender
en oppsummering til Telegram.

## 1. Opprett en Telegram-bot (2 min, gratis)

1. Åpne Telegram, søk opp **@BotFather**, start en samtale.
2. Send `/newbot`, følg instruksjonene (velg navn + brukernavn).
3. Du får en **token** som ser slik ut: `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
   → dette er `TELEGRAM_BOT_TOKEN`.
4. Send en hvilken som helst melding til den nye boten din (f.eks. "hei") slik at den
   har noe å svare på.
5. Åpne i nettleseren:
   `https://api.telegram.org/bot<DIN_TOKEN>/getUpdates`
   (bytt ut `<DIN_TOKEN>` med tokenen din)
6. Se etter `"chat":{"id":123456789,...}` i svaret → dette tallet er `TELEGRAM_CHAT_ID`.

## 2. Legg til secrets i GitHub-repoet (for Telegram-varsling)

1. Push denne mappen til et nytt GitHub-repo (kan være privat).
2. Gå til **Settings → Secrets and variables → Actions → New repository secret**.
3. Legg til:
   - `TELEGRAM_BOT_TOKEN` = tokenen fra steg 1
   - `TELEGRAM_CHAT_ID` = tallet fra steg 1

## 2b. Koble editor.html direkte til GitHub (slipp manuell opplasting)

Med dette oppsettet trenger du **aldri** laste ned/laste opp `config.json`
manuelt igjen - appen lagrer endringene direkte i repoet ditt.

1. Gå til **github.com → Settings (din bruker, ikke repoet) → Developer settings
   → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Gi den et navn, sett utløpsdato du er komfortabel med.
3. Under **Repository access**: velg **Only select repositories** → velg dette
   repoet.
4. Under **Permissions → Repository permissions**: sett **Contents** til
   **Read and write**. La alt annet stå på "No access".
5. Generer tokenet og kopier det (starter med `github_pat_...`).
6. Åpne `editor.html` → klikk på **GitHub-tilkobling** øverst → fyll inn:
   - Bruker/organisasjon (GitHub-brukernavnet ditt)
   - Repo-navn
   - Personal Access Token
7. Appen henter automatisk gjeldende `config.json` fra repoet når feltene er
   fylt ut, og **☁️ Lagre til GitHub** committer endringer direkte.

Tokenet lagres kun i din egen nettlesers lokale lagring - det sendes aldri
noe sted annet enn direkte til api.github.com. Siden det er begrenset til
kun dette repoet og kun lese/skrive av filer, er skaden begrenset selv om
noen skulle få tak i det, men behandle det som et passord.

**Uten GitHub-tilkobling** fungerer alt fortsatt som før - bruk "Last ned
config.json" og last opp filen manuelt i stedet.

## 3. Sett opp søkegrupper (ruter og datoer)

Åpne `editor.html` (dobbeltklikk filen - ingen server eller internett nødvendig
for selve redigeringen). Der kan du:

- Legge til flere **søkegrupper** - hver gruppe har egne Fra- og Til-flyplasser
- Velge **flere flyplasser samtidig** på hver side (f.eks. Oslo + Arlanda +
  København som Fra, Tokyo Haneda + Narita som Til) - appen sjekker alle
  kombinasjoner
- Sette et valgfritt datointervall per gruppe
- Legge til egne flyplasskoder i tekstfeltet under chip-listen hvis en
  destinasjon ikke står i listen fra før
- Trykke **🔍 Søk** for å se dagens treff direkte i en tabell, med seter,
  varighet og bestillingslenke
- **Åpne config.json** for å laste inn et eksisterende oppsett (fra disk)
- **Last ned config.json** for manuell lagring, eller bruk **☁️ Lagre til GitHub**
  (se steg 2b) for å slippe filhåndtering helt

Med GitHub-tilkobling (steg 2b) er du ferdig her - endringer er allerede i
repoet. Uten den: last opp den nye `config.json`-filen til GitHub-repoet ditt
(erstatt den gamle) og commit. Neste kjøring av Actions bruker automatisk de
nye søkegruppene.

**Manuell måte:** rediger `config.json` direkte:

```json
{
  "groups": [
    {
      "label": "Til Tokyo",
      "from": ["OSL", "ARN", "CPH"],
      "to": ["HND", "NRT"],
      "date_from": "2026-10-01",
      "date_to": "2026-10-15"
    },
    {
      "label": "Fra Tokyo hjem",
      "from": ["HND", "NRT"],
      "to": ["OSL", "ARN", "CPH"],
      "date_from": "",
      "date_to": ""
    }
  ]
}
```

Tomme `date_from`/`date_to` betyr ingen datofilter for den gruppen.

## 4. Test manuelt

Gå til **Actions**-fanen i GitHub-repoet → velg "Awardhacks daglig sjekk" →
**Run workflow** → kjør den manuelt en gang for å bekrefte at du får en
Telegram-melding.

## 5. Ferdig

Workflowen kjører nå automatisk hver dag kl. 06:30 UTC (juster cron-uttrykket
i `.github/workflows/award-check.yml` om du vil ha annet tidspunkt).

## Søk direkte i editor.html

Ved siden av søkegruppene har du **🔍 Søk**-knappen som henter dagens data fra
awardhacks.se og viser treff i en tabell med rute, avreise/hjemreise, seter,
varighet og bestillingslenke - i tillegg til den daglige Telegram-varslingen.

**Viktig begrensning:** nettlesere blokkerer som standard forespørsler fra én
nettside til en annen (CORS). Knappen prøver først et direkte kall, og faller
tilbake til en gratis mellomtjeneste (allorigins.win) hvis det blokkeres.
Skulle begge feile, får du en tydelig feilmelding med lenke til å åpne
awardhacks.se manuelt - **Telegram-varslene fra GitHub Actions er upåvirket**,
siden de kjører på en server og ikke i nettleseren.

## Begrensninger å være obs på

- awardhacks.se kapper resultatet til 200 rader - hvis en rute ligger langt
  ned i listen kan den falle utenfor. Fungerer best for ruter med relativt
  få/nye oppføringer.
- Dette er webscraping av en tredjeparts gratis-tjeneste - endres siden sin
  HTML-struktur kan scriptet slutte å finne tabellen. Sjekk Actions-loggen
  jevnlig de første ukene.
