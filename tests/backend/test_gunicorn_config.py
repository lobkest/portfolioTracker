"""Het gunicorn-accesslog bevat geen IP, URL, query, referer of user-agent."""
import os
import runpy
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERBODEN_VELDEN = ("%(h)s", "%(r)s", "%(U)s", "%(q)s", "%(f)s", "%(a)s", "%(l)s", "%(u)s", "%({")


class TestGunicornConfig(unittest.TestCase):
    def setUp(self):
        self.config = runpy.run_path(os.path.join(ROOT, "gunicorn.conf.py"))

    def test_accesslog_formaat_bevat_geen_herleidbare_velden(self):
        formaat = self.config["access_log_format"]
        for veld in VERBODEN_VELDEN:
            self.assertNotIn(veld, formaat)

    def test_accesslog_formaat_bevat_methode_status_grootte_en_duur(self):
        formaat = self.config["access_log_format"]
        for veld in ("%(m)s", "%(s)s", "%(B)s", "%(L)s"):
            self.assertIn(veld, formaat)

    def test_config_zet_verder_niets(self):
        instellingen = {k for k in self.config if not k.startswith("__")}
        self.assertEqual(instellingen, {"access_log_format"})


if __name__ == "__main__":
    unittest.main()
