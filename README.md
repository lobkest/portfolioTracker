# Portfolio Dashboard (portfolioTracker)

Webapp waarmee je een DEGIRO-transactiebestand (Excel) uploadt. De app slaat
de data op onder een gegenereerde 3-letter-code, waarmee je later het
dashboard kunt terugzien zonder opnieuw te hoeven uploaden. De app
analyseert de data (rendement, verdeling ETF/aandeel, land/sector, dividend,
per-aandeel-detail) en toont dit interactief.

Oorspronkelijk gestart als leerproject (vervolg op een eerdere Squat
Tracker-oefening) om Python (Flask) en JavaScript beter te leren, inmiddels
uitgegroeid tot een substantiële, in productie draaiende persoonlijke
portfoliotool.

## Werkwijze: agentic coding

Dit project is grotendeels gebouwd met behulp van AI-agents (Claude, via
Claude Code) als implementatie-partner: nieuwe features, bugfixes en
refactors worden uitgewerkt op basis van gedetailleerde instructiedocumenten
en vervolgens door de agent geïmplementeerd, getest en teruggerapporteerd.

Belangrijk om te vermelden: **het domeinmodel, de architectuurkeuzes en de
kernlogica van de backend (databasestructuur, analysestappen, hoe
transacties/koersen/rendement met elkaar samenhangen) zijn door mijzelf
bedacht en uitgedacht.** De agent implementeert, schrijft tests en helpt bij
debugging binnen dat kader — niet andersom. Elke wijziging wordt lokaal in
VS Code bekeken en pas na eigen review handmatig gecommit en gepusht.

## Tech stack

- **Backend**: Python + Flask
- **Excel inlezen**: pandas + openpyxl (met een handmatige openpyxl-fallback
  voor Order ID's, i.v.m. een kolomkop-verschuiving door merged cells in het
  DEGIRO-exportbestand)
- **Koersdata**: yfinance (koersen) + yahooquery (ticker-zoeken)
- **Land/sector-parsing**: pycountry, aangevuld met directe holdings-CSV/XLSX-
  downloads bij fondsproviders (iShares, VanEck) voor volledige dekking
- **Rendementsberekening**: pyxirr (XIRR), eigen TWR-implementatie
- **Database**: PostgreSQL via Neon (serverless, i.v.m. Render's ephemeral
  filesystem)
- **Frontend**: single-page application — één `templates/index.html` +
  `static/js/app.js` (vanilla JS, geen framework), Chart.js (incl. zoom/pan
  en datalabels-plugin)
- **Hosting**: Render, gunicorn als productieserver
- **Tests**: Python `unittest` (~300+ tests) + JS-tests via `node --test`,
  draait automatisch via GitHub Actions bij elke push
- **Ticker-validatie**: OpenFIGI als aanvullende diagnostische laag bovenop
  yfinance/yahooquery

## Bestandsstructuur

```
portfolioTracker/
├── app.py                  → Flask routes, orkestratie
├── db.py                   → DB-connectie, init_db(), price/dividend-opslag
├── analysis.py              → alle analyselogica: ticker-zoeken en
│                               -validatie, koersen ophalen, split-correctie,
│                               rendement- en dividendberekeningen,
│                               land/sector-verdeling, statistieken
├── requirements.txt
├── .env                     → DATABASE_URL (niet in git)
├── templates/
│   └── index.html            → enige pagina, SPA
├── static/
│   ├── css/style.css
│   ├── js/app.js              → alle frontendlogica
│   └── favicon/                → favicon + PWA-manifest
├── tests/                    → unittest-suite
├── .github/workflows/tests.yml → CI, draait tests bij elke push
├── CLAUDE.md                 → projectinstructies/context voor Claude Code
├── class_degiro.py           → legacy referentiescript (poort-logica, o.a.
│                                split-correctie, oude XIRR/dividend-code)
└── trading_degiro.py         → legacy referentiescript, idem
```

`class_degiro.py` en `trading_degiro.py` zijn de oude, niet-actief-gebruikte
scripts van vóór dit project — puur relevant als referentiemateriaal voor
hoe bepaalde berekeningen (split-correctie, dividend, XIRR) oorspronkelijk
zijn aangepakt.

## Functionaliteit

**Upload-flow**
1. Excel inlezen (Transacties + optioneel Rekeningoverzicht), Order ID's
   apart uitgelezen via openpyxl
2. Rijen zonder echte Order ID krijgen een synthetische, deterministische ID
3. Vergelijking met bestaande portfolio's op Order ID-sets: bestaande code
   aangevuld, of nieuwe 3-letter-code aangemaakt
4. Ticker-zoeken per (ISIN, Beurs), met progressief inkorten van de
   productnaam en handmatige overrides voor bekende edge cases
5. Stock-split-detectie en -correctie
6. Koersdata ophalen (cache + retry bij rate limiting + EUR-conversie)
7. Dashboarddata teruggeven als JSON, zwaardere verrijking (ticker-
   zekerheid, land/sector) lazy achteraf opgehaald

**Dashboard (SPA, sidebar-menu)**
- **Portfolio-home** — waarde vs. geïnvesteerd over tijd
- **Rendement** — waarde-min-geïnvesteerd, XIRR/TWR
- **Per aandeel** — individuele waarde/geïnvesteerd-grafiek per positie,
  vergelijk met benchmark of met een andere eigen positie
- **Verdeling** — taartdiagram huidige posities (ETF/aandeel-onderscheid)
- **Land / Sector** — portfoliobrede en per-ETF-verdeling, met volledige
  dekking waar een providerbron beschikbaar is
- **Statistieken** — GAK, rendement per positie, totalen, all-time high,
  transactiekosten
- **Ticker-zekerheid** — per positie zichtbaar welke ticker gevonden is, hoe
  zeker die match is, en een prijsvergelijking als extra check
- **Instellingen** — bijnaam per positie instellen/resetten

## Bekende eigenaardigheden (goed om te onthouden)

- DEGIRO-Excel-quirk: Order ID-kolomkop staat door merged cells één kolom
  verschoven t.o.v. de waarden — vereist een aparte openpyxl-doorloop
- Ticker-koppeling per **(ISIN, Beurs)**, niet per ISIN alleen — anders
  vervuilt de ene notering van een fonds de prijscontrole van de andere
- Postgres `NUMERIC` moet expliciet naar `float` gecast worden vóór
  berekeningen
- NaN-koersen worden overgeslagen i.p.v. meegenomen in `sum()`
- Stock-splits worden door DEGIRO als NON TRADEABLE-rijen geboekt en moeten
  apart gecorrigeerd worden
- yfinance's ISIN-lookup heeft een zeer lage matchrate — OpenFIGI is
  betrouwbaarder voor ISIN-naar-ticker-validatie

## Setup (lokaal)

1. Python-omgeving met `python -m pip install -r requirements.txt`
2. `.env` met `DATABASE_URL` naar een Postgres-instance (Neon)
3. `python app.py` (let op: `init_db()` draait ook buiten
   `__main__`-context, i.v.m. gunicorn/WSGI)
4. Windows: gebruik `cmd`, niet PowerShell, voor consistentie met de
   projectconventies

## Licentie / status

Privéproject, niet publiek gedeeld. Repo: `lobkest/portfolioTracker`
(privé).
