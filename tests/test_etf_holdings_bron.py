"""
Unit tests voor ETF_HOLDINGS_BRON en de bijbehorende parsers, met de nadruk
op de locale-bug die ontdekt werd bij het toevoegen van IWDA.AS/IMAE.AS/
EMIM.AS/GDX.L: de ishares.com/nl/-site en VanEck se Nederlandse site geven
getallen in Nederlands formaat (punt=duizendtal, komma=decimaal) terug,
i.t.t. de al werkende blackrock.com-bron (Engels formaat) — zonder
locale-bewuste parsing werd bv. gewicht '5,25%' stilzwijgend 525 i.p.v.
5.25.

Draait geheel offline: kleine, handgemaakte voorbeeld-CSV/XLSX-content in
de test zelf, geen echte downloads (dat is test_holdings_url()'s taak, een
losse handmatige verificatiestap, geen onderdeel van deze geautomatiseerde
suite).
"""
import io
import sys
import os
import unittest
from unittest.mock import patch, Mock

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import (
    ETF_HOLDINGS_BRON,
    _PROVIDER_PARSERS,
    _parse_ishares_holdings,
    _parse_vaneck_holdings,
    _parse_percentage_waarde,
    _vertaal_land_nl,
    _land_via_isin,
    _dedupliceer_holdings,
    NL_LAND_VERTALING,
)

NIEUWE_TICKERS = [
    "IWDA.AS", "IMAE.AS", "EMIM.AS", "CNDX.AS", "GDX.L",
    "EUEA.AS", "TDT.AS", "VE6I.DE", "IS3N.DE", "G2X.DE",
]

# (ticker, isin-zusje) — zelfde ISIN, andere beursnotering van hetzelfde
# fonds, dus bewust dezelfde ETF_HOLDINGS_BRON-entry (zelfde bron-URL/
# locale/provider) i.p.v. een eigen bron opzoeken.
ISIN_ZUSJES = [
    ("IS3N.DE", "EMIM.AS"),  # beide IE00BKM4GZ66, iShares Core MSCI EM IMI
    ("G2X.DE", "GDX.L"),     # beide IE00BQQP9F84, VanEck Gold Miners
]


class TestEtfHoldingsBronFormaat(unittest.TestCase):
    def test_alle_nieuwe_tickers_hebben_een_entry(self):
        for ticker in NIEUWE_TICKERS:
            self.assertIn(ticker, ETF_HOLDINGS_BRON, f"{ticker} ontbreekt in ETF_HOLDINGS_BRON")

    def test_elke_entry_heeft_hetzelfde_basisformaat_als_cspx(self):
        cspx_sleutels = set(ETF_HOLDINGS_BRON["CSPX.AS"].keys())
        for ticker in NIEUWE_TICKERS:
            entry = ETF_HOLDINGS_BRON[ticker]
            self.assertIsInstance(entry, dict, f"{ticker}: moet een dict zijn, geen losse string")
            self.assertIn("provider", entry)
            self.assertIn("url", entry)
            # locale is optioneel (default "en"), maar als 'ie er is moet
            # de rest van de verplichte sleutels van CSPX.AS er nog steeds
            # ook staan.
            self.assertTrue(cspx_sleutels <= set(entry.keys()))

    def test_provider_is_een_bekende_geregistreerde_parser(self):
        # Regressietest voor de GDX.L-bug die hier gevonden werd: "VanEck"
        # (met hoofdletters) matcht niet met de lowercase sleutels in
        # _PROVIDER_PARSERS, dus fetch_provider_holdings() zou stilzwijgend
        # "onbekende provider" loggen en op de yfinance-fallback
        # terugvallen zonder dat duidelijk is waarom.
        for ticker in NIEUWE_TICKERS:
            provider = ETF_HOLDINGS_BRON[ticker]["provider"]
            self.assertIn(provider, _PROVIDER_PARSERS, f"{ticker}: provider '{provider}' niet in _PROVIDER_PARSERS")

    def test_url_is_geen_lege_string_en_bevat_geen_asofdate(self):
        for ticker in NIEUWE_TICKERS:
            url = ETF_HOLDINGS_BRON[ticker]["url"]
            self.assertTrue(url.startswith("https://"))
            # Bekende valkuil (zie projectnotities): een hardcoded asOfDate
            # breekt de blackrock.com-link na een dag.
            self.assertNotIn("asOfDate", url)

    def test_vanguard_fondsen_bewust_niet_toegevoegd(self):
        # VWCE.AS/VUSA.AS: Vanguard's holdings-download gaat via een
        # GraphQL-API (complexe request-body), niet via een simpele GET-URL
        # zoals iShares/VanEck — bewust niet geïmplementeerd (te fragiel/
        # complex voor de meerwaarde). Regressietest zodat dit niet per
        # ongeluk als "vergeten" wordt gezien en zonder overleg toegevoegd.
        self.assertNotIn("VWCE.AS", ETF_HOLDINGS_BRON)
        self.assertNotIn("VUSA.AS", ETF_HOLDINGS_BRON)


# --- Voorbeeld-CSV's, exact de structuur van de twee echte iShares-bronnen ---

CSV_ISHARES_EN = (
    'Fund Holdings as of,"26/Aug/2026"\n'
    "\n"
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Price,Location,Exchange,Market Currency\n"
    '"NVDA","NVIDIA","Information Technology","Equity","12,171,389,673.74","7.68","12,171,389,673.74","58,052,989.00","209.66","United States","NASDAQ","USD"\n'
    '"ASML","ASML HOLDING","Information Technology","Equity","1,000,000.00","2.50","1,000,000.00","1,000.00","500.00","Netherlands","AMS","EUR"\n'
).encode("utf-8")

CSV_ISHARES_NL = (
    'Fund Holdings as of,"26/aug/2026"\n'
    " \n"
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Price,Location,Exchange,Market Currency\n"
    '"NVDA","NVIDIA","IT","Aandelen","8.021.108.962,68","5,25","8.021.108.962,68","38.257.698,00","209,66","Verenigde Staten","NASDAQ","USD"\n'
    '"ASML","ASML HOLDING","IT","Aandelen","1.000.000,00","2,50","1.000.000,00","1.000,00","500,00","Nederland","AMS","EUR"\n'
).encode("utf-8")


class TestIsinZusjesZelfdeBron(unittest.TestCase):
    # Regressietest: voorkomt dat IS3N.DE/EMIM.AS of G2X.DE/GDX.L later per
    # ongeluk uit elkaar getrokken worden (bv. een van de twee wordt
    # aangepast bij een site-update, de ander niet), terwijl het toch
    # letterlijk hetzelfde fonds/dezelfde CSV/XLSX-bron is.
    def test_isin_zusjes_hebben_identieke_bron_entry(self):
        for ticker, zusje in ISIN_ZUSJES:
            self.assertEqual(
                ETF_HOLDINGS_BRON[ticker], ETF_HOLDINGS_BRON[zusje],
                f"{ticker} en {zusje} zijn hetzelfde fonds (zelfde ISIN), "
                f"maar hebben een verschillende ETF_HOLDINGS_BRON-entry",
            )


class TestParseIsharesHoldingsLocale(unittest.TestCase):
    def test_engelse_bron_geeft_correcte_gewichten_en_land(self):
        holdings = _parse_ishares_holdings(CSV_ISHARES_EN, locale="en")
        self.assertEqual(len(holdings), 2)
        nvda = next(h for h in holdings if h["naam"] == "NVIDIA")
        self.assertAlmostEqual(nvda["gewicht"], 7.68)
        self.assertEqual(nvda["land"], "United States")

    def test_nederlandse_bron_geeft_correcte_gewichten_en_vertaald_land(self):
        # De kern-regressietest: '5,25' moet 5.25 worden, NIET 525 (comma
        # als duizendtal-scheidingsteken weggehaald) — en 'Verenigde
        # Staten' moet vertaald worden naar 'United States' zodat dit niet
        # als aparte taartpunt naast CSPX.AS' 'United States' verschijnt.
        holdings = _parse_ishares_holdings(CSV_ISHARES_NL, locale="nl")
        self.assertEqual(len(holdings), 2)
        nvda = next(h for h in holdings if h["naam"] == "NVIDIA")
        self.assertAlmostEqual(nvda["gewicht"], 5.25)
        self.assertEqual(nvda["land"], "United States")
        asml = next(h for h in holdings if h["naam"] == "ASML HOLDING")
        self.assertEqual(asml["land"], "Netherlands")

    def test_nederlandse_bron_met_verkeerde_locale_illustreert_de_oorspronkelijke_bug(self):
        # Documenteert WAAROM locale-bewuste parsing nodig is: zonder de
        # fix (Nederlandse data met de Engelse instellingen gelezen) werd
        # '5,25' stilzwijgend 525 — een gewichtensom van bijna 10000%
        # i.p.v. ~100%, pas zichtbaar na expliciet testen.
        holdings = _parse_ishares_holdings(CSV_ISHARES_NL, locale="en")
        nvda = next(h for h in holdings if h["naam"] == "NVIDIA")
        self.assertAlmostEqual(nvda["gewicht"], 525.0)  # de bug, niet het gewenste gedrag


class TestVertaalLandNl(unittest.TestCase):
    def test_bekende_vertaling(self):
        self.assertEqual(_vertaal_land_nl("Verenigde Staten"), "United States")
        self.assertEqual(_vertaal_land_nl("Zuid-Korea"), "South Korea")

    def test_onbekende_naam_blijft_onvertaald_ipv_unknown(self):
        self.assertEqual(_vertaal_land_nl("Absoluut Nergensland"), "Absoluut Nergensland")

    def test_alle_landen_uit_de_echte_iwda_imae_emim_data_zijn_gedekt(self):
        # Vastgelegd tijdens het testen van de echte bronnen (test_holdings_url),
        # zodat een toekomstige wijziging in NL_LAND_VERTALING niet per
        # ongeluk een van deze weglaat.
        verwacht = [
            "-", "Australië", "België", "Brazilië", "Canada", "Chili", "China",
            "Colombia", "Denemarken", "Duitsland", "Egypte", "Europese Unie",
            "Filipijnen", "Finland", "Frankrijk", "Griekenland", "Hong Kong",
            "Hongarije", "Ierland", "India", "Indonesië", "Israël", "Italië",
            "Japan", "Koeweit", "Maleisië", "Mexico", "Nederland", "Nieuw-Zeeland",
            "Noorwegen", "Oostenrijk", "Peru", "Polen", "Portugal", "Qatar",
            "Rusland", "Saoedi-Arabië", "Singapore", "Spanje", "Taiwan",
            "Thailand", "Tsjechië", "Turkije", "Verenigd Koninkrijk",
            "Verenigde Arabische Emiraten", "Verenigde Staten", "Zuid-Afrika",
            "Zuid-Korea", "Zweden", "Zwitserland",
        ]
        for land in verwacht:
            self.assertIn(land, NL_LAND_VERTALING, f"'{land}' ontbreekt in NL_LAND_VERTALING")


class TestLandViaIsin(unittest.TestCase):
    def test_bekende_isin_prefixes(self):
        self.assertEqual(_land_via_isin("CA0084741085"), "Canada")
        self.assertEqual(_land_via_isin("US6516391066"), "United States")
        self.assertEqual(_land_via_isin("GB00BRXH2664"), "United Kingdom")

    def test_ongeldige_isin_geeft_unknown_zonder_crash(self):
        self.assertEqual(_land_via_isin(""), "Unknown")
        self.assertEqual(_land_via_isin(None), "Unknown")
        self.assertEqual(_land_via_isin("X"), "Unknown")


def _maak_vaneck_xlsx(header, rows):
    """Bouwt een kleine in-memory XLSX met dezelfde vorm als VanEck's
    echte export: titelregel, lege regel, dan de kolomkoppen (skiprows=2)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Alle fondsposities per 27-08-2026"])
    ws.append([])
    ws.append(list(header))
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestParseVaneckHoldings(unittest.TestCase):
    def test_geen_land_kolom_land_via_isin_afgeleid(self):
        # Exact de vorm van het echte GDX-bestand: geen Land/Country-kolom,
        # kolomnaam 'Naam positie' (niet 'Naam'/'Name'), gewicht als
        # percentage-string met Nederlandse komma ('10,74%').
        content = _maak_vaneck_xlsx(
            header=["Aantal", "Naam positie", "Ticker", "ISIN", "Aandelen", "Marktwaarde", "% van beheerd vermogen"],
            rows=[
                [1, "Agnico Eagle Mines Ltd", "AEM US", "CA0084741085", 100, "$ 1.00", "10,74%"],
                [2, "Newmont Corp", "NEM US", "US6516391066", 200, "$ 2.00", "10,69%"],
            ],
        )
        holdings = _parse_vaneck_holdings(content, locale="nl")
        self.assertEqual(len(holdings), 2)
        agnico = next(h for h in holdings if "Agnico" in h["naam"])
        self.assertAlmostEqual(agnico["gewicht"], 10.74)
        self.assertEqual(agnico["land"], "Canada")
        newmont = next(h for h in holdings if "Newmont" in h["naam"])
        self.assertEqual(newmont["land"], "United States")

    def test_expliciete_land_kolom_heeft_voorrang_boven_isin_afleiding(self):
        content = _maak_vaneck_xlsx(
            header=["Naam", "ISIN", "Land", "% of Net Assets"],
            rows=[
                ["Test Holding", "CA0084741085", "Custom Land Value", "5.0"],
            ],
        )
        holdings = _parse_vaneck_holdings(content, locale="en")
        self.assertEqual(len(holdings), 1)
        # 'Custom Land Value' (expliciete kolom) i.p.v. 'Canada' (wat de
        # ISIN-afleiding zou geven) — bewijst dat de land-kolom voorrang heeft.
        self.assertEqual(holdings[0]["land"], "Custom Land Value")

    def test_holding_name_kolom_zoals_tdt_as(self):
        # Regressietest voor TDT.AS (VanEck AEX UCITS ETF, Engelstalige
        # VanEck-NL-site): kolomkop is hier "Holding Name" i.p.v. de eerder
        # geziene "Naam positie"/"Naam"/"Name"/"Holding" — zonder deze
        # variant in naam_kolom werden alle rijen stilzwijgend overgeslagen
        # (naam=None -> pd.isna-check faalt de rij), dus 0 holdings i.p.v.
        # een foutmelding.
        content = _maak_vaneck_xlsx(
            header=["Number", "Holding Name", "Ticker", "ISIN", "Shares", "Market Value", "% of Net Assets"],
            rows=[
                [1, "Shell Plc", "SHEL LN", "GB00BP6MXD84", 100, "$ 1.00", "16.10%"],
                [2, "Asml Holding Nv", "ASML NA", "NL0010273215", 200, "$ 2.00", "13.09%"],
            ],
        )
        holdings = _parse_vaneck_holdings(content, locale="en")
        self.assertEqual(len(holdings), 2)
        shell = next(h for h in holdings if h["naam"] == "Shell Plc")
        self.assertAlmostEqual(shell["gewicht"], 16.10)
        self.assertEqual(shell["land"], "United Kingdom")


class TestFetchProviderHoldingsFallback(unittest.TestCase):
    def test_ticker_zonder_bron_geeft_none_zonder_crash(self):
        # VWCE.AS/VUSA.AS (en elke andere ticker zonder ETF_HOLDINGS_BRON-
        # entry) moeten hier gewoon None opleveren, zodat de aanroeper
        # (get_etf_holdings) netjes terugvalt op yfinance-top-10 i.p.v. een
        # KeyError/crash.
        from analysis import fetch_provider_holdings
        self.assertIsNone(fetch_provider_holdings("VWCE.AS"))
        self.assertIsNone(fetch_provider_holdings("VUSA.AS"))
        self.assertIsNone(fetch_provider_holdings("EEN.TICKER.DIE.NIET.BESTAAT"))


class TestFetchProviderHoldingsIsinZusjes(unittest.TestCase):
    # Draait fetch_provider_holdings() echt end-to-end (met gemockte HTTP-
    # respons) voor de twee nieuwe tickers, i.p.v. alleen te vertrouwen op
    # de generieke provider/locale-parsertests: bevestigt dat de bestaande
    # _parse_ishares_holdings()/_parse_vaneck_holdings() zonder enige
    # aanpassing werken voor IS3N.DE/G2X.DE, precies omdat ze dezelfde
    # provider+locale+parser-route volgen als hun ISIN-zusje EMIM.AS/GDX.L.
    @patch("analysis.requests.get")
    def test_is3n_de_gebruikt_ishares_nl_parser_zoals_emim(self, mock_get):
        from analysis import fetch_provider_holdings

        mock_response = Mock()
        mock_response.content = CSV_ISHARES_NL
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        holdings = fetch_provider_holdings("IS3N.DE")

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, ETF_HOLDINGS_BRON["EMIM.AS"]["url"])
        self.assertEqual(len(holdings), 2)
        nvda = next(h for h in holdings if h["naam"] == "NVIDIA")
        self.assertAlmostEqual(nvda["gewicht"], 5.25)
        self.assertEqual(nvda["land"], "United States")

    @patch("analysis.requests.get")
    def test_g2x_de_gebruikt_vaneck_nl_parser_zoals_gdx(self, mock_get):
        from analysis import fetch_provider_holdings

        content = _maak_vaneck_xlsx(
            header=["Aantal", "Naam positie", "Ticker", "ISIN", "Aandelen", "Marktwaarde", "% van beheerd vermogen"],
            rows=[
                [1, "Agnico Eagle Mines Ltd", "AEM US", "CA0084741085", 100, "$ 1.00", "10,74%"],
                [2, "Newmont Corp", "NEM US", "US6516391066", 200, "$ 2.00", "10,69%"],
            ],
        )
        mock_response = Mock()
        mock_response.content = content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        holdings = fetch_provider_holdings("G2X.DE")

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, ETF_HOLDINGS_BRON["GDX.L"]["url"])
        self.assertEqual(len(holdings), 2)
        agnico = next(h for h in holdings if "Agnico" in h["naam"])
        self.assertAlmostEqual(agnico["gewicht"], 10.74)
        self.assertEqual(agnico["land"], "Canada")


class TestDedupliceerHoldings(unittest.TestCase):
    def test_dubbele_naam_wordt_samengevoegd_met_opgeteld_gewicht(self):
        # Regressietest voor de EMIM.AS-crash: 'INDUSTRIAL AND COMMERCIAL
        # BANK OF' kwam 2x voor (A- en H-aandelen onder dezelfde afgekapte
        # naam) -> etf_holdings' primary key (etf_ticker, holding_naam)
        # kan dat niet opslaan zonder eerst samen te voegen.
        holdings = [
            {"naam": "ICBC", "gewicht": 1.0, "land": "China", "sector": "Financials"},
            {"naam": "ICBC", "gewicht": 0.5, "land": "China", "sector": "Financials"},
            {"naam": "ASML", "gewicht": 3.0, "land": "Netherlands", "sector": "Technology"},
        ]
        result = _dedupliceer_holdings(holdings)
        self.assertEqual(len(result), 2)
        icbc = next(h for h in result if h["naam"] == "ICBC")
        self.assertAlmostEqual(icbc["gewicht"], 1.5)

    def test_veel_duplicaten_zoals_inr_usd(self):
        holdings = [{"naam": "INR/USD", "gewicht": 0.1, "land": "Unknown", "sector": None} for _ in range(29)]
        result = _dedupliceer_holdings(holdings)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["gewicht"], 2.9, places=6)

    def test_geen_duplicaten_blijft_ongewijzigd(self):
        holdings = [
            {"naam": "A", "gewicht": 1.0, "land": "X", "sector": None},
            {"naam": "B", "gewicht": 2.0, "land": "Y", "sector": None},
        ]
        self.assertEqual(_dedupliceer_holdings(holdings), holdings)


class TestParsePercentageWaarde(unittest.TestCase):
    def test_kant_en_klaar_getal(self):
        self.assertAlmostEqual(_parse_percentage_waarde(7.68, locale="en"), 7.68)

    def test_nederlandse_percentage_string(self):
        self.assertAlmostEqual(_parse_percentage_waarde("10,74%", locale="nl"), 10.74)

    def test_engelse_string_zonder_procentteken(self):
        self.assertAlmostEqual(_parse_percentage_waarde("7.68", locale="en"), 7.68)


if __name__ == "__main__":
    unittest.main()
