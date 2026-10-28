"""/ping en /sw.js: goedkoop, zonder database."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestPingEnServiceWorker(unittest.TestCase):
    def setUp(self):
        import app as app_module
        import db
        self.client = app_module.app.test_client()
        patcher = patch.object(
            db, "db_connect",
            side_effect=AssertionError("ping en sw.js mogen de database niet raken"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ping_geeft_json_ok_zonder_cache(self):
        res = self.client.get("/ping")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"ok": True})
        self.assertEqual(res.headers["Cache-Control"], "no-store")

    def test_sw_js_wordt_als_javascript_geserveerd_zonder_langdurige_cache(self):
        res = self.client.get("/sw.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("javascript", res.mimetype)
        self.assertEqual(res.headers["Cache-Control"], "no-cache")
        self.assertIn("CACHE_NAAM", res.get_data(as_text=True))
        res.close()


if __name__ == "__main__":
    unittest.main()
