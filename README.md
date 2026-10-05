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

## Wat de app doet

- **Startpagina** (`/`): een transactiebestand uploaden (optioneel met het
  rekeningoverzicht voor dividend), of een bestaande code invoeren. Met
  "Niet opslaan" wordt niets bewaard: een eenmalige analyse op `/analyse`.
- **Portfolio-pagina** (`/p/<code>`): het dashboard. Het tabblad staat in de
  URL (`/p/ABC#rendement`), dus verversen, bladwijzers en de terug-knop werken
  per tabblad. Tabbladen: Portfolio-home, Rendement (met benchmark), Per
  aandeel, Per aandeel aankoop, Verdeling, Land, Sector, Top-bedrijven,
  ETF-overlap, Statistieken, Transacties, Prognose,
  Dividend en Instellingen (bijnamen, ticker-zekerheid, diagnostiek).

Elke positie wordt per (ISIN, beurs) aan een Yahoo-ticker gekoppeld en met
een prijsvergelijking gecontroleerd; koersen worden gecachet in de database.

## Tech stack

- **Backend**: Python + Flask; productie via gunicorn op Render
- **Database**: PostgreSQL bij Neon (Render heeft geen blijvend bestandssysteem)
- **Data**: pandas + openpyxl (Excel), yfinance (koersen), yahooquery en
  OpenFIGI (ticker zoeken), pyxirr (XIRR), pycountry (land uit ISIN), en
  holdings-bestanden van iShares en VanEck
- **Frontend**: Jinja-templates, vanilla JavaScript (geen framework, geen
  build-stap) en Chart.js met plugins
- **Tests**: Python `unittest` en JavaScript via `node --test`; GitHub Actions
  draait ze bij elke push

## Bestandsstructuur

```
portfolioTracker/
├── app.py                      → Flask-routes (+ orkestratie van de upload)
├── portfolio_orchestratie.py   → bouwt de dashboard-respons op
├── upload_verwerking.py        → taakfuncties achter de upload
├── statistieken.py, portfolio_calc.py, portfolio_verdeling.py, dividend.py
├── prijzen.py, yahoo_client.py, ticker_*.py, etf_holdings_provider.py
├── db.py, portfolio_admin.py, transactie_utils.py, diagnostiek.py, debug_utils.py
├── templates/                  → basis.html (skelet), start.html, portfolio.html
├── static/
│   ├── css/style.css
│   └── js/
│       ├── start.js, app.js, gedeeld.js, infotip.js
│       ├── navigatie.js, prognose.js, transacties.js, ...  → pure logica, getest
│       ├── gedeeld/            → hulpfuncties voor de tabbladen (opmaak, grafiek, tabel)
│       └── tabs/               → één bestand per tabblad
├── tests/                      → Python- en JS-tests
├── docs/CODE_OVERZICHT.md      → uitgebreide leesgids (architectuur, flows, database)
└── .github/workflows/tests.yml → CI
```

Hoe alles samenhangt staat in [`docs/CODE_OVERZICHT.md`](docs/CODE_OVERZICHT.md).

## Lokaal draaien (Windows, cmd)

Nodig: Python 3.13 en, voor de JS-tests, Node.js.

```
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

Maak in de projectmap een bestand `.env` (staat niet in git):

```
DATABASE_URL=postgresql://gebruiker:wachtwoord@host/database
OPENFIGI_API_KEY=...        (optioneel)
```

Let op: lokaal en productie gebruiken dezelfde Neon-database, dus lokaal
uploaden of verwijderen raakt echte data.

Starten met `python app.py` en openen op http://127.0.0.1:5000.

**Tests**

```
node --test tests/test_*.js
```

De Python-tests draai je zonder database door `.env` tijdelijk te hernoemen
(`set DATABASE_URL=` werkt in cmd niet: `.env` wordt dan alsnog ingelezen):

```
ren .env .env.bak
python -m unittest discover -s tests
ren .env.bak .env
```

Tests die een database nodig hebben, draaien alleen tegen een lokale
database (localhost) en worden anders overgeslagen; de CI draait ze tegen een
eigen wegwerp-Postgres.
