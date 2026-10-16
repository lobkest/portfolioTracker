"""Laadbalk deel 2: tellers, parallelle koersdownload in vaste volgorde, en de stromen van bijwerken en ophalen
(gemockt, geen database)."""
import json
import os
import sys
import time
import unittest
from io import BytesIO
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import voortgang
from voortgang import (
    bereken_procent, meld_teller, Voortgang,
    STAPPEN_BIJWERKEN, STAPPEN_OPHALEN, STAPPEN_OPHALEN_ZONDER_HERBEPALEN,
)

STAPPEN = (("a", "A", 10), ("b", "B", 30), ("c", "C", 60))


def _events(v):
    events = []
    while not v.events.empty():
        events.append(v.events.get_nowait())
    return events


class TestBerekenProcentMetTeller(unittest.TestCase):
    def test_halverwege_en_begrenzing(self):
        self.assertEqual(bereken_procent(STAPPEN, "b", 0, 4), 10)
        self.assertEqual(bereken_procent(STAPPEN, "b", 2, 4), 25)
        self.assertEqual(bereken_procent(STAPPEN, "b", 4, 4), 40)
        self.assertEqual(bereken_procent(STAPPEN, "b", 9, 4), 40)
        self.assertEqual(bereken_procent(STAPPEN, "b", -3, 4), 10)

    def test_totaal_nul_geeft_beginpercentage(self):
        self.assertEqual(bereken_procent(STAPPEN, "b", 5, 0), 10)

    def test_nieuwe_stappenlijsten_lopen_op_en_blijven_onder_100(self):
        for stappen in (STAPPEN_BIJWERKEN, STAPPEN_OPHALEN, STAPPEN_OPHALEN_ZONDER_HERBEPALEN):
            procenten = [bereken_procent(stappen, stap) for stap, _, _ in stappen]
            self.assertEqual(procenten, sorted(procenten))
            self.assertLess(procenten[-1], 100)

    def test_ophalen_zonder_herbepalen_begint_bij_nul(self):
        self.assertEqual(bereken_procent(STAPPEN_OPHALEN_ZONDER_HERBEPALEN, "koersen_analyseren"), 0)


class TestMeldTeller(unittest.TestCase):
    def test_no_op_zonder_actieve_voortgang(self):
        meld_teller(1, 2)

    def test_zonder_tellers_onderdrukt_de_teller(self):
        v = Voortgang(STAPPEN)
        v.start_stap("b")
        token = voortgang.zet_voortgang(v)
        try:
            with voortgang.zonder_tellers():
                meld_teller(1, 2)
            meld_teller(1, 2)
        finally:
            voortgang.reset_voortgang(token)
        self.assertEqual(len(_events(v)), 2)

    def test_geen_event_zonder_stap_of_met_totaal_nul(self):
        v = Voortgang(STAPPEN)
        v.teller(1, 2)
        v.start_stap("b")
        v.teller(1, 0)
        self.assertEqual(len(_events(v)), 1)

    def test_event_krijgt_gedaan_en_totaal(self):
        v = Voortgang(STAPPEN)
        v.start_stap("c")
        v.teller(1, 2)
        self.assertEqual(_events(v)[1],
                         {"type": "voortgang", "stap": "c", "tekst": "C", "procent": 70, "gedaan": 1, "totaal": 2})

    def test_hooguit_een_event_per_procentpunt_plus_het_laatste(self):
        v = Voortgang(STAPPEN)
        v.start_stap("b")
        for gedaan in range(1, 1001):
            v.teller(gedaan, 1000)
        events = _events(v)[1:]
        self.assertLessEqual(len(events), 31)
        self.assertEqual((events[-1]["gedaan"], events[-1]["procent"]), (1000, 40))

    def test_percentage_gaat_nooit_terug(self):
        v = Voortgang(STAPPEN)
        v.start_stap("c")
        v.teller(2, 4)
        v.start_stap("b")
        v.teller(1, 4)
        procenten = [e["procent"] for e in _events(v)]
        self.assertEqual(procenten, sorted(procenten))


class TestDownloadParallel(unittest.TestCase):
    def test_resultaten_horen_bij_hun_ticker_ook_bij_andere_afrondvolgorde(self):
        import prijzen
        vertraging = {"AAA": 0.15, "BBB": 0.01, "CCC": 0.08, "DDD": 0.0}

        def download(t):
            time.sleep(vertraging[t])
            return f"koersen-{t}"

        stale = {"AAA": "2024-01-01", "BBB": "2024-01-02", "CCC": "2024-01-03", "DDD": "2024-01-04"}
        self.assertEqual(prijzen._download_parallel(stale, download, 4),
                         ["koersen-AAA", "koersen-BBB", "koersen-CCC", "koersen-DDD"])

    def test_telt_vanuit_de_aanroepende_thread_met_offset(self):
        import prijzen
        v = Voortgang(STAPPEN)
        v.start_stap("b")
        token = voortgang.zet_voortgang(v)
        try:
            prijzen._download_parallel(["A", "B"], lambda t: t, 2, al_gedaan=2, totaal=4)
        finally:
            voortgang.reset_voortgang(token)
        laatste = _events(v)[-1]
        self.assertEqual((laatste["gedaan"], laatste["totaal"]), (4, 4))

    def test_fout_in_een_download_komt_door(self):
        import prijzen

        def download(t):
            raise ValueError(t)

        with self.assertRaises(ValueError):
            prijzen._download_parallel(["A"], download, 1)


def _regels(response):
    return [json.loads(r) for r in response.get_data(as_text=True).split("\n") if r.strip()]


class TestBijwerkenEnOphalenStroom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.m = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        self.m.toegangslimiet.wis()
        self.addCleanup(self.m.toegangslimiet.wis)

    def _bijwerken(self, query="?voortgang=1"):
        return self.client.post(f"/api/portfolio/ABC/bijwerken{query}",
                                data={"bestand1": (BytesIO(b"x"), "t.xlsx")}, content_type="multipart/form-data")

    def _ophalen(self, query):
        return self.client.get(f"/api/portfolio/ABC{query}")

    def test_bijwerken_stroom_geeft_status_en_data_van_het_gewone_antwoord(self):
        with patch.object(self.m, "db_portfolio_bestaat", return_value=False):
            gewoon = self._bijwerken("")
            self.m.toegangslimiet.wis()
            regels = _regels(self._bijwerken())
        self.assertEqual(gewoon.status_code, 404)
        self.assertEqual(regels[0]["stap"], "bestand_inlezen")
        self.assertEqual(regels[-1], {"type": "klaar", "status": 404, "data": gewoon.get_json()})

    def test_bijwerken_onbekende_code_telt_ook_met_stroom_als_foute_poging(self):
        with patch.object(self.m, "db_portfolio_bestaat", return_value=False), \
                patch.object(self.m.toegangslimiet, "registreer") as registreer:
            self._bijwerken()
        self.assertEqual(registreer.call_count, 2)

    def test_bijwerken_exception_geeft_500_in_de_klaar_regel(self):
        with patch.object(self.m, "db_portfolio_bestaat", side_effect=RuntimeError("boem")):
            regels = _regels(self._bijwerken())
        self.assertEqual(regels[-1]["status"], 500)
        self.assertIn("error", regels[-1]["data"])

    def test_ophalen_onbekende_code_geeft_404_in_de_klaar_regel_en_telt_als_poging(self):
        with patch.object(self.m, "build_portfolio_response", return_value=None), \
                patch.object(self.m.toegangslimiet, "registreer") as registreer:
            gewoon = self._ophalen("")
            na_gewoon = registreer.call_count
            regels = _regels(self._ophalen("?voortgang=1"))
        self.assertEqual(gewoon.status_code, 404)
        self.assertEqual(na_gewoon, 2)
        self.assertEqual(registreer.call_count, 4)
        self.assertEqual(regels[-1], {"type": "klaar", "status": 404, "data": gewoon.get_json()})

    def test_ophalen_zonder_herbepalen_begint_bij_koersen(self):
        with patch.object(self.m, "build_portfolio_response", return_value={"naam": "x"}), \
                patch.object(self.m, "_markeer_gebruikt"):
            regels = _regels(self._ophalen("?voortgang=1"))
        self.assertEqual([r["stap"] for r in regels if r["type"] == "voortgang"], ["koersen_analyseren", "klaar_maken"])
        self.assertEqual(regels[0]["procent"], 0)
        self.assertEqual(regels[-1]["status"], 200)
        self.assertEqual(regels[-1]["data"]["naam"], "x")

    def test_ophalen_met_herbepalen_heeft_de_extra_stap(self):
        with patch.object(self.m, "build_portfolio_response", return_value={"naam": "x"}), \
                patch.object(self.m, "_markeer_gebruikt"), \
                patch.object(self.m, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.m, "_herbepaal_tickers"):
            regels = _regels(self._ophalen("?voortgang=1&herbepaal_alle_tickers=true"))
        self.assertEqual([r["stap"] for r in regels if r["type"] == "voortgang"],
                         ["tickers_herbepalen", "koersen_analyseren", "klaar_maken"])

    def test_ophalen_exception_geeft_500_in_de_klaar_regel(self):
        with patch.object(self.m, "build_portfolio_response", side_effect=RuntimeError("boem")):
            regels = _regels(self._ophalen("?voortgang=1"))
        self.assertEqual(regels[-1]["status"], 500)


if __name__ == "__main__":
    unittest.main()
