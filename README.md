# Portfolio Dashboard (portfolioTracker)

Webapp waarmee je een DEGIRO-transactiebestand (Excel) uploadt. De app slaat
de data op onder een gegenereerde 3-letter-code, waarmee je later het
dashboard kunt terugzien zonder opnieuw te hoeven uploaden. De app
analyseert de data en toont dit interactief.

## Werkwijze: agentic coding

Dit project is grotendeels gebouwd met behulp van AI-agents (Claude, via
Claude Code) als implementatie-partner: nieuwe features, bugfixes en
refactors worden uitgewerkt op basis van gedetailleerde instructiedocumenten
en vervolgens door de agent geïmplementeerd, getest en teruggerapporteerd.

Belangrijk om te vermelden: **het domeinmodel, de architectuurkeuzes en de
kernlogica van de backend (databasestructuur, analysestappen, hoe
transacties/koersen/rendement met elkaar samenhangen) zijn door mijzelf
uitgedacht en in python code gemaakt, daarna is pas een front-end erbij gemaakt (met behulp van agentic coding).** De agent implementeert, schrijft tests en helpt bij debugging binnen dat kader, niet andersom. Elke wijziging wordt lokaal in VS Code bekeken en pas na eigen review handmatig gecommit en gepusht.

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
  vanilla JS in `static/js/` (geen framework), Chart.js (incl. zoom/pan-,
  datalabels- en annotation-plugin)
- **Hosting**: Render, gunicorn als productieserver
- **Tests**: Python `unittest` (ruim 400 tests) + JS-tests via `node --test`,
  draait automatisch via GitHub Actions bij elke push
- **Ticker-validatie**: prijsvergelijking met Yahoo's historische koersen,
  plus OpenFIGI als extra ISIN-gebaseerd signaal bovenop yahooquery

## Bestandsstructuur

```
portfolioTracker/
├── app.py                      → Flask-routes (+ orkestratie van de upload)
├── db.py                       → DB-connectie, init_db(), opslag en caches
├── upload_verwerking.py        → taakfuncties achter de upload
├── portfolio_orchestratie.py   → bouwt de dashboard-respons op
├── portfolio_calc.py           → split-correctie, waarde-tijdreeksen
├── portfolio_verdeling.py      → verdeling, land/sector, bedrijven, ETF-overlap
├── statistieken.py             → rendement, XIRR, TWR, GAK, jaaroverzicht
├── dividend.py                 → rekeningoverzicht en dividend
├── prijzen.py                  → koersen ophalen/cachen, valuta naar EUR
├── ticker_matching.py          → ticker zoeken (yahooquery, overrides, OpenFIGI)
├── ticker_prijscheck.py        → prijsvergelijking Yahoo ↔ DEGIRO
├── ticker_zekerheid.py         → zekerheidsoordeel per ticker
├── ticker_classificatie.py     → ETF/aandeel, land/sector/holdings
├── etf_holdings_provider.py    → volledige holdings bij de fondsprovider
├── yahoo_client.py, portfolio_admin.py, transactie_utils.py, debug_utils.py
├── requirements.txt
├── .env                        → omgevingsvariabelen (niet in git)
├── templates/index.html        → enige pagina, SPA
├── static/
│   ├── css/style.css
│   ├── js/                     → app.js + kleine pure modules (prognose,
│   │                              transacties, bedrijven, menu, infotip)
│   └── favicon/                → favicon + PWA-manifest
├── tests/                      → unittest-suite + JS-tests
├── docs/CODE_OVERZICHT.md      → uitgebreid code-overzicht (architectuur, flows)
└── .github/workflows/tests.yml → CI, draait tests bij elke push
```

Wil je weten hoe alles samenhangt (routes, datastroom, database, frontend),
lees dan [`docs/CODE_OVERZICHT.md`](docs/CODE_OVERZICHT.md).

## Functionaliteit

**Upload-flow**
1. Excel inlezen (Transacties + optioneel Rekeningoverzicht voor dividend),
   Order ID's apart uitgelezen via openpyxl
2. Rijen zonder echte Order ID krijgen een synthetische, deterministische ID
3. Vergelijking met bestaande portfolio's op Order ID-sets: bestaande code
   aangevuld, of nieuwe 3-letter-code aangemaakt
4. Ticker-zoeken per (ISIN, Beurs), met progressief inkorten van de
   productnaam, handmatige overrides voor bekende edge cases en een lichte
   prijscontrole (bij een afwijking worden alternatieve tickers doorgerekend)
5. Koersdata ophalen (cache + retry bij rate limiting + EUR-conversie),
   stock-split-correctie
6. Dashboarddata teruggeven als JSON; het netwerk-zware deel (verdeling,
   land/sector, bedrijven, ETF-overlap) wordt daarna lazy opgehaald

Opties op het startscherm: **Niet opslaan** (eenmalige analyse, er wordt
niets in de database bewaard) en **Ticker-informatie voor alle posities
opnieuw bepalen**. Een bestaand portfolio haal je op met je code.

**Dashboard (SPA, sidebar-menu, op mobiel een hamburgermenu)**
- **Portfolio-home** — waarde vs. geïnvesteerd over tijd, totalen
- **Rendement** — waarde-min-geïnvesteerd, optioneel vergeleken met een
  benchmark (S&P 500, Nasdaq 100, AEX) of een eigen positie
- **Per aandeel** — waarde/geïnvesteerd per positie, met land/sector-
  verdeling bij een ETF
- **Per aandeel aankoop** — koers met aankoop-/verkoopmomenten en aantal
  aandelen, met knoppen om meer koershistorie te laden
- **Verdeling** — taartdiagram huidige posities (ETF/aandeel-onderscheid)
- **Land / Sector** — portfoliobrede verdeling als taart of gestapelde staaf
  per bron, met volledige dekking waar een providerbron beschikbaar is
- **Top N bedrijven** — onderliggende bedrijven via ETF's en losse aandelen
- **ETF-overlap** — overlap-matrix tussen ETF's, met detail per paar
- **Statistieken** — GAK, rendement per positie, verkochte posities,
  rendement per jaar, XIRR/TWR, all-time high, transactiekosten
- **Transacties** — sorteerbaar, gepagineerd transactieoverzicht
- **XIRR & rendement** — rendement%, XIRR en TWR over de tijd
- **Prognose** — projectie van de waarde met instelbaar rendement en inleg
- **Dividend** — ontvangen dividend (uit het rekeningoverzicht), cumulatief
  en per uitkering
- **Instellingen** — data verwijderen, code wijzigen, bijnaam per positie,
  en **Ticker-zekerheid**: per positie welke ticker gevonden is, hoe zeker
  die match is, met prijsvergelijking en alternatieven

Tabbladen die een opgeslagen code nodig hebben (o.a. Dividend, Transacties,
Instellingen) zijn niet beschikbaar bij een "Niet opslaan"-analyse.


## Bekende eigenaardigheden (goed om te onthouden)

- DEGIRO-Excel-quirk: Order ID-kolomkop staat door merged cells één kolom
  verschoven t.o.v. de waarden — vereist een aparte openpyxl-doorloop
- Ticker-koppeling per **(ISIN, Beurs)**, niet per ISIN alleen, anders
  vervuilt de ene notering van een fonds de prijscontrole van de andere
- Postgres `NUMERIC` moet expliciet naar `float` gecast worden vóór
  berekeningen
- Stock-splits worden door DEGIRO als NON TRADEABLE-rijen geboekt en moeten
  apart gecorrigeerd worden
- Yahoo's zoekindex geeft niet altijd de juiste beursnotering terug; daarom
  een prijscontrole, OpenFIGI als extra ISIN-check en handmatige overrides
  per (ISIN, Beurs)
