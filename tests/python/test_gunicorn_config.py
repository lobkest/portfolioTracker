"""Het gunicorn-accesslog bevat geen IP, URL, query, referer of user-agent."""
import os
import runpy
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERBODEN_VELDEN = ("%(h)s", "%(r)s", "%(U)s", "%(q)s", "%(f)s", "%(a)s", "%(l)s", "%(u)s", "%({")
INSTELLINGEN = {"access_log_format", "logger_class"}
HULPNAMEN = {"os", "sys", "maak_logger_class"}


def nep_gunicorn_glogging():
    """gunicorn.glogging bestaat niet op Windows (import grp); een nep-module met een lege Logger volstaat hier."""
    module = types.ModuleType("gunicorn.glogging")
    module.Logger = type("Logger", (), {})
    return {"gunicorn": types.ModuleType("gunicorn"), "gunicorn.glogging": module}


class TestGunicornConfig(unittest.TestCase):
    def setUp(self):
        with patch.dict(sys.modules, nep_gunicorn_glogging()):
            self.config = runpy.run_path(os.path.join(ROOT, "gunicorn.conf.py"))

    def test_accesslog_formaat_bevat_geen_herleidbare_velden(self):
        formaat = self.config["access_log_format"]
        for veld in VERBODEN_VELDEN:
            self.assertNotIn(veld, formaat)

    def test_accesslog_formaat_bevat_methode_status_grootte_en_duur(self):
        formaat = self.config["access_log_format"]
        for veld in ("%(m)s", "%(s)s", "%(B)s", "%(L)s"):
            self.assertIn(veld, formaat)

    def test_config_zet_alleen_het_formaat_en_de_logger_class(self):
        namen = {k for k in self.config if not k.startswith("__")} - HULPNAMEN
        self.assertEqual(namen, INSTELLINGEN)

    def test_logger_class_is_de_filterlogger(self):
        self.assertEqual(self.config["logger_class"].__name__, "FilterLogger")


if __name__ == "__main__":
    unittest.main()
