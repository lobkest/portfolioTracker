"""Accesslog: ruis weg, fouten altijd erin, code gemaskeerd, geen IP en geen querystring."""
import os
import sys
import unittest
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import accesslog
from accesslog import accesslog_regel


class TestAccesslogRegel(unittest.TestCase):
    def test_static_wordt_overgeslagen(self):
        self.assertIsNone(accesslog_regel("GET", "/static/js/app.js", 200, 0.001))

    def test_vaste_bestanden_worden_overgeslagen(self):
        for pad in ("/favicon.ico", "/robots.txt", "/sitemap.xml"):
            self.assertIsNone(accesslog_regel("GET", pad, 200, 0.001), pad)

    def test_304_wordt_overgeslagen(self):
        self.assertIsNone(accesslog_regel("GET", "/p/ABC", 304, 0.001))

    def test_fouten_worden_altijd_gelogd_ook_bij_static(self):
        self.assertEqual(accesslog_regel("GET", "/static/js/weg.js", 404, 0.002), "GET /static/js/weg.js 404 0.002s")
        self.assertIn(" 429 ", accesslog_regel("GET", "/api/portfolio/ABC", 429, 0.0))
        self.assertIn(" 500 ", accesslog_regel("POST", "/upload", 500, 1.5))

    def test_code_wordt_gemaskeerd(self):
        self.assertEqual(accesslog_regel("GET", "/api/portfolio/ABC/verrijking", 200, 0.1234),
                         "GET /api/portfolio/***/verrijking 200 0.123s")
        self.assertEqual(accesslog_regel("GET", "/p/ABC", 200, 0.05), "GET /p/*** 200 0.050s")
        self.assertEqual(accesslog_regel("DELETE", "/api/portfolio/ABC", 200, 0.05),
                         "DELETE /api/portfolio/*** 200 0.050s")

    def test_pad_zonder_code_blijft_staan(self):
        self.assertEqual(accesslog_regel("POST", "/upload", 200, 2.0), "POST /upload 200 2.000s")


def maak_logger(nep_cfg):
    module = ModuleType("gunicorn.glogging")

    class Logger:
        def __init__(self):
            self.cfg = nep_cfg
            self.access_log = MagicMock()
            self.error = MagicMock()

    module.Logger = Logger
    with patch.dict(sys.modules, {"gunicorn": ModuleType("gunicorn"), "gunicorn.glogging": module}):
        return accesslog.maak_logger_class()()


def cfg(**extra):
    basis = dict(accesslog="-", logconfig=None, logconfig_dict={}, syslog=False, disable_redirect_access_to_syslog=False)
    return SimpleNamespace(**{**basis, **extra})


def roep(logger, pad, status, query="", ip="203.0.113.7", duur=timedelta(milliseconds=12)):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": pad, "QUERY_STRING": query, "REMOTE_ADDR": ip,
               "HTTP_X_FORWARDED_FOR": ip, "HTTP_USER_AGENT": "Mozilla"}
    logger.access(SimpleNamespace(status_code=status), SimpleNamespace(), environ, duur)


class TestFilterLogger(unittest.TestCase):
    def test_logt_gemaskeerd_pad_zonder_ip_en_query(self):
        logger = maak_logger(cfg())
        roep(logger, "/api/portfolio/ABC/verrijking", 200, query="x=ABC")
        logger.access_log.info.assert_called_once_with("%s", "GET /api/portfolio/***/verrijking 200 0.012s")
        regel = logger.access_log.info.call_args.args[1]
        for verboden in ("ABC", "203.0.113.7", "x=", "Mozilla"):
            self.assertNotIn(verboden, regel)

    def test_static_en_304_worden_overgeslagen(self):
        logger = maak_logger(cfg())
        roep(logger, "/static/css/style.css", 200)
        roep(logger, "/p/ABC", 304)
        logger.access_log.info.assert_not_called()

    def test_fout_bij_static_wordt_gelogd(self):
        logger = maak_logger(cfg())
        roep(logger, "/static/js/weg.js", 404)
        logger.access_log.info.assert_called_once()

    def test_zonder_accesslog_wordt_niets_gelogd(self):
        logger = maak_logger(cfg(accesslog=None))
        roep(logger, "/upload", 500)
        logger.access_log.info.assert_not_called()

    def test_duur_als_getal_werkt_ook(self):
        logger = maak_logger(cfg())
        roep(logger, "/upload", 200, duur=1.5)
        self.assertEqual(logger.access_log.info.call_args.args[1], "GET /upload 200 1.500s")


if __name__ == "__main__":
    unittest.main()
