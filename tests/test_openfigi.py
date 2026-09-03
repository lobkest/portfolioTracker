"""
Unit tests voor analysis.haal_openfigi_resultaten() — EXPERIMENTEEL/
DIAGNOSTISCH paneel op de Ticker-zekerheid-pagina (zie CLAUDE.md). Draait
geheel offline: analysis.requests.post wordt gemockt, dus geen echte
OpenFIGI/netwerk-calls nodig.
"""
import sys
import os
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from analysis import haal_openfigi_resultaten


def _mock_response(status_code=200, json_data=None):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


class TestHaalOpenfigiResultaten(unittest.TestCase):
    @patch("analysis.requests.post")
    def test_succesvolle_match(self, mock_post):
        mock_post.return_value = _mock_response(200, [{
            "data": [{
                "ticker": "AAPL", "exchCode": "US", "name": "APPLE INC",
                "securityType": "Common Stock", "marketSector": "Equity",
                "compositeFIGI": "BBG000B9XVN3",
            }]
        }])
        result = haal_openfigi_resultaten("US0378331005")
        self.assertIsNone(result["fout"])
        self.assertEqual(len(result["resultaten"]), 1)
        self.assertEqual(result["resultaten"][0]["ticker"], "AAPL")

    @patch("analysis.requests.post")
    def test_geen_match(self, mock_post):
        mock_post.return_value = _mock_response(200, [{"warning": "No identifier found."}])
        result = haal_openfigi_resultaten("XX0000000000")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("No identifier found", result["fout"])

    @patch("analysis.requests.post")
    def test_rate_limit(self, mock_post):
        mock_post.return_value = _mock_response(429)
        result = haal_openfigi_resultaten("US0378331005")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("rate limit", result["fout"].lower())

    @patch("analysis.requests.post")
    def test_netwerkfout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("boom")
        result = haal_openfigi_resultaten("US0378331005")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("niet bereikbaar", result["fout"].lower())

    @patch("analysis.requests.post")
    def test_meerdere_beursnoteringen(self, mock_post):
        mock_post.return_value = _mock_response(200, [{
            "data": [
                {"ticker": "VWCE", "exchCode": "GR", "name": "VANGUARD FTSE ALL-WRLD"},
                {"ticker": "VWCE", "exchCode": "MI", "name": "VANGUARD FTSE ALL-WRLD"},
            ]
        }])
        result = haal_openfigi_resultaten("IE00BK5BQT80")
        self.assertEqual(len(result["resultaten"]), 2)

    @patch("analysis.requests.post")
    def test_lege_isin_geen_crash_geen_netwerkcall(self, mock_post):
        result = haal_openfigi_resultaten("")
        self.assertEqual(result["resultaten"], [])
        self.assertIsNotNone(result["fout"])
        mock_post.assert_not_called()

    @patch("analysis.requests.post")
    def test_none_isin_geen_crash_geen_netwerkcall(self, mock_post):
        result = haal_openfigi_resultaten(None)
        self.assertEqual(result["resultaten"], [])
        self.assertIsNotNone(result["fout"])
        mock_post.assert_not_called()

    @patch("analysis.requests.post")
    def test_onverwachte_status_code(self, mock_post):
        mock_post.return_value = _mock_response(500)
        result = haal_openfigi_resultaten("US0378331005")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("500", result["fout"])


if __name__ == "__main__":
    unittest.main()
