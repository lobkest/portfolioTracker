# Code-overzicht — Portfolio Dashboard (portfolioTracker)

> Laatst gecontroleerd tegen de code op 30-09-2026; op 05-10-2026 bijgewerkt voor het Valuta-tabblad, de herschreven Excel-inleesstappen en de verplaatste Python-tests (de testaantallen in hoofdstuk 7 zijn daarbij met grep geteld, niet opnieuw gedraaid).
> Alles hieronder is uit de bronbestanden gelezen, niet uit CLAUDE.md overgenomen. Waar ik iets niet zeker
> kon vaststellen staat het woord **onzeker**. Hoe CLAUDE.md en de code zich tot elkaar verhouden, staat onderaan
> bij [Stand van zaken](#stand-van-zaken-claudemd-en-de-code).

## Inhoud

1. [Het grote plaatje](#1-het-grote-plaatje)
2. [De route van een upload, stap voor stap](#2-de-route-van-een-upload-stap-voor-stap)
3. [Per Python-module](#3-per-python-module)
4. [Database](#4-database)
5. [Frontend](#5-frontend)
6. [Externe bronnen](#6-externe-bronnen)
7. [Tests](#7-tests)
8. [Waar moet ik zijn als ik ... wil aanpassen?](#8-waar-moet-ik-zijn-als-ik--wil-aanpassen)
9. [Woordenlijst](#9-woordenlijst)
10. [Leesgids: van full-stack-basis naar deze code](#10-leesgids-van-full-stack-basis-naar-deze-code)
11. [Stand van zaken: CLAUDE.md en de code](#stand-van-zaken-claudemd-en-de-code)
12. [Onzekerheden en open vragen](#onzekerheden-en-open-vragen)

---

## 1. Het grote plaatje

### Wat de app doet

Je uploadt een DeGiro-transactiebestand (Excel, en optioneel het "Rekeningoverzicht" voor dividend). De app:

1. leest de transacties in en koppelt elke positie aan een Yahoo Finance-ticker (zoeken op productnaam/ISIN, en controleren met de prijs);
2. haalt historische koersen op (yfinance) en berekent de waarde van je portfolio per dag;
3. slaat alles op in een PostgreSQL-database (Neon) onder een gegenereerde **3-letter-code** (bv. `ABC`), zodat je later
   met alleen die code je dashboard terug kunt zien;
4. toont dat in een dashboard op een eigen pagina (`/p/<code>`) (rendement, verdeling ETF/aandeel, land/sector/valuta, top-bedrijven, ETF-overlap,
   statistieken, dividend, transacties, prognose).

Je kunt ook **"Niet opslaan"** kiezen: dan wordt er niets in de database bewaard en is er geen code.

### Ingangspunt en opstarten

| Wat | Waar | Toelichting |
|---|---|---|
| Ingangspunt | `app.py` | Bevat `app = Flask(__name__)`, direct onder de imports. Dit `app`-object is wat een WSGI-server nodig heeft. |
| Database initialiseren | `app.py`: `db_init()`, direct onder `app = Flask(__name__)` | Staat **op moduleniveau** (dus bij het *importeren* van `app.py`, niet in `if __name__ == "__main__"`), achter `if os.environ.get("DATABASE_URL")`. Daardoor draait het onder gunicorn, en in een test die `app` importeert alleen als `DATABASE_URL` is ingesteld. |
| Lokaal draaien | `python app.py` | Onderaan `app.py`: `app.run(debug=True)`. Alleen bedoeld voor lokaal. |
| Configuratie | `.env` met `DATABASE_URL` | `db.py` roept `load_dotenv()` aan; `db_connect()` doet `psycopg2.connect(os.environ["DATABASE_URL"])`. Zonder `DATABASE_URL` slaat `app.py` `db_init()` over; pas de eerste echte databasecall crasht dan met een `KeyError`. |
| Optionele omgevingsvariabele | `OPENFIGI_API_KEY` | Alleen gelezen in `ticker_matching.py`; mag ontbreken. |
| Productie (Render) | gunicorn | `gunicorn` staat in `requirements.txt`. Het **startcommando staat niet in de repo** (geen `Procfile`, `render.yaml` of dergelijke gevonden) — het is dus waarschijnlijk in het Render-dashboard ingesteld. Voor een Flask-object `app` in `app.py` is `gunicorn app:app` de gebruikelijke vorm, maar dat kan ik hier niet verifiëren: **onzeker**. |
| Gunicorn-timeout | niet in de repo | Diverse commentaren in de code gaan uit van "de standaard gunicorn-timeout van 30 s", en de frontend breekt zelf af na 55 s (`fetchMetTimeout`) resp. 60 s (upload). Wat er op Render echt is ingesteld: **onzeker**. |

Wat `db_init()` (in `db.py`) doet: één verbinding openen, dan `CREATE TABLE IF NOT EXISTS` voor elke tabel (zie [hoofdstuk 4](#4-database)),
`commit`, sluiten. Het is dus veilig om het bij elke start opnieuw uit te voeren — ook bij elke gunicorn-worker.

### Hoe de modules elkaar aanroepen

Lagen (van boven naar beneden): **browser → routes → orkestratie → analyse/ticker-logica → data/infra → extern**.
Pijlen betekenen "importeert" (en dus "roept aan"). `debug_utils.py` (alleen `dprint()`/`meet_tijd()`) en `diagnostiek.py` (meldingen
per laadbeurt) zijn weggelaten uit het diagram omdat veel modules ze importeren; de exacte importlijst per module staat in de tabel eronder.

```mermaid
flowchart LR
    subgraph FE["Browser"]
        HTML["templates/<br/>basis.html, start.html, portfolio.html"]
        JS["static/js/<br/>start.js, app.js, gedeeld.js, infotip.js,<br/>navigatie.js, overdracht.js, menu.js, prognose.js,<br/>transacties.js, bedrijven.js, dividend.js,<br/>diagnostiek.js, bestandskeuze.js,<br/>gedeeld/*.js, tabs/*.js"]
    end

    subgraph ROUTES["Routes"]
        APP["app.py"]
    end

    subgraph ORK["Orkestratie"]
        UPL["upload_verwerking.py"]
        ORC["portfolio_orchestratie.py"]
    end

    subgraph ANA["Analyse"]
        CALC["portfolio_calc.py"]
        STAT["statistieken.py"]
        VERD["portfolio_verdeling.py"]
        DIV["dividend.py"]
        ADM["portfolio_admin.py"]
    end

    subgraph TICK["Ticker-logica"]
        ZEK["ticker_zekerheid.py"]
        MATCH["ticker_matching.py"]
        PCHK["ticker_prijscheck.py"]
        CLASS["ticker_classificatie.py"]
    end

    subgraph DATA["Data en infrastructuur"]
        DB["db.py"]
        PRIJ["prijzen.py"]
        YC["yahoo_client.py"]
        ETFP["etf_holdings_provider.py"]
        UTIL["transactie_utils.py"]
    end

    subgraph EXT["Externe bronnen"]
        NEON[("Neon PostgreSQL")]
        YF["yfinance"]
        YQ["yahooquery"]
        FIGI["OpenFIGI API"]
        PROV["iShares / VanEck sites"]
    end

    HTML --> JS
    JS -- "fetch()" --> APP

    APP --> UPL
    APP --> ORC
    APP --> VERD
    APP --> STAT
    APP --> DIV
    APP --> ADM
    APP --> ZEK
    APP --> CALC
    APP --> PRIJ
    APP --> YC
    APP --> DB

    UPL --> ZEK
    UPL --> ADM
    UPL --> DIV
    UPL --> DB

    ORC --> CALC
    ORC --> STAT
    ORC --> VERD
    ORC --> DIV
    ORC --> ZEK
    ORC --> CLASS
    ORC --> PRIJ
    ORC --> UTIL
    ORC --> DB

    CALC --> UTIL
    STAT --> UTIL
    VERD --> CLASS
    DIV --> DB
    ADM --> DB

    ZEK --> MATCH
    ZEK --> PCHK
    ZEK --> CLASS
    ZEK --> DB
    ZEK --> UTIL
    PCHK --> PRIJ
    PCHK --> CLASS
    PCHK --> YC
    PCHK --> DB
    MATCH --> YC
    MATCH --> UTIL
    MATCH --> DB
    CLASS --> ETFP
    CLASS --> YC
    CLASS --> DB
    PRIJ --> YC
    PRIJ --> DB

    DB --> NEON
    PRIJ --> YF
    YC --> YF
    CLASS --> YF
    PCHK --> YF
    MATCH --> YQ
    MATCH --> FIGI
    ETFP --> PROV
```

Als je Markdown-preview geen Mermaid toont: GitHub rendert dit blok wel.

Exacte imports tussen projectmodules (afgeleid uit de code, `debug_utils` en `diagnostiek` staan erbij):

| Module | Importeert van andere projectmodules |
|---|---|
| `app.py` | `db`, `debug_utils`, `diagnostiek`, `dividend`, `portfolio_admin`, `portfolio_calc`, `portfolio_orchestratie`, `portfolio_verdeling`, `prijzen`, `statistieken`, `ticker_zekerheid`, `upload_verwerking`, `yahoo_client` |
| `upload_verwerking.py` | `db`, `debug_utils`, `diagnostiek`, `dividend`, `portfolio_admin`, `ticker_zekerheid`, `transactie_utils` |
| `portfolio_orchestratie.py` | `db`, `debug_utils`, `diagnostiek`, `dividend`, `portfolio_calc`, `portfolio_verdeling`, `prijzen`, `statistieken`, `ticker_classificatie`, `ticker_zekerheid`, `transactie_utils` |
| `portfolio_calc.py` | `debug_utils`, `diagnostiek`, `transactie_utils` |
| `statistieken.py` | `transactie_utils` |
| `portfolio_verdeling.py` | `ticker_classificatie` |
| `dividend.py` | `db` |
| `portfolio_admin.py` | `db` |
| `ticker_zekerheid.py` | `db`, `debug_utils`, `ticker_classificatie`, `ticker_matching`, `ticker_prijscheck`, `transactie_utils` |
| `ticker_prijscheck.py` | `db`, `debug_utils`, `prijzen`, `ticker_classificatie`, `yahoo_client` |
| `ticker_matching.py` | `db`, `debug_utils`, `transactie_utils`, `yahoo_client` |
| `ticker_classificatie.py` | `db`, `debug_utils`, `etf_holdings_provider`, `yahoo_client` |
| `prijzen.py` | `db`, `debug_utils`, `diagnostiek`, `yahoo_client` |
| `etf_holdings_provider.py` | `debug_utils` |
| `debug_utils.py`, `yahoo_client.py` | `diagnostiek` |
| `db.py` | `transactie_utils` |
| `diagnostiek.py`, `transactie_utils.py` | *(geen)* — het zijn de "bladeren" van de boom |

Er zijn geen circulaire imports; dat is precies waarom `transactie_utils.py` en `yahoo_client.py` als losse modules bestaan, die
zelf hooguit `diagnostiek` importeren: helpers die meerdere domeinmodules nodig hebben, zouden anders een import in een kring opleveren.

### De vier "soorten" code, kort

- **Routes** (`app.py`): een request lezen, één orkestratiefunctie aanroepen, JSON teruggeven. (In de praktijk zitten er een paar
  routes met eigen validatie of logica in — zie [hoofdstuk 3](#3-per-python-module), sectie `app.py`. SQL staat nergens buiten `db.py`.)
- **Orkestratie** (`portfolio_orchestratie.py`, `upload_verwerking.py`, en `_upload_impl()` in `app.py`): voegt taakfuncties samen tot een
  compleet antwoord.
- **Taakfuncties** (de domeinmodules): één berekening, één DB-call of één netwerkcall.
- **Frontend**: `templates/*.html` (startpagina en portfolio-pagina) + `static/js/` (met de mappen `gedeeld/` en `tabs/`, zie 5.1) + `static/css/style.css`; praat met de routes via `fetch()`.

## 2. De route van een upload, stap voor stap

Dit hoofdstuk volgt één upload van klik tot dashboard. Er zijn twee takken die uit elkaar gaan in `_upload_impl()`:
**opslaan** (standaard) en **"Niet opslaan"**. Daarna staat de route waarmee je een bestaand portfolio met een code ophaalt.

### 2.0 Overzicht in één blik

```
Browser                      Flask (app.py)                       Domeinmodules
-------                      --------------                       -------------
uploadForm submit
  FormData ───────────────►  POST /upload  upload()
                               └─ _upload_impl()
                                    ├─ Excel → df              (upload_verwerking)
                                    ├─ niet_opslaan?  ──ja──►  ticker-resolutie → analyze_transacties()  ─► JSON
                                    └─ nee: order-ids → code → tickers → INSERT → ticker-backfill
                                            → build_portfolio_response(code)  ────────────────► JSON (kern)
start.js: overdracht bewaren  ◄─────────────────────────────────────────────────────────────────┘
  └─ naar /p/<code> of /analyse (nieuwe pagina, zie 2.6)
app.js: toonDashboard(data)
  └─ laadVerrijking(code) ──► GET /api/portfolio/<code>/verrijking  → analyze_transacties_verrijking ─► JSON
```

### 2.1 Stap voor stap: `POST /upload`

| # | Waar (bestand → functie) | Wat gebeurt er | Data in → data uit |
|---|---|---|---|
| 1 | `static/js/start.js` → submit-handler van `#uploadForm` | Bouwt een `FormData` uit het formulier (`naam`, `bestand1`, optioneel `bestand2`, vinkjes `niet_opslaan` en `herbepaal_alle_tickers`). Is er geen `bestand2` gekozen, dan wordt dat veld met `formData.delete("bestand2")` weggehaald. Toont de overlay "Analyseren..." en roept `fetchMetTimeout("/upload", ..., 60000)` aan. | formulier → `multipart/form-data` |
| 2 | `app.py` → `upload()` | Dunne wrapper om `_upload_impl()`: elke onverwachte exception wordt een nette JSON-foutmelding met status 500, in plaats van een hangende request. | request → `_upload_impl()`-resultaat of `{"error": ...}` |
| 3 | `app.py` → `_upload_impl()` | Zet de Yahoo-call-teller op nul (`reset_yahoo_call_teller()`), leest `naam`, controleert of `bestand1` er is (anders status 400). | |
| 4 | `upload_verwerking.py` → `_lees_transacties_excel()` | `pd.read_excel()`, kolomnamen `.strip()`, controle op `VERWACHTE_KOLOMMEN` (ontbreekt er een → `OngeldigExcelBestand`; `_upload_impl()` geeft dan een 400 met "Ongeldig Excel-bestand: kolom(men) ontbreken: …" en stopt), `Datum` → datetime (`dayfirst=True`), `Order ID` uit de kolom zelf of (als die leeg is) uit de naamloze buurkolom (`_kolom_of_naamloze_buurkolom()`), de `Unnamed`-kolommen weg, en `_meld_order_ids()` voor Diagnostiek. | bestandsobject → **DataFrame `df`** (kolommen zoals DeGiro ze heeft: `Datum`, `Tijd`, `Product`, `ISIN`, `Beurs`, `Aantal`, `Koers`, `Totaal EUR`, ...) |
| 5 | `upload_verwerking.py` → `_adjust_transaction_exchange_rates()` | Voegt de hulpkolom `_koers_eur` toe (= `Koers` gedeeld door `Wisselkoers` als die er is en niet 0, anders `Koers`) en meldt in Diagnostiek hoeveel transacties een wisselkoers gebruikten. Kosten en `Waarde EUR` worden rechtstreeks uit `KOSTEN_KOLOM` resp. `WAARDE_KOLOM` gelezen (al EUR); de kolommen bestaan altijd (zie stap 4), een lege cel geeft NaN. | `df` → `df` + 1 kolom |
| 6 | `app.py` → `_upload_impl()` | Leest de vinkjes: `niet_opslaan` en `herbepaal_alle_tickers` (`== "on"`, want een aangevinkte HTML-checkbox stuurt "on"). **Hier splitst de route** ↓ | |

### 2.2 Tak A — "Niet opslaan" (geen database, geen code)

| # | Waar | Wat gebeurt er | Data |
|---|---|---|---|
| A1 | `upload_verwerking.py` → `_ticker_resolutie_niet_opslaan_pad(df)` | `_bouw_posities(df)` groepeert `df` per **(ISIN, Beurs)**. Per groep een tuple `(productnaam, isin, beurs, [{datum, koers}, ...])`. Roept `basis_ticker_zekerheid_parallel()` aan (12 threads) → per positie `find_ticker_met_snelle_prijscheck()`. | `df` → `(ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw)`: een dict `{(isin, beurs): ticker}` plus twee lijsten van dicts |
| A2 | `upload_verwerking.py` → `_bouw_transacties_df_niet_opslaan()` | Bouwt een DataFrame met **dezelfde kolommen als wat normaal uit de database komt** (`datum`, `product`, `isin`, `beurs`, `ticker`, `aantal`, `koers`, `totaal_eur`, `echte_naam`, `transactiekosten`, `waarde_eur`, `tijd`). Zo hoeft de analysecode niet te weten waar de data vandaan komt. | `df` + dict → **`transacties_df`** |
| A3 | `portfolio_orchestratie.py` → `analyze_transacties(transacties_df, code=None, naam)` | Doet **kern én verrijking** achter elkaar (zie 2.4). `code=None`, dus geen dividend uit de database. | `transacties_df` → dict |
| A4 | `app.py` → `_upload_impl()` | Voegt `ticker_zekerheid`, `ticker_posities_ruw`, `transacties_lijst` (`transacties_overzicht_uit_df()`) en `dividend` (`_dividend_niet_opslaan()`: bestand 2 via `_verwerk_dividend_bestand_zonder_opslaan()`, daarna `bouw_dividend_samenvatting()`; `{"beschikbaar": False}` zonder bestand 2) aan het dict toe en geeft `jsonify(result)` terug. | dict → JSON |

Gevolgen van deze tak (allemaal zichtbaar in de code):

- Er worden **geen Order ID's** bepaald, niets naar Postgres geschreven, en er is **geen code**.
- `bestand2` (rekeningoverzicht) wordt **genegeerd**: de functie keert terug vóórdat `_verwerk_dividend_bestand_indien_aanwezig()` zou draaien.
- De frontend verbergt daarom de knoppen Instellingen, Bijnamen, Dividend en Transacties (`toonDashboard()`), en tabbladen die een code nodig hebben tonen een melding.
- De dure, prijs-geverifieerde ticker-check draait hier bewust **niet** mee; de frontend kan die later los aanvragen via `POST /api/ticker-zekerheid-check` (zie [hoofdstuk 5](#5-frontend)). Reden (uit de commentaren): een groter portfolio met koude cache liep anders over de gunicorn-timeout.

### 2.3 Tak B — Opslaan

| # | Waar | Wat gebeurt er | Data |
|---|---|---|---|
| B1 | `upload_verwerking.py` → `_create_synthetic_order_ids(df)` | De echte Order ID's zijn al ingelezen in stap 4 (in het DeGiro-bestand staat de kop "Order ID" door samengevoegde cellen één kolom verschoven; daarom pakt `_kolom_of_naamloze_buurkolom()` bij een lege kolom de naamloze buurkolom). Deze stap vult alleen de ontbrekende aan: rijen zonder Order ID krijgen een **synthetische ID**: `"SYN-"` + eerste 16 tekens van de MD5 over `Datum\|Tijd\|Product\|ISIN\|Aantal\|Totaal EUR`, plus een volgnummer voor identieke rijen. | `df` → `df` + kolom `Order ID` |
| B2 | `app.py` → `db_connect()` | Opent één verbinding + cursor voor de volgende stappen. | |
| B3 | `upload_verwerking.py` → `_vind_of_maak_portfolio_code(cur, df, naam)` | Roept `find_matching_code()` (in `portfolio_admin.py`) aan: die vergelijkt de set Order ID's van deze upload met de set van **elk** bestaand portfolio. Is een bestaande set een deelverzameling van de nieuwe → dat is een update van hetzelfde portfolio (de ontbrekende ID's zijn de nieuwe rijen). Is de nieuwe set een deelverzameling van de bestaande → niets nieuws. Geen match → `generate_code(cur)` maakt een nieuwe, nog niet gebruikte 3-letter-code en er komt een rij in `portfolios`. Bij een match en een ingevulde `naam` wordt de naam bijgewerkt. Daarna meldt `_meld_nieuwe_rijen_kwaliteit(rows_to_insert)` de datakwaliteit van de nieuwe rijen (zie Diagnostiek). | `df` → `(code, match_code, rows_to_insert)` |
| B4 | `upload_verwerking.py` → `_ticker_resolutie_opslaan_pad(cur, code, rows_to_insert, herbepaal_alle_tickers)` | Alleen voor de **nieuwe** rijen, gegroepeerd met `_bouw_posities()`. Haalt (tenzij het vinkje aan staat) de al bekende tickers van deze code uit `transacties` op en geeft die door als `bekende_tickers`, zodat de dure yahooquery-zoekopdracht voor bekende posities wordt overgeslagen. Roept `vind_tickers_met_snelle_prijscheck_parallel()` aan (12 threads). Gaf een groep geen ticker, dan probeert `_probeer_andere_productnamen()` de namen van de andere rijen. | → dict `{(isin, beurs): ticker}` |
| B5 | `upload_verwerking.py` → `_insert_nieuwe_transacties()` | `INSERT ... ON CONFLICT (code, order_id) DO NOTHING` per rij. **Let op:** een `except Exception: pass` slikt elke fout per rij stilzwijgend in. | rijen → `transacties` |
| B6 | `app.py` → `conn.commit()` | Maakt de inserts definitief. | |
| B7 | `ticker_zekerheid.py` → `backfill_verouderde_tickers(code, forceer)` | Alleen bij een **bestaande** code (`match_code`): herbeoordeelt de opgeslagen tickers. Overschrijft alleen als de oude ticker een prijsprobleem heeft en de nieuwe kandidaat niet (of altijd herzoeken bij `forceer=True`, het vinkje). | |
| B8 | `upload_verwerking.py` → `_verwerk_dividend_bestand_indien_aanwezig(code)` | Is er een `bestand2`: `verwerk_rekeningoverzicht()` (in `dividend.py`) → lijst dividendrecords → `db_save_dividenden()` (upsert in `dividenden`). | Excel → records → DB |
| B9 | `portfolio_orchestratie.py` → `_wis_portfolio_basis_cache(code)` | Cache leegmaken ná alle mutaties hierboven, zodat het volgende stuk verse data ziet. | |
| B10 | `portfolio_orchestratie.py` → `build_portfolio_response(code)` | Haalt de "basis" op (zie 2.5) en roept `analyze_transacties_kern()` aan. Levert de **kern** (zonder verrijking). | code → dict |
| B11 | `app.py` → `_upload_impl()` | `log_yahoo_call_samenvatting()` en `jsonify(...)`. | dict → JSON |

### 2.4 Kern versus verrijking

De dashboardgegevens zijn in twee delen gesplitst zodat de Home-pagina snel klaar is (zie ook CLAUDE.md, Flows): classificatie
en land/sector/holdings-opzoekingen zijn het netwerk-zware deel, dat bij een nieuw portfolio met koude cache de meeste tijd kost.

| | **Kern** — `analyze_transacties_kern()` | **Verrijking** — `analyze_transacties_verrijking()` |
|---|---|---|
| Geleverd via | `POST /upload`, `GET /api/portfolio/<code>`, en de antwoorden van bijnaam/reset-bijnaam/wijzig-code | `GET /api/portfolio/<code>/verrijking` (lui, door de frontend aangeroepen); bij "Niet opslaan" wordt het direct meegestuurd via `analyze_transacties()` |
| JSON-sleutels | `code`, `naam`, `chart_data` (`labels`, `waarde`, `geinvesteerd`, `rendement`), `per_ticker`, `per_ticker_aankoop`, `statistieken`, `tickers`, `ticker_waarschuwingen`, `laatste_koersdatum`, `laatst_opgehaald_op` | `verdeling`, `verdeling_samenvatting`, `land_sector_verdeling`, `valuta_verdeling`, `bedrijven_verdeling`, `etf_overlap` |
| Tabbladen die het gebruiken | Portfolio-home, Rendement, Per aandeel, Per aandeel aankoop, Statistieken, Prognose, Bijnamen (de %-weergave van het tabblad Rendement heeft een eigen lui endpoint, zie hoofdstuk 5) | Verdeling, Land, Sector, Valuta, Top N bedrijven, ETF-overlap |
| Kost | Koersen (uit cache, eventueel incrementeel verversen) + rekenwerk + DB-lezen (dividend, prijswaarschuwingen uit cache) | `classify_tickers()` + per ETF sector/holdings + per aandeel land/sector → mogelijk veel Yahoo-calls |
| Geen koersdata? | Geeft `{"code", "naam", "chart_data": None}` terug; de frontend toont "Geen koersdata gevonden" | Geeft lege structuren terug |

Wat `analyze_transacties_kern()` intern doet, in volgorde: (1) split-correctie + koersen ophalen — óf overslaan als
`prijs_data_al_klaar` is meegegeven; (2) `db_get_laatste_prijs_update()`; (3) `compute_value_over_time()`, `compute_per_ticker()`,
`compute_per_ticker_koers_en_aankopen()`; (4) ticker- en echte-namen-dicts; (5) `ticker_waarschuwingen_voor_transacties()`;
(6) `bereken_dividend_samenvatting(code)` (alleen als er een code is) voor "dividend per ticker"; (7) `bereken_statistieken()`; (8) alles
in één dict gieten. Let op: bij "Niet opslaan" draait `analyze_transacties_verrijking()` daarna nóg een keer split-correctie +
`get_prices()` (een warme cache-hit, maar dubbel werk). Bij opslaan en ophalen gebeurt dat niet: dan geeft `build_portfolio_response()`
de al opgehaalde koersen door via `prijs_data_al_klaar`.

### 2.5 De "basis" en de korte in-process cache

`_haal_portfolio_basis(code)` in `portfolio_orchestratie.py` is de gedeelde eerste stap voor de kern, de verrijking en de
Ticker-zekerheid-routes:

1. `db_get_portfolio_naam_en_transacties()` (`SELECT` op `portfolios`: bestaat de code? en op `transacties`: de 12 kolommen van `TRANSACTIE_KOLOMMEN`) → naam + lijst tuples;
2. → **`transacties_df`** (DataFrame), `transactiekosten` en `waarde_eur` naar `float`;
3. `compute_split_adjusted_shares(transacties_df)` → voegt kolom `adj_aantal` toe;
4. `get_prices(tickers, start_date, verversen)` → **`price_data`** (DataFrame: index = datum, kolommen = tickers, waarden = koers in EUR);
5. resultaat `(naam, transacties_df, price_data)` wordt **20 seconden** bewaard in het dict `_basis_cache` (per proces, met een
   `threading.Lock`), zodat de 2–3 requests van één portfolio-bezoek niet drie keer hetzelfde ophalen.

`_wis_portfolio_basis_cache(code)` moet aangeroepen worden ná elke wijziging aan de transacties van een code (upload, bijnaam, code
wijzigen, verwijderen, geforceerde ticker-herberekening). Bij meerdere gunicorn-workers heeft elke worker zijn eigen cache; een miss
betekent alleen dat het request het "trage" pad neemt, niet dat er iets stukgaat.

### 2.6 Wat de frontend doet met het antwoord

De upload gebeurt op de startpagina (`/`), het dashboard staat op een andere pagina (`/p/<code>`, of `/analyse` bij "niet opslaan"). Het antwoord
moet dus mee naar die pagina: `gaNaarPortfolioPagina(data)` in `start.js` zet het met `bewaarOverdracht()` (`overdracht.js`) eenmalig in
`sessionStorage` en navigeert met `location.assign()`. Op de portfolio-pagina leest `startPortfolioPagina()` (`app.js`) het met `haalOverdracht()`,
dat de overdracht meteen wist. Zo blijven de Excel-meldingen van de upload (Diagnostiek) behouden en hoeft de server niet nog een keer te rekenen.
Past het antwoord niet in `sessionStorage`: bij opslaan navigeert de startpagina toch (de portfolio-pagina haalt dan zelf op, zie 2.8), bij
"niet opslaan" blijft ze staan met een melding.

`toonDashboard(data)` in `app.js`: `huidigeData = data`; alle per-portfolio-toestand resetten; knoppen tonen/verbergen afhankelijk van
`data.code`; de ticker-waarschuwingsbanner tonen; `wisselView(viewUitUrl())` (het tabblad uit de URL-hash, zie 5.3). Daarna: staat `data.verdeling` al in het antwoord
(niet-opslaan) → `verrijkingStatus = "klaar"`; is er een code → `laadVerrijking(code)` (haalt `/verrijking` op op de achtergrond en doet
`Object.assign(huidigeData, data)`, daarna tekent het het actieve tabblad opnieuw als dat een verrijkings-tabblad is).

### 2.7 Route: een bestaand portfolio ophalen met een code

Frontend: `#codeForm` submit-handler in `start.js` (code in hoofdletters) → `fetch("/api/portfolio/<CODE>")`, of met
`?herbepaal_alle_tickers=true` als het vinkje "Ticker-informatie ... opnieuw bepalen" aan staat → dezelfde overdracht als bij een upload
(2.6) → `/p/<CODE>` → `toonDashboard(data)`. Een onbekende code geeft de foutmelding direct op de startpagina. Het vinkje komt bewust nooit in
de URL van de portfolio-pagina: een refresh zou de dure herbepaling herhalen.

Backend: `app.py` → `api_portfolio(code)`:

1. `code.strip().upper()`; `reset_yahoo_call_teller()`;
2. bij `?herbepaal_alle_tickers=true`: `backfill_verouderde_tickers(code, forceer=True)` en daarna `_wis_portfolio_basis_cache(code)`
   (anders zou de cache nog de oude tickers teruggeven);
3. `build_portfolio_response(code)` → `_haal_portfolio_basis()` → `analyze_transacties_kern()` (zie 2.4/2.5). `None` (code bestaat
   niet) → JSON-fout met status 404;
4. `log_yahoo_call_samenvatting()` en `jsonify(result)`.

Daarna volgt dezelfde `toonDashboard()` → `laadVerrijking()`-vervolgstap als bij een upload. Het verschil met een upload: er wordt niets
ingelezen of weggeschreven (behalve de optionele ticker-backfill), en de koersen worden wel opnieuw "ververst" — `get_prices()` heeft
standaard `verversen=True`, dus voor tickers waarvan de cache niet vers is wordt bij **elke** portfolio-opening incrementeel bij Yahoo
bijgehaald, tenzij dezelfde ticker minder dan 2 minuten eerder al is ververst (`DREMPEL_HERGEBRUIK_KOERS`).

### 2.8 Route: de portfolio-pagina openen of verversen (`/p/<code>`)

1. `app.py` → `portfolio_pagina(code)`: geeft alleen `portfolio.html` terug met de code in `<body data-code="ABC">`. Geen database. Kleine
   letters → redirect naar `/p/ABC`; een code die niet aan `is_geldige_code()` voldoet → redirect naar `/?melding=ongeldige-code`.
2. `app.js` → `startPortfolioPagina()`: ligt er een overdracht klaar (direct na upload of ophalen), dan `toonDashboard()`. Anders (refresh,
   bookmark, gedeelde link) `haalPortfolioOp()`: `GET /api/portfolio/<code>` → `toonDashboard()`.
3. Fouten in `haalPortfolioOp()`: status 404 → `location.replace("/?melding=onbekende-code")`; elke andere fout (timeout, 500, netwerk) →
   het blok `#laadFout` met de knop "Opnieuw proberen".
4. `/analyse` (`analyse_pagina()`) is dezelfde template zonder code. Zonder overdracht valt er niets te tonen: terug naar `/`. Een refresh
   van een "niet opslaan"-analyse brengt je dus naar de startpagina.
5. De startpagina toont de melding uit `?melding=` als vaste tekst per sleutel (`startMeldingTekst()` in `navigatie.js`) en haalt de query
   daarna weg met `history.replaceState()`.

## 3. Per Python-module

19 Python-modules (zonder tests), in de map `portfolioTracker/`. Per module: verantwoordelijkheid, een functietabel en bijzonderheden.
De kolom **"Aangeroepen door"** is met een script uit de code afgeleid (een AST-doorloop van alle `.py`-bestanden); "—" betekent: geen
aanroeper in de productiecode gevonden (de functie wordt dan alleen door tests, via een dict of via HTTP gebruikt — dat staat erbij).

### Waar staat wat? (snelle opzoektabel)

| Onderwerp | Bestand | Kernfuncties |
|---|---|---|
| Portfoliowaarde en geïnvesteerd bedrag per dag | `portfolio_calc.py` | `compute_value_over_time()` |
| Waarde/geïnvesteerd per aandeel | `portfolio_calc.py` | `compute_per_ticker()`, `compute_per_ticker_koers_en_aankopen()` |
| **Rendement** (totaal, per positie, per jaar) | `statistieken.py` | `bereken_totaal_rendement()`, `bereken_positie_rendement()`, `bereken_jaar_rendement()`, `bereken_jaren_overzicht()` |
| **XIRR** | `statistieken.py` | `bereken_xirr()` (via `pyxirr`), `_bouw_xirr_cashflows()` |
| **TWR** | `statistieken.py` | `bereken_twr()` |
| Rendement/XIRR/TWR over tijd | `statistieken.py` | `bereken_rendement_over_tijd()` |
| Benchmarkvergelijking | `statistieken.py` | `bereken_benchmark_vergelijking()`, `BENCHMARK_TICKERS` |
| **GAK en kostprijs** | `statistieken.py` | `bereken_holdings_en_gesloten()`; een tweede, parallelle implementatie zit in `compute_per_ticker()` |
| **Split-correctie** (aantallen in de waardereeks) | `portfolio_calc.py` | `compute_split_adjusted_shares()` |
| Split-correctie (bij de prijscontrole van tickers) | `ticker_prijscheck.py` | `_haal_splits_op()`, `_cumulatieve_split_factor()` |
| **Koersen ophalen en cachen** | `prijzen.py` (+ `db.py`, `yahoo_client.py`) | `get_prices()`, `db_save_prices()`, `db_upsert_prices()`, `download_met_retry()` |
| Valuta naar EUR | `prijzen.py` | `_converteer_naar_eur()`, `_fx_prijzen_serie()` |
| **Ticker zoeken** | `ticker_matching.py` | `find_ticker_detailed()`, `_zoek_product_progressief()`, `BEURS_MAP`, `MANUAL_TICKER_OVERRIDES_ISIN` |
| **Ticker verifiëren** | `ticker_prijscheck.py`, `ticker_zekerheid.py`, `ticker_matching.py` | `vergelijk_prijs_op_datum()`, `find_ticker_met_snelle_prijscheck()` (licht), `verifieer_ticker_met_prijs()` (volledig), `haal_openfigi_resultaten()` |
| **ETF-holdings** | `etf_holdings_provider.py`, `ticker_classificatie.py` | `ETF_HOLDINGS_BRON`, `fetch_provider_holdings()`, `get_etf_holdings()` |
| **ETF-overlap** | `portfolio_verdeling.py` | `bereken_etf_overlap()`, `bereken_etf_overlap_detail()` |
| **Verdeling ETF/aandeel** | `portfolio_orchestratie.py` + `portfolio_verdeling.py` + `ticker_classificatie.py` | het `verdeling`-blok in `analyze_transacties_verrijking()`, `bereken_verdeling_samenvatting()`, `classify_tickers()` |
| **Land / sector / top-bedrijven** | `portfolio_verdeling.py` | `compute_land_sector_verdeling()`, `bereken_bedrijven_verdeling()` |
| **Statistieken** (tabblad) | `statistieken.py` | `bereken_statistieken()` |
| **Dividend** | `dividend.py` (+ `db.py`) | `verwerk_rekeningoverzicht()`, `bereken_dividend_samenvatting()`; in de UI `maakDividendUitkeringenTabel()` (incl. het "herinvesteerd"-label) |
| **Prognose** | **geen Python** — alleen `static/js/prognose.js` (rekenkern) en `static/js/tabs/prognose.js` (formulier en grafiek) | `berekenPrognose()`, `bouwPrognoseGrafiekData()` (JS) |

---

### `app.py` — de Flask-routes

**Verantwoordelijkheid:** het Flask-object aanmaken, `db_init()` draaien, en 19 routes definiëren. 21 functies in totaal (19 routes, `_upload_impl()` en `_dividend_niet_opslaan()`).

| Route | Methode | Functie | Wat | Aangeroepen door (frontend) |
|---|---|---|---|---|
| `/` | GET | `home()` | rendert `templates/start.html` (startpagina) | de browser |
| `/p/<code>` | GET | `portfolio_pagina()` | rendert `templates/portfolio.html` met `data-code`; redirect bij kleine letters of een ongeldige code (zie 2.8). Geen database | de browser; `gaNaarPortfolioPagina()` |
| `/analyse` | GET | `analyse_pagina()` | dezelfde template zonder code, voor "niet opslaan" | `gaNaarPortfolioPagina()` |
| `/upload` | POST | `upload()` → `_upload_impl()` | zie [hoofdstuk 2](#2-de-route-van-een-upload-stap-voor-stap) | submit-handler van `#uploadForm` |
| `/api/portfolio/<code>` | GET | `api_portfolio()` | kern voor een bestaande code | submit-handler van `#codeForm` (`start.js`), `haalPortfolioOp()` (`app.js`) |
| `/api/portfolio/<code>/verrijking` | GET | `portfolio_verrijking()` | verrijking (verdeling/land/sector/bedrijven/overlap) | `laadVerrijking()` |
| `/api/etf-overlap-detail` | GET | `etf_overlap_detail()` | holdings van één ETF-paar; query `a` en `b` | `toonEtfOverlapDetail()` |
| `/api/portfolio/<code>/benchmark-vergelijking` | GET | `benchmark_vergelijking()` | hypothetisch rendement als dezelfde cashflows in een benchmark (`?benchmark=`) of eigen ticker (`?eigen_ticker=`) waren gestoken | `wisselBenchmark()`, `wisselEigenAandeel()` |
| `/api/portfolio/<code>/rendement-over-tijd` | GET | `rendement_over_tijd()` | reeks rendement%/XIRR%/TWR% per maandeinde | `toonRendementOverTijd()` |
| `/api/portfolio/<code>/ticker-koers-bereik` | GET | `ticker_koers_bereik()` | extra koershistorie voor 1 ticker (`ticker`, `vanaf`, `tot`): `labels`, `koers`, `holdings` (aantal stuks per datum, via `holdings_op_datums()`), `vroegste_beschikbare_datum` | `laadMeerHistorie()` |
| `/api/portfolio/<code>/ticker-zekerheid/lijst` | GET | `ticker_zekerheid_lijst()` | alleen de lijst posities, zonder prijscontrole | `toonInstellingenTicker()` |
| `/api/portfolio/<code>/ticker-zekerheid/positie` | GET | `ticker_zekerheid_positie()` | volledige verificatie van 1 positie (`isin`, `beurs`) | `toonInstellingenTicker()` |
| `/api/ticker-zekerheid-check` | POST | `ticker_zekerheid_check()` | volledige verificatie voor een "niet opslaan"-analyse, op meegestuurde transacties | `controleerTickerZekerheidUitgebreid()` |
| `/api/portfolio/<code>/dividend` | GET | `dividend()` | `bereken_dividend_samenvatting()`; `{"beschikbaar": False}` als er geen dividenden zijn | `toonDividend()` |
| `/api/portfolio/<code>/transacties` | GET | `transacties_overzicht()` | `{"lijst": db_get_transacties_overzicht(code)}` | `toonTransacties()` |
| `/api/portfolio/<code>/bijnaam` | POST | `set_bijnaam()` | `UPDATE transacties SET product = ...` voor alle rijen met die ticker | `slaBijnaamOp()` |
| `/api/portfolio/<code>/reset-bijnaam` | POST | `reset_bijnaam()` | `product` = live `longName` (`haal_long_names()`), anders `echte_naam` | `resetBijnaam()` |
| `/api/portfolio/<code>/korte-namen` | GET | `get_korte_namen()` | `bepaal_korte_naam_voorstellen()`: `[{ticker, huidig, voorstel}]`, schrijft niets; 502 als Yahoo niets teruggeeft | `laadKorteNamen()` |
| `/api/portfolio/<code>/korte-namen` | POST | `pas_korte_namen_toe()` | berekent de voorstellen opnieuw, `db_wijzig_bijnamen()` voor alle tickers met een voorstel (één transactie) | `pasKorteNamenToe()` |
| `/api/portfolio/<code>` | DELETE | `verwijder_portfolio()` | `db_delete_portfolio()` + cache wissen | handler van `#verwijderPortfolioBtn` |
| `/api/portfolio/<code>/wijzig-code` | POST | `wijzig_code()` | valideert met `is_geldige_code()`, dan `db_wijzig_portfolio_code()` | handler van `#wijzigCodeBtn` |

**Bijzonderheden en valkuilen**

- `app.py` is **niet helemaal "alleen dunne routes"**: `_upload_impl()` is een lange orkestratiefunctie, en `ticker_koers_bereik()`, `dividend()`,
  `transacties_overzicht()` doen een eigen validatie of berekening (de "bestaat de code?"-check via `db_portfolio_bestaat()`). `benchmark_vergelijking()` bevat validatielogica en roept `get_prices()` rechtstreeks aan.
- Er is **geen authenticatie of gebruikersbegrip**: wie een code kent, kan alles lezen, wijzigen en met de `DELETE`-route verwijderen.
- De route-functie `dividend()` heeft dezelfde naam als de module `dividend.py`. Dat werkt omdat `app.py` alleen losse functies uit die module
  importeert, maar het is verwarrend bij zoeken.
- Alle routes worden door de frontend gebruikt. Ticker-zekerheid gaat in twee stappen: `/ticker-zekerheid/lijst` (snel) en daarna per positie
  `/ticker-zekerheid/positie`, zodat geen enkel request lang genoeg duurt voor de gunicorn-timeout.

---

### `upload_verwerking.py` — taakfuncties achter `/upload`

**Verantwoordelijkheid:** Excel inlezen, kolommen normaliseren, tickers oplossen (twee paden), Order ID's bepalen, portfolio-code zoeken/maken,
inserten en het dividendbestand verwerken. `_upload_impl()` in `app.py` roept ze in volgorde aan (zie hoofdstuk 2).

Constanten: `VERWACHTE_KOLOMMEN` (de 15 benoemde kolommen die een geldig bestand moet hebben, zonder de `Unnamed: n`-kolommen; volgorde maakt niet uit), `KOSTEN_KOLOM` (`"Transactiekosten en/of kosten van derden EUR"`), `WAARDE_KOLOM` (`"Waarde EUR"`), `WISSELKOERS_KOLOM` (`"Wisselkoers"`).

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `_lees_transacties_excel()` | Excel → DataFrame, kolommen strippen, controle op `VERWACHTE_KOLOMMEN` (anders `OngeldigExcelBestand`), `Datum` parsen (`dayfirst=True`) | bestandsobject → DataFrame | `_upload_impl()` |
| `_adjust_transaction_exchange_rates()` | voegt `_koers_eur` toe en meldt het gebruik van de wisselkoers in Diagnostiek | DataFrame → DataFrame | `_upload_impl()` |
| `_kolom_of_naamloze_buurkolom()` | de `Order ID`-kolom, of bij een lege kolom de naamloze buurkolom | DataFrame, kolomnaam → Series | `_lees_transacties_excel()` |
| `_normaliseer_tijd()` | maakt van een tijdcel (string, `time` of `datetime`) een `"HH:MM:SS"`-string voor een Postgres-`TIME` | cel → tekst of `None` | `_insert_nieuwe_transacties()` |
| `_ticker_resolutie_niet_opslaan_pad()` | lichte parallelle ticker-zekerheid per (ISIN, Beurs) | DataFrame → `(dict, lijst, lijst)` | `_upload_impl()` |
| `_bouw_transacties_df_niet_opslaan()` | bouwt een DataFrame in dezelfde vorm als uit de database | DataFrame + dict → DataFrame | `_upload_impl()` |
| `_create_synthetic_order_ids()` | synthetische ID's voor rijen zonder Order ID | DataFrame → DataFrame | `_upload_impl()` |
| `_meld_order_ids()` | Diagnostiek-melding over echte versus synthetische ID's | Series → — | `_lees_transacties_excel()` |
| `_vind_of_maak_portfolio_code()` | bestaande code zoeken (`find_matching_code()`) of nieuwe maken (`generate_code()`), en daarna de kwaliteitsmelding voor de nieuwe rijen doen (`_meld_nieuwe_rijen_kwaliteit()`) | cursor, DataFrame, naam → `(code, match_code, rows_to_insert)` | `_upload_impl()` |
| `_ticker_resolutie_opslaan_pad()` | tickers voor de nieuwe rijen, met hergebruik van bekende tickers | cursor, code, DataFrame, vlag → dict | `_upload_impl()` |
| `_bouw_posities()` | groepeert per (ISIN, Beurs) tot `(product, isin, beurs, transacties)`; product is dat van de eerste rij | DataFrame → lijst | `_ticker_resolutie_niet_opslaan_pad()`, `_ticker_resolutie_opslaan_pad()` |
| `_probeer_andere_productnamen()` | probeert de lichte check met de productnaam van elke rij van een groep tot er een ticker is | groep, transacties, bekende ticker → resultaat-dict | `_ticker_resolutie_opslaan_pad()` |
| `_insert_nieuwe_transacties()` | roept per rij `db_insert_transactie()` aan (`INSERT ... ON CONFLICT (code, order_id) DO NOTHING`) | rijen → aantal ingevoegd | `_upload_impl()` |
| `_verwerk_dividend_bestand_indien_aanwezig()` | leest `request.files["bestand2"]`, slaat dividenden op | code → (schrijft naar DB) | `_upload_impl()` |

**Bijzonderheden en valkuilen**

- `_koers_eur` = `Koers` / `Wisselkoers` (als die er is en niet 0). Dát is wat in de kolom `transacties.koers` terechtkomt: **altijd in EUR**, ook voor een
  niet-EUR-genoteerde positie.
- `_insert_nieuwe_transacties()` vangt per rij elke exception af en gaat door: een mislukte rij (bv. door een ontbrekende ticker in de dict) wordt niet opgeslagen. `_meld_insert_resultaat()` meldt het aantal als `FOUT` in Diagnostiek en print `[upload] WARN`. De teruggegeven teller telt ook rijen die door `ON CONFLICT` genegeerd werden, en `_upload_impl()` gebruikt hem niet.
- `_kolom_of_naamloze_buurkolom()`: is de `Order ID`-kolom helemaal leeg, dan wordt de eerste niet-lege naamloze buurkolom gebruikt (rechts, dan links); is die er niet, dan blijft de lege kolom staan en krijgt elke rij via `_create_synthetic_order_ids()` een synthetische ID.
- `_verwerk_dividend_bestand_indien_aanwezig()` importeert `request` uit Flask — deze module is daardoor niet buiten een request te draaien voor dit ene stuk.
- De taakfuncties volgen dezelfde stappen als in hoofdstuk 2; `_upload_impl()` in `app.py` roept ze in die volgorde aan.

---

### `portfolio_orchestratie.py` — de laag tussen routes en domeinmodules

**Verantwoordelijkheid:** transacties + koersen ophalen (met korte cache), en daaruit de dashboard-respons opbouwen (kern + verrijking).

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `_haal_portfolio_basis()` | `db_get_portfolio_naam_en_transacties()` + split-correctie + `get_prices()`, 20 s gecachet in `_basis_cache` | `code` → `(naam, transacties_df, price_data)` of `(None, None, None)` | `portfolio_verrijking()`, `_ticker_zekerheid_groepen()`, `build_portfolio_response()` |
| `_wis_portfolio_basis_cache()` | verwijdert de cache-entry van een code | `code` → — | `_upload_impl()`, `api_portfolio()`, `set_bijnaam()`, `reset_bijnaam()`, `verwijder_portfolio()`, `wijzig_code()` |
| `_laad_split_gecorrigeerde_transacties()` | `db_get_portfolio_naam_en_transacties()` + split-correctie, **zonder** koersen en zonder cache | `code` → `transacties_df` of `None` als de code niet bestaat | `_laad_transacties_en_resultaat()`, `ticker_koers_bereik()` |
| `_laad_transacties_en_resultaat()` | `_laad_split_gecorrigeerde_transacties()` + `get_prices()` + `compute_value_over_time()` | `code` → `(transacties_df, resultaat)`; `(None, None)` als de code niet bestaat; `(df, None)` zonder koersdata | `benchmark_vergelijking()`, `rendement_over_tijd()` |
| `_ticker_zekerheid_groepen()` | groepeert transacties per (ISIN, Beurs), zonder corporate-action-rijen | `code` → lijst `((isin, beurs), info)` of `None` | `ticker_zekerheid_lijst()`, `ticker_zekerheid_positie()` |
| `build_portfolio_response()` | basis ophalen → kern | `code, verversen` → dict of `None` | `_upload_impl()`, `api_portfolio()`, `set_bijnaam()`, `reset_bijnaam()`, `wijzig_code()` |
| `analyze_transacties_kern()` | de snelle helft van het dashboard | `transacties_df, code, naam, ...` → dict | `build_portfolio_response()`, `analyze_transacties()` |
| `analyze_transacties_verrijking()` | Verdeling (+ `verdeling_samenvatting`), Land/Sector, Valuta, Bedrijven, ETF-overlap | `transacties_df, code, ...` → dict | `portfolio_verrijking()`, `analyze_transacties()` |
| `analyze_transacties()` | kern + verrijking in één keer (voor "niet opslaan") | `transacties_df, code, naam` → dict | `_upload_impl()` |

**Bijzonderheden en valkuilen**

- `_laad_split_gecorrigeerde_transacties()` (en dus `_laad_transacties_en_resultaat()`) haalt dezelfde gegevens op als `_haal_portfolio_basis()` (via dezelfde db-functie) maar gebruikt **de cache niet** (en geeft altijd verse koersen via `get_prices()`
  met de standaardinstellingen). De kolomlijst staat op één plek: `TRANSACTIE_KOLOMMEN` in `db.py`. Verschil: alleen `_haal_portfolio_basis()` cast `transactiekosten`/`waarde_eur` naar `float`; de andere laat ze als `Decimal`.
- `import resource` is Unix-only; op Windows is `resource` dan `None` en vervallen de `[memory]`-logregels stilzwijgend.
- De cache is een gewoon `dict` in het proces: bij twee gunicorn-workers zijn dat twee aparte caches.
- Bij "niet opslaan" krijgt `analyze_transacties_verrijking()` het **niet-split-gecorrigeerde** `transacties_df` mee (de kern past de correctie alleen lokaal toe),
  en doet de correctie + `get_prices()` daarom zelf nog eens.

### `portfolio_admin.py` — portfolio-code beheren en uploads matchen

**Verantwoordelijkheid:** 3-letter-codes genereren/valideren en bepalen of een nieuwe upload bij een bestaand portfolio hoort (via Order ID-overlap).
Constante: `CODE_LENGTH = 3`; `app.py` geeft die ook als `code_lengte` mee aan `portfolio.html` (de `maxlength` van het veld "Nieuwe code"). Importeert alleen uit `db.py` (de SQL zelf staat daar).

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `is_geldige_code()` | `re.fullmatch` op exact `CODE_LENGTH` hoofdletters A–Z | tekst → bool | `portfolio_pagina()`, `wijzig_code()` |
| `generate_code()` | willekeurige code, herhaalt tot `db_portfolio_bestaat_met_cursor()` zegt dat hij nog niet in `portfolios` staat | cursor → tekst | `_vind_of_maak_portfolio_code()` |
| `find_matching_code()` | eerste code waarvan de bestaande set (uit `db_get_order_id_sets()`) ⊆ nieuwe set (dan: nieuwe − bestaande = te inserten), of nieuwe set ⊆ bestaande set (dan: niets nieuws) | cursor, set → `(code, set)` of `(None, None)` | `_vind_of_maak_portfolio_code()` |

**Valkuilen:** `db_get_order_id_sets()` (in `db.py`) leest bij elke upload alle Order ID's van alle portfolio's; de match is het *eerste* portfolio dat aan een van beide
deelverzameling-voorwaarden voldoet.

---

### `transactie_utils.py` — drie kleine gedeelde helpers

**Verantwoordelijkheid:** drie functies op ruwe transactierijen en datums, zonder DB/netwerk, gebruikt door meerdere modules (daarom een eigen bestand: anders circulaire imports).

| Functie | Wat | Aangeroepen door |
|---|---|---|
| `_is_corporate_action_row()` | `True` als `beurs == "DEG"` (hoofdletters, gestript) of `"NON TRADEABLE"` in de productnaam: de boekingsrijen die DeGiro voor splits e.d. maakt | `compute_split_adjusted_shares()`, `_ticker_zekerheid_groepen()`, `_meld_koersdekking()`, `_meld_nieuwe_rijen_kwaliteit()`, `_bouw_xirr_cashflows()`, `bereken_twr()`, `find_ticker_detailed()` |
| `_sorteer_chronologisch()` | sorteert op datum **plus tijd** (stabiele mergesort); rijen zonder tijd tellen als 00:00:00 | `compute_value_over_time()`, `compute_per_ticker()`, `compute_per_ticker_koers_en_aankopen()`, `bereken_holdings_en_gesloten()` |
| `formatteer_datum_nl()` | datum (date, Timestamp of ISO-string) als `dd-mm-jjjj` voor meldingen die de gebruiker ziet | `compute_split_adjusted_shares()`, `_meld_dividend_records()`, `prijswaarschuwing_voor_ticker()`, `verifieer_ticker_met_prijs()` |

**Waarom `_sorteer_chronologisch()` bestaat:** de database sorteert niet, en `datum` is alleen een DATE (het tijdstip staat in `tijd`). Een verkoop vóór de koop van
dezelfde dag verwerken gaf een "onbekende" verkoopkoers op Statistieken. Een ontbrekende tijd telt als 00:00:00; mergesort houdt de volgorde daarbinnen stabiel.

---

### `debug_utils.py` — logging

`DEBUG = True` (bovenin), `dprint()` (print alleen als `DEBUG`) en `meet_tijd(label)` (contextmanager die `[timing] label: 0.42s` print). Alles staat standaard
**aan**, dus ook productie print de `dprint`-regels. Alle geprinte vaste tekst is ASCII (`WARN`/`OK` i.p.v. emoji), zodat een Windows-console met cp1252
niet crasht. Er staan geen uitgecommentarieerde prints in de code: wat niet gelogd hoeft te worden, staat er niet.

---

### `diagnostiek.py` — meldingen per laadbeurt (Instellingen > Diagnostiek)

**Verantwoordelijkheid:** meldingen verzamelen over wat er tijdens het laden goed ging, minder verwacht was of misging, en die meesturen in het
JSON-antwoord. Geen afhankelijkheden op andere projectmodules, niets in de database.

- **Meldingsformaat:** een dict `{categorie, niveau, tekst, sleutel}`. Niveaus zijn constanten: `FOUT`, `LET_OP`, `INFO`, `GOED`.
- **`meld(categorie, niveau, tekst, sleutel=None)`** voegt een melding toe aan de huidige request (op Flask's `g`). Een tweede melding met dezelfde
  `(categorie, sleutel)` vervangt de eerste; zonder sleutel geldt de tekst als sleutel. Een ongeldig niveau wordt `INFO`. Gooit nooit een exception.
- **Alleen per laadbeurt:** de meldingen leven één request lang. Buiten een app-context (unittests, losse scripts, én de worker-threads van een
  `ThreadPoolExecutor`, zoals de prijscheck bij ticker-resolutie) is `meld()` een stille no-op. Daarom meldt `ticker_prijscheck.py` niets.
- **Meesturen:** `voeg_diagnostiek_toe(resultaat)` zet `haal_meldingen()` onder de sleutel `diagnostiek`. Gebruikt in `app.py` bij upload (opslaan
  en niet opslaan), ophalen met code en `/verrijking`. Niet bij bijnaam/code wijzigen.
- **Basis-cache:** `_haal_portfolio_basis()` bewaart bij een miss de meldingen die tijdens het ophalen ontstonden (`meldingen_sinds()`) in de
  cache-entry en geeft ze bij een hit opnieuw door (`meld_opnieuw()`). Laadtijden gaan bewust niet mee: bij een hit is die tijd niet besteed.
- **Threads:** meldingen alleen vanuit hoofdthread-code. Bij parallel werk (ticker-resolutie, ETF-cache opwarmen) volgt een samenvatting ná de
  parallelle stap. De Yahoo-tellers zijn wel globaal, dus calls uit threads tellen mee; de melding zelf komt uit de hoofdthread.
- **Frontend:** `static/js/diagnostiek.js` (puur, getest) voegt samen (nieuwste wint per categorie + sleutel), telt, groepeert en bepaalt het
  hoogste niveau per categorie (`hoogsteNiveau()`, `categorieStandaardOpen()`). `tabs/diagnostiek.js` bewaart de meldingen in `diagnostiekMeldingen`, gereset in
  `toonDashboard()` (nieuwe upload of andere code). Elke categorie is een `<details>`-blok: open bij `LET_OP`/`FOUT`, anders ingeklapt; een eigen
  open/dicht-keuze blijft staan tot een nieuwe upload/code.

**Categorie Wisselkoersen** (de eerste):

| Waar | Melding |
|---|---|
| `_adjust_transaction_exchange_rates()` | Excel-bron: `GOED` (wisselkoers gebruikt voor N van M transacties), `INFO` (kolom leeg). Een ontbrekende kolom komt hier niet meer: dat is een `OngeldigExcelBestand`. Alleen bij een upload. |
| `_haal_valuta_op()` | `LET_OP` per ticker: valuta niet op te halen, geen valuta van Yahoo, of valuta zonder FX-paar (bv. CHF). Naast de bestaande WARN-print. |
| `_fx_prijzen_serie()` | Per FX-paar: `GOED` (N koersen vanaf datum; uit cache / gedownload / ververst) of `FOUT` (geen koersdata). De herkomst noteert `get_prices()` via `_noteer_fx_bron()`. |

Geen FX-melding betekent: bij deze laadbeurt is niets gedownload of ververst (alles vers uit de cache), niet dat er iets mis is.

**Upload-categorieën** (alleen direct na een upload; Order ID's en Opslaan alleen in het opslaan-pad):

| Categorie | Waar | Melding |
|---|---|---|
| Order ID's | `_lees_transacties_excel()` → `_meld_order_ids()` | `GOED` alle ID's echt; `INFO` N van M synthetisch; `LET_OP` aantal ID-rijen ≠ aantal transacties (alles synthetisch; een eerder opgeslagen portfolio met echte ID's kan dan niet herkend worden, zie `find_matching_code()`). Nooit ID-waarden in de tekst. |
| Opslaan | `_vind_of_maak_portfolio_code()` | `INFO` nieuwe portfolio / bestaande aangevuld met N / geen nieuwe transacties. |
| Opslaan | `_meld_nieuwe_rijen_kwaliteit()` (vanuit `_vind_of_maak_portfolio_code()`) | `LET_OP` N gewone aankopen zonder `Waarde EUR` (GAK valt terug op `Totaal EUR`); `INFO` N corporate-action-rijen zonder ticker. Losse lege kostencellen bewust niet (kan echt €0 zijn). |
| Opslaan | `_insert_nieuwe_transacties()` → `_meld_insert_resultaat()` | `GOED` N opgeslagen (`cur.rowcount`), `INFO` K genegeerd (ON CONFLICT), `FOUT` J mislukt met alleen het fouttype + `[upload] WARN`-print. Bij een `psycopg2.Error` alleen de `FOUT` ("kan de hele upload hebben teruggedraaid"): na een DB-fout faalt de rest van de transactie. Het insert-gedrag zelf is ongewijzigd. |
| Dividend | `_verwerk_dividend_bestand_indien_aanwezig()` → `_meld_dividend_records()` | `GOED`/`INFO` samenvatting (EUR, gekoppeld, herinvesteerd, zonder conversie); `LET_OP` per uitkering zonder valutaconversie, max. `MAX_LOSSE_DIVIDEND_MELDINGEN` (5), daarboven één "Nog K ..."-melding. |
| Dividend | `_verwerk_dividend_bestand_zonder_opslaan()` | Bij "niet opslaan": zelfde `_meld_dividend_records()`-meldingen, zonder `db_save_dividenden`. |

**Laad-categorieën:**

| Categorie | Waar | Melding |
|---|---|---|
| Koersen | `get_prices()` → `_noteer_koers_bron()` + `_meld_koersen()` | `GOED` "N tickers: X uit cache, Y nieuw gedownload, Z ververst" (alleen niet-FX; per request opgeteld, per ticker telt de sterkste herkomst); `LET_OP` per ticker zonder koersdata (telt niet mee in de waarde, de inleg wel). |
| Koersen | `_meld_koersdekking()` (in `_haal_portfolio_basis()` en de niet-opslaan-kern) | `LET_OP` per ticker waarvan de eerste koers meer dan `MARGE_EERSTE_KOERS_DAGEN` (5) na de eerste echte transactie van die ticker ligt: tot dan telt de positie met waarde 0, de inleg wel. |
| Koersen | `meld_yahoo_samenvatting()` (`yahoo_client.py`, vanuit de routes) | `INFO` Yahoo-calls, retries en mislukte calls; `LET_OP` bij retries, `FOUT` bij mislukte calls. `/verrijking` meldt het verschil t.o.v. de stand bij de start (`yahoo_teller_stand()`). De retry-tellers veranderen niets aan het retry-gedrag. |
| Splits | `compute_split_adjusted_shares()` | `INFO` per toegepaste split (datum, factor); `LET_OP` per ISIN met corporate-action-rijen zonder bepaalde factor (met reden). Kan ook bij een niet-split (bv. ISIN-wissel) terecht zijn. |
| ETF-holdings | `_meld_etf_holdings()` (na het `verrijking_totaal`-blok) | Per ETF uit `per_etf[..]["land_bron"]`: `GOED` volledige holdings van de aanbieder; `INFO` alleen Yahoo-top-10; `LET_OP` geen holdings met landinformatie. Geen extra `get_etf_holdings()`-calls. |
| Laadtijden | `meet_tijd()` → `meld_laadtijd()` | Alleen de fasen in `LAADTIJD_FASEN`; `INFO` met de duur, `LET_OP` boven `DREMPEL_LAADTIJD_LET_OP_SECONDEN` (10 s; aanname: een derde van de standaard gunicorn-timeout van 30 s, de echte waarde staat niet in de repo). De `[timing]`-print blijft. |

**Nieuwe categorie toevoegen:** een constante `CATEGORIE_...` in `diagnostiek.py`, en `meld(CATEGORIE_..., niveau, tekst, sleutel=...)` naast de
bestaande logica (niet in plaats van `dprint`). Meld vanuit de hoofdthread; vanuit een thread gaat de melding verloren. Per categorie een
samenvatting; losse meldingen per ticker/ISIN alleen voor `LET_OP`/`FOUT`. De frontend hoeft niets te weten van nieuwe categorieën.

`debug_utils.py` importeert `diagnostiek` (voor de `meet_tijd`-hook). Dat geeft geen circulaire import zolang `diagnostiek.py` zelf alleen Flask importeert.

---

### `portfolio_calc.py` — tijdreeksen en split-correctie

**Verantwoordelijkheid:** de per-dag-berekeningen op `transacties_df` + `price_data` voor Home, Per aandeel en Per aandeel aankoop, plus de split-correctie.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `compute_split_adjusted_shares()` | voegt kolom `adj_aantal` toe: het aantal aandelen zoals het na latere splits zou zijn | `transacties_df` → kopie met `adj_aantal` | `_haal_portfolio_basis()`, `_laad_split_gecorrigeerde_transacties()`, `analyze_transacties_kern()`, `analyze_transacties_verrijking()` |
| `compute_value_over_time()` | per handelsdag: `waarde`, `geinvesteerd`, `rendement` | `transacties_df, price_data` → DataFrame (index = datum) | `_laad_transacties_en_resultaat()`, `analyze_transacties_kern()` |
| `compute_per_ticker()` | per ticker: `labels`, `waarde`, `geinvesteerd`, `nog_in_bezit` | idem → dict per ticker | `analyze_transacties_kern()` |
| `compute_per_ticker_koers_en_aankopen()` | per ticker: kale koers, aantal aangehouden, `nog_in_bezit` (zelfde drempel als `compute_per_ticker()`), aankoop- en verkoopdatums | idem → dict per ticker | `analyze_transacties_kern()` |
| `holdings_op_datums()` | aantal aangehouden stuks (cumulatieve `adj_aantal`) op elke gevraagde datum; vóór de eerste trade en na volledige verkoop 0, tussentijdse nul-periodes blijven staan | `trades_df` (1 ticker), `datums` → lijst floats | `ticker_koers_bereik()` |

**Hoe de split-correctie werkt** (`compute_split_adjusted_shares()`): per ISIN worden de corporate-action-rijen gezocht. Daarna een "conversierij": een echte
transactierij met `koers == 0` en `aantal > 0`. Per conversie: `shares_before` = som van `adj_aantal` van eerdere echte trades met koers > 0; `new_shares` = som
van positieve corporate-action-rijen tussen de laatste echte trade en de conversiedatum; `ratio = (shares_before + new_shares) / shares_before`. Alle eerdere
niet-corporate-action-rijen van die ISIN krijgen `adj_aantal *= ratio`. Wordt er voor een ISIN met corporate-action-rijen geen factor bepaald (geen conversierij, geen aandelen
vóór de conversie, of geen nieuwe aandelen), dan blijft `adj_aantal` ongewijzigd en volgt een `LET_OP`-melding in Diagnostiek (categorie Splits); de details gaan via `dprint` naar de log.

**Bijzonderheden en valkuilen**

- **"Geïnvesteerd" betekent hier twee dingen.** In `compute_value_over_time()` is het de **netto cashflow**: `invested += -totaal_eur` bij elke rij (dus inclusief
  kosten, en een verkoop verlaagt het met de verkoopopbrengst). In `compute_per_ticker()` is het de **kostenbasis van de nu aangehouden stukken** volgens de
  gemiddelde-kostprijs-methode, op basis van `waarde_eur` (zonder kosten; valt terug op `totaal_eur` als die NULL is). Het portfolio-totaal op Home/Statistieken
  (`chart_data.geinvesteerd`, `statistieken.totalen.geinvesteerd`) is de eerste; de som van de per-aandeel-lijnen komt daar dus niet noodzakelijk mee overeen.
- In `compute_value_over_time()` geldt: een ticker zonder koersdata telt niet mee in `waarde`, maar zijn cashflow telt wel mee in `geinvesteerd` (de `LET_OP`-melding daarover komt uit `get_prices()`, niet uit deze functie).
  Transacties ná de laatste koersdatum tellen ook niet mee; dáárvoor print hij wel `[waarde] WARN ...`.
- De crop-range per ticker: begint 1 dag vóór de eerste activiteit, eindigt 1 dag ná de laatste als de positie niet meer wordt aangehouden. "Nog in bezit" is bepaald
  op het **aandelenaantal** (`abs(holdings) > 1e-6`), niet op `geinvesteerd` (dat blijft na een winstgevende verkoop > 0). De crop-range telt ook datums met "activiteit" mee, zodat een koop + volledige
  verkoop op één dag (aantal per saldo 0) toch zichtbaar blijft. `compute_per_ticker_koers_en_aankopen()` gebruikt dezelfde crop-logica (bewust gekopieerd, niet gedeeld).
- `compute_split_adjusted_shares()` wordt met één getalvoorbeeld getest (`tests/backend/test_diagnostiek_laden.py`: 10 stuks + 30 nieuwe → factor 4); de meeste
  andere tests geven `adj_aantal` als invoer of mocken de functie. **Onzeker:** of de detectie voor alle DeGiro-variantexports werkt, is niet uit de code
  alleen af te leiden.

---

### `statistieken.py` — rendement, XIRR, TWR, GAK, jaaroverzicht

**Verantwoordelijkheid:** de berekeningen voor het Statistieken-tabblad en het Rendement-tabblad (%-weergave). Bijna alles zijn **pure functies** (getallen/DataFrames in,
getallen uit, geen DB/netwerk), daardoor met de hand na te rekenen en goed te testen. Enige externe library: `pyxirr`.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `bereken_positie_rendement()` | rendement van 1 positie: `waarde = aantal × koers`, `geinvesteerd = gak × aantal` | `gak, aantal, koers` → dict | `bereken_statistieken()` |
| `bereken_totaal_rendement()` | `rendement_eur = waarde − geinvesteerd`; `rendement_pct` (None bij geinvesteerd 0) | twee getallen → dict | `bereken_statistieken()`, `bereken_rendement_over_tijd()` |
| `bereken_jaar_rendement()` | winst van 1 kalenderjaar; % = winst / (startwaarde + ingelegd) | drie getallen → dict | `bereken_jaren_overzicht()` |
| `bereken_xirr()` | geannualiseerd rendement via `pyxirr.xirr`; `None` bij < 2 cashflows of een fout | lijst `(datum, bedrag)` → fractie of `None` | `bereken_statistieken()`, `bereken_rendement_over_tijd()` |
| `bereken_twr()` | time-weighted return: sub-periode-rendement `waarde_eind / (waarde_start + cf) − 1`, aan elkaar vermenigvuldigd | `transacties_df, resultaat` → fractie of `None` | `bereken_statistieken()`, `bereken_rendement_over_tijd()` |
| `bereken_holdings_en_gesloten()` | **GAK/kostprijs**: per ticker één pas door de transacties; geeft open én gesloten posities | `transacties_df` → `(open, gesloten)` | `bereken_statistieken()` |
| `bereken_jaren_overzicht()` | per kalenderjaar: start/eind, ingelegd, winst, dagen verstreken | `resultaat, eerste_datum` → lijst dicts | `bereken_statistieken()` |
| `_bouw_xirr_cashflows()` | cashflow-lijst (echte transacties + fictieve eindwaarde) | `transacties_df, resultaat` → lijst | `bereken_statistieken()`, `bereken_benchmark_vergelijking()`, `bereken_rendement_over_tijd()` |
| `bereken_benchmark_vergelijking()` | simuleert dezelfde cashflows in een benchmark | `transacties_df, resultaat, koersen` → dict of `None` | `benchmark_vergelijking()` |
| `bereken_rendement_over_tijd()` | rendement%/XIRR%/TWR% per maandeinde (+ laatste datum) | `transacties_df, resultaat` → dict met lijsten | `rendement_over_tijd()` |
| `bereken_totale_transactiekosten()` | som van `transactiekosten`; `beschikbaar=False` als de kolom leeg is | `transacties_df` → dict | `bereken_statistieken()` |
| `bereken_statistieken()` | orkestratie voor het Statistieken-tabblad | `transacties_df, price_data, resultaat, ...` → dict | `analyze_transacties_kern()` |

Constante: `BENCHMARK_TICKERS = {"S&P 500": "VUSA.AS", "Nasdaq 100": "CNDX.AS", "AEX": "IAEA.AS"}`.

**Bijzonderheden en valkuilen**

- **GAK-methode** (`bereken_holdings_en_gesloten()`): lopend gemiddelde. Bij een koop: `aantal += n`, `kostprijs += waarde_eur` (zonder kosten; fallback `totaal_eur`). Bij een verkoop
  (alleen als er een echte cashflow is, `totaal_eur != 0`): de kostenbasis van de verkochte stukken (`GAK × verkocht`) gaat eraf. Een **split-boekingsrij** (aantal ≠ 0, `totaal_eur = 0`)
  verandert alleen het aantal, niet de kostenbasis; zo verdunt een split de GAK vanzelf. Voorbeeld uit `tests/backend/test_rendement.py`: 10 stuks × €10, dan −10 (DEG) en +20 (conversie) →
  20 stuks, kostenbasis €100, GAK €5.
- **Gerealiseerd resultaat** = verkoopopbrengst (`totaal_eur`, dus mét kosten) − kostenbasis van het verkochte deel (GAK, zonder kosten). Aan de koopkant tellen kosten dus niet mee, aan de
  verkoopkant wel.
- **XIRR** gebruikt `totaal_eur` (inclusief kosten) plus één fictieve verkoop van de huidige waarde op de laatste datum.
- **All-time high** is de hoogste waarde van `rendement` (waarde − geïnvesteerd), niet de hoogste portefeuillewaarde.
- `gemiddeld_jaarrendement_pct` is het **rekenkundig gemiddelde** van de jaarlijkse `winst_pct`; `aantal_jaren` = dagen / 365,25.
- `bereken_rendement_over_tijd()` herrekent XIRR én TWR voor elke maandstap vanaf het begin; dat is de reden dat dit een apart, lui endpoint is. Elke stap
  krijgt een eigen fictieve eind-cashflow (de waarde op die datum). Vlak na een storting lopen XIRR en TWR duidelijk uiteen; dat is verwacht, geen bug.
- **TWR**: de cashflow telt mee in de noemer van de sub-periode die op die datum eindigt; corporate-action-rijen tellen niet als cashflow; sub-periodes
  met een noemer ≈ 0 (vóór de eerste aankoop) worden overgeslagen. XIRR is volgorde-onafhankelijk; alleen de GAK-boekhouding hangt van de verwerkingsvolgorde af.
- **Benchmark-vergelijking**: dezelfde cashflows (datum, bedrag) worden in de benchmark gestoken. De benchmarks zijn accumulerende UCITS-ETF's in EUR (geen
  dividend-boekhouding nodig). IAEA.AS (AEX) heeft pas koersen vanaf 29-07-2020; begint een benchmark later dan de eerste cashflow, dan begint de reeks later en is
  `onvolledige_dekking` `True`.

---

### `dividend.py` — het rekeningoverzicht en de dividend-samenvatting

**Verantwoordelijkheid:** het DeGiro-"Account"/rekeningoverzicht inlezen, per uitkering het netto-bedrag in EUR bepalen (inclusief koppelen aan valutaconversie-rijen), en de
opgeslagen dividenden samenvatten voor de UI.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `_koppel_valutaconversie_paren()` | vindt paren "Valuta Debitering"/"Valuta Creditering" met exact hetzelfde `(Datum, Tijd)`; bepaalt per paar welke rij EUR is | DataFrame → lijst dicts | `verwerk_rekeningoverzicht_df()` |
| `_match_valutaconversie()` | zoekt het nog ongebruikte paar met dezelfde valuta en (binnen tolerantie 0,02) hetzelfde bedrag | paren, valuta, bedrag, datum → paar of `None` | `verwerk_rekeningoverzicht_df()` |
| `_clusters_binnen_venster()` | groepeert op datum gesorteerde items zolang twee opeenvolgende ≤ `max_dagen` uit elkaar liggen | items, dagen → lijst clusters | `verwerk_rekeningoverzicht_df()` |
| `verwerk_rekeningoverzicht_df()` | het eigenlijke rekenwerk: netten per (Datum, ISIN), omrekenen naar EUR, gepoolde conversies, `herinvesteerd`-vlag | DataFrame → lijst records | `verwerk_rekeningoverzicht()` |
| `verwerk_rekeningoverzicht()` | Excel inlezen + kolommen hernoemen, dan `verwerk_rekeningoverzicht_df()` | bestandsobject → lijst records | `_verwerk_dividend_bestand_indien_aanwezig()` |
| `bereken_dividend_samenvatting()` | leest `dividenden` (via `db_get_dividenden()`), koppelt ISIN → ticker/bijnaam via `transacties`, bouwt `totaal_netto`, `per_ticker`, `cumulatief`, `lijst` | `code` → dict of `None` | `dividend()` (route), `analyze_transacties_kern()` |

Constante: `DIVIDEND_POOL_MAX_DAGEN_VERSCHIL = 3`.

**Bijzonderheden en valkuilen**

- De kolomkoppen "Mutatie" en "Saldo" zijn in het Excel-bestand samengevoegd over twee kolommen; `verwerk_rekeningoverzicht()` hernoemt daarom **op positie-naam**:
  `"Mutatie"` → `valuta_mutatie`, `"Unnamed: 8"` → `mutatie`, `"Saldo"` → `valuta_saldo`, `"Unnamed: 10"` → `saldo`. Verschuift het DeGiro-formaat, dan breekt dit.
- Koppelen aan de conversie gaat op **tijdstip van de conversie-rijen onderling** plus valuta+bedrag — **nooit** op de Valutadatum van de dividendrij (die loopt vaak een dag vóór).
- "Dividend Herinvestering"-rijen worden **meegenomen** in het netten (anders klopt het netto-bedrag niet) en zetten de vlag `herinvesteerd`. Die vlag
  komt als echte bool in `lijst[].herinvesteerd` (`db_get_dividenden()` cast naar `bool`); de frontend toont er een groen label "herinvesteerd" mee in de
  uitkeringenlijst (zie hoofdstuk 5). Getest in `tests/backend/test_dividend.py` (`TestDividendSamenvattingHerinvesteerd`).
- Lukt de koppeling niet, dan blijven `bruto_eur`/`belasting_eur`/`netto_eur` expliciet `None`: nooit een gok. Zulke rijen blijven wel in `lijst` staan, maar tellen niet mee in `totaal_netto`.
- `dividend_id` = `"DIV-"` + eerste 16 tekens MD5 over `datum|isin|bruto_ruw|belasting_ruw` (de ruwe bedragen, dus stabiel bij latere verbeteringen aan de EUR-omrekening).

---

### `portfolio_verdeling.py` — verdeling, land, sector, bedrijven, overlap

**Verantwoordelijkheid:** portfoliobrede aggregaties over alle huidige holdings (aantal × laatste koers). Gebruikt de **ruwe** kolom `aantal`, niet `adj_aantal`.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `compute_land_sector_verdeling()` | land en sector portfoliobreed (in €), plus per ETF en per bron | `transacties_df, price_data, is_etf_map` → dict met `land`, `land_europa`, `sector`, `per_etf`, `land_per_bron_top`, `land_per_bron_europa_top`, `sector_per_bron` | `analyze_transacties_verrijking()` |
| `compute_valuta_verdeling()` | huidige holdings (aantal × laatste koers, in €) per noteringsvaluta, ook van een ETF; kleine valuta's < `LAND_OVERIG_DREMPEL` naar "Overig" via `_voeg_kleine_landen_samen()` | `transacties_df, price_data` → dict met `valuta` (taart) en `valuta_per_bron` (staaf) | `analyze_transacties_verrijking()` |
| `bereken_bedrijven_verdeling()` | top-N onderliggende bedrijven (via ETF-holdings en losse aandelen) met uitsplitsing per bron | idem (+ `top_n`, standaard `BEDRIJVEN_TOP_N_STANDAARD` = 10) → dict met `top`, `overig`, `dekking_pct`, `totaal_waarde`, `top_n_standaard`, `bronnen` | `analyze_transacties_verrijking()` |
| `bereken_etf_overlap()` | overlapmatrix tussen aangehouden ETF's: Σ min(gewicht) over gedeelde bedrijven; `{}` bij < 2 ETF's | idem → dict `{a: {b: fractie}}` | `analyze_transacties_verrijking()` |
| `bereken_etf_overlap_detail()` | gewichten per bedrijf voor één ETF-paar | `etf_a, etf_b` → lijst | `etf_overlap_detail()` |
| `_holdings_gewicht_en_naam_per_bedrijf()` | holdings van 1 ETF, samengevoegd per genormaliseerde bedrijfsnaam | ticker → `(gewichten, namen)` | `bereken_etf_overlap()`, `bereken_etf_overlap_detail()` |
| `_normaliseer_bedrijfsnaam()` | lowercase, leestekens weg, `BEDRIJF_NAAM_OVERRIDES` toepassen | naam → sleutel | `bereken_bedrijven_verdeling()`, `_holdings_gewicht_en_naam_per_bedrijf()` |
| `_sorteer_tickers_voor_dropdown()` | eerst nog-in-bezit (groot→klein), dan verkocht (op piekwaarde) | `per_ticker` → gesorteerde tickers | `analyze_transacties_kern()` |
| `_sorteer_verdeling_groot_naar_klein()` | sorteert op `waarde` aflopend | lijst → lijst | `analyze_transacties_verrijking()` |
| `bereken_verdeling_samenvatting()` | ETF- vs. aandeelwaarde en -percentage (0–100) van de verdelingslijst; NaN/None/≤ 0 overgeslagen | lijst → dict `totaal`, `etf_pct`, `aandeel_pct` | `analyze_transacties_verrijking()` |
| `_voeg_kleine_landen_samen()` | landen < `LAND_OVERIG_DREMPEL` (0,5%) → "Overig" (de **taart**) | dict → dict | `compute_land_sector_verdeling()` |
| `_beperk_tot_top_n_per_bron()` | top `LAND_STAAF_TOP_N` (10) categorieën op totaal, de rest per bron opgeteld in "Overig" (de **staaf**) | `{categorie: {bron: bedrag}}` → idem | `compute_land_sector_verdeling()` |
| `_groepeer_europa_samen()` | alle `EUROPESE_LANDEN` → "Europe" | dict → dict | `compute_land_sector_verdeling()` |
| `_groepeer_europa_samen_per_bron()` | idem op de per-bron-structuur | dict → dict | `compute_land_sector_verdeling()` |

Constanten: `LAND_OVERIG_DREMPEL`, `LAND_STAAF_TOP_N = 10`, `EUROPESE_LANDEN` (frozenset; Rusland en Turkije zijn bewust **niet** opgenomen), `BEDRIJF_NAAM_OVERRIDES`, en `BEDRIJVEN_TOP_N_STANDAARD = 10` en
`BEDRIJVEN_TOP_N_MAX = 50`. `analyze_transacties_verrijking()` vraagt `top_n=BEDRIJVEN_TOP_N_MAX` op; de frontend knipt de lijst zelf in tot de gekozen N (geen nieuw request bij wisselen tussen 10/20/50).

**Bijzonderheden en valkuilen**

- **"Overig" bij Land is bewust per weergave anders:** de taart (`land`/`land_europa`) voegt landen < 0,5% samen, de gestapelde staaf (`land_per_bron_top`/`land_per_bron_europa_top`) toont de top 10 + de rest. De onbeperkte land-per-bron-dict bestaat alleen
  intern in `compute_land_sector_verdeling()` en gaat niet mee in de JSON.
- Wat niet gedekt is (bijv. bij `yfinance_top10` alles buiten de top 10, of een leeg antwoord) gaat naar `"Unknown"`, zodat de totalen kloppen en onbekend zichtbaar blijft.
- Gewichten van ETF-holdings zijn **fracties 0–1**; bedragen zijn in euro; `bereken_bedrijven_verdeling()` geeft percentages van het portfolio.
- Overlap telt alleen bedrijven waarvan de genormaliseerde naam gelijk is; providers schrijven namen verschillend (`BEDRIJF_NAAM_OVERRIDES` vangt uitzonderingen zoals ASML op).
- Dit bestand roept veel `get_etf_holdings()`/`get_land_sector()` aan; die zijn gecachet in de database, en `_verwarm_land_sector_cache_parallel()` warmt de cache parallel voordat deze
  functies sequentieel draaien.

### `prijzen.py` — koersen ophalen en cachen, valuta naar EUR

**Verantwoordelijkheid:** `get_prices()` levert een DataFrame met koersen in EUR voor een lijst tickers, met de tabel `prijzen` als cache en Yahoo als bron.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `get_prices()` | koersen (EUR) ophalen: uit cache, nieuwe tickers volledig downloaden, verouderde incrementeel verversen | `tickers, start_date, verversen=True` → DataFrame (index = datum, kolommen = tickers) | `_haal_portfolio_basis()`, `_laad_transacties_en_resultaat()`, `analyze_transacties_kern()`, `analyze_transacties_verrijking()`, `benchmark_vergelijking()`, `ticker_koers_bereik()`, `_fx_prijzen_serie()` |
| `_converteer_naar_eur()` | rekent `raw[t]` in-place om voor tickers in USD, GBP of GBp (pence, gedeeld door 100) | `raw, tickers, verversen` → — | `get_prices()` |
| `_haal_valuta_op()` | noteringsvaluta van een ticker via `yf.Ticker(t).info["currency"]`; mislukte opvraging of geen valuta → `"EUR"` met een `[koersen] WARN`-print; een valuta zonder FX-paar (bv. CHF) wordt teruggegeven, ook met een `WARN` | ticker → valutacode | `_converteer_naar_eur()` |
| `_fx_prijzen_serie()` | FX-koersreeks (bv. `USDEUR=X`) vanaf `FX_ANKER_DATUM`, via dezelfde cache; gememoized per request op Flask's `g`; één lock per FX-paar | `valuta, verversen` → Series (leeg bij onbekende valuta) | `_converteer_naar_eur()`, `_fx_koers_op_datum()` |

Constanten: `FX_PAAR_PER_VALUTA` (`USD`→`USDEUR=X`, `GBP` en `GBp`→`GBPEUR=X`), `FX_ANKER_DATUM = pd.Timestamp("2005-01-01")`, `DREMPEL_HERGEBRUIK_KOERS` (2 minuten), `_fx_serie_locks`.

Niet in de tabel: de Diagnostiek-helpers `_noteer_koers_bron()`, `_meld_koersen()`, `_noteer_fx_bron()`, `_fx_bron()` en `_meld_fx_reeks()`. Ze houden per request bij of een reeks uit de cache kwam, gedownload of ververst is, en maken daar de meldingen van (zie `diagnostiek.py`).

**De logica van `get_prices()`:**

1. Eén query naar `prijzen` voor de vroegste én laatste gecachte datum per ticker, één voor de `bijgewerkt_op` van "vandaag", één voor alle rijen vanaf `start_date`.
2. Per ticker: niet in cache, **of** cache begint > 5 dagen ná `start_date` → **missing** (volledig downloaden). Anders: tenzij de rij van vandaag < 2 minuten geleden is ververst → **stale**.
3. `missing`: één bulk-`download_met_retry()`, `ffill`, `_converteer_naar_eur()`, dan `db_save_prices()` (`ON CONFLICT DO NOTHING`).
4. `stale` (en `verversen=True`): per ticker een download vanaf de laatste gecachte datum, omrekenen, dan `db_upsert_prices()` (`DO UPDATE`, ook `bijgewerkt_op`).
5. Alles samenvoegen, `pivot()` en `ffill()`.

**Valkuilen**

- `yf.download(..., auto_adjust=True)`: de koersen zijn gecorrigeerd voor splits én dividend, en historische rijen worden bij `db_save_prices()` nooit overschreven. Of dat na een latere dividenduitkering
  merkbaar inconsistent wordt, kon ik niet uit de code afleiden: **onzeker**.
- `_converteer_naar_eur()` doet per te downloaden ticker een `yf.Ticker(t).info.get("currency")`-call (via `_haal_valuta_op()`), zonder retry en zonder cache; bij een fout of een ontbrekende valuta wordt EUR aangenomen. Alleen USD/GBP/GBp
  worden omgerekend — een ticker in een andere valuta wordt als EUR behandeld. In al die gevallen verschijnt een `[koersen] WARN`-regel in de terminal (altijd, ook met `DEBUG = False`), zodat een mogelijk verkeerde koers terug te vinden is.
- `FX_ANKER_DATUM` moet **na** Yahoo's echte eerste datum van elk FX-paar liggen, anders ziet `get_prices()` de cache steeds als "te kort" en downloadt hij elke keer opnieuw. Getest: USDEUR=X begint op 01-12-2003, GBPEUR=X op 17-09-2003. Het is een vaste datum (niet per aanroep), omdat
  `get_prices()` een cache tot 5 dagen na de startdatum al goed genoeg vindt; voor een punt-in-tijd-FX-lookup kan dat een andere handelsdag opleveren.
- De FX-memo op `g` onthoudt ook met welke `verversen`-waarde hij gevuld is: een memo met `verversen=False` (prijscheck tegen een historische datum)
  mag een latere aanroep met `verversen=True` (actuele koersen omrekenen) in hetzelfde request niet blokkeren. Er is bewust geen module-brede cache:
  die zou de 2-minuten-verversing van `get_prices()` omzeilen. De lock per FX-paar voorkomt dat parallelle threads hetzelfde paar tegelijk downloaden.
- `verversen=False` (gebruikt bij bijnaam/code wijzigen) slaat de incrementele verversing over; nog niet gecachte tickers worden altijd gedownload.

---

### `yahoo_client.py` — gedeelde Yahoo-infrastructuur

**Verantwoordelijkheid:** tellen van Yahoo-calls (voor de performance-meting en Diagnostiek) en de retry-logica. Importeert van de projectmodules alleen `diagnostiek`.

| Functie | Wat | Aangeroepen door |
|---|---|---|
| `reset_yahoo_call_teller()` | zet de teller op 0 | `_upload_impl()`, `api_portfolio()` |
| `_tel_yahoo_call()` | telt één call van een soort (bv. `"yf.download"`), thread-safe met een lock | `download_met_retry()`, `_fetch_yf_info()`, `_classify_ticker_uncached()`, `get_etf_sector_verdeling()`, `get_etf_holdings()`, `_yahoo_search()`, `_haal_koers_en_dagrange_op()`, `_haal_dagrange_op()`, `_haal_splits_op()`, `_haal_valuta_op()` |
| `_tel_yahoo_retry()` | telt een retry of een definitief mislukte call (alleen voor Diagnostiek; verandert het retry-gedrag niet) | `_met_rate_limit_retry()`, `download_met_retry()` |
| `yahoo_teller_stand()` | de huidige stand van de tellers, om later het verschil te kunnen melden | `portfolio_verrijking()`, `meld_yahoo_samenvatting()` |
| `meld_yahoo_samenvatting()` | zet het aantal calls, retries en mislukte calls als melding in Diagnostiek (categorie Koersen) | `_upload_impl()`, `api_portfolio()`, `portfolio_verrijking()` |
| `log_yahoo_call_samenvatting()` | print `[timing] Yahoo-calls sinds laatste reset: N totaal -> {...}` | `_upload_impl()`, `api_portfolio()`, `portfolio_verrijking()` |
| `_is_rate_limit_fout()` | herkent "rate limit", "too many requests", "invalid crumb", "error 401" in de foutmelding | `_met_rate_limit_retry()` |
| `_met_rate_limit_retry(actie, pogingen, wachttijd)` | voert een callable uit met max. `RATE_LIMIT_POGINGEN` (3) pogingen en oplopende wachttijd (`RATE_LIMIT_WACHTTIJD_BASIS` × poging = 8 s, 16 s); geeft `(resultaat, None)` of `(None, fout)` | `_fetch_yf_info()`, `_haal_dagrange_op()`, `_haal_koers_en_dagrange_op()` |
| `download_met_retry()` | `yf.download` met 3 pogingen en **vaste** 5 s wachttijd; retryt op **elke** fout; geeft bij mislukken een lege `Series` | `get_prices()` |

**Waarom twee retry-varianten:** `_met_rate_limit_retry()` retryt alleen bij rate-limit-achtige fouten (met backoff); `download_met_retry()` bij álle fouten met vaste wachttijd en een andere "leeg"-vorm. Ze zijn bewust niet samengevoegd.

---

### `ticker_matching.py` — welke Yahoo-ticker hoort bij deze DeGiro-positie?

**Verantwoordelijkheid:** zoeken via `yahooquery`, beurs-matching, handmatige overrides en de OpenFIGI-lookup als extra signaal.

Constanten: `BEURS_MAP` (DeGiro-beurscode → lijst Yahoo-exchange-codes: `EAM`, `XAMS`, `XET`, `FRA`, `TDG`, `LSE`, `XLON`, `NYSE`, `NASDAQ`, `ARCA`, `EPA`, `EBR`, `BME`, `BIT`, `SWX`, `TSE`, `ASX`, `NDQ`),
`MANUAL_TICKER_OVERRIDES` (naam-prefix → ticker; alleen als fallback), `MANUAL_TICKER_OVERRIDES_ISIN` (`(ISIN, Beurs)` → ticker; wordt **vóór** het zoeken gecheckt), `OPENFIGI_API_KEY`.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `find_ticker_detailed()` | hoofdfunctie: overrides → zoeken op naam (progressief inkorten) → zoeken op ISIN → fallbacks | `product, isin, beurs` → `{"ticker", "zekerheid", "alternatieven"}` | `find_ticker_met_snelle_prijscheck()`, `verifieer_ticker_met_prijs()` |
| `_zoek_product_progressief()` | zoekt de volledige naam; geen beurs-match → laatste woord eraf, opnieuw, tot 2 woorden | `product, beurs, targets` → `(symbol, zekerheid, alternatieven)` | `find_ticker_detailed()` |
| `_zoek_op_isin()` | zoekt op ISIN; zelfde uitkomst als `_zoek_product_progressief()` | `isin, targets, beurs` → `(symbol, zekerheid, alternatieven)` | `find_ticker_detailed()` |
| `_kies_beste()` | een "zekere" kandidaat wint van een onzekere, verder gaat de eerste voor | twee kandidaat-tuples → tuple | `find_ticker_detailed()` |
| `_naam_override()` | ticker uit `MANUAL_TICKER_OVERRIDES` (`product.upper().startswith(sleutel)`) of `None` | product → tekst of `None` | `find_ticker_detailed()` |
| `_als_resultaat()` | kandidaat-tuple → `{ticker, zekerheid, alternatieven}`, geen symbol = `geen_match` | tuple → dict | `find_ticker_detailed()` |
| `_alternatieven_naast()` | de overige quotes als `[{symbol, exchange}]` | quotes, symbol → lijst | `_zoek_product_progressief()`, `_zoek_op_isin()` |
| `_woorden_varianten()` | de naam van vol naar ingekort (min. 2 woorden) | tekst → lijst | `_zoek_product_progressief()` |
| `_yahoo_search()` | `yahooquery.search()`, geeft altijd een lijst (leeg bij een fout) | query → lijst quotes | `_zoek_product_progressief()`, `find_ticker_detailed()`, `_verzamel_extra_kandidaten()`, `_verrijk_met_openfigi_kandidaten()` |
| `_kies_beurs_match()` | eerste kandidaat op een van de verwachte beurzen | quotes, targets → `(symbol, exchange)` of `None` | `_zoek_product_progressief()`, `find_ticker_detailed()` |
| `_onzeker_fallback()` | neemt het eerste zoekresultaat als "onzeker" | quotes → `(symbol, alternatieven)` | idem |
| `haal_openfigi_resultaten()` | OpenFIGI-lookup per ISIN, met permanente DB-cache | `isin` → `{"resultaten": [...], "fout": ...}` | `_verrijk_met_openfigi_kandidaten()`, `_voeg_openfigi_check_toe()`, `prijswaarschuwing_voor_ticker()` |
| `_openfigi_root_matches()` | telt OpenFIGI-resultaten waarvan de ticker-root gelijk is aan of begint met die van de ticker (zonder Yahoo-suffix) | ticker, resultaten → int of `None` | `_voeg_openfigi_check_toe()`, `prijswaarschuwing_voor_ticker()` |

**Volgorde in `find_ticker_detailed()`:**
(1) corporate-action-rij → `geen_match`; (2) `MANUAL_TICKER_OVERRIDES_ISIN` → `zeker`; (3) `targets = BEURS_MAP.get(beurs, [])`; (4) `_zoek_product_progressief()`; (5) alleen als dat niet `zeker` was: ook op ISIN zoeken;
(6) "zeker" wint altijd van "onzeker"; (7) is er geen `zeker`: `MANUAL_TICKER_OVERRIDES` (`product.upper().startswith(sleutel)`) → `zeker`; (8) anders het beste "onzeker"-resultaat, of `geen_match`.
Zekerheid is dus altijd één van `"zeker"`, `"onzeker"`, `"geen_match"`.

**Valkuilen**

- `_yahoo_search()` heeft **geen retry** en slikt fouten in: een rate limit geeft `[]`, en dat is niet te onderscheiden van "niets gevonden".
- Een beurscode die niet in `BEURS_MAP` staat geeft `targets = []`: er kan dan nooit een "zekere" beurs-match uit het zoeken komen.
- Yahoo's zoekindex geeft niet elke notering terug voor elke spelling van een naam; daarvoor is `MANUAL_TICKER_OVERRIDES_ISIN` (voorbeeld in de code: BYD → `BY6.MU`, en `("IE00B3RBWM25", "EAM")` → `VWRL.AS`).
- OpenFIGI "geen match" wordt als lege lijst gecachet; fouten en rate limits niet.

---

### `ticker_prijscheck.py` — prijsvergelijking Yahoo ↔ DeGiro voor één (ticker, datum)

**Verantwoordelijkheid:** de historische Yahoo-slotkoers (en dagrange high/low) ophalen, omrekenen naar EUR, corrigeren voor splits, en vergelijken met de DeGiro-transactieprijs.

Constanten: `PRIJSCHECK_DREMPEL_OK = 0.02`, `PRIJSCHECK_DREMPEL_WAARSCHUWING = 0.06`, `DAGRANGE_TOLERANTIE = 0.05`.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `vergelijk_prijs_op_datum()` | **de kern**: cache lezen/vullen (`ticker_prijscheck`), FX + split-correctie, afwijking en `niveau` bepalen | `ticker, datum, bekende_koers` → dict (`yahoo_koers`, `afwijking_pct`, `niveau` = `"ok"`/`"mild"`/`"waarschuwing"`, `match`, `high`, `low`, `binnen_dagrange`, ...) | `_ticker_heeft_prijsprobleem()`, `_zoek_betere_alternatieven()`, `find_ticker_met_snelle_prijscheck()`, `prijswaarschuwing_voor_ticker()`, `verifieer_ticker_met_prijs()` |
| `_prijscheck_is_probleem()` | is dit één check een "probleem"? Primair: valt de koers buiten de dagrange (± 5%)? Zonder dagrange: `match is False` (afwijking ≥ 6%) | check-dict → bool | `_ticker_heeft_prijsprobleem()`, `find_ticker_met_snelle_prijscheck()`, `prijswaarschuwing_voor_ticker()`, `verifieer_ticker_met_prijs()` |
| `_haal_koers_en_dagrange_op()` | slotkoers + high + low in één `yf.download` | ticker, datum → `(close, high, low)` of `(None,)*3` | `vergelijk_prijs_op_datum()` |
| `_haal_dagrange_op()` | alleen `(high, low)` | idem → tuple | `vergelijk_prijs_op_datum()` |
| `_haal_splits_op()` | splitsgeschiedenis via `yf.Ticker(t).splits`, 30 dagen gecachet in `ticker_splits` | ticker → `{iso_datum: ratio}` | `_cumulatieve_split_factor()` |
| `_cumulatieve_split_factor()` | product van alle splitsratio's ná een datum | ticker, datum → float | `vergelijk_prijs_op_datum()` |
| `_fx_koers_op_datum()` | EUR-koers van een valuta op de eerste handelsdag op/na een datum | valuta, datum → float of `None` | `vergelijk_prijs_op_datum()` |

**Bijzonderheden en valkuilen**

- Alle downloads gebruiken een buffer van 7 dagen en pakken de **eerste geldige handelsdag op of na** de datum (weekend/feestdag).
- Drie niveaus: afwijking < 2% → `ok`; 2–6% → `mild` (telt niet als probleem); ≥ 6% → `waarschuwing`. `match` is `False` alleen bij `waarschuwing`.
  Een kleine afwijking is normaal: Yahoo's slotkoers wordt vergeleken met een intraday-transactieprijs.
- `DAGRANGE_TOLERANTIE` (5%): exact `low <= koers <= high` bleek te strak; bekend-goede tickers (VUSA.AS, G2X.DE) vielen er ~1–2% buiten.
- Zonder FX-omrekening leek bv. NFLX (Yahoo in USD) ~17% af te wijken; zonder splitcorrectie "week" een oude BYD-transactie 71% af.
- Waarom de split-correctie nodig is: `auto_adjust=True` geeft historische koersen op de *huidige* aandelenbasis, terwijl DeGiro de destijds werkelijke prijs vermeldt.
- De cache `ticker_prijscheck` is **permanent** en cachet ook mislukte lookups (`yahoo_slotkoers = NULL`); een rij zonder high/low wordt bij een volgend gebruik aangevuld.
- Geen FX-koers beschikbaar (andere valuta dan USD/GBP/GBp) → geen vergelijking (`match = None`), bewust geen rauwe vergelijking tussen verschillende valuta. Dit print een `[prijscheck] WARN`-regel.
- Valuta onbekend (`None` in `ticker_info`) → de Yahoo-koers wordt als EUR behandeld, met een `[prijscheck] WARN`-regel.

---

### `ticker_classificatie.py` — ETF of aandeel, plus land/sector/holdings met cache

**Verantwoordelijkheid:** bepalen of een ticker een ETF is en land/sector/holdings opzoeken, met de database als cache.

| Functie | Wat | Input → output | Aangeroepen door |
|---|---|---|---|
| `classify_tickers()` | batch-variant met 1,5 s pauze tussen niet-gecachete Yahoo-calls | lijst tickers → `{ticker: bool}` | `analyze_transacties_verrijking()` |
| `classify_ticker()` | één ticker, cache-first (`ticker_info`) | ticker → bool | `_land_sector_voor_weergave()`, `_zoek_betere_alternatieven()`, `verifieer_ticker_met_prijs()` |
| `_classify_ticker_uncached()` | de echte yfinance-lookup: `quoteType`, land, sector, valuta, beurs, fondsfamilie, categorie | ticker → dict of `None` (rate limit) | `classify_ticker()`, `classify_tickers()`, `_ticker_details_met_cache()` |
| `_fetch_yf_info()` | `yf.Ticker(t).info` met `_met_rate_limit_retry()` | ticker → dict of `None` | `_classify_ticker_uncached()`, `get_land_sector()` |
| `_ticker_details_met_cache()` | land/sector/valuta/... uit `ticker_info`; opnieuw ophalen als `valuta` en `quote_type` beide NULL zijn | ticker → dict | `vergelijk_prijs_op_datum()`, `_zoek_betere_alternatieven()`, `verifieer_ticker_met_prijs()` |
| `get_valuta()` | noteringsvaluta uit `ticker_info` (via `_ticker_details_met_cache()`); `"Unknown"` i.p.v. `None`, en Yahoo's `GBp` telt als `GBP` | ticker → tekst | `compute_valuta_verdeling()`, `_verwarm_land_sector_cache_parallel()` |
| `get_land_sector()` | land + sector van een los aandeel (30 dagen cache in `ticker_land_sector`); `"Unknown"` i.p.v. `None` | ticker → `(land, sector)` | `compute_land_sector_verdeling()`, `_verwarm_land_sector_cache_parallel()`, `get_etf_holdings()`, `_land_sector_voor_weergave()` |
| `get_etf_sector_verdeling()` | `funds_data.sector_weightings`, 30 dagen cache | ticker → `{sector: fractie}` (leeg bij mislukking, dan niet gecachet) | `compute_land_sector_verdeling()`, `_verwarm_land_sector_cache_parallel()`, `_sector_samenvatting()` |
| `get_etf_holdings()` | holdings: eerst provider (`fetch_provider_holdings()`), dan `funds_data.top_holdings` (max 10); 30 dagen cache | ticker → lijst dicts (`holding_naam`, `holding_ticker`, `gewicht`, `land`, `bron`) | `bereken_bedrijven_verdeling()`, `compute_land_sector_verdeling()`, `_holdings_gewicht_en_naam_per_bedrijf()`, `_verwarm_land_sector_cache_parallel()`, `_top_holding_land()` |
| `_verwarm_land_sector_cache_parallel()` | vult de caches parallel (`ThreadPoolExecutor`, 8 workers) | tickers, `is_etf_map` → — | `analyze_transacties_verrijking()` |
| `_sector_naam()` | `consumer_cyclical` → `Consumer Cyclical` | tekst → tekst | `get_etf_sector_verdeling()` |

**Bijzonderheden en valkuilen**

- ETF's hebben **geen** land/sector in `.info`; daarom `sector_weightings`/`top_holdings` voor ETF's en `.info` alleen voor losse aandelen.
- Is `quoteType` leeg, dan beslist een heuristiek (≥ 2 van 5 signalen → ETF).
- `_classify_ticker_uncached()` schrijft als bijproduct ook `ticker_land_sector` weg, zodat `get_land_sector()` daarna geen tweede identieke call hoeft te doen.
- `get_etf_holdings()`: een verse `yfinance_top10`-cache telt niet als "goed genoeg" zodra er inmiddels een provider-URL bekend is (`ETF_HOLDINGS_BRON`); dan wordt geprobeerd te upgraden naar `provider_csv`.
- `db_get_cached_classifications()` (in `db.py`) heeft **geen** leeftijdscheck: `ticker_info` verloopt nooit. Alleen `_ticker_details_met_cache()` herhaalt bij stale rijen.
- Bij een mislukte call wordt `False` (= "aandeel") teruggegeven maar **niet** gecachet; die keer telt de positie dus als aandeel.

---

### `ticker_zekerheid.py` — hoe zeker zijn we van deze ticker?

**Verantwoordelijkheid:** de orkestratie die prijscontrole (`ticker_prijscheck.py`), zoekresultaten (`ticker_matching.py`), OpenFIGI en classificatie combineert tot een zekerheidsoordeel. Er zijn **twee niveaus**:
een **lichte** check die bij elke upload draait, en een **volledige** check die alleen op de Ticker-zekerheid-pagina draait.

Constanten: `PRIJSCHECK_DREMPEL_ALTERNATIEVEN = 0.10`, `MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE = 2`, `MIN_STEEKPROEF_VOOR_VOLLEDIGE_MATCH = 2`, `TICKER_RESOLUTIE_POOL_GROOTTE = 12`.

| Functie | Wat | Aangeroepen door |
|---|---|---|
| `find_ticker_met_snelle_prijscheck()` | **licht**: ticker zoeken, prijs op de laatste transactiedatum vergelijken, alleen bij afwijking escaleren (zie hieronder) | `vind_tickers_met_snelle_prijscheck_parallel()`, `_ticker_resolutie_opslaan_pad()`, `backfill_verouderde_tickers()` |
| `_begin_resultaat()`, `_voeg_prijscheck_laatste_toe()`, `_moet_escaleren()`, `_voeg_steekproef_toe()`, `_corrigeer_met_alternatief()` | de stappen van `find_ticker_met_snelle_prijscheck()`: elke krijgt de resultaat-dict en geeft een nieuwe terug (zie hieronder) | `find_ticker_met_snelle_prijscheck()` |
| `vind_tickers_met_snelle_prijscheck_parallel()` | de vorige voor meerdere posities in een `ThreadPoolExecutor` (12 workers), met optionele `bekende_tickers` | `basis_ticker_zekerheid_parallel()`, `_ticker_resolutie_opslaan_pad()` |
| `basis_ticker_zekerheid_parallel()` | idem, resultaat in dezelfde vorm als de volledige check (`_naar_basis_vorm()`); het "niet opslaan"-pad | `_ticker_resolutie_niet_opslaan_pad()` |
| `_naar_basis_vorm()` | wikkelt een lichte resultaat in de vorm die de frontend-kaart verwacht (velden die alleen de volledige check kent staan op `None`) | `basis_ticker_zekerheid_parallel()` |
| `verifieer_ticker_met_prijs()` | **volledig**: 3 steekproefdatums, land/sector/valuta/beurs, alternatieven, OpenFIGI-kandidaten | `ticker_zekerheid_positie()`, `verifieer_tickers_met_prijs_parallel()` |
| `_leeg_resultaat()`, `_voeg_prijsoordeel_toe()`, `_voeg_kaartvelden_toe()`, `_voeg_alternatieven_toe()` | de stappen van `verifieer_ticker_met_prijs()`, elk krijgt de resultaat-dict en geeft een nieuwe terug: lege kaart, steekproefchecks + zekerheid/waarschuwing, ETF/land/sector/valuta/beurs, alternatieven + OpenFIGI-kandidaten (alleen als niet "zeker") | `verifieer_ticker_met_prijs()` |
| `verifieer_tickers_met_prijs_parallel()` | de vorige voor meerdere posities (6 workers) | `ticker_zekerheid_check()` |
| `_zoek_betere_alternatieven()` | rekent kandidaat-tickers door tegen de steekproef; stopt bij een overtuigende match | `find_ticker_met_snelle_prijscheck()`, `verifieer_ticker_met_prijs()` |
| `_verzamel_extra_kandidaten()` | extra zoekopdracht (volledige naam en ISIN, zonder beurs-beperking) als er geen alternatieven zijn | `verifieer_ticker_met_prijs()` |
| `_verrijk_met_openfigi_kandidaten()` | voegt kandidaten toe via de OpenFIGI-ticker-roots | `verifieer_ticker_met_prijs()` |
| `_voeg_openfigi_check_toe()` | zet `openfigi_root_bekend`/`openfigi_root_matches`; bij "root niet gevonden" een extra waarschuwing en "zeker" → "onzeker" | `find_ticker_met_snelle_prijscheck()`, `verifieer_ticker_met_prijs()` |
| `_kies_steekproef_transacties()` | eerste, middelste en laatste transactie met koers > 0 | `_voeg_steekproef_toe()`, `_corrigeer_met_alternatief()`, `verifieer_ticker_met_prijs()` |
| `_geldige_transacties()` | transacties zonder splitrijen (koers 0 of leeg) | lijst → lijst | `find_ticker_met_snelle_prijscheck()`, `_kies_steekproef_transacties()`, `_ticker_heeft_prijsprobleem()`, `prijswaarschuwing_voor_ticker()` |
| `_grootste_afwijking()` | grootste `afwijking_pct` van een lijst checks, `None` als er geen is | lijst → getal of `None` | `_voeg_steekproef_toe()`, `_corrigeer_met_alternatief()` |
| `_land_sector_voor_weergave()` | land/sector-weergave voor de kaart; voor een ETF geen los land, maar top-3 sectoren en "land grootste holding" | `verifieer_ticker_met_prijs()`, `_zoek_betere_alternatieven()` |
| `_sector_samenvatting()` | top-N sectoren van een ETF als tekst (sectoren op 0% tellen niet mee) | `_land_sector_voor_weergave()` |
| `_top_holding_land()` | land van de zwaarste holding van een ETF | `_land_sector_voor_weergave()` |
| `backfill_verouderde_tickers()` | herbeoordeelt opgeslagen tickers van een code en corrigeert de database | `_upload_impl()`, `api_portfolio()` |
| `_ticker_heeft_prijsprobleem()` | heeft een gevonden ticker een prijsprobleem op de laatste transactiedatum (of geen koersdata)? | `backfill_verouderde_tickers()` |
| `prijswaarschuwing_voor_ticker()` | leest alleen de gecachete prijscheck + OpenFIGI, geeft een waarschuwingstekst of `None`; doet **geen** live zoekopdracht | `ticker_waarschuwingen_voor_transacties()` |
| `ticker_waarschuwingen_voor_transacties()` | de vorige voor elke ticker in `transacties_df` | `analyze_transacties_kern()` |

**Het escalatietrapje in `find_ticker_met_snelle_prijscheck()`:**

1. Zoek de ticker met `find_ticker_detailed()` — óf sla dat over als `bekende_ticker` is meegegeven.
2. Vergelijk **alleen de laatste transactiedatum** (`vergelijk_prijs_op_datum()`; meestal 1 gecachete call). Geen afwijking → klaar.
3. Bij een probleem (buiten de dagrange, of helemaal geen koersdata): ook de rest van de steekproef (eerste/middelste/laatste) controleren, en een waarschuwing maken.
4. Is de grootste afwijking nog steeds > 10% (of nog steeds geen koersdata): alternatieven doorrekenen met `_zoek_betere_alternatieven()` en mogelijk **automatisch vervangen**:
   - **Tier 1:** alternatief op een verwachte beurs én ≥ 2 kloppende datums;
   - **Tier 2** (alleen als tier 1 niets vond): op een andere beurs, maar klopt op **alle** gecontroleerde datums (≥ 2 gecontroleerd);
   - anders hooguit een `aanbevolen_alternatief` als suggestie.
   Bij een automatische vervanging worden alleen `ticker`/`zekerheid` overschreven; er komt geen apart veld bij dat de oude ticker noemt.
5. Aan het eind, één keer, volgt `_voeg_openfigi_check_toe()`.

De functie is een reeks stappen op één resultaat-dict: `_begin_resultaat()` (1), `_voeg_prijscheck_laatste_toe()` (2), `_moet_escaleren()` beslist over (3) `_voeg_steekproef_toe()` en (4) `_corrigeer_met_alternatief()`. Zonder ticker of zonder geldige transacties doen de stappen niets.

**Bijzonderheden en valkuilen**

- Het veld `openfigi_kandidaten_debug` is **tijdelijk/diagnostisch** (volgens de eigen docstring), en bijbehorende UI-code staat in `maakOpenfigiKandidatenDebugBlok()` in `static/js/tabs/ticker_zekerheid.js`.
- Een automatische correctie gebeurt alleen in `find_ticker_met_snelle_prijscheck()`; `backfill_verouderde_tickers()` overschrijft alleen als de **nieuwe** kandidaat zelf géén prijsprobleem heeft.
- Bij `bekende_ticker` heeft de escalatie geen alternatieven (die kwamen uit de overgeslagen zoekopdracht); dat is bewust — `backfill_verouderde_tickers()` vangt dat daarna op.

---

### `etf_holdings_provider.py` — volledige ETF-holdings bij de fondsaanbieder

**Verantwoordelijkheid:** de volledige holdingslijst (incl. land per positie) downloaden en parsen, i.p.v. yfinance's top 10.

`ETF_HOLDINGS_BRON` is een dict `{ticker: {"provider", "url", optioneel "locale"}}`. Ingevuld voor 11 tickers. iShares: `CSPX.AS`, `IWDA.AS`, `IMAE.AS`, `EMIM.AS`, `IS3N.DE`, `CNDX.AS`, `EUEA.AS`. VanEck: `GDX.L`, `G2X.DE`, `TDT.AS`, `VE6I.DE`.
`IS3N.DE` en `G2X.DE` zijn een andere notering van hetzelfde fonds als `EMIM.AS` resp. `GDX.L` en gebruiken dezelfde bron-URL.

| Functie | Wat | Aangeroepen door |
|---|---|---|
| `fetch_provider_holdings()` | `requests.get(url, timeout=30)`, juiste parser kiezen, ontdubbelen; `None` bij elke fout | `get_etf_holdings()` |
| `_parse_ishares_holdings()` | iShares-CSV (`skiprows=2`; kolommen `Name`, `Weight (%)`, `Sector`, `Location`/`Country`) | via `_PROVIDER_PARSERS` (dict, dus niet als directe aanroep zichtbaar) |
| `_parse_vaneck_holdings()` | VanEck-XLSX (`skiprows=2`; kolomnamen wisselen per fonds/site; land uit een kolom, anders uit de ISIN-prefix) | via `_PROVIDER_PARSERS` |
| `_parse_percentage_waarde()` | `"10,74%"` / `7.68` → float, afhankelijk van `locale` | de `_parse_*`-functies |
| `_holding_rij()` | normaliseert één rij (`naam`, `gewicht`, `land`, `sector`); land wordt `"Unknown"` als leeg | de `_parse_*`-functies |
| `_vertaal_land_nl()` + `NL_LAND_VERTALING` | Nederlandse landnaam → Engelse (zodat één land niet twee taartpunten wordt) | `_parse_ishares_holdings()` |
| `_land_via_isin()` | land uit de eerste 2 tekens van een ISIN, via `pycountry` | `_parse_vaneck_holdings()` |
| `_dedupliceer_holdings()` | holdings met dezelfde naam samenvoegen (som van gewicht) | `fetch_provider_holdings()` |

**Valkuilen**

- `locale` is een eigenschap van de **bron-URL**, niet van het fonds: Nederlands getalformaat (`5,25%`) gelezen als Engels geeft een gewichtensom van ~10000%. De blackrock.com-bron (CSPX.AS, CNDX.AS, `locale=en_GB` in de URL) is Engels; ishares.com/nl
  (IWDA.AS, IMAE.AS, EMIM.AS) en VanEck's Nederlandse site (GDX.L) zijn Nederlands, ook qua landnamen. TDT.AS gaat via VanEck's Engelstalige NL-site (`/nl/en/`).
- `NL_LAND_VERTALING` gebruikt de gangbare Engelse namen zoals yfinance ("South Korea"), niet de ISO-namen van pycountry; anders wordt één land twee taartpunten.
- `_dedupliceer_holdings()` is nodig omdat `etf_holdings` als PK `(etf_ticker, holding_naam)` heeft: EMIM.AS levert bv. `INR/USD` 29 keer (FX-hedges).
- Land via ISIN-prefix is het land van registratie, niet waar het bedrijf actief is (bv. een Britse ISIN voor het Zuid-Afrikaanse AngloGold Ashanti).
- Voor iShares/blackrock.com-URL's mag **geen `asOfDate`** in de URL (een datum die niet exact klopt geeft een lege CSV, geen fout).
- VWCE.AS en VUSA.AS (Vanguard) staan er bewust niet in: de Vanguard-download loopt via een GraphQL-API; ze vallen terug op `yfinance_top10`.
  Er is daarom ook geen Vanguard-parser; `_PROVIDER_PARSERS` kent alleen `"ishares"` en `"vaneck"`.

---

### `db.py` — database-connectie, schema, opslag en cache-helpers

**Verantwoordelijkheid:** alle SQL van de app; buiten `db.py` staat geen `cur.execute`. Elke functie begint met `db_`, zodat je op de aanroepplek ziet dat de database wordt geraakt.
Functies met een `cur`-parameter draaien binnen de transactie van de aanroeper (`_upload_impl()` in `app.py`, `backfill_verouderde_tickers()`, `generate_code()`), die zelf commit;
alle andere functies openen en sluiten zelf een verbinding via `db_connect()`.
Hoofdstuk 4 beschrijft de tabellen; hier alleen de functies.

| Groep | Functies | Aangeroepen door |
|---|---|---|
| Verbinding en schema | `db_connect()`, `db_init()` | overal; `db_init()` alleen op moduleniveau in `app.py` |
| Classificatie (`ticker_info`) | `db_get_cached_classifications()`, `db_save_classification()`, `db_get_ticker_details()` | `classify_ticker()`, `classify_tickers()`, `_ticker_details_met_cache()` |
| Land/sector (`ticker_land_sector`) | `db_get_cached_land_sector()`, `db_save_land_sector()` | `get_land_sector()`, `_classify_ticker_uncached()` |
| ETF-caches | `db_get_cached_etf_sector_verdeling()`, `db_save_etf_sector_verdeling()`, `db_get_cached_etf_holdings()`, `db_save_etf_holdings()` | `get_etf_sector_verdeling()`, `get_etf_holdings()` |
| Prijscheck/splits/OpenFIGI | `db_get_cached_prijscheck()`, `db_save_prijscheck()`, `db_get_cached_splits()`, `db_save_splits()`, `db_get_cached_openfigi()`, `db_save_openfigi()` | `vergelijk_prijs_op_datum()`, `_haal_splits_op()`, `haal_openfigi_resultaten()` |
| Koersen (`prijzen`) | `db_get_gecachte_prijzen()`, `db_save_prices()`, `db_upsert_prices()`, `db_get_laatste_prijs_update()` | `get_prices()`, `analyze_transacties_kern()` |
| Portfolio beheren | `db_delete_portfolio()`, `db_wijzig_portfolio_code()`, `db_portfolio_bestaat()`, `db_portfolio_bestaat_met_cursor()` (cur), `db_maak_portfolio()` (cur), `db_zet_portfolio_naam()` (cur), `db_get_order_id_sets()` (cur) | `verwijder_portfolio()`, `wijzig_code()`, `dividend()`, `transacties_overzicht()`, `generate_code()`, `find_matching_code()`, `_vind_of_maak_portfolio_code()` |
| Transacties lezen | `TRANSACTIE_KOLOMMEN` (constante) en `db_get_portfolio_naam_en_transacties()`, `db_get_transacties_overzicht()`, `db_get_isin_ticker_product()`, `db_get_bekende_tickers()` (cur), `db_get_transacties_voor_tickercheck()` (cur) | `_haal_portfolio_basis()`, `_laad_split_gecorrigeerde_transacties()`, `transacties_overzicht()`, `bereken_dividend_samenvatting()`, `_ticker_resolutie_opslaan_pad()`, `backfill_verouderde_tickers()` |
| Transacties schrijven | `db_insert_transactie()` (cur), `db_wijzig_ticker()` (cur), `db_wijzig_bijnaam()`, `db_herstel_echte_naam()` | `_insert_nieuwe_transacties()`, `backfill_verouderde_tickers()`, `set_bijnaam()`, `reset_bijnaam()` |
| Dividend | `db_save_dividenden()`, `db_get_dividenden()` | `_verwerk_dividend_bestand_indien_aanwezig()`, `bereken_dividend_samenvatting()` |

Functies met `(cur)` krijgen een cursor van de aanroeper.

**Bijzonderheden en valkuilen**

- **Elke functie zonder `cur`-parameter opent en sluit zijn eigen verbinding** (geen connection pool). Dat is eenvoudig, maar betekent veel round-trips naar Neon.
- `CACHE_GELDIGHEID = "30 days"` geldt voor `ticker_land_sector`, `etf_sector_verdeling`, `etf_holdings` en `ticker_splits`.
- `db_save_dividenden()` en `db_save_prijscheck()` zijn **upserts** (`DO UPDATE`), `db_save_prices()` is `DO NOTHING` en `db_upsert_prices()` is `DO UPDATE`: kies bewust welke je nodig hebt.
- `db_wijzig_portfolio_code()` maakt eerst een nieuwe `portfolios`-rij, verhuist dan transacties/dividenden en verwijdert daarna de oude rij (de foreign key laat een directe hernoeming niet toe).

## 4. Database

Alle tabellen worden aangemaakt in `db_init()` (`db.py`), PostgreSQL bij Neon. Er zijn **11 tabellen**: 3 met persoonlijke data (`portfolios`, `transacties`, `dividenden`) en 8 die
"anonieme marktdata/cache" zijn (`prijzen`, `ticker_info`, `ticker_land_sector`, `etf_sector_verdeling`, `etf_holdings`, `ticker_prijscheck`, `ticker_splits`, `openfigi_cache`).
`db_delete_portfolio()` verwijdert alleen de eerste groep; de caches blijven staan.

### 4.1 Persoonlijke data

#### `portfolios`
| Kolom | Type | Betekenis |
|---|---|---|
| `code` | TEXT, primary key | de 3-letter-code |
| `naam` | TEXT | optionele naam |
| `aangemaakt_op` | TIMESTAMP, default nu | |

Schrijven: `_vind_of_maak_portfolio_code()` (via `db_maak_portfolio()` en `db_zet_portfolio_naam()`), `db_wijzig_portfolio_code()`, `db_delete_portfolio()`.
Lezen: `generate_code()` (bestaat de code al? via `db_portfolio_bestaat_met_cursor()`), `_haal_portfolio_basis()`, `_laad_split_gecorrigeerde_transacties()` (voor `ticker_koers_bereik()`, `benchmark_vergelijking()`, `rendement_over_tijd()`; beide via `db_get_portfolio_naam_en_transacties()`), de routes `dividend()` en `transacties_overzicht()` (via `db_portfolio_bestaat()`), `db_wijzig_portfolio_code()`.

#### `transacties`
| Kolom | Type | Betekenis |
|---|---|---|
| `id` | SERIAL, primary key | |
| `code` | TEXT, NOT NULL, foreign key → `portfolios(code)` | |
| `datum` | DATE, NOT NULL | transactiedatum |
| `tijd` | TIME | uitvoeringstijd; nodig voor de chronologische volgorde binnen een dag |
| `product` | TEXT, NOT NULL | **bijnaam** (bij een nieuwe ticker Yahoo's `longName`, anders `echte_naam`; één waarde per ticker per portfolio; aanpasbaar via Instellingen → Bijnamen) |
| `echte_naam` | TEXT | de productnaam zoals in het Excel-bestand; dit gaat naar de Yahoo-zoekopdracht |
| `isin` | TEXT, NOT NULL | |
| `beurs` | TEXT | DeGiro-beurscode (sleutel in `BEURS_MAP`); corporate-action-rijen hebben `DEG` |
| `ticker` | TEXT | gevonden Yahoo-ticker |
| `aantal` | NUMERIC, NOT NULL | negatief bij verkoop |
| `koers` | NUMERIC | koers per stuk **in EUR** (`_koers_eur`, zie hoofdstuk 3) |
| `totaal_eur` | NUMERIC, NOT NULL | totaalbedrag inclusief kosten/AutoFX; negatief bij koop |
| `waarde_eur` | NUMERIC | kale waarde (aantal × koers, zonder kosten); basis voor de GAK |
| `transactiekosten` | NUMERIC | DeGiro-transactiekosten; `NULL` als de cel in het Excel-bestand leeg is |
| `order_id` | TEXT | echte UUID of synthetische `SYN-...` |
| | `UNIQUE (code, order_id)` | voorkomt dubbele rijen bij herhaalde upload |

Schrijven: `_insert_nieuwe_transacties()` (INSERT via `db_insert_transactie()`); `backfill_verouderde_tickers()`
(UPDATE `ticker` via `db_wijzig_ticker()`); `set_bijnaam()`/`reset_bijnaam()` (UPDATE `product` via `db_wijzig_bijnaam()`/`db_herstel_echte_naam()`); `db_wijzig_portfolio_code()` (UPDATE `code`); `db_delete_portfolio()`.
Lezen: `_haal_portfolio_basis()`, `_laad_split_gecorrigeerde_transacties()` (beide via `db_get_portfolio_naam_en_transacties()`), `db_get_order_id_sets()`, `_ticker_resolutie_opslaan_pad()` (via `db_get_bekende_tickers()`), `backfill_verouderde_tickers()` (via `db_get_transacties_voor_tickercheck()`),
`bereken_dividend_samenvatting()` (via `db_get_isin_ticker_product()`), `db_get_transacties_overzicht()`.

#### `dividenden`
| Kolom | Type | Betekenis |
|---|---|---|
| `id` | SERIAL, primary key | |
| `code` | TEXT, NOT NULL | portfolio-code (**geen** foreign key in het schema) |
| `dividend_id` | TEXT, NOT NULL | `DIV-` + hash van datum, ISIN en de ruwe bedragen |
| `datum`, `product`, `isin`, `valuta` | | |
| `bruto_eur`, `belasting_eur`, `netto_eur` | NUMERIC | in EUR; `NULL` als de valutaconversie niet te koppelen was |
| `herinvesteerd` | BOOLEAN, default FALSE | er was ook een "Dividend Herinvestering"-rij |
| | `UNIQUE (code, dividend_id)` | |

Schrijven: `db_save_dividenden()` (**upsert**), `db_wijzig_portfolio_code()`, `db_delete_portfolio()`. Lezen: `db_get_dividenden()` (via `bereken_dividend_samenvatting()`).

### 4.2 Marktdata en caches

| Tabel | Kolommen (behalve de sleutel) | Betekenis | Schrijft | Leest |
|---|---|---|---|---|
| `prijzen` — PK `(ticker, datum)` | `koers_eur`, `bijgewerkt_op` | dagkoersen in EUR; ook FX-paren (bv. `USDEUR=X`) omdat yfinance die als ticker behandelt | `db_save_prices()`, `db_upsert_prices()` (beide vanuit `get_prices()`) | `db_get_gecachte_prijzen()` (vanuit `get_prices()`), `db_get_laatste_prijs_update()` |
| `ticker_info` — PK `ticker` | `is_etf`, `land`, `sector`, `quote_type`, `valuta`, `yahoo_beurs`, `fund_family`, `category`, `bijgewerkt_op` | ETF/aandeel-classificatie + Yahoo-metadata | `db_save_classification()` | `db_get_cached_classifications()`, `db_get_ticker_details()` |
| `ticker_land_sector` — PK `ticker` | `land`, `sector`, `bijgewerkt_op` | land/sector van een los aandeel of holding-ticker | `db_save_land_sector()` | `db_get_cached_land_sector()` |
| `etf_sector_verdeling` — PK `(etf_ticker, sector)` | `gewicht`, `bijgewerkt_op` | sectorverdeling per ETF, gewicht als fractie 0–1 | `db_save_etf_sector_verdeling()` (delete + bulk insert) | `db_get_cached_etf_sector_verdeling()` |
| `etf_holdings` — PK `(etf_ticker, holding_naam)` | `holding_ticker`, `gewicht`, `land`, `bron`, `bijgewerkt_op` | holdings per ETF; `bron` is `'provider_csv'` of `'yfinance_top10'` | `db_save_etf_holdings()` (delete + bulk insert) | `db_get_cached_etf_holdings()` |
| `ticker_prijscheck` — PK `(ticker, datum)` | `yahoo_slotkoers`, `valuta`, `high`, `low`, `opgehaald_op` | historische Yahoo-slotkoers + dagrange voor de prijsvergelijking | `db_save_prijscheck()` (upsert) | `db_get_cached_prijscheck()` |
| `ticker_splits` — PK `ticker` | `splits` (JSONB), `bijgewerkt_op` | `{iso_datum: ratio}` | `db_save_splits()` | `db_get_cached_splits()` |
| `openfigi_cache` — PK `isin` | `resultaten` (JSONB), `opgehaald_op` | OpenFIGI-antwoord per ISIN | `db_save_openfigi()` | `db_get_cached_openfigi()` |

De functies die deze helpers aanroepen: zie de tabel "db.py" in hoofdstuk 3 (bv. `classify_ticker()`, `get_land_sector()`, `get_etf_holdings()`, `vergelijk_prijs_op_datum()`, `_haal_splits_op()`, `haal_openfigi_resultaten()`).

### 4.3 Cache-gedrag per tabel

| Cache | Vervalt na | Mislukte lookup | Bijzonderheid |
|---|---|---|---|
| `prijzen` | nooit als geheel; wel **incrementeel verversen** vanaf de laatste gecachte datum bij elke portfolio-opening, tenzij < 2 minuten geleden | niets opslaan (`download_met_retry()` geeft een lege `Series`) | een cache die te laat begint (> 5 dagen na `start_date`) telt als "missing" en wordt volledig opnieuw gedownload |
| `ticker_info` | **nooit** (`db_get_cached_classifications()` filtert niet op leeftijd) | niet cachen; die keer telt de ticker als "aandeel" | `_ticker_details_met_cache()` haalt een rij opnieuw op als `valuta` en `quote_type` beide NULL zijn |
| `ticker_land_sector` | 30 dagen | niet cachen | `land = NULL` (Yahoo heeft het niet) wordt wél gecachet |
| `etf_sector_verdeling` | 30 dagen | lege uitkomst niet cachen | |
| `etf_holdings` | 30 dagen | lege uitkomst niet cachen | een verse `yfinance_top10`-cache wordt overruled zodra er een provider-URL bekend is |
| `ticker_prijscheck` | **nooit** (historische koersen veranderen niet) | **wél** cachen (met `NULL`) | rij zonder high/low wordt bij een volgend gebruik aangevuld |
| `ticker_splits` | 30 dagen | fout: niet cachen; "geen splits" (lege dict): wel cachen | |
| `openfigi_cache` | **nooit** | fout/rate limit: niet cachen; "geen match": wél cachen (lege lijst) | |

Naast de database bestaan er drie **in-process** caches: `_basis_cache` (20 s, `portfolio_orchestratie.py`), de per-request FX-memo op Flask's `g` (`_fx_serie_cache`, `prijzen.py`) en de Yahoo-call-teller (`yahoo_client.py`).

### 4.4 Backfill-mechanismen

"Backfill" betekent hier: een waarde die in een bestaande rij nog `NULL` (of verouderd) is, alsnog invullen. Er zijn twee mechanismen:

1. **Ticker-backfill**: `backfill_verouderde_tickers()` (bij upload naar een bestaande code of bij "ophalen met code" met het vinkje) herbeoordeelt de opgeslagen tickers; zie hoofdstuk 3.
2. **"Self-healing" bij lezen** (geen apart commando): `_ticker_details_met_cache()` (stale `ticker_info`), `vergelijk_prijs_op_datum()` (mist high/low → aanvullen), `get_etf_holdings()` (upgrade van
   `yfinance_top10` naar `provider_csv`), `get_prices()` (cache begint te laat → opnieuw downloaden) en `db_save_dividenden()` als upsert (een herberekening overschrijft een oude `NULL`-rij; met `DO NOTHING` bleef een foute rij voor altijd staan).

**Geen data-backfill voor `transacties`:** een upload naar een bestaande code voegt alleen nieuwe Order ID's in (`ON CONFLICT (code, order_id) DO NOTHING`).
Staat `transactiekosten`, `waarde_eur` of `tijd` in een al opgeslagen rij op `NULL`, dan wordt die bij een latere upload **niet** meer aangevuld.
Herstel: het portfolio verwijderen (Instellingen) en het bestand opnieuw uploaden. Zolang `waarde_eur` `NULL` is, valt de GAK-berekening terug op `totaal_eur`.

**Let op bij tests:** een deel van de tests werkt met een **echte database** (zie hoofdstuk 7) en gebruikt eigen test-codes (zoals `TESTDIV`). Die tests draaien alleen tegen een lokale database (CI-container of Docker), nooit tegen Neon.

## 5. Frontend

### 5.1 De bestanden en hoe ze samenwerken

| Bestand | Rol |
|---|---|
| `templates/basis.html` | Het gedeelde skelet (Jinja-overerving): `<head>`, `<body data-code="...">`, de laad-overlay en de scripts die beide pagina's nodig hebben. De andere twee templates vullen de blokken `head_scripts`, `inhoud` en `scripts` in. |
| `templates/start.html` | De **startpagina** (`/`): het upload-formulier (`#uploadForm`) en het code-formulier (`#codeForm`), `#startMelding` en `#errorMsg`. Laadt geen Chart.js. |
| `templates/portfolio.html` | De **portfolio-pagina** (`/p/<code>` en `/analyse`): `#laadFout` en `#dashboardSection` (zijmenu + `.content`). Elk tabblad heeft een eigen verborgen blok `<div id="tab-<view>" data-views="<view>">` met de vaste opmaak erin (koppen, uitleg, formuliervelden, lege containers); de scripts in `static/js/tabs/` vullen alleen in. Eén gedeelde `<canvas id="rendementChart">` in `#chartWrapper` dient voor **alle** grafiek-tabbladen en verhuist naar het `data-grafiek-plek` van het actieve tabblad. |
| `static/js/app.js` | Opstarten, gedeelde toestand en navigatie van de portfolio-pagina (~300 regels): `huidigeData`, `chart`, `verrijkingStatus`; `startPortfolioPagina()`, `haalPortfolioOp()`, `toonDashboard()` met `RESET_PER_TAB`; `wisselView()`, `pasViewToe()`, `plaatsGrafiek()`, `gaNaarView()`, `TOON_PER_VIEW`, `VIEWS_MET_CODE`; `laadVerrijking()` en `toonVerrijkingWachtstatusIndienNodig()`; de hoofdtabs/subtabs (`ververMenu()`) en de ticker-waarschuwingsbanner. Laadt als laatste script. |
| `static/js/gedeeld/` | Hulpfuncties met DOM of Chart.js die meerdere tabbladen gebruiken (alleen portfolio-pagina, geen tests): `opmaak.js` (`formatDatum()`, `formatteerEuro()`, `formatPct()`, `klasseVoorRendement()`, `kortNaam()`, `toonAlleen()`), `grafiek.js` (kleurenpalet, `kleurVoorIndex()`, `kleurVoorTicker()`, `maakStrepenPatroon()`, `updateChart()`, `renderGestapeldeStaafgrafiek()`), `tabel.js` (`maakSorteerbareTabel()`, `maakCel()`, `maakRendementCel()`) en `tegels.js` (`maakStatTegel()`, `maakTotalenSectie()`). Niet te verwarren met `gedeeld.js`: dat delen de start- en de portfolio-pagina. |
| `static/js/tabs/` | Eén bestand per tabblad met de DOM-code van dat tabblad: de `toon...()`-functie, de eigen toestand (met een `reset...()`-functie als die per portfolio geldt, zie 5.2), de `fetch()`-aanroepen en de event-listeners. Welk bestand bij welk tabblad hoort staat in 5.4. `land_sector.js` bedient drie tabbladen (Land, Sector en Valuta delen de weergavekeuze). Staat er een bestand met dezelfde naam direct in `static/js/` (`prognose.js`, `transacties.js`, `bedrijven.js`, `dividend.js`, `diagnostiek.js`), dan is dat de pure, geteste rekenkern en het bestand in `tabs/` de DOM-kant. |
| `static/js/start.js` | De startpagina: submit-handlers van `#uploadForm` en `#codeForm`, `gaNaarPortfolioPagina()`, `toonStartMelding()`, `koppelBestandWisKnop()`. |
| `static/js/gedeeld.js` | DOM-helpers voor beide pagina's: `fetchMetTimeout()`, `toonLaadOverlay()`/`verbergLaadOverlay()`, `sessieOpslag()`. |
| `static/js/navigatie.js` | Pure logica voor paden en URL's: `portfolioPad()`, `startPadMetMelding()`, `viewUitHash()`, `viewInLijst()`, `elementZichtbaar()` (zichtbaarheid uit `data-views` en `data-vereist-code`), `maakTabWisselaar()` (de fade tussen tabbladen, zie 5.3), `startMeldingTekst()` en de constanten (`START_PAD`, `ANALYSE_PAD`, `STANDAARD_VIEW`, de meldingsleutels). |
| `static/js/overdracht.js` | Pure logica voor de eenmalige overdracht start → portfolio-pagina: `bewaarOverdracht(opslag, data)`, `haalOverdracht(opslag, code)` (wist na lezen). De opslag komt als parameter mee, zodat de test een nep-object kan gebruiken. |
| `static/js/prognose.js` | Rekenkern van het Prognose-tabblad: `berekenPrognose()` (gebruikt `berekenPrognosePad()`, `berekenGeinvesteerdPad()` en `maandRenteVanJaarPct()`), `valideerPrognoseInvoer()`, `genereerToekomstDatums()` en `bouwPrognoseGrafiekData()`. Puur JS, geen DOM. Maandrente = `(1 + jaarrendement)^(1/12) − 1`; inleg komt na de groei van die maand erbij. |
| `static/js/menu.js` | `MENU_GROEPEN` (hoofdtabs, subtabs, tandwiel) en de pure functies `groepVanView()`, `eersteView()`, `zichtbareGroepen()`, `zichtbareViews()`. |
| `static/js/transacties.js` | Sorteren en pagineren voor het Transacties-tabblad: `sorteerTransacties()`, `totaalPaginas()`, `pagineer()`. De tabelbouwer `maakSorteerbareTabel()` gebruikt de laatste twee voor elke tabel met paginering. |
| `static/js/dividend.js` | Pure logica voor de dividendgrafiek: `bouwDividendDatasets()` (één dataset per ticker; bij één datum zichtbare punten, `DIVIDEND_ENKEL_PUNT_RADIUS`, omdat één punt geen lijn geeft). |
| `static/js/bedrijven.js` | Pure logica voor het Top-N-bedrijven-tabblad: `maakBedrijfsnaamLeesbaar()` en `maakUniekeWeergaveNamen()` (nettere namen, **alleen voor weergave**; de ruwe naam blijft de sleutel), `breekLabelAf()`, `effectieveTopN()`, `kiesTopN()`, `snijTopBedrijven()` (lijst inkorten tot N en het restant herberekenen), `gebruikHorizontaleStaven()`, `bedrijvenTitel()` en de constante `BEDRIJVEN_TOP_N_KNOPPEN`. |
| `static/js/diagnostiek.js` | Pure logica voor Instellingen → Diagnostiek: `voegMeldingenSamen()` (nieuwste wint per categorie + sleutel), `telPerNiveau()`, `groepeerPerCategorie()`, `diagnostiekTellerTekst()`, `hoogsteNiveau()`, `categorieStandaardOpen()`. Het tekenen zelf gebeurt in `toonDiagnostiek()` in `tabs/diagnostiek.js`. |
| `static/js/bestandskeuze.js` | Eén pure functie `bestandSelectieWeergave()`: welke tekst de rij "gekozen bestand + x-knop" onder een bestandsveld toont. De DOM-kant is `koppelBestandWisKnop()` in `start.js`. |
| `static/js/infotip.js` | Bouwt van `<span class="infoTip">` een (i)-knop met tooltip (`initInfoTips()`, start vanzelf bij `DOMContentLoaded`). Raakt de DOM, heeft geen exports en (nog) geen test. |
| `static/css/style.css` | Opmaak, één bestand (~1220 regels) met bovenaan een inhoudsopgave en zes secties: 1 basis (reset, elementen, `[hidden]`), 2 layout en menu, 3 gedeelde componenten, 4 per tabblad, 5 hulpklassen die moeten winnen (kleuren `.positief`/`.negatief`/`.mild`/`.gedempt`/`.vet` en marges `.margeBoven10`, `.margeOnder15`, ...), 6 mobiel. Sectie 5 staat bewust na 3 en 4: bij gelijke specificiteit wint de latere regel. Onder `@media (max-width: 768px)` (en liggend tot 900 px) worden de hoofdtabs een vaste onderbalk. `.badge` (+ kleurvarianten `.badgeHerinvesteerd`, `.badgeDeelsVerkocht`, `.badgeGesloten`) is het kleine label in een tabelcel. Wat de JS bouwt krijgt zijn opmaak uit klassen, niet uit inline stijlen: `.dataTabel`/`.compacteTabel`/`.kleineTabel`/`.overlapMatrix` (tabellen), `.tegelRij`/`.statTegel`/`.statWaarde` (tegels), `.tickerKaart` en verwanten (Ticker-zekerheid), `.paginaNavigatie`. Ook `portfolio.html` heeft geen inline stijlen meer, op `style="display: none;"` na (elementen die JS of de zichtbaarheidslogica aan- en uitzet). In JS blijft inline alleen wat uit data of de zichtbaarheidslogica komt: `display`, de hoogte van de Top-bedrijven-grafiek en de celkleur in de overlap-matrix. Zet JS een klasse met `className =` op een element uit de HTML, neem dan de vaste klasse van dat element mee (zoals `"melding foutTekst"` bij `#instellingenMsg`). Er is geen dark mode. |

**Laadvolgorde:** beide pagina's laden uit `basis.html` eerst `navigatie.js`, `overdracht.js`, `infotip.js` en `gedeeld.js`. De startpagina laadt daarna `bestandskeuze.js` en `start.js`.
De portfolio-pagina laadt in de `<head>` de externe bibliotheken van cdnjs (Chart.js 4.4.0, hammer.js 2.0.8, chartjs-plugin-zoom 2.0.1, chartjs-plugin-datalabels 2.2.0,
chartjs-plugin-annotation 3.0.1, luxon 3.7.2, chartjs-adapter-luxon 1.3.1) en na de gedeelde scripts, in deze volgorde:

1. de pure modules `prognose.js`, `menu.js`, `diagnostiek.js`, `bedrijven.js`, `transacties.js`, `dividend.js`;
2. `gedeeld/opmaak.js`, `gedeeld/grafiek.js`, `gedeeld/tabel.js`, `gedeeld/tegels.js`;
3. de tabbladen in menuvolgorde: `tabs/portfolio.js`, `rendement.js`, `per_aandeel.js`, `per_aandeel_aankoop.js`, `verdeling.js`, `land_sector.js`, `bedrijven.js`, `etf_overlap.js`, `statistieken.js`, `transacties.js`, `prognose.js`, `dividend.js`, `instellingen.js`, `bijnamen.js`, `ticker_zekerheid.js`, `diagnostiek.js`;
4. als laatste `app.js`.

Ze zijn gewone `<script>`-tags, geen ES-modules. `tests/test_scripts.js` bewaakt dat elk geladen script en elk gelinkt CSS-bestand bestaat, dat er geen script dubbel wordt geladen, dat elk bestand in `gedeeld/` en `tabs/` ook echt geladen wordt, dat `app.js` als laatste komt en dat `RESET_PER_TAB` klopt (zie 5.2).

**Waarom losse bestanden zonder `import` werken.** Alle scripts delen één globale scope: een `function`, `let` of `const` op het hoogste niveau van het ene bestand is in elk ander bestand te gebruiken. De volgorde doet er alleen toe voor code die *tijdens het laden* draait. Bijna alles staat in functies, en die draaien pas na een klik of een `fetch()`, als alle scripts al geladen zijn; daarom mag een tabblad `huidigeData` of `toonVerrijkingWachtstatusIndienNodig()` uit `app.js` gebruiken, ook al laadt `app.js` later. Wat wél tijdens het laden draait: de event-listeners op het hoogste niveau van elk bestand (het element moet dan in de HTML staan, anders crasht het script: `test_pagina_routes.py` bewaakt dat), `Chart.register()` in `gedeeld/grafiek.js`, en in `app.js` het object `TOON_PER_VIEW`, dat direct naar de `toon...()`-functies verwijst. Daarom laadt `app.js` na de tabbladen, en eindigt het met `startPortfolioPagina()`. Keerzijde van één globale scope: twee bestanden mogen niet dezelfde naam op het hoogste niveau declareren (dat geeft een `SyntaxError` bij het laden).

**Browsercache.** De script- en CSS-adressen hebben geen versie-parameter. Flask stuurt statische bestanden met `Cache-Control: no-cache` en een `ETag` (`SEND_FILE_MAX_AGE_DEFAULT` is niet ingesteld): de browser mag ze bewaren, maar vraagt bij elke paginalading per bestand na of het veranderd is. Na een deploy krijg je dus vanzelf de nieuwe scripts; een harde refresh (Ctrl+F5) is alleen nodig als er iets tussen zit dat die header negeert.

**Het "pure module"-patroon.** `prognose.js`, `menu.js`, `transacties.js`, `bedrijven.js`, `dividend.js`, `diagnostiek.js`, `bestandskeuze.js`, `navigatie.js` en `overdracht.js` zijn een IIFE `(function (root) { ... })(window of globalThis)` die aan het eind ofwel
`module.exports` zet (onder Node, voor de tests) ofwel `Object.assign(root, exportsObj)` (in de browser). Daardoor worden hun functies gewone **globale functies** die `app.js`, `start.js` en de scripts in `gedeeld/` en `tabs/`
zonder `import` kunnen aanroepen — en tegelijk zijn ze met `node --test` te testen zonder browser. Zo'n bestand raakt bewust geen DOM aan.

**Rekenen in Python, tonen in JS.** Financiële en inhoudelijke berekeningen (sommen, percentages, rendement, aggregaties, top-N + Overig) horen in de backend en
worden met Python-unittests getest; de frontend doet formatteren, sorteren, kleuren en grafiekconfiguratie. Zo komen o.a. de ETF/aandeel-verhouding
(`verdeling_samenvatting`), `nog_in_bezit` op `per_ticker_aankoop`, de `holdings` van `/ticker-koers-bereik` en de top-10 + Overig van de Land-staaf
(`land_per_bron_top`) kant-en-klaar uit de backend. Drie bewuste uitzonderingen: `prognose.js` (puur, getest, werkt op gebruikersinvoer, geen
netwerkaanroep nodig) en `snijTopBedrijven()` in `bedrijven.js`, die `overigPct` herberekent zodat de top-N zonder request te kiezen is (afwijking t.o.v. de
backend hooguit ±0,01% door afronding). En de derde: `renderGestapeldeStaafgrafiek()` (`gedeeld/grafiek.js`) voegt bronnen onder `BRON_OVERIG_DREMPEL` (0,5%)
samen tot "Overige bronnen" — dat ruimt alleen de legenda op en verandert de data niet (de staafhoogtes blijven gelijk). Daarnaast telt die functie per
staaf de totalen op voor het %-label (een lichte afgeleide van al geleverde data).

**Waarom één `<canvas>`?** Elke `toon...()`-functie roept eerst `chart.destroy()` aan en maakt daarna een nieuwe `new Chart(...)` op dezelfde canvas. De variabele `chart` (globaal, in `app.js`) houdt de huidige grafiek vast.
`plaatsGrafiek(view)` verhuist `#chartWrapper` (zoomknop + canvas) met `appendChild` naar de `<div data-grafiek-plek>` in het blok van het tabblad: een element kan maar op één plek staan, dus dit is verplaatsen, geen kopie. Een tabblad zonder plek (Statistieken, Transacties, ETF-overlap, Instellingen) toont geen grafiek. Staat op de plek ook `data-zoombaar`, dan is de zoomknop zichtbaar. Een `chart.resize()` is niet nodig: elke `toon...()`-functie maakt de grafiek opnieuw aan, ná het verhuizen.
Uitzondering: Top-bedrijven heeft een eigen canvas (`#bedrijvenChart` in `#bedrijvenChartWrapper`, variabele `bedrijvenChart`), omdat de hoogte daar meegroeit met het aantal bedrijven en vegen de pagina moet scrollen (`touch-action: pan-y`). Dat tabblad heeft dus geen `data-grafiek-plek`.

### 5.2 Hoe de data na de upload in de frontend bewaard wordt

- Het JSON-antwoord komt in de globale variabele **`huidigeData`** (in het geheugen). Later binnenkomende delen worden erbij gevoegd met `Object.assign(huidigeData, data)`: de verrijking (`laadVerrijking()`), en de antwoorden van
  bijnaam/reset/wijzig-code — zo verdwijnen de al opgehaalde verrijkingsvelden niet.
- `sessionStorage` wordt alleen gebruikt voor de eenmalige overdracht van de startpagina naar de portfolio-pagina (zie 2.6), en die wordt na het lezen gewist. Pagina verversen op `/p/<code>` haalt de portfolio dus opnieuw op bij de server (zie 2.8); op `/analyse` ga je terug naar de startpagina.
- Overige gedeelde toestand in `app.js`: `chart`, `verrijkingStatus` (`null`/`"laden"`/`"fout"`/`"klaar"`) en `actieveView`.
- Toestand van één tabblad staat bovenaan het bestand van dat tabblad in `tabs/`: `prognoseInvoer` en `prognoseResultaat` (`prognose.js`), `benchmarkVergelijkingData` en `eigenAandeelVergelijkingData` (`rendement.js`),
  `transactiesRuweLijst` en `transactiesStaat` (sorteerkolom, richting, pagina, rijen per pagina; `transacties.js`), `landSectorWeergave` (`"taart"`/`"staaf"`, `land_sector.js`), `meerHistorieUitgeput` (`per_aandeel_aankoop.js`), `bedrijvenTopN` en `bedrijvenChart` (`bedrijven.js`), en
  `diagnostiekMeldingen` + `diagnostiekOpenKeuze` (de meldingen van de laatste laadbeurt en welke categorieën je zelf open/dicht hebt gezet; `diagnostiek.js`).
- **Resetten bij een nieuwe portfolio.** Elk tab-bestand met toestand die bij één portfolio hoort, heeft een eigen `reset...()` zonder parameters: `resetDiagnostiek()`, `resetPrognose()` (alleen het resultaat, de invoer blijft), `resetRendement()` (ook de twee keuzelijsten), `resetPerAandeelAankoop()` en `resetTransacties()` (rijen per pagina blijft). `toonDashboard()` roept ze allemaal aan via de lijst `RESET_PER_TAB` in `app.js`. Bewust zonder reset: `land_sector.js` (taart/staaf-keuze) en `bedrijven.js` (gekozen top-N); dat zijn weergavekeuzes die over portfolio's heen blijven staan. `tests/test_scripts.js` controleert dat elke functie in `RESET_PER_TAB` bestaat en dat elk ander tab-bestand met een `let` op het hoogste niveau een reset in de lijst heeft (op die twee uitzonderingen na, die met reden in de test staan).

### 5.3 Navigatie en menu

1. De navigatie heeft 4 hoofdtabs (Overzicht, Rendement, Samenstelling, Posities) met per hoofdtab een rij subtabs, plus een tandwiel in de header voor Instellingen. De indeling staat op één plek: `MENU_GROEPEN` in `menu.js`. `app.js` bouwt de hoofdtabs daaruit (`#hoofdTabs`) en bouwt bij elke `pasViewToe()` de subtabs (`#subTabs`) van de actieve groep opnieuw (`ververMenu()`). Een klik op een hoofdtab opent `eersteView()` van die groep; een hoofdtab zonder toegestane subtab (`zichtbareGroepen()`) is verborgen. Navigeren gaat nog steeds per view.
2. Het actieve tabblad staat in de **URL-hash** (`/p/ABC#rendement`). Klik → `gaNaarView(view)` zet alleen `location.hash`; de `hashchange`-listener leest het tabblad met `viewUitUrl()` (→ `viewUitHash()` in `navigatie.js`) en roept `wisselView(view)` aan → korte fade (class `tabWisselt`, `TAB_FADE_MS` = 90 ms) → **`pasViewToe(view)`**. `wisselView` wordt gemaakt door `maakTabWisselaar()` (`navigatie.js`), die de timer van de fade zelf bijhoudt: komt er een nieuwe wissel binnen terwijl de vorige nog wacht, dan wordt die timer geannuleerd en de nieuwe view direct toegepast. Zonder dat kon bij snel doorklikken een oude timer later alsnog een eerder tabblad tonen dan de hash zei. De klok (`setTimeout`/`clearTimeout`) is een parameter, zodat `test_navigatie.js` hem met een nepklok test. Daardoor werken refresh, bookmarks en de terug-/vooruit-knop van de browser per tabblad. Een lege of onbekende hash geeft `STANDAARD_VIEW` (`portfolio`). Klik je op het tabblad dat al open staat, dan verandert de hash niet en roept `gaNaarView()` `wisselView()` rechtstreeks aan.
3. `pasViewToe()` doet twee dingen:
   (a) **Zichtbaarheid.** Wat bij welk tabblad hoort, staat in de HTML zelf, met attributen (meerdere views gescheiden door spaties):
   - `data-views="dividend"`: zichtbaar op precies deze tabbladen, verborgen op alle andere. Elk tabblad-blok `tab-<view>` heeft dit, en ook de paar elementen die meerdere tabbladen delen en daarom buiten de blokken staan (`aandeelSelect`, `weergaveToggleBtn`).
   - `data-vereist-code="ja"` / `"nee"`: alleen zichtbaar mét een opgeslagen code (`codeText`, de twee benchmark-keuzelijsten, de €/%-schakelaar van Rendement) of juist zonder (`nietOpgeslagenText`, de "alleen voor een opgeslagen portfolio"-teksten). De twee intro-teksten van Ticker-zekerheid gebruiken beide waarden: één met code, één zonder. Mag naast `data-views` staan; beide voorwaarden moeten dan kloppen.
   - `data-verberg-buiten="land"`: buiten deze tabbladen verborgen; óp het tabblad beslist de `toon...()`-functie zelf of het verschijnt (meldingen, `geenData`, `verrijkingLaadt`/`verrijkingFout`, de Europa-checkbox, ...).
   - `data-grafiek-plek` (eventueel met `data-zoombaar`): hier komt de gedeelde grafiek te staan, zie 5.1.
   De regel voor de eerste twee is de pure functie `elementZichtbaar()` in `navigatie.js`; `pasViewToe()` heeft geen losse regels per element meer. Toestanden binnen een tabblad (laden, fout, leeg, inhoud) zijn vaste elementen met het `hidden`-attribuut; `toonAlleen(ids, zichtbaarId)` laat er één van zien.
   (b) **Tekenen.** Het object `TOON_PER_VIEW` koppelt elke view aan zijn `toon...()`-functie (zie de tabel hieronder); `TOON_PER_VIEW[view]?.()` roept hem aan (`?.` = alleen als er een functie is; `instellingen` heeft er geen).
4a. Website (breed scherm): de pagina vult de schermbreedte. Home, Dividend, Rendement, Prognose, Top-bedrijven en de twee Posities-tabbladen hebben `<div class="tabSplit">` met links `.tabInfo` (informatie, instellingen) en rechts `.tabGrafiek` (de gedeelde grafiek via `data-grafiek-plek`); de grid-regels staan in een desktop-`@media` in `style.css`, op mobiel blijft het blokken onder elkaar. Bij Posities vervangen de knoppen in `[data-aandeel-knoppen]` (`bouwAandeelKnoppen()` in `tabs/per_aandeel.js`) de dropdown `#aandeelSelect` op de website; de select blijft de bron van de keuze, een klik zet hem en stuurt een `change`-event. De dropdown is op de website met CSS verborgen, de knoppen op mobiel.
4. Mobiel (zelfde breakpoint als voorheen): de hoofdtabs zijn een vaste onderbalk (`env(safe-area-inset-bottom)`), de subtabs een horizontaal scrollbare rij bovenaan de content. Een `matchMedia("(max-width: 768px)")`-listener (in `tabs/bedrijven.js`) hertekent het Top-N-bedrijven-tabblad (staand/liggend, zie `tekenBedrijven()`) bij het kantelen van het scherm of een venster-formaatwijziging over dat breakpoint heen, als dat tabblad open staat.
5. Bij een "niet opslaan"-analyse (`data.code` is leeg) verbergt `ververMenu()` de subtabs Algemeen en Bijnamen (`VIEWS_MET_CODE`); Dividend en Transacties lezen `huidigeData.dividend` en `huidigeData.transacties_lijst`. Een hash naar zo'n tabblad valt dan terug op `portfolio`.
6. "Nieuwe upload" staat links in de header en is een gewone link naar `/`.
7. Instellingen: na "Code wijzigen" past `history.replaceState()` de URL aan naar `/p/<nieuwe code>` (zonder herladen, de hash blijft staan); na verwijderen gaat de pagina met `location.replace()` naar `/?melding=verwijderd`, zodat de terug-knop niet op de verwijderde portfolio uitkomt.

### 5.4 Tabel per tabblad

| Menu (`data-view`) | Bestand in `tabs/` | Tekent | Data/API | Grafiek of tabel |
|---|---|---|---|---|
| Portfolio-home (`portfolio`) | `portfolio.js` | `toonPortfolio()` | `huidigeData.chart_data` en `statistieken.totalen`; komt uit `/upload` of `GET /api/portfolio/<code>` | lijngrafiek Waarde + Geïnvesteerd via `updateChart()`; tegels `maakTotalenSectie()`; regel "Koersen laatst opgehaald ..." |
| Rendement (`rendement`) | `rendement.js` | `toonRendement()`; `wisselBenchmark()`, `wisselEigenAandeel()` | `chart_data.rendement`; optioneel `GET .../benchmark-vergelijking?benchmark=` of `?eigen_ticker=` | lijngrafiek (`updateChart()`), met een gestippelde extra lijn per gekozen vergelijking |
| Per aandeel (`peraandeel`) | `per_aandeel.js` | `toonPerAandeel(ticker)`, `toonEtfDrilldown()` | `per_ticker[ticker]`, `land_sector_verdeling.per_etf` | lijngrafiek Waarde/Geïnvesteerd + bij een ETF twee lijstjes land/sector |
| Per aandeel aankoop (`peraandeelaankoop`) | `per_aandeel_aankoop.js` | `toonPerAandeelAankoop(ticker)`, `laadMeerHistorie()` | `per_ticker_aankoop[ticker]`; knoppen "+6 maanden/+1 jaar/+3 jaar/Tot nu" → `GET .../ticker-koers-bereik` | **eigen** `new Chart` (niet `updateChart()`): koers + trapvormige lijn "aantal aandelen" op een tweede y-as, aankoop-/verkoopmomenten als verticale annotatielijnen (annotation-plugin) |
| Verdeling (`verdeling`) | `verdeling.js` | `toonVerdeling()` | `huidigeData.verdeling` en `verdeling_samenvatting` (verrijking) | cirkeldiagram; ETF-vlakken met diagonaal streeppatroon (`maakStrepenPatroon()`), labels via de datalabels-plugin |
| Land (`land`) | `land_sector.js` | `toonLand()` | `land_sector_verdeling` (`land`, `land_europa` voor de taart; `land_per_bron_top`, `land_per_bron_europa_top` voor de staaf) | cirkel (`toonPlatteVerdeling()`) of gestapelde staaf per bron (`renderGestapeldeStaafgrafiek()`), wisselbaar met `weergaveToggleBtn`; vinkje "Europese landen samenvoegen" (`#europaCheckbox`) |
| Sector (`sector`) | `land_sector.js` | `toonSector()` | `land_sector_verdeling.sector` / `sector_per_bron` | idem |
| Valuta (`valuta`) | `land_sector.js` | `toonValuta()` | `valuta_verdeling.valuta` / `valuta_per_bron` (verrijking) | idem (zelfde taart/staaf-keuze als Land en Sector) |
| Top N bedrijven (`bedrijven`) | `bedrijven.js` | `toonBedrijven()` (en `tekenBedrijven()`) | `bedrijven_verdeling` (verrijking) | gestapelde staaf (`renderGestapeldeStaafgrafiek()`), met keuzeknoppen 10/20/50 en een invulveld |
| ETF-overlap (`etfoverlap`) | `etf_overlap.js` | `renderEtfOverlapTabel()`; klik op een vakje → `toonEtfOverlapDetail()` | `etf_overlap`; detail via `GET /api/etf-overlap-detail?a=&b=` | **HTML-tabel**, geen Chart.js: matrix met achtergrondintensiteit; detailtabel `maakEtfOverlapDetailTabel()` |
| Statistieken (`statistieken`) | `statistieken.js` | `toonStatistieken()` | `huidigeData.statistieken` | tabellen (`maakPositieTabel()`, `maakGeslotenPositiesTabel()`, `maakJarenTabel()`, `maakGeavanceerdSectie()`) via `maakSorteerbareTabel()`; geen grafiek |
| Transacties (`transacties`) | `transacties.js` | `toonTransacties()`, `renderTransactiesTabel()` | `GET .../transacties` (één keer, dan onthouden in `transactiesRuweLijst`); bij "niet opslaan" `huidigeData.transacties_lijst` | `maakSorteerbareTabel()` met `TRANSACTIES_KOLOMMEN`, de toestand `transactiesStaat` en `sorteerTransacties()` als sortering: sorteren over de **volledige** lijst en paginering (25 of 50 per pagina) |
| Rendement, %-weergave | `rendement.js` | `toonRendementOverTijd()` (via de €/%-schakelaar in `toonRendement()`) | `GET .../rendement-over-tijd` (bij elke keer opnieuw) | lijngrafiek met drie lijnen Rendement%, XIRR%, TWR% (tooltip via `formatPct`); alleen met code |
| Prognose (`prognose`) | `prognose.js` | `toonPrognose()`, `berekenEnToonPrognose()`, `tekenPrognoseChart()` | **geen API**: `huidigeData.chart_data` + `prognose.js` | lijngrafiek met een echte **tijd-as** (luxon-adapter), gestippelde prognose- en bandbreedtelijnen |
| Dividend (`dividend`) | `dividend.js` | `toonDividend()` | `GET .../dividend` | gestapelde cumulatieve lijngrafiek (`toonDividendChart()`), totalentabel (`maakDividendTotalenTabel()`) en de volledige uitkeringenlijst (`maakDividendUitkeringenTabel()`); rijen met `herinvesteerd: true` krijgen in de kolom "Aandeel" een groen label "herinvesteerd" met tooltip (CSS `.badge` + `.badgeHerinvesteerd`) |
| Instellingen (`instellingen`) | `instellingen.js` | *(geen toon-functie; statisch blok `#tab-instellingen`, het bestand bevat alleen de listeners van de twee knoppen)* | `DELETE /api/portfolio/<code>`; `POST .../wijzig-code` | twee formulier-achtige knoppen: data verwijderen, code wijzigen |
| Bijnamen (`instellingen-bijnamen`) | `bijnamen.js` | `toonInstellingen()`; `slaBijnaamOp()`, `resetBijnaam()` | `huidigeData.tickers`; `POST .../bijnaam` en `.../reset-bijnaam` | invoerrij per ticker |
| Ticker-zekerheid (`instellingen-ticker`) | `ticker_zekerheid.js` | `toonInstellingenTicker()` (opgeslagen) of `toonInstellingenTickerBasis()` (niet opslaan) | `GET .../ticker-zekerheid/lijst`, dan per positie `GET .../ticker-zekerheid/positie` (maximaal 4 tegelijk, `voerMetConcurrencyLimietUit()`, `TICKER_POSITIE_TIMEOUT_MS` = 30 s per aanroep); bij "niet opslaan" `huidigeData.ticker_zekerheid` en `POST /api/ticker-zekerheid-check` | kaarten per positie (`maakTickerZekerheidKaart()`, `maakPrijscontroleTabel()`, `maakAlternatievenTabel()`, ...) |
| Diagnostiek (`instellingen-diagnostiek`) | `diagnostiek.js` | `toonDiagnostiek()` | **geen eigen API**: de `diagnostiek`-sleutel uit de antwoorden van upload, ophalen en `/verrijking`, verzameld in `diagnostiekMeldingen` | teller + één inklapbaar `<details>`-blok per categorie (zie `diagnostiek.py` in hoofdstuk 3) |

**Eén tabelbouwer.** Alle tabellen met kolommen komen uit `maakSorteerbareTabel(kolommen, rijen, opts)` in `gedeeld/tabel.js`. Een kolom is `{label, renderTd}`, plus `waarde` (een getal per rij) als je erop wilt kunnen sorteren. Zonder opties begint de tabel in de aangeleverde volgorde en onthoudt hij de sortering alleen zolang hij op het scherm staat (Statistieken, de dividend-uitkeringen, het overlap-detail). Opties: `legeTekst`; `klasse` (andere CSS-klasse dan `dataTabel`, zoals `compacteTabel` voor de dividend-totalen); `staat` (een object van de aanroeper met `sorteerKolom`, `sorteerRichting`, `pagina`, `paginaGrootte`: de keuze overleeft dan een tabwissel, en met `paginaGrootte` komt er paginanavigatie onder); `sorteer` (een eigen sorteerfunctie op de `sleutel` van de kolom, voor tekst of datum+tijd). Alleen Transacties gebruikt `staat` en `sorteer`. Buiten de bouwer vallen de twee kleine tabellen op Ticker-zekerheid (`maakPrijscontroleTabel()`, `maakAlternatievenTabel()`: opmaak per rij en een voetnoot) en de overlap-matrix (geen kolommentabel).

Bij Verdeling/Land/Sector/Bedrijven/ETF-overlap begint elke `toon...()` met `toonVerrijkingWachtstatusIndienNodig()`: staat `verrijkingStatus` op `"laden"` of `"fout"`, dan wordt "Bezig met laden..." resp. een foutmelding met "Opnieuw proberen"-knop
(`#verrijkingOpnieuwBtn` → `laadVerrijking()`) getoond en stopt de functie.

### 5.5 Foutafhandeling en laadgedrag

- `toonLaadOverlay(tekst)` / `verbergLaadOverlay()`: een volledig scherm-overlay bij acties die merkbaar duren (upload, code ophalen, bijnaam opslaan, benchmark ophalen, verwijderen). De overlay staat als vast element `#laadOverlay` in `templates/basis.html` en de functies in `gedeeld.js`; ze zetten alleen de tekst en het `hidden`-attribuut (`.laadOverlay[hidden]` in `style.css` is nodig omdat `display: flex` anders wint). Niet gebruikt bij de Prognose (puur client-side). Op de startpagina blijft de overlay staan terwijl de browser naar de portfolio-pagina navigeert; de `pageshow`-listener in `start.js` verbergt hem weer als je met de terug-knop terugkomt (de browser zet de pagina dan terug zoals hij was).
- `fetchMetTimeout(url, opties, timeoutMs = 55000)` breekt zelf af en gooit `Error("TIMEOUT")`; de upload gebruikt `UPLOAD_TIMEOUT_MS` (60 s, `start.js`). Ticker-zekerheid gebruikt een eigen `AbortController`: `TICKER_POSITIE_TIMEOUT_MS` (30 s) per positie en `TICKER_UITGEBREID_TIMEOUT_MS` (60 s) voor de uitgebreide check bij "niet opslaan".
- De banner `#tickerWaarschuwingBanner` (`toonTickerWaarschuwingBanner()`) toont `ticker_waarschuwingen` bij elk tabblad, met een knop die naar Ticker-zekerheid springt.

## 6. Externe bronnen

### 6.1 Overzicht per bron

| Bron | Waarvoor | Waar in de code | Retry / rate limit | Cache |
|---|---|---|---|---|
| **Yahoo — `yf.download`** (yfinance) | historische dagkoersen van alle tickers en FX-paren | `get_prices()` via `download_met_retry()` in `yahoo_client.py` | 3 pogingen, **vaste** 5 s wachttijd, op elke fout; daarna een lege `Series` | tabel `prijzen` |
| **Yahoo — `yf.download`** (slotkoers, high, low) | prijsvergelijking voor de ticker-zekerheid | `_haal_koers_en_dagrange_op()`, `_haal_dagrange_op()` in `ticker_prijscheck.py` | `_met_rate_limit_retry()`: 3 pogingen, 8 s en 16 s wachten, alleen bij rate-limit-achtige fouten | tabel `ticker_prijscheck` (permanent) |
| **Yahoo — `yf.Ticker(t).info`** | ETF-of-aandeel, land, sector, valuta, beurs, fondsfamilie, categorie | `_fetch_yf_info()` in `ticker_classificatie.py` | `_met_rate_limit_retry()` (zoals hierboven) | `ticker_info`, `ticker_land_sector` |
| **Yahoo — `yf.Ticker(t).info`** (alleen `currency`) | bepalen of een koers omgerekend moet worden | `_haal_valuta_op()` (via `_converteer_naar_eur()`) in `prijzen.py` | **geen retry, geen cache**; bij een fout wordt "EUR" aangenomen, met een `[koersen] WARN`-print | — |
| **Yahoo — `funds_data`** (`sector_weightings`, `top_holdings`, `fund_overview`) | sectorverdeling en top-10 van ETF's; categorie als fallback | `get_etf_sector_verdeling()`, `get_etf_holdings()`, `_classify_ticker_uncached()` | **geen retry**: een fout geeft een lege uitkomst (die niet gecachet wordt) | `etf_sector_verdeling`, `etf_holdings` (30 dagen) |
| **Yahoo — `yf.Ticker(t).splits`** | splitsgeschiedenis voor de prijscontrole | `_haal_splits_op()` in `ticker_prijscheck.py` | **geen retry**; bij een fout `{}` en niet cachen | `ticker_splits` (30 dagen) |
| **yahooquery — `search`** | ticker zoeken op productnaam, ISIN of OpenFIGI-root | `_yahoo_search()` in `ticker_matching.py` | **geen retry**; fouten geven `[]` | **geen** (bewust niet: elke upload zoekt live, tenzij `bekende_ticker` de zoekopdracht overslaat) |
| **OpenFIGI** (`POST https://api.openfigi.com/v3/mapping`) | alle bekende noteringen per ISIN, als extra validatiesignaal | `haal_openfigi_resultaten()` in `ticker_matching.py`; timeout 10 s; optionele header `X-OPENFIGI-APIKEY` uit `OPENFIGI_API_KEY` | HTTP 429 en andere fouten geven een foutmelding zonder te cachen | tabel `openfigi_cache` (permanent; "geen match" wordt als lege lijst gecachet) |
| **ETF-aanbieders** (blackrock.com/ishares.com, vaneck.com) | volledige holdingslijst met land per positie | `fetch_provider_holdings()` in `etf_holdings_provider.py`; `requests.get` met een browser-`User-Agent`, timeout 30 s | geen retry; elke fout → `None` → terugval op yfinance-top-10 | `etf_holdings` (30 dagen, `bron = 'provider_csv'`) |
| **cdnjs.cloudflare.com** (in de browser) | Chart.js en plugins, hammer.js, luxon | `templates/portfolio.html` | — | de browsercache |
| **Neon PostgreSQL** | alle opslag | `db.py`, via `DATABASE_URL` | — | — |

### 6.2 Rate limits en gelijktijdigheid

Yahoo's rate limiting is het bekende pijnpunt van dit project; dat zie je terug in de opzet:

- **Herkenning:** `_is_rate_limit_fout()` kijkt in de tekst van de fout naar "rate limit", "too many requests", "invalid crumb" en "error 401" (de laatste twee komen voor bij veel gelijktijdige calls).
- **Spreiding over threads (`ThreadPoolExecutor`):** de lichte ticker-resolutie gebruikt 12 threads (`TICKER_RESOLUTIE_POOL_GROOTTE`), de volledige verificatie 6
  (`verifieer_tickers_met_prijs_parallel()`), het opwarmen van de land/sector-cache 8 (`_verwarm_land_sector_cache_parallel()`). De frontend doet maximaal 4 gelijktijdige `/ticker-zekerheid/positie`-aanroepen.
  - De 12 is gemeten op een test met koude cache en 28 posities: 4 → 8 workers halveerde de tijd bijna, 8 → 12 gaf nog ~13% winst, 12 → 16 nauwelijks meer, zonder
    aantoonbaar hoger rate-limit-risico. De 6 van de volledige check viel buiten die meting en is bewust lager (die doet per positie tot 1 + N kandidaten × 3 datums aan calls).
  - Waarom de volledige check parallel moet: bij fondsen met meerdere noteringen (VWCE.AS/.DE/.MI) liggen de koersen zo dicht bij elkaar dat geen kandidaat
    "overtuigend" wint, dus wordt de hele kandidatenlijst doorgerekend. Sequentieel duurde dat op de echte portfolio (12 posities, tot 7 kandidaten) ~84 s,
    zelfs met een warme cache: ruim boven de standaard gunicorn-timeout van 30 s.
  - Ook de lichte check draait parallel: bij veel nog nooit gecontroleerde tickers kan zelfs 1 call per positie, sequentieel, de timeout halen.
- **Bewust traag:** `classify_tickers()` wacht 1,5 s tussen twee niet-gecachete Yahoo-calls.
- **Zo min mogelijk calls:** permanente en 30-dagen-caches (zie [hoofdstuk 4](#4-database)); het "bekende ticker"-pad (`bekende_ticker`, `bekende_tickers`) dat de zoekopdracht overslaat; escaleren pas bij een echte afwijking
  (`find_ticker_met_snelle_prijscheck()`); en `DREMPEL_HERGEBRUIK_KOERS` (2 min) tegen dubbele koersverversing binnen één portfolio-opening.
- **Meten:** elke Yahoo-call wordt geteld per soort (`_tel_yahoo_call()`); `log_yahoo_call_samenvatting()` print aan het eind van een upload/opening `[timing] Yahoo-calls sinds laatste reset: N totaal -> {...}`.
  `meet_tijd("...")` print de duur per stap als `[timing] label: 1.23s`. Dit zijn de eerste plekken om te kijken als iets traag is.
- **Timeouts:** `requests` heeft timeouts (10 s OpenFIGI, 30 s providers). yfinance-calls hebben in de code **geen eigen timeout**; de bovengrens is de gunicorn-timeout op Render (**onzeker** hoeveel die is) en aan de frontend-kant `fetchMetTimeout()`.

## 7. Tests

### 7.1 Opzet

- **Python:** `unittest` (geen pytest), 59 bestanden `tests/test_*.py` met samen 532 `def test_...`-methodes (geteld op 30-09-2026). Geen `tests/__init__.py`; elk bestand zet zelf
  `sys.path.insert(0, <projectmap>)` zodat `import statistieken` enz. werkt.
- **JavaScript:** 10 bestanden `tests/test_*.js` met Node's ingebouwde testrunner (`node --test`), geen `package.json`. Op 30-09-2026 slaagden alle 145 tests (`test_prognose.js` 21, `test_menu.js` 10, `test_transacties.js` 14, `test_bedrijven.js` 30, `test_bestandskeuze.js` 4, `test_diagnostiek.js` 12, `test_navigatie.js` 31, `test_overdracht.js` 10, `test_dividend.js` 6, `test_scripts.js` 10).
  Getest wordt alleen wat in de "pure module"-bestanden zit (`prognose.js`, `menu.js`, `transacties.js`, `bedrijven.js`, `dividend.js`, `bestandskeuze.js`, `diagnostiek.js`, `navigatie.js`, `overdracht.js`).
  Drie bestanden lezen daarnaast bronbestanden als tekst: `test_menu.js` (`style.css` en `basis.html`, mobiele CSS-regels), `test_navigatie.js` (`portfolio.html`: tabblad-blokken, `data-views`, `data-verberg-buiten`, `data-vereist-code`, `data-grafiek-plek`, en de id's uit de toestand-lijsten in `tabs/`) en `test_scripts.js` (script- en stylesheet-tags in de templates tegen de bestanden in `static/`, en `RESET_PER_TAB` tegen de tab-bestanden, zie 5.1 en 5.2).
- **Afspraak (CLAUDE.md):** elke feature of bugfix krijgt kleine, gerichte unit tests, bij voorkeur op pure rekenfuncties met met de hand na te rekenen voorbeelden.
- **Wat ik zelf gedaan heb:** op 30-09-2026 de JS-tests (145 geslaagd) en de hele Python-suite met een lege `DATABASE_URL` (in Git Bash, zodat `.env` niet wordt ingelezen; in cmd werkt dat niet, zie 7.3): 532 tests, waarvan 494 uitgevoerd en geslaagd en 38 overgeslagen. De 47 database-vrije bestanden (453 tests) draaien volledig; de 12 **[DB]**-bestanden (79 tests) draaien alleen hun database-vrije klassen, de rest wordt overgeslagen omdat die de echte database aanraakt (zie hieronder).

### 7.2 Welk testbestand hoort bij welke module

Tussen haakjes het aantal tests. **[DB]** = het bestand wordt overgeslagen zonder bereikbare lokale database (localhost/127.0.0.1), omdat het de echte database aanraakt (direct, of via een functie die niet gemockt is). Alleen `app.py` importeren is geen reden om over te slaan: `db_init()` draait alleen met `DATABASE_URL`.

| Module | Testbestanden |
|---|---|
| `statistieken.py` | `test_rendement.py` (37), `test_twr.py` (6), `test_rendement_over_tijd.py` (6), `test_benchmark_vergelijking.py` (5), `test_gak_waarde_eur.py` (4), `test_gedeeltelijke_verkoop.py` (8), `test_chronologische_sortering.py` (11, ook `portfolio_calc` en `transactie_utils`) |
| `portfolio_calc.py` | `test_nog_in_bezit.py` (4), `test_per_ticker_koers_en_aankopen.py` (10), `test_holdings_op_datums.py` (11), `test_performance_regressie.py` (3, golden master) |
| `dividend.py` | `test_dividend.py` (14, database-vrij; o.a. het `herinvesteerd`-veld in `lijst`) |
| `portfolio_verdeling.py` | `test_bedrijven_verdeling.py` (10), `test_etf_overlap.py` (7), `test_europa_groepering.py` (11), `test_land_overig.py` (8), `test_land_sector_per_bron.py` (3), `test_land_staaf_top_n.py` (12), `test_verdeling_samenvatting.py` (8), `test_verdeling_sortering.py` (5) |
| `prijzen.py`, `yahoo_client.py`, `ticker_classificatie.py` | `test_koersen_cache.py` (7), `test_fx_caching_en_retry.py` (13), `test_fx_serie_memoization.py` (7), `test_prijzen_upsert.py` (2), `test_valuta_waarschuwing.py` (12, ook de Wisselkoersen-meldingen) |
| `diagnostiek.py` en de meldingen (alle database-vrij) | `test_diagnostiek.py` (18: module, Wisselkoersen, cache-hit), `test_diagnostiek_upload.py` (25: Order ID's, Opslaan, Dividend, insert-regressie), `test_diagnostiek_laden.py` (26: Koersen, Yahoo-tellers, Splits, ETF-holdings, Laadtijden, cache-hit) |
| `ticker_matching.py` | `test_ticker_zoeken.py` (10), `test_beurs_map_tdg.py` (3), `test_openfigi.py` (27, ook `ticker_zekerheid`) |
| `ticker_prijscheck.py` | `test_koers_dagrange_samenvoegen.py` (5), `test_dagrange_prijscheck.py` (7, deels **[DB]**), `test_ticker_verificatie.py` (22, deels **[DB]**) |
| `ticker_zekerheid.py` | `test_snelle_prijscheck.py` (23), `test_escalatiepoort_dagrange.py` (5), `test_automatische_ticker_correctie.py` (3), `test_alternatieve_kandidaten.py` (11), `test_basis_ticker_zekerheid.py` (4, via `basis_ticker_zekerheid_parallel()`), `test_niet_opslaan_performance.py` (2), `test_backfill_ticker.py` (11, **[DB]**) |
| `etf_holdings_provider.py` | `test_etf_holdings_bron.py` (26) |
| `portfolio_admin.py` | `test_code_validatie.py` (5) |
| `transactie_utils.py` | `test_datum_nl.py` (3, `formatteer_datum_nl()`); `_sorteer_chronologisch()` zit in `test_chronologische_sortering.py` |
| `db.py` (echte database) | `test_wijzig_code_db.py` (3), `test_dividend_db.py` (4), `test_laatste_prijs_update.py` (3) — allemaal **[DB]** |
| `tests/db_helper.py` (de skip-decorator zelf) | `test_db_helper.py` (8, database-vrij: alleen localhost/127.0.0.1 toegestaan, Neon-URL geweigerd vóór een verbindingspoging) |
| Routes en orkestratie (`app.py`, `portfolio_orchestratie.py`, `upload_verwerking.py`) | database-vrij: `test_pagina_routes.py` (10, de pagina-routes `/`, `/p/<code>` en `/analyse`; bewaakt ook dat elk element-id uit alle scripts die een pagina laadt op die pagina bestaat en dat `maxlength` uit `CODE_LENGTH` komt), `test_basis_cache.py` (3), `test_herbepaal_tickers_ophalen_route.py` (4), `test_etf_overlap_detail_route.py` (2), `test_benchmark_vergelijking_eigen_ticker.py` (4); `test_upload_route_foutafhandeling.py` (5, deels **[DB]**); **[DB]**: `test_gefaseerd_laden.py` (4), `test_ticker_koers_bereik_route.py` (5), `test_transacties_overzicht_route.py` (4), `test_ticker_zekerheid_positie_route.py` (4), `test_corporate_action_filtering.py` (7) |
| JavaScript | `test_prognose.js`, `test_menu.js`, `test_transacties.js`, `test_bedrijven.js`, `test_bestandskeuze.js`, `test_diagnostiek.js`, `test_navigatie.js`, `test_overdracht.js`, `test_dividend.js`, `test_scripts.js` |

**Niet (direct) getest, voor zover ik zag:** `dprint()` in `debug_utils.py` (`meet_tijd()` wel, via `test_diagnostiek_laden.py`), `infotip.js`, `gedeeld.js`, `start.js`, `app.js` en de scripts in `gedeeld/` en `tabs/` (alles met DOM). `compute_split_adjusted_shares()` heeft één test met een echt getal (factor 4, in `test_diagnostiek_laden.py`).

### 7.3 Draaien

Liefst niet lokaal: de CI (GitHub Actions, zie 7.4) draait alle tests bij elke push. Lokaal alleen met een lokale Docker-database, of **zonder database** door `.env` tijdelijk te hernoemen. Vanuit de projectmap (Windows cmd, zoals in CLAUDE.md):

```
ren .env .env.bak
python -m unittest discover -s tests -v
ren .env.bak .env
```

Andere commando's (met `.env` hernoemd zoals hierboven):

```
:: alleen één testbestand
python -m unittest discover -s tests -p "test_rendement.py" -v

:: alle JavaScript-tests (zelfde commando als de CI, geen database nodig)
node --test tests/test_prognose.js tests/test_menu.js tests/test_transacties.js tests/test_bedrijven.js tests/test_bestandskeuze.js tests/test_diagnostiek.js tests/test_navigatie.js tests/test_overdracht.js tests/test_dividend.js tests/test_scripts.js
```

`set DATABASE_URL=` werkt in cmd **niet**: het verwijdert de variabele, en daarna leest `load_dotenv()` de Neon-URL uit `.env` alsnog in. Alleen in Git Bash werkt een lege waarde: `DATABASE_URL= python -m unittest discover -s tests -v`.

**Let op — echte database:** de **[DB]**-tests (`@vereist_database` uit `tests/db_helper.py`) draaien alleen als `DATABASE_URL` naar **localhost of 127.0.0.1** wijst en die database bereikbaar is: de CI-container (zie 7.4) of een lokale Docker-Postgres. Een andere host, zoals Neon, wordt geweigerd vóór er een verbinding wordt geopend, zonder uitzondering. Lokaal met de Neon-URL uit `.env` worden ze dus overgeslagen.
Ze schrijven met eigen test-codes (zoals `TESTDIV`, nooit 3 hoofdletters) en ruimen na afloop op (`setUp`/`tearDown` verwijderen de test-code).

### 7.4 CI (GitHub Actions)

`.github/workflows/tests.yml` draait bij **elke push** (en via de knop "Run workflow") twee jobs, allebei alleen op Ubuntu: `test-js` (Node 20, `node --test ...`) en `test-db` (Python 3.13, met database).
De job `test` (Python op Windows en macOS, zonder database) is **tijdelijk uitgeschakeld**: hij staat uitgecommentarieerd in het bestand. `test-db` start een wegwerp-Postgres-container (`postgres:18`), zet `DATABASE_URL` naar die container, maakt het schema aan met een losse stap (`python -c "from db import db_init; db_init()"`) en draait dan de hele Python-suite, **[DB]**-tests inbegrepen. Er gaat geen secret en geen Neon-verbinding mee. De lijst JS-bestanden in het workflowbestand is handmatig; een nieuw `test_*.js` moet je daar zelf aan toevoegen.

## 8. Waar moet ik zijn als ik ... wil aanpassen?

### 8.1 Een nieuwe tab/pagina toevoegen

Uitgangspunt: een tabblad is een knop in het menu + een blok in de HTML + een bestand in `static/js/tabs/` met een `toon...()`-functie + een regel in `TOON_PER_VIEW` + eventueel een API-route. Volgorde:

1. **`templates/portfolio.html`:** voeg het tabblad toe aan een groep in `MENU_GROEPEN` (`menu.js`). Voeg in `.content` een blok `<div id="tab-mijnview" data-views="mijnview" style="display: none;">...</div>` toe met de vaste opmaak erin (koppen, uitleg, lege containers).
   Wil je de gedeelde grafiek, zet dan op de gewenste plek in dat blok `<div data-grafiek-plek></div>` (met `data-zoombaar` voor de zoomknop). Eigen meldingen of hulpelementen die je tabblad zelf aan/uit zet, krijgen `data-verberg-buiten="mijnview"`; iets dat alleen mét of zonder code mag verschijnen, krijgt `data-vereist-code`.
2. **`static/js/tabs/mijnview.js`:** maak een nieuw bestand met bovenaan één regel commentaar en daaronder de `toon...()`-functie (hier `toonMijnView()` genoemd: een verzonnen voorbeeldnaam, die bestaat dus niet), de toestand van het tabblad en de event-listeners.
   Patroon: data uit `huidigeData` lezen, of een lazy `fetch()` (zie `toonDividend()` in `tabs/dividend.js` of `toonTransacties()` in `tabs/transacties.js`); voor verrijkingsdata begin je met `toonVerrijkingWachtstatusIndienNodig()`.
   Voor een grafiek: `updateChart(labels, datasets)` (lijn) of een eigen `new Chart(...)` op `#rendementChart` na `if (chart) chart.destroy()`. Voor een tabel: `maakSorteerbareTabel()` (zie 5.4). Opmaak via klassen in `style.css`, niet via `element.style`.
   Kies namen op het hoogste niveau die nog niet bestaan (alle scripts delen één scope, zie 5.1). Een listener op het hoogste niveau mag alleen naar een id verwijzen dat in `portfolio.html` staat.
3. **Script-tag:** voeg het bestand toe aan het blok `scripts` onderaan `portfolio.html`, tussen de andere `tabs/`-bestanden en dus vóór `app.js`. `tests/test_scripts.js` faalt als je dit vergeet.
4. **`static/js/app.js`:** voeg `"mijnview": toonMijnView,` toe aan `TOON_PER_VIEW` (daarmee is `#mijnview` ook meteen een geldige URL-hash). `pasViewToe(view)` hoef je niet aan te passen. Hoort er toestand bij één portfolio, schrijf dan in je tab-bestand een `resetMijnView()` en zet die in `RESET_PER_TAB` (de test faalt anders, zie 5.2).
   Alleen voor opgeslagen portfolio's? Zet de view dan ook in `VIEWS_MET_CODE` (de menuknop verdwijnt bij "niet opslaan" en de hash valt terug op het eerste tabblad) en vang `!huidigeData.code` af in je `toon`-functie.
   `tests/test_navigatie.js` controleert dat elk menu-tabblad een blok `tab-<view>` heeft; de lijsten met grafiek- en zoom-tabbladen in die test moet je wel bijwerken.
5. **Backend nodig?** Nieuwe route in `app.py` (dun houden), rekenwerk in een domeinmodule, eventueel `_laad_transacties_en_resultaat()` of `_haal_portfolio_basis()` hergebruiken.
6. **Tests:** pure rekenlogica in Python testen (`tests/`), pure JS-logica in een eigen `static/js/<naam>.js` in het "pure module"-patroon (laden vóór `gedeeld/`) en een `tests/test_<naam>.js`; voeg dat bestand toe aan de `node --test`-regel in `.github/workflows/tests.yml`.
7. **CSS:** `static/css/style.css`, in sectie 4 (per tabblad) op de plek van je tabblad in de menuvolgorde; geen inline stijlen behalve `display: none` voor wat JS aan- en uitzet. Denk aan de mobiele `@media`-blokken in sectie 6.

### 8.2 Een nieuwe analyse of grafiek toevoegen

1. **Schrijf een pure functie** in het passende domeinmodule (`statistieken.py` voor rendement/statistiek, `portfolio_calc.py` voor tijdreeksen, `portfolio_verdeling.py` voor aggregaties over holdings): DataFrames/getallen in, dict uit, geen DB/netwerk. Test met een met de hand na te rekenen voorbeeld.
2. **Kies waar hij wordt aangeroepen:**
   - goedkoop en altijd nodig → in `analyze_transacties_kern()` (`portfolio_orchestratie.py`), en voeg een sleutel aan het teruggegeven dict toe;
   - netwerkzwaar (Yahoo, holdings) → in `analyze_transacties_verrijking()`;
   - alleen bij een specifiek tabblad of duur → een apart lui endpoint in `app.py`, zoals `rendement_over_tijd()` en `benchmark_vergelijking()` (gebruik `_laad_transacties_en_resultaat()`).
3. **Frontend:** een `toon...()`-functie die de nieuwe sleutel uit `huidigeData` leest (zie 8.1), of een `fetch()` naar het nieuwe endpoint.
4. Controleer of de nieuwe sleutel **JSON-veilig** is: `NaN` wordt door Flask als het ongeldige token `NaN` geserialiseerd en breekt `response.json()` in de browser (zie het commentaar in `compute_per_ticker_koers_en_aankopen()`; gebruik `pd.notna()` en geef `None`).
5. Rekenen met bedragen uit Postgres: cast `NUMERIC` (Decimal) expliciet naar `float`.

### 8.3 Een nieuwe kolom in `transacties` toevoegen

Voorbeeld uit de code: `waarde_eur`. Hetzelfde pad volgden `transactiekosten` en `tijd`.

1. **`db.py`, `db_init()`:** voeg de kolom toe (achteraan de kolomlijst) in `CREATE TABLE transacties`. Let op: `CREATE TABLE IF NOT EXISTS` raakt een al bestaande tabel niet aan — voeg de kolom in de bestaande Neon-database dus eenmalig met de hand toe (bv. `ALTER TABLE transacties ADD COLUMN <kolom> <type>;` in Neon's SQL-editor).
2. **`upload_verwerking.py`:**
   - zet de Excel-kolom in `VERWACHTE_KOLOMMEN` (anders ontbreekt hij mogelijk in de controle in `_lees_transacties_excel()`), geef hem een constante zoals `WAARDE_KOLOM` en lees hem waar nodig, bv. in `_adjust_transaction_exchange_rates()` of `_insert_nieuwe_transacties()`;
   - geef hem door aan `db_insert_transactie()` in `_insert_nieuwe_transacties()` (nieuwe parameter in de aanroep);
   - neem hem op in `_bouw_transacties_df_niet_opslaan()` zodat "niet opslaan" dezelfde kolommen heeft.
3. **`db.py`:** neem de kolom op in de `INSERT` van `db_insert_transactie()` (kolomlijst **en** `VALUES`-plaatsaanduiding **en** parameters) en in `TRANSACTIE_KOLOMMEN` (de lijst voor de `SELECT` in `db_get_portfolio_naam_en_transacties()`; die ene lijst dekt zowel `_haal_portfolio_basis()` als `_laad_split_gecorrigeerde_transacties()`). Cast `NUMERIC` in `portfolio_orchestratie.py` naar `float` waar nodig.
4. **Tonen?** Dan ook `db_get_transacties_overzicht()` in `db.py`, `TRANSACTIES_KOLOMMEN` in `static/js/tabs/transacties.js` (kop en cel in één regel) en `VERGELIJKERS` in `static/js/transacties.js` (de sortering, onder dezelfde sleutel).
5. **Tests + bestaande data:** test de nieuwe kolom met een eigen test-code. Al opgeslagen rijen krijgen de kolom niet vanzelf gevuld (er is geen data-backfill, zie 4.4): portfolio verwijderen en opnieuw uploaden.

### 8.4 Een ticker-probleem oplossen

Symptomen: een waarschuwingsbanner bovenaan ("koers wijkt af van Yahoo"), een positie zonder koersdata, of "Onzeker" op de Ticker-zekerheid-pagina.

1. **Kijk eerst op Instellingen → Ticker-zekerheid.** Per positie zie je de gevonden ticker, de prijscontrole per datum (`niveau`, dagrange), alternatieven en de OpenFIGI-regel.
2. **Klopt een alternatief, en de automatische correctie greep niet?** Dan zet je het handmatig vast in `ticker_matching.py`:
   - `MANUAL_TICKER_OVERRIDES_ISIN[(ISIN, Beurs)] = "TICKER.XX"` — geldt **vóór** het zoeken en overschrijft ook een "zekere" match (zo is BYD opgelost);
   - `MANUAL_TICKER_OVERRIDES["NAAM-PREFIX"]` — alleen als fallback ná een mislukte zoekopdracht.
3. **Beurscode niet herkend?** Voeg de DeGiro-beurscode toe aan `BEURS_MAP` in `ticker_matching.py` (zonder vermelding is `targets` leeg en komt er nooit een "zekere" beurs-match).
4. **Al opgeslagen tickers herberekenen:** upload het bestand opnieuw met het vinkje "Ticker-informatie voor alle posities opnieuw bepalen", of gebruik hetzelfde vinkje bij "Ophalen met code" (`backfill_verouderde_tickers(code, forceer=True)`). Zonder vinkje herzoekt de backfill alleen posities met een prijsprobleem.
5. **Koers zelf fout (niet de ticker)?** Kijk of de valuta USD/GBP/GBp is; andere valuta's worden in `_converteer_naar_eur()` en `_fx_koers_op_datum()` niet omgerekend (zoek in de terminal naar `[koersen] WARN` en `[prijscheck] WARN`). Bij een split: `compute_split_adjusted_shares()` (waardereeks) en `_cumulatieve_split_factor()` (prijscheck).
6. **Logs lezen:** `[ticker]` (met `WARN` bij een blinde fallback), `[koersen] WARN`/`[prijscheck] WARN` (valuta onbekend of niet omgerekend), `[prijscheck-debug]`, `[alternatieven-debug]`, `[timing]`-regels (`DEBUG = True` in `debug_utils.py`). Wil je dieper kijken, voeg dan tijdelijk een eigen `dprint` toe (en haal die daarna weer weg).
7. **Snelste handmatige fix in de data** (aan je eigen risico, controleer eerst met een `SELECT` met dezelfde `WHERE`): `UPDATE transacties SET ticker = ... WHERE code = ... AND isin = ... AND beurs = ...`, gevolgd door een verse portfolio-opening (de basis-cache van 20 s verloopt vanzelf).

### 8.5 Een ETF toevoegen aan `ETF_HOLDINGS_BRON`

1. Zoek op de site van de aanbieder de knop "Holdings downloaden"/"Full holdings"; rechtsklik → "Kopieer linkadres". Bij iShares/blackrock.com-URL's: **laat `asOfDate` weg**.
2. Test de URL **voordat** je hem toevoegt, vanuit de projectmap (download + de parser uit `_PROVIDER_PARSERS`, zonder iets op te slaan):
   ```
   python -c "import requests; from etf_holdings_provider import _PROVIDER_PARSERS, _PROVIDER_USER_AGENT; r = requests.get('<URL>', headers={'User-Agent': _PROVIDER_USER_AGENT}, timeout=30); h = _PROVIDER_PARSERS['ishares'](r.content, locale='nl'); print(len(h), sum(x['gewicht'] for x in h))"
   ```
   (Parser is `"ishares"` of `"vaneck"`; `locale` is `"nl"` als de site Nederlands getalformaat geeft, anders `"en"`.) De gewichtensom moet dicht bij 100 liggen; ~10000 wijst op een verkeerde `locale`.
3. Voeg een entry toe aan `ETF_HOLDINGS_BRON` in `etf_holdings_provider.py`: `"TICKER.AS": {"provider": "...", "locale": "...", "url": "..."}`. Een andere notering van hetzelfde fonds (zoals `IS3N.DE` naast `EMIM.AS`) krijgt gewoon dezelfde URL.
4. Komt er een Nederlandse landnaam voor die niet in `NL_LAND_VERTALING` staat, dan blijft die onvertaald staan (als aparte taartpunt) — vul de tabel aan.
5. Je hoeft de cache niet te legen: `get_etf_holdings()` probeert een verse `yfinance_top10`-cache alsnog te upgraden naar `provider_csv` zodra er een provider-URL bekend is.
6. Test: zie `tests/backend/test_etf_holdings_bron.py` als voorbeeld. Vanguard-ETF's (VWCE.AS, VUSA.AS) horen er bewust niet in.

### 8.6 Voorbeeld: kleine UI-wijzigingen

Twee soorten kleine wijzigingen, en welke bestanden je daarvoor raakt.

**Een element verplaatsen, zoals het Land-vinkje ("Europese landen samenvoegen")**

| Bestand | Wat je aanpast |
|---|---|
| `templates/portfolio.html` | Het blok `<label id="europaCheckboxWrapper">` staat in `#tab-land` direct onder het `data-grafiek-plek`; verplaatsen = dit blok elders in de HTML zetten. |
| `static/js/tabs/land_sector.js` | **Niets**: `toonLand()` zoekt het element op `id` (`europaCheckboxWrapper`, `europaCheckbox`), dus zolang de id's gelijk blijven werkt de JS ongewijzigd. |

Les: verplaats je een element **zonder de id te wijzigen**, dan is de HTML-wijziging genoeg.

**Een hulptekst-icoontje (i) toevoegen**

| Bestand | Wat je aanpast |
|---|---|
| `templates/start.html` of `portfolio.html` | Zet naast het label een `<span class="infoTip" data-label="…">uitlegtekst</span>`, samen in een `<div class="labelRij">`. `infotip.js` wordt op beide pagina's al geladen. |
| `static/js/infotip.js` | **Niets**: bouwt bij het laden van elke `.infoTip` een (i)-knop met tooltip (hover, focus, tik; Escape en klik buiten sluiten). |
| `static/css/style.css` | Niets, tenzij je de opmaak wilt veranderen: `.labelRij`, `.infoTipKnop`, `.infoTipTekst`, `.infoTip.open .infoTipTekst`. |
| Tests | Geen: `infotip.js` raakt de DOM en heeft geen test. |

**Vuistregel uit deze voorbeelden:** puur visuele wijziging = de templates (structuur) en `style.css` (uiterlijk); gedrag = het bestand van het tabblad in `static/js/tabs/` (navigatie: `app.js`; rekenwerk: een pure module in `static/js/`); verandert een JSON-vorm, dan ook de bijbehorende Python-functie én de test.

## 9. Woordenlijst

| Term | Uitleg | Waar in de code |
|---|---|---|
| **GAK** | Gemiddelde aankoopkoers (kostprijs per stuk) volgens de lopende-gemiddelde-methode: bij elke koop `kostprijs += waarde`, `aantal += n`; bij een verkoop gaat `GAK × verkocht` van de kostprijs af en verandert de GAK zelf niet. Gebaseerd op `waarde_eur` (zonder kosten). | `bereken_holdings_en_gesloten()`, `compute_per_ticker()` |
| **Kostenbasis / kostprijs** | `GAK × aantal`: wat je betaald hebt voor wat je nu bezit. De noemer van het positierendement. | `bereken_positie_rendement()` |
| **Geïnvesteerd** | Kan twee dingen betekenen: netto cashflow (Home, totaal) of kostenbasis van de huidige stukken (per aandeel). Zie de valkuil bij `portfolio_calc.py`. | `compute_value_over_time()`, `compute_per_ticker()` |
| **Rendement (€ / %)** | `waarde − geïnvesteerd`, en dat gedeeld door geïnvesteerd. Houdt géén rekening met *wanneer* je inlegde. | `bereken_totaal_rendement()` |
| **XIRR** | Geannualiseerd rendement dat wél rekening houdt met de datum van elke in- en uitleg (zoals een rente op rente). Cashflows: transacties plus een fictieve verkoop van de huidige waarde. Via `pyxirr`. | `bereken_xirr()`, `_bouw_xirr_cashflows()` |
| **TWR** | Time-weighted return: rendement per sub-periode aan elkaar vermenigvuldigd, zodat de *timing* van stortingen het cijfer niet vertekent. | `bereken_twr()` |
| **Split-correctie** | DeGiro boekt een aandelensplitsing als "NON TRADEABLE"/`DEG`-rijen. `adj_aantal` corrigeert oude aantallen naar de post-split-basis; bij de prijscontrole wordt Yahoo's koers met de cumulatieve split-factor vermenigvuldigd. | `compute_split_adjusted_shares()`, `_cumulatieve_split_factor()` |
| **Corporate action / DEG-rij** | Boekingsrij van DeGiro die geen echte koop/verkoop is (`beurs == "DEG"` of "NON TRADEABLE" in de productnaam). | `_is_corporate_action_row()` |
| **Order ID** | Unieke ID (UUID, 36 tekens met 4 streepjes) per DeGiro-order. Staat in het Excel-bestand één kolom verschoven ten opzichte van de kop, dus pakt de code bij een lege kolom de naamloze buurkolom. | `_kolom_of_naamloze_buurkolom()` |
| **Synthetische ID** | Vervanging voor een ontbrekende Order ID: `"SYN-" + md5(datum\|tijd\|product\|isin\|aantal\|totaal)[:16] + "-" + volgnummer`. Deterministisch, dus stabiel bij herupload. | `_create_synthetic_order_ids()` |
| **Portfolio-code** | 3 hoofdletters; de enige "sleutel" tot je data. | `generate_code()`, `is_geldige_code()` |
| **(ISIN, Beurs)** | Het paar waarop tickers gekoppeld worden, niet ISIN alleen: hetzelfde fonds kan op meerdere beurzen (met andere ticker en koers) genoteerd staan. | `upload_verwerking.py`, `_ticker_zekerheid_groepen()` |
| **Ticker** | Het Yahoo-symbool (bv. `VUSA.AS`). DeGiro geeft alleen productnaam, ISIN en beurs. | `find_ticker_detailed()` |
| **Ticker-zekerheid** | Hoe zeker we zijn dat de ticker klopt: `zeker`/`onzeker`/`geen_match`, versterkt of afgezwakt door de prijscontrole en OpenFIGI. | `ticker_zekerheid.py` |
| **Prijscontrole / niveau** | Vergelijking DeGiro-prijs ↔ Yahoo-slotkoers op dezelfde datum: `ok` (< 2%), `mild` (2–6%), `waarschuwing` (≥ 6%). | `vergelijk_prijs_op_datum()` |
| **Dagrange** | Het intraday-`High`/`Low` van de handelsdag. Valt de DeGiro-prijs (± 5%) erbuiten, dan is dat het primaire "probleem"-criterium. | `_prijscheck_is_probleem()`, `DAGRANGE_TOLERANTIE` |
| **Escalatietrapje** | De lichte check begint met 1 datum en kost meer moeite (meer datums, dan alternatieve tickers) alleen als er een afwijking is. | `find_ticker_met_snelle_prijscheck()` |
| **Tier 1 / Tier 2** | De twee voorwaarden waaronder een alternatieve ticker automatisch wordt overgenomen (beurs + ≥ 2 datums, resp. alle datums kloppen). | `find_ticker_met_snelle_prijscheck()` |
| **Override** | Handmatig vastgezette ticker: op naam-prefix (fallback) of op `(ISIN, Beurs)` (vóór het zoeken). | `MANUAL_TICKER_OVERRIDES(_ISIN)` |
| **OpenFIGI-root** | Het deel van een ticker vóór het Yahoo-beurssuffix (`BY6` in `BY6.MU`); wordt vergeleken met OpenFIGI's lijst noteringen voor de ISIN. | `_openfigi_root_matches()` |
| **Kern / verrijking** | De snelle helft van de dashboard-respons (koersen en berekeningen) versus de netwerk-zware helft (verdeling, land, sector, bedrijven, overlap), die lui wordt opgehaald. | `analyze_transacties_kern()`, `analyze_transacties_verrijking()` |
| **Basis** | `(naam, transacties_df, price_data)`, 20 s gecachet, gedeeld door meerdere requests van één bezoek. | `_haal_portfolio_basis()` |
| **Niet opslaan** | Analyse zonder database en zonder code; kern en verrijking komen in één antwoord. | `analyze_transacties()` |
| **Backfill** | Een verouderde waarde in bestaande rijen alsnog corrigeren (hier: opgeslagen tickers). | `backfill_verouderde_tickers()` |
| **`missing` / `stale`** | In `get_prices()`: `missing` = ticker niet (genoeg) in de cache → volledig downloaden; `stale` = wel gecachet maar verouderd → incrementeel bijwerken. | `get_prices()` |
| **`verversen`** | Parameter van `get_prices()`: `False` slaat het incrementeel verversen over (bijnaam/code wijzigen). | `get_prices()` |
| **FX-anker** | Vaste startdatum (01-01-2005) voor de FX-koersreeks, zodat de cache na de eerste keer altijd "ver genoeg terug" is. | `FX_ANKER_DATUM` |
| **Wisselkoers / `_koers_eur`** | DeGiro's eigen omrekenkoers per transactie; `koers / wisselkoers` wordt als EUR-koers opgeslagen. | `_adjust_transaction_exchange_rates()` |
| **Totaal EUR vs Waarde EUR** | `totaal_eur` bevat AutoFX en transactiekosten; `waarde_eur` is aantal × koers zonder kosten. GAK en kostprijs gebruiken `waarde_eur`. | `transacties`-tabel |
| **AutoFX** | DeGiro's automatische valutaconversie bij een niet-EUR-transactie (kosten zitten in `totaal_eur`). | `transacties.totaal_eur`; zie CLAUDE.md, Data en rekenen |
| **`provider_csv` / `yfinance_top10`** | Herkomst van ETF-holdings: volledige lijst van de aanbieder, of Yahoo's top 10 (dekking ~35–40% voor brede fondsen). | `etf_holdings.bron`, `get_etf_holdings()` |
| **Unknown / Overig / Europe** | Land- en sectorbuckets: niet-gedekt of onbekend; landen onder 0,5%; en de samenvoeging van Europese landen. | `portfolio_verdeling.py` |
| **ETF-overlap** | Voor twee ETF's: Σ min(gewicht) over gedeelde bedrijven (genormaliseerde naam). | `bereken_etf_overlap()` |
| **Herinvestering (dividend)** | DeGiro-rij "Dividend Herinvestering" die (een deel van) het "Dividend"-bedrag opheft; wordt meegenomen in het netten en zet `herinvesteerd`, zichtbaar als label "herinvesteerd" in de uitkeringenlijst. | `verwerk_rekeningoverzicht_df()`, `maakDividendUitkeringenTabel()` |
| **Gepoolde conversie / STAP A** | Meerdere dividenden van dezelfde valuta en dagen in één valutaconversie; aandelen naar rato verdeeld. | `verwerk_rekeningoverzicht_df()`, `_clusters_binnen_venster()` |
| **`dprint` / `meet_tijd`** | Debug-print en tijdmeting (`[timing]`). | `debug_utils.py` |
| **Neon / Render / gunicorn** | Neon = gehoste PostgreSQL; Render = de hostingdienst; gunicorn = de WSGI-server waarmee Flask in productie draait. | buiten de code (zie hoofdstuk 1) |

## 10. Leesgids: van full-stack-basis naar deze code

Dit hoofdstuk heeft twee delen. [10.1](#101-hoe-werkt-een-full-stack-webapp-en-hoe-hangt-dat-samen-in-dit-project) legt uit hoe een full-stack webapp in
elkaar zit, telkens met een bestand of functie uit dit project als voorbeeld. [10.2](#102-leesvolgorde-in-14-stappen) is een leesvolgorde door de
code, van eenvoudig naar complex.

### 10.1 Hoe werkt een full-stack webapp (en hoe hangt dat samen in dit project)

#### Frontend en backend

Een webapp bestaat uit twee programma's die met elkaar praten:

- De **frontend** draait in de browser van de gebruiker: de templates in `templates/`, `static/css/style.css` en de bestanden in `static/js/`. De browser
  downloadt ze één keer en voert ze daarna zelf uit.
- De **backend** draait op een server (hier: Render): `app.py` en alle andere `.py`-bestanden. De backend leest de database, praat met Yahoo en
  rekent.
- De **database** (PostgreSQL bij Neon) is een derde, aparte server. Alleen de backend praat ermee, nooit de browser.

Full stack betekent: je werkt aan alle lagen tegelijk.

#### HTML, CSS en JavaScript, en waarom het dashboard een single-page application is

- **HTML** is de structuur: welke knoppen, formulieren en vakken er zijn (`templates/start.html` en `templates/portfolio.html`).
- **CSS** is het uiterlijk: kleuren, afstanden, en hoe het menu op een telefoon een uitschuifpaneel wordt (`static/css/style.css`).
- **JavaScript** is het gedrag: wat er gebeurt als je klikt, data ophalen, grafieken tekenen (`static/js/`).

Het project heeft twee pagina's: de startpagina (`/`) en de portfolio-pagina (`/p/<code>`). De portfolio-pagina zelf is een **single-page application (SPA)**: alle tabbladen staan er al in als verborgen `<div>`'s
(`#tab-statistieken`, `#tab-transacties`, ...). Klik je in het menu, dan verandert alleen de hash in de URL (`#rendement`); `app.js` reageert daarop met `wisselView(view)`, en die roept `pasViewToe(view)` aan.
`pasViewToe()` zet bij het ene tabblad-blok `style.display = "block"` en bij de andere `"none"`, en tekent de inhoud met een `toon...()`-functie (uit het bestand van dat tabblad in `static/js/tabs/`). De browser
laadt dus geen nieuwe pagina: dat voelt sneller en de opgehaalde data (`huidigeData`) blijft in het geheugen staan. Keerzijde: verversen
(F5) gooit dat geheugen weg; omdat de code in de URL staat, haalt de pagina de portfolio daarna zelf opnieuw op (zie 2.8).

Het deel van de browser dat de pagina als boom van elementen bijhoudt, heet de **DOM**. `document.getElementById(...)` en `.style.display` zijn
DOM-aanroepen.

#### API en routes: hoe de browser de server aanroept

De frontend vraagt data op met een **request** (verzoek) aan een **URL**; de backend stuurt een **response** (antwoord) terug. Het afgesproken
geheel van URL's en antwoorden heet de **API**. Een **route** is één zo'n URL in de backend, gekoppeld aan een Python-functie.

Het request heeft een **HTTP-methode**. De belangrijkste hier: `GET` (iets ophalen, verandert niets) en `POST` (iets insturen of wijzigen). Dit
project gebruikt daarnaast één keer `DELETE` (portfolio verwijderen).

Het antwoord is bijna altijd **JSON**: tekst in de vorm van JavaScript-objecten (`{"beschikbaar": true, "totaal_netto": 123.45}`). Python maakt
er met `jsonify()` JSON van; JavaScript maakt er met `res.json()` weer een object van.

Voorbeeld: het Dividend-tabblad. In `app.py`:

```python
@app.route("/api/portfolio/<code>/dividend")
def dividend(code):
    ...
    samenvatting = bereken_dividend_samenvatting(code)
    if samenvatting is None:
        return jsonify({"beschikbaar": False})
    samenvatting["beschikbaar"] = True
    return jsonify(samenvatting)
```

En in `static/js/tabs/dividend.js`, in `toonDividend()`:

```javascript
res = await fetch(`/api/portfolio/${huidigeData.code}/dividend`);
data = await res.json();
```

`fetch()` doet standaard een `GET`. De upload is een `POST`: het formulier gaat als `FormData` (met het Excel-bestand erin) mee in
`fetchMetTimeout("/upload", { method: "POST", body: formData }, 60000)`, en in `app.py` staat `@app.route("/upload", methods=["POST"])`.

Een route heet ook wel **endpoint**. Hoofdstuk 3 heeft de volledige lijst.

#### Flask: wat een route-functie doet, en waarom `app.py` dun is

**Flask** is de Python-bibliotheek die de routes regelt. `@app.route(...)` boven een functie zegt: "roep deze functie aan als er een request voor
deze URL binnenkomt". De functie leest wat er meekomt (`request.files`, `request.args`, stukken van de URL zoals `<code>`), doet haar werk en
geeft een response terug (`jsonify(...)`, eventueel met een statuscode zoals `404`).

`app.py` is bewust **dun**: de route-functies roepen vooral functies uit andere modules aan. De echte logica staat in bijvoorbeeld
`statistieken.py` of `dividend.py`. Voordelen: die modules zijn te testen zonder een webserver, en een route blijft kort genoeg om in één
oogopslag te lezen. Een laag die meerdere taakfuncties achter elkaar aanroept, heet **orkestratie** (`portfolio_orchestratie.py`,
`upload_verwerking.py`).

#### Database: PostgreSQL bij Neon

**PostgreSQL** is een relationele database: tabellen met rijen en kolommen, die je met **SQL** bevraagt (`SELECT ... FROM transacties WHERE code = %s`).
**Neon** host die database in de cloud. De backend verbindt ermee via `psycopg2` en de `DATABASE_URL` (`db_connect()` in `db.py`).
Er is geen **ORM** (een laag die tabellen als Python-klassen verpakt); de SQL staat gewoon in de code.

`db_init()` in `db.py` maakt alle tabellen aan met `CREATE TABLE IF NOT EXISTS`: bestaat de tabel al, dan gebeurt er niets. Daarom is het veilig
om het bij elke start te draaien, maar voegt het ook geen nieuwe kolom toe aan een bestaande tabel (zie 8.3).

Er zijn twee soorten tabellen (hoofdstuk 4):

- **Gebruikersdata**: `portfolios`, `transacties`, `dividenden`. Van jou, en weg als je je portfolio verwijdert.
- **Cachetabellen**: `prijzen`, `ticker_info`, `etf_holdings`, enz. Een **cache** is een bewaarde kopie van iets dat duur is om op te halen. Hier:
  antwoorden van Yahoo en de ETF-aanbieders. Ze zijn anoniem (geen code erin) en blijven staan.

#### Externe bronnen: waarom alles gecachet wordt

De backend haalt data bij andere diensten: Yahoo Finance (via de bibliotheken `yfinance` en `yahooquery`), OpenFIGI en de sites van iShares en
VanEck (hoofdstuk 6). Elke zo'n **netwerkcall** duurt tientallen tot honderden milliseconden, kan mislukken, en Yahoo weigert je tijdelijk
(**rate limit**) als je te veel tegelijk vraagt. Daarom:

- worden antwoorden bewaard in de cachetabellen, zodat de volgende keer de database genoeg is;
- probeert `yahoo_client.py` een mislukte call opnieuw (**retry**) met een wachttijd ertussen;
- draaien veel calls tegelijk in een **thread pool** (`ThreadPoolExecutor`), maar met een maximum aantal tegelijk.

#### Gefaseerd laden: kern en verrijking

Op Render draait de app onder **gunicorn**. Die breekt een request af dat te lang duurt (standaard na 30 seconden; wat er op Render is ingesteld, is
**onzeker**). Het netwerk-zware deel van het dashboard (verdeling, land, sector, bedrijven, overlap) kan bij een koude cache langer duren. Daarom is het
dashboard in twee requests gesplitst (2.4):

1. de **kern** (koersen en berekeningen) komt direct mee met `/upload` of `GET /api/portfolio/<code>`;
2. de **verrijking** haalt `laadVerrijking()` daarna op de achtergrond op via `GET /api/portfolio/<code>/verrijking`.

Zo staat de Home-grafiek er snel, en vult de rest zich later aan. Hetzelfde idee zit achter de losse "luie" endpoints (rendement-over-tijd,
dividend, transacties, ...): alleen ophalen als je dat tabblad opent.

#### Pure functies tegenover code met DOM, netwerk of database

Een **pure functie** krijgt invoer, geeft uitvoer, en doet verder niets: geen database, geen netwerk, geen DOM, geen globale toestand. Dezelfde
invoer geeft altijd dezelfde uitvoer. Voorbeelden: `bereken_xirr()` en `bereken_holdings_en_gesloten()` in `statistieken.py`, of `berekenPrognose()` in `prognose.js`.

Pure functies zijn makkelijk te testen: je geeft een met de hand na te rekenen voorbeeld mee en vergelijkt de uitkomst. Daarom staat het rekenwerk
in pure Python-functies (zie "Rekenen in Python, tonen in JS" in 5.1), en doet de frontend vooral tonen.

In de frontend geldt hetzelfde: `prognose.js`, `menu.js`, `transacties.js`, `bedrijven.js`, `dividend.js`, `diagnostiek.js` en `bestandskeuze.js` raken de DOM
niet aan (net als `navigatie.js` en `overdracht.js`). Door het IIFE-patroon (5.1) werken ze zowel in de browser als in **Node.js** (JavaScript buiten de browser), dus kun je ze testen zonder
browser. `app.js`, `start.js`, `gedeeld.js`, `infotip.js` en alles in `gedeeld/` en `tabs/` raken de DOM wel aan en hebben geen eigen tests.

#### Tests en CI

- **Python**: `unittest` (ingebouwd in Python), bestanden `tests/test_*.py`. Een test roept een functie aan en controleert de uitkomst met
  `self.assertEqual(...)` e.d.
- **JavaScript**: Node's ingebouwde testrunner (`node --test`), bestanden `tests/test_*.js`.
- **Mocken**: in een test een echte functie (bijvoorbeeld een Yahoo-call) vervangen door een nepversie met een vast antwoord, zodat de test
  snel en voorspelbaar is.
- **CI** (continuous integration): bij elke `git push` draait GitHub Actions automatisch alle tests (`.github/workflows/tests.yml`, zie 7.4). Je
  ziet op GitHub een groen vinkje of een rood kruis.

De meeste tests draaien **zonder database**: dat is sneller, en lokaal deelt dit project de database met productie. Tests die toch een database
nodig hebben (`@vereist_database`) draaien alleen tegen een wegwerpdatabase op localhost (de CI-job `test-db`), nooit tegen Neon.

#### Deployment: lokaal en op Render

- **Lokaal**: `python app.py` start Flask's eigen ontwikkelserver (`app.run(debug=True)` onderaan `app.py`). Handig tijdens het bouwen, niet voor
  productie.
- **Render**: draait de app met **gunicorn**, een WSGI-server die meerdere requests tegelijk aankan. **WSGI** is de standaardafspraak tussen een
  Python-webapp en de server die hem draait; het `app`-object in `app.py` is wat gunicorn nodig heeft. Het startcommando staat niet in de repo
  (**onzeker**; vermoedelijk `gunicorn app:app` in het Render-dashboard).
- **Instellingen**: lokaal staan geheime waarden als `DATABASE_URL` in `.env` (staat in `.gitignore`, dus niet op GitHub); `load_dotenv()` in `db.py`
  leest ze in als **omgevingsvariabelen**. Op Render vul je dezelfde variabelen in het dashboard in. De code leest ze in beide gevallen met
  `os.environ[...]`.
- Een nieuwe versie online zetten = `git push`; Render bouwt en start dan opnieuw.

#### De datastroom in één schema

Van Excel-upload tot grafiek (tak "opslaan", zie hoofdstuk 2 voor alle stappen):

```mermaid
flowchart TD
    XL["Excel-bestand van DeGiro"] --> FORM["start.js: submit-handler van uploadForm<br/>FormData + fetchMetTimeout()"]
    FORM -- "POST /upload" --> UP["app.py: upload() → _upload_impl()"]
    UP --> UV["upload_verwerking.py<br/>Excel inlezen, Order ID's, code zoeken/maken,<br/>tickers, _insert_nieuwe_transacties()"]
    UV -- "INSERT" --> DB[("Neon PostgreSQL<br/>transacties, portfolios")]
    UP --> BPR["portfolio_orchestratie.py<br/>build_portfolio_response()"]
    DB -- "SELECT" --> BASIS["_haal_portfolio_basis()<br/>+ get_prices() (prijzen.py)"]
    BPR --> BASIS
    BASIS --> KERN["analyze_transacties_kern()<br/>portfolio_calc.py, statistieken.py"]
    KERN -- "JSON (kern)" --> OVD["start.js: gaNaarPortfolioPagina()<br/>overdracht in sessionStorage → /p/CODE"]
    OVD --> TD["app.js: startPortfolioPagina()<br/>→ toonDashboard()"]
    TD --> TP["toonPortfolio() → updateChart()"]
    TP --> CH["Chart.js-grafiek op canvas rendementChart"]
    TD -. "daarna: GET /api/portfolio/CODE/verrijking" .-> VER["laadVerrijking()<br/>→ analyze_transacties_verrijking()"]
```

#### Woordenlijst (techniek)

Hoofdstuk 9 bevat de woorden uit het domein (GAK, XIRR, ticker, ...). Hieronder de technische termen.

| Term | Betekenis in dit project |
|---|---|
| **Frontend / backend** | Code in de browser (`templates/`, `static/`) / code op de server (`*.py`). |
| **Request / response** | Een verzoek van de browser aan de server / het antwoord daarop. |
| **Route / endpoint** | Een URL in `app.py` met een Python-functie erachter, bv. `/api/portfolio/<code>/dividend`. |
| **API** | Het geheel van routes en de vorm van hun antwoorden. |
| **HTTP-methode** | `GET` (ophalen), `POST` (insturen/wijzigen), `DELETE` (verwijderen). |
| **JSON** | Tekstformaat voor data tussen backend en frontend; `jsonify()` in Python, `res.json()` in JS. |
| **`fetch()`** | De JS-functie waarmee de frontend een request doet. |
| **SPA** | Single-page application: de portfolio-pagina is één HTML-pagina, tabbladen wisselen via `pasViewToe()`. |
| **DOM** | De boom van HTML-elementen die JS kan lezen en aanpassen. |
| **Flask** | De Python-webbibliotheek achter `app.py`. |
| **WSGI / gunicorn** | De afspraak tussen een Python-webapp en de server / de server die de app op Render draait. |
| **Orkestratie** | Code die taakfuncties in de juiste volgorde aanroept (`_upload_impl()`, `build_portfolio_response()`). |
| **Pure functie** | Invoer → uitvoer, zonder database, netwerk, DOM of globale toestand. |
| **IIFE** | Een functie die zichzelf direct uitvoert; het patroon waarmee de pure JS-modules zowel in de browser als in Node werken. |
| **SQL** | De taal waarmee je de database bevraagt en wijzigt. |
| **ORM** | Een laag die tabellen als klassen verpakt; wordt **niet** gebruikt, de SQL staat in de code. |
| **Primary key / UNIQUE** | Kolom(men) die een rij uniek maken; `UNIQUE (code, order_id)` voorkomt dubbele transacties. |
| **Cache** | Bewaarde kopie van iets dat duur is om op te halen: cachetabellen in Neon, `_basis_cache` in het geheugen. |
| **Upsert** | Invoegen, of bijwerken als de rij al bestaat (`ON CONFLICT ... DO UPDATE`); bv. `db_save_dividenden()`. |
| **Rate limit** | Een externe dienst die tijdelijk weigert omdat je te veel vraagt. |
| **Retry / backoff** | Een mislukte call opnieuw proberen / met steeds langere wachttijd. |
| **Thread / thread pool** | Parallelle uitvoering binnen één proces / een vast aantal threads dat taken deelt (`ThreadPoolExecutor`). |
| **Timeout** | Maximale wachttijd waarna een call of request wordt afgebroken (`fetchMetTimeout()`, gunicorn). |
| **Omgevingsvariabele / `.env`** | Instelling buiten de code (`DATABASE_URL`); lokaal uit `.env`, op Render uit het dashboard. |
| **unittest / `node --test`** | De testframeworks voor Python / JavaScript. |
| **Mock** | Nepversie van een functie in een test, met een vast antwoord. |
| **CI / GitHub Actions** | Automatisch testen bij elke push / de dienst van GitHub die dat doet. |

### 10.2 Leesvolgorde in 14 stappen

Van eenvoudig naar complex. Elke stap bouwt voort op de vorige. Bij "Zo lees je het" staat steeds een testbestand: tests zijn de kortste beschrijving
van wat een functie hoort te doen.

#### Stap 1 — Kleine helpers

- **Lees:** `debug_utils.py`, `transactie_utils.py`, `portfolio_admin.py`.
- **Wat doen deze bestanden:** `debug_utils.py` print logregels (`dprint()`) en meet hoe lang een stap duurt (`meet_tijd()`). `transactie_utils.py` herkent
  DeGiro's boekingsrijen voor splits (`_is_corporate_action_row()`) en zet transacties op datum én tijd op volgorde (`_sorteer_chronologisch()`).
  `portfolio_admin.py` maakt en controleert de 3-letter-codes en zoekt bij een upload het bijbehorende portfolio.
- **Waar in de stack:** backend, hulpfuncties.
- **Waarom nu:** korte bestanden die bijna niets anders importeren; je leert de schrijfstijl van het project kennen zonder de rest te hoeven snappen.
- **Wat je hier leert:** kleine, herbruikbare helpers; waarom je die in een apart bestand zet (geen circulaire imports); reguliere expressies (`is_geldige_code()`).
- **Zo lees je het:** begin met `is_geldige_code()` en open `tests/backend/test_code_validatie.py` ernaast. Lees daarna `_sorteer_chronologisch()` met
  `tests/backend/test_chronologische_sortering.py`.

#### Stap 2 — Het datamodel

- **Lees:** `db.py`: eerst `db_init()`, dan één `get_cached_*`/`save_*`-paar (bv. `db_get_cached_land_sector()` en `db_save_land_sector()`), dan `db_save_prices()` en `db_upsert_prices()`.
- **Wat doen deze bestanden:** `db.py` opent de verbinding met de database, maakt alle tabellen aan en bevat de functies die caches lezen en schrijven.
  Het is de plek waar Python en PostgreSQL elkaar raken.
- **Waar in de stack:** backend, database.
- **Waarom nu:** alle andere modules lezen of schrijven deze tabellen; als je weet wat er bewaard wordt, begrijp je de rest sneller.
- **Wat je hier leert:** hoe een tabel met primary key en `UNIQUE` eruitziet; het verschil tussen gebruikersdata en een cachetabel; `ON CONFLICT DO NOTHING`
  tegenover een upsert (`DO UPDATE`).
- **Zo lees je het:** leg `db_init()` naast de tabellen in hoofdstuk 4. Vergelijk `db_save_prices()` met `db_upsert_prices()`; `tests/backend/test_prijzen_upsert.py` bewaakt dat
  `db_upsert_prices()` echt `DO UPDATE` gebruikt.

#### Stap 3 — Pure JavaScript

- **Lees:** `static/js/menu.js`, `static/js/prognose.js`, met `tests/test_menu.js` en `tests/test_prognose.js`. Als extra: `bestandskeuze.js` en `diagnostiek.js`.
- **Wat doen deze bestanden:** `menu.js` bevat de indeling van hoofdtabs en subtabs (`MENU_GROEPEN`) en pure zoekfuncties daarop. `prognose.js` rekent uit hoe je vermogen kan groeien bij een
  gekozen rendement en inleg. Geen van beide raakt de pagina zelf aan; `app.js` en `tabs/prognose.js` gebruiken hun uitkomst.
- **Waar in de stack:** frontend, logica.
- **Waarom nu:** de makkelijkste ingang tot de frontend: puur rekenwerk, zonder DOM of netwerk, en je kunt de tests meteen zelf draaien.
- **Wat je hier leert:** wat een pure functie is; het IIFE-patroon waarmee één bestand in de browser én in Node werkt; hoe een JS-test eruitziet.
- **Zo lees je het:** begin onderaan elk bestand (`exportsObj`) om te zien wat het naar buiten geeft. Lees dan `maandRenteVanJaarPct()` en
  `berekenPrognosePad()` met de eerste tests in `tests/test_prognose.js` (bv. "10% rendement, start 1000 → 1100"). Draai
  `node --test tests/test_prognose.js tests/test_menu.js`.

#### Stap 4 — De pagina en de routes

- **Lees:** `templates/basis.html`, `start.html` en `portfolio.html` (alleen doorbladeren), de routetabel in hoofdstuk 3, dan `app.py`.
- **Wat doen deze bestanden:** `start.html` bevat het upload- en het code-formulier; `portfolio.html` het menu en een verborgen sectie per tabblad; `basis.html` wat ze delen.
  `app.py` definieert de 19 routes: welke URL's de browser kan aanroepen en welke functie antwoordt.
- **Waar in de stack:** frontend-structuur en de API-laag van de backend.
- **Waarom nu:** met deze twee bestanden heb je de plattegrond: welke schermen er zijn en welke vragen de frontend aan de backend kan stellen.
- **Wat je hier leert:** hoe de browser de server aanroept (route, methode, JSON); waarom routes dun zijn; hoe een foutantwoord eruitziet (statuscode 404/500).
- **Zo lees je het:** zoek in `portfolio.html` de `data-view`-knoppen en de bijbehorende `tab-<view>`-blokken. Lees in `app.py` eerst `api_portfolio()` (kort), dan
  `upload()` en `_upload_impl()`. `tests/backend/test_upload_route_foutafhandeling.py` laat zien wat er gebeurt als er iets misgaat.

#### Stap 5 — De ingang van alle data: de upload

- **Lees:** `upload_verwerking.py` en `find_matching_code()` in `portfolio_admin.py`.
- **Wat doen deze bestanden:** `upload_verwerking.py` leest het Excel-bestand, maakt de kolommen netjes, zoekt de Order ID's, bepaalt of dit een nieuw of
  bestaand portfolio is, zoekt tickers en schrijft de nieuwe rijen in de database. `find_matching_code()` herkent een bestaand portfolio aan
  overlappende Order ID's.
- **Waar in de stack:** backend, orkestratie en data-invoer.
- **Waarom nu:** alles wat het dashboard later toont, komt hier binnen; de rest van de code gaat ervan uit dat deze stap goed ging.
- **Wat je hier leert:** een bestandsupload verwerken met pandas; idempotent inserten (twee keer hetzelfde uploaden geeft geen dubbele rijen, dankzij
  `UNIQUE` + `ON CONFLICT`); waarom echte data eigenaardigheden heeft (de verschoven Order ID-kolom).
- **Zo lees je het:** volg de tabel in 2.1 en 2.3 stap voor stap mee in de code. `tests/backend/test_diagnostiek_upload.py` test `_create_synthetic_order_ids()`, `_meld_order_ids()` en het opslaan
  met kleine voorbeeldtabellen.

#### Stap 6 — Hoe alles aan elkaar hangt

- **Lees:** `portfolio_orchestratie.py`: `_haal_portfolio_basis()`, `analyze_transacties_kern()`, `analyze_transacties_verrijking()`. Daarna `diagnostiek.py`.
- **Wat doen deze bestanden:** `portfolio_orchestratie.py` haalt transacties en koersen op en roept de rekenmodules aan om er één JSON-antwoord van te
  maken, in twee delen (kern en verrijking). `diagnostiek.py` verzamelt tijdens één request meldingen over wat er goed of mis ging.
- **Waar in de stack:** backend, orkestratie.
- **Waarom nu:** na de invoer (stap 5) zie je hier hoe die data wordt omgezet in wat de frontend krijgt; de rekenmodules uit de volgende stappen worden
  hier aangeroepen.
- **Wat je hier leert:** orkestratie; een korte in-process cache (`_basis_cache`, 20 s) en waarom je die moet legen na een wijziging
  (`_wis_portfolio_basis_cache()`); toestand die maar één request leeft (Flask's `g` in `diagnostiek.py`).
- **Zo lees je het:** noteer welke functies `analyze_transacties_kern()` aanroept en welke sleutels het teruggegeven dict heeft; vergelijk met de tabel in
  2.4. `tests/backend/test_basis_cache.py` laat zien wanneer de cache wel en niet wordt gebruikt.

#### Stap 7 — Rekenen: rendement, XIRR, TWR en GAK

- **Lees:** `statistieken.py`, met `tests/backend/test_rendement.py` en `tests/backend/test_twr.py`.
- **Wat doen deze bestanden:** `statistieken.py` berekent alle cijfers van het Statistieken-tabblad: rendement, XIRR, TWR, gemiddelde aankoopkoers (GAK),
  gesloten posities en het jaaroverzicht. Het zijn pure functies: getallen en tabellen erin, getallen eruit.
- **Waar in de stack:** backend, logica.
- **Waarom nu:** je kent nu de vorm van de data (stap 2 en 6); hier zie je wat ermee berekend wordt, en je kunt elke uitkomst met de hand controleren.
- **Wat je hier leert:** financiële berekeningen als pure, geteste functies; testen met met de hand na te rekenen voorbeelden.
- **Zo lees je het:** begin met `bereken_totaal_rendement()` en `bereken_positie_rendement()` (een paar regels). Lees dan `bereken_holdings_en_gesloten()`
  samen met de GAK-tests in `tests/backend/test_rendement.py` (o.a. het splitvoorbeeld: 10 × €10, dan −10 en +20 → GAK €5). De woordenlijst in hoofdstuk 9 legt
  GAK, XIRR en TWR uit.

#### Stap 8 — Tijdreeksen achter de grafieken

- **Lees:** `portfolio_calc.py`, met `tests/backend/test_nog_in_bezit.py` en `tests/backend/test_per_ticker_koers_en_aankopen.py`.
- **Wat doen deze bestanden:** `portfolio_calc.py` rekent per dag uit hoeveel je portfolio waard was en hoeveel je had ingelegd, in totaal en per aandeel.
  Het corrigeert ook oude aantallen voor aandelensplitsingen. Dit zijn de lijnen in de grafieken van Home en Per aandeel.
- **Waar in de stack:** backend, logica.
- **Waarom nu:** na de losse cijfers (stap 7) zie je hoe dezelfde transacties een reeks per dag worden.
- **Wat je hier leert:** werken met pandas-tijdreeksen; waarom "geïnvesteerd" twee betekenissen heeft (zie de valkuil bij `portfolio_calc.py`); waarom
  "nog in bezit" op aantal stuks wordt bepaald.
- **Zo lees je het:** lees `compute_value_over_time()` eerst, dan `compute_per_ticker()`. De tests in `tests/backend/test_nog_in_bezit.py` tonen het randgeval van
  een volledig verkochte positie.

#### Stap 9 — Koersen en valuta

- **Lees:** `prijzen.py` en `yahoo_client.py`, met `tests/backend/test_koersen_cache.py` en `tests/backend/test_fx_caching_en_retry.py`.
- **Wat doen deze bestanden:** `prijzen.py` levert de dagkoersen in euro: uit de `prijzen`-tabel als die er al zijn, anders van Yahoo, en rekent dollars en
  ponden om. `yahoo_client.py` telt Yahoo-calls en probeert mislukte calls opnieuw.
- **Waar in de stack:** backend, data en externe bron.
- **Waarom nu:** stap 7 en 8 rekenen met koersen; hier zie je waar die vandaan komen.
- **Wat je hier leert:** hoe een cache-tabel werkt (eerst kijken wat je al hebt, alleen het ontbrekende downloaden); incrementeel verversen; retry en backoff;
  waarom er twee retry-varianten zijn.
- **Zo lees je het:** volg de vijf stappen onder "De logica van `get_prices()`" in hoofdstuk 3 in de code. Kijk daarna in `tests/backend/test_koersen_cache.py` wanneer
  een ticker `missing` of `stale` is, en in `tests/backend/test_fx_caching_en_retry.py` hoe de FX-reeks vanaf `FX_ANKER_DATUM` gecachet, ververst en
  bij een fout opnieuw geprobeerd wordt.

#### Stap 10 — Dividend: een afgerond stuk domeinlogica

- **Lees:** `dividend.py`, met `tests/backend/test_dividend.py`.
- **Wat doen deze bestanden:** `dividend.py` leest het rekeningoverzicht van DeGiro, zoekt per dividenduitkering het echte bedrag in euro (via de
  bijbehorende valutaconversie) en vat alle dividenden samen voor het Dividend-tabblad.
- **Waar in de stack:** backend, logica en data.
- **Waarom nu:** een compleet onderdeel met een duidelijk begin (Excel) en eind (JSON), dat je los van de rest kunt begrijpen.
- **Wat je hier leert:** omgaan met rommelige echte data (samengevoegde kolomkoppen, gepoolde conversies); liever `None` dan een gok; een upsert om oude,
  foute rijen te kunnen overschrijven.
- **Zo lees je het:** lees eerst `verwerk_rekeningoverzicht()` (inlezen), dan `verwerk_rekeningoverzicht_df()` (het rekenwerk). In `tests/backend/test_dividend.py` volg je
  eerst `test_usd_dividend_gebruikt_gekoppelde_valuta_creditering` (één uitkering, één conversie) en daarna
  `test_twee_dividenden_zelfde_dag_gepoold_in_een_conversie`.

#### Stap 11 — De verrijking: land, sector, bedrijven, overlap

- **Lees:** `ticker_classificatie.py`, `etf_holdings_provider.py`, `portfolio_verdeling.py`.
- **Wat doen deze bestanden:** `ticker_classificatie.py` bepaalt of een ticker een ETF of aandeel is en zoekt land, sector en holdings op (met cache).
  `etf_holdings_provider.py` downloadt de volledige holdingslijst bij iShares en VanEck. `portfolio_verdeling.py` telt dat alles op tot de verdelingen per
  land, sector en bedrijf, en de overlap tussen ETF's.
- **Waar in de stack:** backend, logica en externe bronnen.
- **Waarom nu:** dit is de "verrijking" uit stap 6; met de koerslogica van stap 9 in je hoofd is het cachepatroon herkenbaar.
- **Wat je hier leert:** een externe bron met een terugvaloptie (aanbieder → Yahoo top-10 → `"Unknown"`); parallel een cache opwarmen; aggregeren met een
  "Overig"-bucket.
- **Zo lees je het:** begin bij `compute_land_sector_verdeling()` en volg de aanroepen naar `get_etf_holdings()` en `fetch_provider_holdings()`.
  `tests/backend/test_etf_holdings_bron.py` laat de parsers werken op kleine voorbeeldgegevens (ook met een verkeerde `locale`); `tests/backend/test_land_overig.py` en `tests/backend/test_etf_overlap.py` tonen de
  optelregels.

#### Stap 12 — Ticker-matching en -controle

- **Lees:** `ticker_matching.py` → `ticker_prijscheck.py` → `ticker_zekerheid.py`, met `tests/backend/test_ticker_zoeken.py` en `tests/backend/test_snelle_prijscheck.py`.
- **Wat doen deze bestanden:** `ticker_matching.py` zoekt bij een DeGiro-product de Yahoo-ticker. `ticker_prijscheck.py` vergelijkt Yahoo's koers met de
  prijs die je bij DeGiro betaalde. `ticker_zekerheid.py` combineert die twee tot een oordeel ("zeker", "onzeker") en vervangt een foute ticker soms
  automatisch.
- **Waar in de stack:** backend, logica en externe bronnen.
- **Waarom nu:** het ingewikkeldste deel; het gebruikt koersen (stap 9), classificatie (stap 11) en threads. Nu heb je alle bouwstenen.
- **Wat je hier leert:** een beslisboom met escalatie (eerst goedkoop controleren, pas bij twijfel duur); parallel werk met een thread pool; hoe je een
  onbetrouwbare externe bron met een tweede signaal (prijs, OpenFIGI) controleert.
- **Zo lees je het:** begin bij `find_ticker_detailed()` (de volgorde staat in hoofdstuk 3), dan `vergelijk_prijs_op_datum()` en `_prijscheck_is_probleem()`, en
  tot slot het escalatietrapje in `find_ticker_met_snelle_prijscheck()`. `tests/backend/test_snelle_prijscheck.py` laat de treden zien;
  `tests/backend/test_escalatiepoort_dagrange.py` toont wanneer er wel en niet geëscaleerd wordt.

#### Stap 13 — De frontend: `start.js`, `app.js` en de tabbladen

- **Lees:** eerst `static/js/start.js` (kort), `navigatie.js` en `overdracht.js`; dan `static/js/app.js` in deze volgorde: `startPortfolioPagina()`, `toonDashboard()`, `wisselView()`/`pasViewToe()`, `laadVerrijking()`. Daarna `gedeeld/grafiek.js` (`updateChart()`) en `gedeeld/tabel.js`,
  en de tabbladen van klein naar groot: `tabs/portfolio.js`, `tabs/dividend.js`, `tabs/transacties.js`, tenslotte `tabs/ticker_zekerheid.js` (`toonInstellingenTicker()`).
- **Wat doen deze bestanden:** `start.js` verstuurt de formulieren en stuurt door naar de portfolio-pagina; `app.js` start die pagina op en wisselt de tabbladen; elk bestand in `tabs/` is wat de gebruiker op één tabblad ziet en doet: data ophalen met `fetch()` en
  grafieken en tabellen tekenen met Chart.js en de helpers uit `gedeeld/`.
- **Waar in de stack:** frontend, gedrag.
- **Waarom nu:** de frontend is samen zo'n 3000 regels, verdeeld over kleine bestanden; met de backend in je hoofd herken je elke JSON-sleutel die ze gebruiken.
- **Wat je hier leert:** event-gestuurd programmeren (code die reageert op klikken); asynchrone code (`async`/`await`); globale toestand bijhouden en resetten;
  hoe je gelijktijdige requests begrenst (`voerMetConcurrencyLimietUit()`).
- **Zo lees je het:** lees via de datastroom, niet van boven naar beneden: volg één klik van menuknop tot grafiek. Gebruik de tabel in 5.4 als index.
  Hoe de losse bestanden elkaar zonder `import` vinden staat in 5.1; voor een nieuw tabblad staat het stappenplan in 8.1.

#### Stap 14 — Uiterlijk en kleine onderdelen

- **Lees:** `static/css/style.css` en `static/js/infotip.js`.
- **Wat doen deze bestanden:** `style.css` bepaalt hoe alles eruitziet (lees eerst de inhoudsopgave bovenaan), en maakt van de hoofdtabs op een smal scherm een onderbalk. `infotip.js` maakt
  van elke `<span class="infoTip">` een (i)-knop met uitleg.
- **Waar in de stack:** frontend, uiterlijk en gedrag.
- **Waarom nu:** los van de rest te begrijpen; nuttig zodra je zelf iets aan de pagina wilt veranderen.
- **Wat je hier leert:** responsive design met `@media`-regels; een component dat met muis, toetsenbord én tik werkt (toegankelijkheid).
- **Zo lees je het:** zoek in `style.css` op `@media (max-width: 768px)` en kijk wat er met `.hoofdTabs` gebeurt. `tests/test_menu.js` controleert een paar
  van die mobiele regels door `style.css` en `basis.html` als tekst te lezen. Lees in `infotip.js` de event-listeners van onder naar boven.

### 10.3 Tip

Gebruik bij het lezen de tabellen in hoofdstuk 3 als kaart en zoek in de code op de naam van de functie. Weet je niet wat een functie hoort te doen,
open dan eerst het testbestand uit 7.2: een test is een klein, concreet voorbeeld van verwacht gedrag. De docstrings en comments in de code zijn bewust
kort; het waarom van valkuilen staat in CLAUDE.md ("Achtergrond en eigenaardigheden") en de details staan in dit document. Onbekende woorden: de
technische termen staan in 10.1, de domeintermen in hoofdstuk 9.

## Stand van zaken: CLAUDE.md en de code

Gecontroleerd op 30-09-2026, na stap 4 (reset per tab-bestand, `maakTabWisselaar()`, geen inline stijlen meer in `portfolio.html`, `style.css` in secties): CLAUDE.md en de code komen overeen. CLAUDE.md is bewust een korte regelset voor Claude Code; de
uitgebreide beschrijving staat alleen in dit document.

**Bewust anders**

- **`.gitignore` bevat `CLAUDE.md`**: CLAUDE.md is een lokaal bestand en staat daarom niet in git. Git kan het dus ook niet herstellen; maak zelf een kopie
  vóór grote wijzigingen.

## Onzekerheden en open vragen

Dingen die ik niet met zekerheid uit de code kon vaststellen, of waar mijn beschrijving op aannames berust:

1. **Productie-opstart:** het gunicorn-startcommando, het aantal workers en de timeout staan niet in de repo. `gunicorn app:app` is een aanname op grond van de bestandsnaam.
2. **Split-detectie:** `compute_split_adjusted_shares()` schaalt op basis van *positieve* corporate-action-rijen. In `tests/backend/test_rendement.py` is het voorbeeld-splitpatroon voor de GAK juist een *negatieve* DEG-rij (−10) plus een conversierij (+20).
   Of de schaling voor dat patroon iets doet of wordt overgeslagen, kan ik niet uit de code alleen afleiden; de enige getaltest (`test_diagnostiek_laden.py`, factor 4) gebruikt een positieve corporate-action-rij.
3. **`auto_adjust=True` en de `prijzen`-cache:** koersen zijn dividend- en splitgecorrigeerd op het moment van downloaden, en historische rijen worden nooit overschreven. Of dat na latere dividenden zichtbaar inconsistent wordt, weet ik niet.
4. **Valuta's:** alleen USD, GBP en GBp worden naar EUR omgerekend. Wat er in de praktijk met een ticker in een andere valuta gebeurt (vermoedelijk: behandeld als EUR), heb ik niet getest.
5. **Yahoo-timeouts:** er staat nergens een expliciete timeout op yfinance-calls; wat yfinance zelf doet, weet ik niet.
6. **Hoe DeGiro's exportformaat precies is:** kolomnamen (`Waarde EUR`, `Wisselkoers`, de lange kostenkolom), positie-afhankelijke hernoemingen in het rekeningoverzicht (`Unnamed: 8`/`10`) en het Order-ID-gedrag beschrijf ik zoals de code ze verwacht, niet zoals DeGiro ze nu levert.
7. **Diepte van mijn lezing:** de Python-modules heb ik volledig gelezen. De frontend-scripts (`app.js`, `gedeeld/` en `tabs/`, samen circa 3000 regels) heb ik gelezen via de datastroom en de belangrijkste functies; enkele opmaakfuncties (`maakPositieTabel()`, `maakGeslotenPositiesTabel()`,
   `maakJarenTabel()`, `maakTickerZekerheidKaart()`, `vulPrognoseFormulier()`, ...) beschrijf ik op grond van naam, commentaar en aanroeper, niet regel voor regel. De Python-testbestanden (59 bestanden, 532 tests op 30-09-2026, zie 7.1) zijn niet allemaal regel voor regel doorgelezen; de koppeling test ↔ module in 7.2 is gebaseerd op imports, bestandsnamen en docstrings.
8. **Niet uitgevoerd:** de database-delen van de **[DB]**-Python-tests (12 bestanden, ze hebben een lokale database nodig) en de app zelf. Wel gedraaid: de JS-tests (145 geslaagd) en de Python-suite zonder database (494 geslaagd, 38 overgeslagen, 30-09-2026).
9. **Mermaid-diagram:** ik heb het niet kunnen renderen; de syntax is met zorg geschreven maar niet visueel gecontroleerd.
