# Beveiligingsaudit (OWASP-basis)

Datum: 2026-10-29. Status per punt: OK / gefixt / voorstel / risico. Niet gecommit, nooit met Neon verbonden; alle tests draaiden zonder database.

Fixes in deze ronde (4):
1. Ticker-lengtecontrole op `/api/etf-overlap-detail`, `/api/etf-holdings` en `/api/portfolio/<code>/ticker-koers-bereik` (`TICKER_MAX_TEKENS`).
2. Datumvalidatie op `ticker-koers-bereik` (`_is_iso_datum()`): 400 in plaats van een 500 bij een ongeldige `vanaf`/`tot`.
3. `permissions: contents: read` in `.github/workflows/tests.yml`. De push vraagt daarom een token met `workflow`-scope.
4. Tests: `tests/backend/test_beveiligingsaudit.py` (7 tests, incl. een waakhond op `permissions` in alle workflows).

## A. Toegang aan serverkant: OK, met voorstellen

"Bestaat" = de server controleert het bestaan voordat er data wordt gelezen of gewijzigd. Onbekend geeft 404 via `_geen_portfolio()` en telt als foute poging.

| Route | Methode | Code | Bestaanscheck |
|---|---|---|---|
| `/`, `/privacy`, `/analyse`, `/ping`, `/sw.js`, `/favicon.ico`, `/robots.txt`, `/sitemap.xml` | GET | nee | n.v.t. |
| `/p/<code>` | GET | ja | alleen formaat (`is_geldige_code`, anders redirect); raakt bewust geen database |
| `/upload` | POST | nee | n.v.t. (limiet `CATEGORIE_UPLOAD`) |
| `/api/portfolio/<code>/bijwerken` | POST | ja | `db_portfolio_bestaat` |
| `/api/portfolio/<code>` | GET | ja | `build_portfolio_response` geeft None, dan 404 |
| `/api/portfolio/<code>` | DELETE | ja | `db_portfolio_bestaat` + gevoelige-actie-limiet |
| `/api/portfolio/<code>/wijzig-code` | POST | ja | `db_portfolio_bestaat`; nieuwe code via `is_geldige_code` |
| `.../verrijking`, `.../verdeling-over-tijd`, `.../transacties` | GET | ja | basis/resultaat None, dan 404 |
| `.../benchmark-vergelijking`, `.../rendement-over-tijd`, `.../box3`, `.../koersoverzicht`, `.../ticker-koers-bereik` | GET | ja | laadfunctie geeft None, dan 404 |
| `.../dividend`, `.../upload-meldingen`, `.../dividend-verwachting`, `.../historisch-rendement` | GET | ja | `db_portfolio_bestaat` |
| `.../bijnaam`, `.../bijnamen`, `.../reset-bijnaam` | POST | ja | `db_portfolio_bestaat`; bijnaam via `valideer_bijnaam` |
| `.../korte-namen` | GET, POST | ja | `db_portfolio_bestaat` |
| `.../wat-als` (POST), `.../beurs-alternatieven` (GET), `.../vergelijkbare-etfs` (GET) | | ja | basis/bron None, dan 404 |
| `.../ticker-zekerheid/lijst`, `/positie`, `/alle-prijzen` (GET), `/wijzig` (POST) | | ja | groepen None, dan 404; onbekende positie 404; ticker bij `wijzig` via regex |
| `/api/wat-als`, `/api/beurs-alternatieven`, `/api/vergelijkbare-etfs`, `/api/box3/bereken`, `/api/ticker-zekerheid-check` | POST | nee | n.v.t. ("niet opslaan", stateless; limiet per categorie) |
| `/api/zoek`, `/api/isin-kandidaten`, `/api/effect-profiel`, `/api/etf-overlap-detail`, `/api/etf-holdings` | GET | nee | n.v.t. (invoerlengtes; limiet per categorie, behalve `etf-holdings`, zie L) |

- Elke route met code controleert eerst het bestaan; bij een onbekende code wordt niets gelezen of gewijzigd. Een extra formaatcheck (`is_geldige_code`) op de API-routes is bewust niet toegevoegd: de bestaanscheck geeft al 404 en DB-testcodes zoals `TESTDIV` (7 tekens) zouden anders breken.
- **Ontwikkeltabbladen zijn alleen in de interface verborgen (risico, laag).** `ontwikkelaar` komt uit de omgevingsvariabele `ONTWIKKEL_CODES` (`portfolio_admin.py`, `ontwikkel_codes()`); de codes staan niet in de repo en tests gebruiken verzonnen codes. Maar `/api/portfolio/<code>/box3` weigert andere codes niet (staat ook zo in CODE_OVERZICHT). Het levert alleen de eigen data van de bezoeker. Voorstel: in `box3_basis()` `is_ontwikkel_code(code)` eisen en anders 404 (±5 regels + test). Niet gedaan: het verandert het gedrag van de route en Box 3 gaat mogelijk binnenkort live.
- Open (laag): `ticker-koers-bereik`, `set_bijnaam` en `reset_bijnaam` controleren niet dat `ticker` in de portfolio zit. Bijnamen schrijven alleen onder de eigen code; koers-bereik is nu een lengte- en datumgecontroleerde Yahoo-doorgifte, vergelijkbaar met `/api/etf-holdings`.
- Open (laag): `/api/ticker-zekerheid-check` heeft geen maximum aan `posities` (wel een limiet per IP). Voorstel: maximaal ±100 posities, anders 400 (±4 regels).

## B. Excel-upload: OK

- `MAX_UPLOAD_BYTES = 5 MB` is `MAX_CONTENT_LENGTH`; een 413 geeft JSON met een nette melding (`te_groot`). Al getest.
- `bestand_controle.controleer_excel_bestand()` controleert extensie (`.xlsx`/`.xls`), inhoud (zip met `[Content_Types].xml` en `xl/workbook.xml`), geen macro's, ≤200 zip-onderdelen, ≤50 MB uitgepakt en ≤50.000 rijen (`MAX_RIJEN`). Een leeg, kapot of niet-Excel-bestand geeft 400 met een melding (`OngeldigExcelBestand`), geen 500.
- Er wordt niets naar schijf geschreven (alles in `BytesIO`, `_lees_bestanden()`), dus `secure_filename` is niet nodig.
- `defusedxml` is **niet** geïnstalleerd via `requirements.txt`. Lokaal staat het er wel (als afhankelijkheid van andere pakketten), op Render dus waarschijnlijk niet. Voorstel: `defusedxml==0.7.1` toevoegen; openpyxl gebruikt het dan automatisch (1 regel). Nu bieden de zip-grenzen de mitigatie.

## C. XSS: OK

- `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write`: alle 14 plekken in `static/js` zetten een lege of vaste tekst (`""`, `<option value="">Geen</option>`). `tests/test_xss_sinks.js` bewaakt dit voor alle JS-bestanden. Geen `|safe` of `Markup` in templates of Python; Jinja-autoescaping staat aan.
- Productnamen, bijnamen, foutmeldingen en zoekinvoer komen dus via `textContent`/DOM-API op de pagina. Er is geen `escapeHtml`-helper nodig. Chart.js-tooltips en -legenda's gebruiken geen eigen HTML (de waakhond dekt dit).
- `_geen_portfolio()` echoot de code in een JSON-fout; JSON wordt niet als HTML getoond.

## D. SQL-injectie: OK

- 99 `execute`-aanroepen in `db.py`; waarden gaan overal via `%s`-parameters of `execute_values`.
- Dynamische SQL (f-strings) gebruikt alleen vaste modulelijsten (`TRANSACTIE_KOLOMMEN`, `ORDER_ID_KOLOMMEN`, `BRONKOLOMMEN`, `REKENING_REGEL_KOLOMMEN`, `_ISHARES_FONDS_KOLOMMEN`) en de constante `CACHE_GELDIGHEID`. Geen `ORDER BY` uit invoer. Geen wijziging.

## E. Uitgaande verzoeken: OK

- Hosts zijn vast: iShares (`ISHARES_BASIS_URL` en URL's uit `ETF_HOLDINGS_BRON`), OpenFIGI (vaste URL, ISIN als JSON-body), Yahoo via yfinance/yahooquery. Gebruikersinvoer komt niet in een host- of pad-deel van een `requests`-URL terecht.
- Alle `requests`-aanroepen hebben een `timeout` (30/60/30/30 s in `etf_holdings_provider.py`, 10 s in `ticker_matching.py`). Het zijn letterlijke getallen in plaats van benoemde constanten; cosmetisch, niet aangepast.
- Gebruikersinvoer richting Yahoo (ticker, zoekterm) is nu overal lengtebegrensd (`ZOEK_MAX_TEKENS`, `TICKER_MAX_TEKENS`); drie ontbrekende plekken zijn gefixt.

## F. Foutafhandeling: OK

- `debug=True` staat alleen onder `if __name__ == "__main__"`; gunicorn gebruikt dat pad niet.
- Onverwachte fouten geven een generieke melding (soms met foutcode); details gaan naar de log via `maskeer_codes()`/`print_traceback()`. `str(e)` gaat alleen naar de client voor `OngeldigExcelBestand` en `UploadHoortBijBestaandePortfolio`: bewuste gebruikersmeldingen.
- Twijfelgeval, niet gewijzigd: `ticker_matching.py` (`"OpenFIGI niet bereikbaar: {e}"`) komt via `openfigi_kandidaten_debug` in het Ticker-zekerheid-antwoord (bewust blok, zie CLAUDE.md). Een `requests`-uitzondering bevat de URL maar niet de API-sleutel (die zit in een header). Risico laag; voorstel: alleen `type(e).__name__` tonen (1 regel).

## G. Security-headers: OK (bestond al); voorstel voor CSP en HSTS

- `na_request()` zet `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` en `Referrer-Policy: same-origin`. Dat laatste is strenger dan het gevraagde `strict-origin-when-cross-origin`, dus niet aangepast. Getest in `test_toegang_routes.py`.
- Voorstel CSP (niet gebouwd): `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`. Alle scripts zijn zelf gehost (`static/vendor/`, bewaakt door `test_privacy_pagina.py`). Eerst controleren op inline `<script>` en `style="..."` in templates en op `data:`/`blob:` bij Chart.js; starten met `Content-Security-Policy-Report-Only`. Omvang: ±1 dag.
- Voorstel HSTS: `Strict-Transport-Security: max-age=31536000` alleen wanneer `request.is_secure` (via ProxyFix en `X-Forwarded-Proto`); eerst een korte `max-age` proberen. Omvang: ±5 regels.

## H. CSRF en methodes: voorstel

- Wijzigende routes zijn POST/DELETE. De JSON-routes lezen `request.get_json(silent=True)`; zonder `Content-Type: application/json` geeft dat `None`, dus een cross-site formulier (urlencoded/`text/plain`) wijzigt niets. Een JSON-fetch vanaf een andere origin vraagt een CORS-preflight, en er is geen CORS ingesteld. Er zijn geen cookies of sessies (en dus geen `SECRET_KEY`): de code in het pad is het geheim, er is geen ambient authority.
- Resterend (laag): de multipart-routes (`/upload`, `.../bijwerken`) accepteren formulieren van elke origin; bijwerken vereist de code.
- GET-routes die iets wijzigen: `GET /api/portfolio/<code>?herbepaal_alle_tickers=true` herbepaalt tickers en wist caches, en elke ophaal-GET zet `laatst_gebruikt`. Voorstel: de herbepaal-variant naar een POST verplaatsen (±20 regels, frontend + test). Bestaand ontwerp, niet aangepast.
- Voorstel bij toekomstige cookies/sessies: `SameSite=Lax` plus een CSRF-token of een `Origin`-controle in `before_request`.

## I. Geheimen: OK

- Er is geen `SECRET_KEY`. `DATABASE_URL`, `OPENFIGI_API_KEY` en `ONTWIKKEL_CODES` komen alleen uit omgevingsvariabelen; geen fallback-geheimen. `.env` staat in `.gitignore` en komt in de hele geschiedenis niet voor (ook geen `.pem`/`.key`).
- Git-geschiedenis (alle branches) gescand op `postgres(ql)://`, `neon.tech`, `password`, `secret`, `api_key`, `token`, `ghp_`, `sk-`: alleen placeholders (`postgresql://gebruiker:***@host/database`, `postgres:***@localhost:5432/testdb`, `ep-voorbeeld-123456...neon.tech` in een test, `postgresql://x`). Geen echt geheim gevonden, dus niet gestopt.
- `tests/test_files/Account_test.xlsx` en `Transactions_test.xlsx` staan in de repo (kopieën ook in `scratch/`, dat in `.gitignore` staat). Niet inhoudelijk beoordeeld: **controleer zelf** of dit anonieme testdata is. Staat er echte data in, dan helpt verwijderen pas na een herschrijving van de git-geschiedenis.

## J. Logging: OK

- Logs gebruiken `maskeer_codes()`/`print_traceback()`; de accesslog laat IP en URL weg (`gunicorn.conf.py`, `test_logs_zonder_codes.py`). Gericht gezocht naar prints van DataFrames, bestandsinhoud of bedragen: niets gevonden. Productnamen en ISIN's mogen volgens CLAUDE.md. Geen wijziging.

## K. Dependencies en CI

- `pip_audit -r requirements.txt --no-deps`: 6 bevindingen in 3 pakketten. Geen upgrades uitgevoerd.
  - `certifi 2026.7.22`: PYSEC-2026-4320, fix in 2026.10.18/19
  - `urllib3 2.7.0`: PYSEC-2026-4175/4176/4177, fix in 2.8.0
  - `werkzeug 3.1.8`: PYSEC-2026-4312, fix in 3.1.9
  Voorstel: deze drie bumpen en de hele suite draaien (±1 uur; urllib3 2.8 kan `requests`/`yfinance` raken).
- Geen `package.json`, dus geen `npm audit`. Alle versies in `requirements.txt` zijn exact gepind (`==`), inclusief transitieve pakketten.
- `tests.yml` had geen `permissions`: **gefixt** (`permissions: contents: read`, top-level). Pushen vraagt een token met `workflow`-scope. De Python-matrixjob in dat bestand staat uitgecommentarieerd en is niet aangeraakt.

## L. Overig (alleen rapporteren)

- Rate limiting is **aanwezig** (`toegangslimiet.py`, in-memory, per worker): foute codes per IP en globaal, gevoelige acties, upload, externe bronnen, berekening, zoeken (`CATEGORIE_PER_ZWAAR_ENDPOINT`). Beperkingen: de tellers resetten bij een herstart en gelden per worker; `/api/etf-holdings` en de GET-routes `.../ticker-zekerheid/*` hebben geen eigen categorie. Voorstel: `etf_holdings` toevoegen aan `CATEGORIE_PER_ZWAAR_ENDPOINT` (1 regel).
- Bewaartermijn: **automatisch**. `ruim_verlopen_portfolios_op()` draait bij het opstarten en na elke upload, hooguit 1× per uur per proces, 6 maanden na `laatst_gebruikt`. Op een rustige site zonder uploads gebeurt het dus alleen bij een herstart.
