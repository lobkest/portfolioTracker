"""Laadbalk: bereken_procent, meld_stap en de NDJSON-stroom van /upload?voortgang=1 (gemockt, geen database)."""
import json
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import voortgang
from voortgang import bereken_procent, meld_stap, Voortgang, STAPPEN_UPLOAD_OPSLAAN, STAPPEN_UPLOAD_NIET_OPSLAAN

STAPPEN = (("a", "A", 10), ("b", "B", 30), ("c", "C", 60))


class TestBerekenProcent(unittest.TestCase):
    def test_eerste_stap_begint_op_nul(self):
        self.assertEqual(bereken_procent(STAPPEN, "a"), 0)

    def test_oplopend_met_hand_nagerekende_waarden(self):
        self.assertEqual(bereken_procent(STAPPEN, "b"), 10)
        self.assertEqual(bereken_procent(STAPPEN, "c"), 40)

    def test_laatste_stap_onder_100(self):
        for stappen in (STAPPEN_UPLOAD_OPSLAAN, STAPPEN_UPLOAD_NIET_OPSLAAN):
            self.assertLess(bereken_procent(stappen, stappen[-1][0]), 100)

    def test_overgeslagen_stap_laat_percentage_doorspringen(self):
        voor = Voortgang(STAPPEN)
        voor.start_stap("a")
        voor.start_stap("c")
        procenten = [voor.events.get()["procent"], voor.events.get()["procent"]]
        self.assertEqual(procenten, [0, 40])


class TestMeldStap(unittest.TestCase):
    def test_no_op_zonder_actieve_voortgang(self):
        self.assertIsNone(voortgang.huidige_voortgang())
        meld_stap("a")

    def test_zet_event_met_actieve_voortgang(self):
        v = Voortgang(STAPPEN)
        token = voortgang.zet_voortgang(v)
        try:
            meld_stap("b")
            meld_stap("onbekend")
        finally:
            voortgang.reset_voortgang(token)
        event = v.events.get_nowait()
        self.assertEqual(event, {"type": "voortgang", "stap": "b", "tekst": "B", "procent": 10})
        self.assertTrue(v.events.empty())


def _regels(response):
    return [json.loads(r) for r in response.get_data(as_text=True).split("\n") if r.strip()]


class TestUploadStroom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        self.app_module.toegangslimiet.wis()
        self.addCleanup(self.app_module.toegangslimiet.wis)

    def _post(self, query="?voortgang=1"):
        return self.client.post("/upload" + query, data={"bestand1": (BytesIO(b"x"), "t.xlsx")},
                                content_type="multipart/form-data")

    def test_stroom_eindigt_met_een_klaar_regel(self):
        def impl():
            meld_stap("bestand_inlezen")
            meld_stap("klaar_maken")
            return self.app_module.jsonify({"ok": 1})

        with patch.object(self.app_module, "_upload_impl", impl), \
                patch.object(self.app_module, "ruim_verlopen_portfolios_op"):
            response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual(response.headers["X-Accel-Buffering"], "no")
        regels = _regels(response)
        self.assertEqual([r["type"] for r in regels], ["voortgang", "voortgang", "klaar"])
        self.assertEqual(regels[-1], {"type": "klaar", "status": 200, "data": {"ok": 1}})

    def test_foutstatus_van_het_werk_komt_in_de_klaar_regel(self):
        with patch.object(self.app_module, "_upload_impl",
                          lambda: (self.app_module.jsonify({"error": "fout"}), 400)), \
                patch.object(self.app_module, "ruim_verlopen_portfolios_op"):
            regels = _regels(self._post())
        self.assertEqual(regels, [{"type": "klaar", "status": 400, "data": {"error": "fout"}}])

    def test_exception_geeft_klaar_met_status_500(self):
        def impl():
            raise RuntimeError("boem")

        with patch.object(self.app_module, "_upload_impl", impl):
            regels = _regels(self._post())
        self.assertEqual(len(regels), 1)
        self.assertEqual(regels[0]["type"], "klaar")
        self.assertEqual(regels[0]["status"], 500)
        self.assertIn("error", regels[0]["data"])

    def test_foute_code_poging_wordt_geregistreerd(self):
        def impl():
            self.app_module.g.foute_code_poging = True
            return self.app_module.jsonify({"error": "x"}), 400

        with patch.object(self.app_module, "_upload_impl", impl), \
                patch.object(self.app_module, "ruim_verlopen_portfolios_op"), \
                patch.object(self.app_module.toegangslimiet, "registreer") as registreer:
            self._post()
        self.assertEqual(registreer.call_count, 2)

    def test_zonder_parameter_geen_stroom(self):
        with patch.object(self.app_module, "_upload_impl", lambda: self.app_module.jsonify({"ok": 1})), \
                patch.object(self.app_module, "ruim_verlopen_portfolios_op"):
            response = self._post("")
        self.assertEqual(response.mimetype, "application/json")
        self.assertEqual(response.get_json(), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
