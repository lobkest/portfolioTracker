"""
Unit tests voor de fix "GAK gebruikt verkeerde kolom (Totaal EUR i.p.v.
Waarde EUR)" (zie CLAUDE.md): de aankoop-kant van de GAK-berekening moet
uitgaan van de kale Waarde EUR (aantal x koers, zonder AutoFX/
transactiekosten), niet van Totaal EUR (dat kosten meetelt en de GAK
structureel te hoog maakt). Reproduceert de EMIM-casus uit een echte
DEGIRO-upload (ISIN IE00BKM4GZ66, 29 stuks, GAK 48,161034 volgens DEGIRO
zelf, was 48,37 in de app vóór deze fix).

Draait geheel offline: geen database, geen yfinance-calls.
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import bereken_holdings_en_gesloten, compute_per_ticker


class TestGakGebruiktWaardeEurNietTotaalEur(unittest.TestCase):
    def _transactie(self, datum, tijd, aantal, koers, waarde_eur, totaal_eur, ticker="TEST.AS"):
        return {
            "datum": pd.Timestamp(datum), "tijd": tijd, "ticker": ticker,
            "aantal": aantal, "koers": koers,
            "waarde_eur": waarde_eur, "totaal_eur": totaal_eur,
            "adj_aantal": aantal, "product": ticker, "beurs": "EAM",
        }

    def test_gak_gebruikt_waarde_eur_niet_totaal_eur(self):
        """Reproductie van de EMIM-casus: GAK moet 48.161034 zijn (o.b.v.
        Waarde EUR), niet 48.37 (o.b.v. Totaal EUR)."""
        rijen = [
            self._transactie("2026-06-17", "13:39:00", 12, 48.923, -587.076, -588.076),
            self._transactie("2026-06-17", "13:41:00", -12, 48.901, 586.812, 585.812),
            self._transactie("2026-06-17", "13:42:00", 12, 48.885, -586.620, -589.620),
            self._transactie("2026-08-31", "09:04:00", 17, 47.650, -810.050, -813.050),
        ]
        df = pd.DataFrame(rijen)
        open_posities, _ = bereken_holdings_en_gesloten(df)
        self.assertAlmostEqual(open_posities["TEST.AS"]["aantal"], 29.0)
        self.assertAlmostEqual(open_posities["TEST.AS"]["gak"], 48.161034, places=5)
        self.assertNotAlmostEqual(open_posities["TEST.AS"]["gak"], 48.37, places=2)

    def test_gak_valt_terug_op_totaal_eur_zonder_waarde_eur_kolom(self):
        """Oude rijen zonder waarde_eur (nog niet gebackfilld) mogen niet
        crashen; GAK gebruikt dan totaal_eur (huidig gedrag) als fallback."""
        rijen = [{
            "datum": pd.Timestamp("2025-08-21"), "tijd": "10:00:00", "ticker": "TEST.AS",
            "aantal": 2, "koers": 585.142, "totaal_eur": -1173.284,
            "adj_aantal": 2, "product": "TEST.AS", "beurs": "EAM",
            # waarde_eur ontbreekt bewust
        }]
        df = pd.DataFrame(rijen)
        open_posities, _ = bereken_holdings_en_gesloten(df)
        self.assertAlmostEqual(open_posities["TEST.AS"]["gak"], 586.642, places=3)

    def test_verkoopkant_blijft_ongewijzigd_op_totaal_eur(self):
        """gerealiseerd_eur/gemiddelde_verkoopkoers moeten NIET meeveranderen
        door deze fix — expliciet bewaakt zodat een toekomstige refactor dit
        niet per ongeluk ook omzet."""
        rijen = [
            self._transactie("2025-03-25", "10:00:00", 2, 101.20, -202.400, -205.400),
            self._transactie("2025-07-28", "10:00:00", -2, 104.154, 208.308, 206.308),
        ]
        df = pd.DataFrame(rijen)
        _, gesloten = bereken_holdings_en_gesloten(df)
        # verkoopopbrengst blijft o.b.v. totaal_eur (206.308), niet waarde_eur (208.308)
        self.assertAlmostEqual(
            gesloten["TEST.AS"]["gemiddelde_verkoopkoers"], 206.308 / 2, places=3
        )
        self.assertNotAlmostEqual(
            gesloten["TEST.AS"]["gemiddelde_verkoopkoers"], 208.308 / 2, places=3
        )

    def test_compute_per_ticker_gebruikt_ook_waarde_eur(self):
        """Zelfde fix moet ook in compute_per_ticker() (per-aandeel-grafiek)
        zitten — niet alleen in bereken_holdings_en_gesloten()."""
        rijen = [
            self._transactie("2026-06-17", "13:39:00", 12, 48.923, -587.076, -588.076),
            self._transactie("2026-06-17", "13:41:00", -12, 48.901, 586.812, 585.812),
            self._transactie("2026-06-17", "13:42:00", 12, 48.885, -586.620, -589.620),
            self._transactie("2026-08-31", "09:04:00", 17, 47.650, -810.050, -813.050),
        ]
        df = pd.DataFrame(rijen)
        price_data = pd.DataFrame(
            {"TEST.AS": [48.9, 47.65]},
            index=[pd.Timestamp("2026-06-17"), pd.Timestamp("2026-08-31")],
        )
        result = compute_per_ticker(df, price_data)
        # eindstand geinvesteerd = GAK (48.161034) x 29 stuks = 1396.67
        self.assertAlmostEqual(result["TEST.AS"]["geinvesteerd"][-1], 1396.67, places=2)


if __name__ == "__main__":
    unittest.main()
