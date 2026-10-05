"""Nieuwe cachetabellen koersen, koers_splits en prijscheck_koersen, tegen een lokale testdatabase (CI-container).

Eigen test-tickers (ZZTEST...) die nergens anders voorkomen; alles wordt voor en na elke test opgeruimd.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database

T1, T2, T3 = "ZZTESTKOERS1", "ZZTESTKOERS2", "ZZTESTKOERS3"


def _ruim_op():
    from db import db_connect
    conn = db_connect()
    cur = conn.cursor()
    for tabel in ("koersen", "koers_splits", "prijscheck_koersen"):
        cur.execute(f"DELETE FROM {tabel} WHERE ticker LIKE 'ZZTESTKOERS%'")
    conn.commit()
    cur.close()
    conn.close()


@vereist_database
class TestKoersenTabellen(unittest.TestCase):
    def setUp(self):
        _ruim_op()
        self.addCleanup(_ruim_op)

    def test_opslaan_en_ophalen_van_ruwe_koersen(self):
        from db import db_save_koersen, db_get_gecachte_koersen
        db_save_koersen(
            [(T1, date(2021, 1, 25), 0.8737), (T1, date(2021, 1, 26), 2.51), (T2, date(2021, 1, 26), 10.0)],
            {T1: {"2021-01-26": 1 / 3}, T2: {}},
        )
        datums, vandaag, koersen = db_get_gecachte_koersen([T1, T2, T3], date(2021, 1, 27), date(2021, 1, 1))
        self.assertEqual(datums, {T1: (date(2021, 1, 25), date(2021, 1, 26)), T2: (date(2021, 1, 26), date(2021, 1, 26))})
        self.assertEqual(vandaag, {})
        self.assertEqual(sorted((t, d, float(k)) for t, d, k in koersen),
                         [(T1, date(2021, 1, 25), 0.8737), (T1, date(2021, 1, 26), 2.51), (T2, date(2021, 1, 26), 10.0)])

    def test_start_datum_filtert_de_koersrijen(self):
        from db import db_save_koersen, db_get_gecachte_koersen
        db_save_koersen([(T1, date(2021, 1, 25), 1.0), (T1, date(2021, 1, 26), 2.0)], {T1: {}})
        _, _, koersen = db_get_gecachte_koersen([T1], date(2021, 1, 27), date(2021, 1, 26))
        self.assertEqual([(d, float(k)) for _, d, k in koersen], [(date(2021, 1, 26), 2.0)])

    def test_splits_leeg_is_iets_anders_dan_onbekend(self):
        from db import db_save_koersen, db_get_koers_splits
        db_save_koersen([(T1, date(2021, 1, 26), 2.51), (T2, date(2021, 1, 26), 10.0)], {T1: {"2021-01-26": 0.5}, T2: {}})
        splits = db_get_koers_splits([T1, T2, T3])
        self.assertEqual(splits[T1], {"2021-01-26": 0.5})
        self.assertEqual(splits[T2], {})
        self.assertNotIn(T3, splits)

    def test_tweede_keer_opslaan_werkt_bij_en_voegt_splits_samen(self):
        from db import db_save_koersen, db_get_gecachte_koersen, db_get_koers_splits
        db_save_koersen([(T1, date(2021, 1, 26), 1.0)], {T1: {"2021-01-26": 0.5}})
        db_save_koersen([(T1, date(2021, 1, 26), 1.5), (T1, date(2021, 1, 27), 2.0)], {T1: {"2022-07-26": 0.05}})
        _, _, koersen = db_get_gecachte_koersen([T1], date(2021, 2, 1), date(2021, 1, 1))
        self.assertEqual(sorted((d, float(k)) for _, d, k in koersen), [(date(2021, 1, 26), 1.5), (date(2021, 1, 27), 2.0)])
        self.assertEqual(db_get_koers_splits([T1])[T1], {"2021-01-26": 0.5, "2022-07-26": 0.05})

    def test_mislukte_splits_laten_geen_koersen_achter(self):
        from db import db_save_koersen, db_get_gecachte_koersen
        with self.assertRaises(Exception):
            db_save_koersen([(T1, date(2021, 1, 26), 1.0)], {T1: {"2021-01-26": None}})
        datums, _, koersen = db_get_gecachte_koersen([T1], date(2021, 2, 1), date(2021, 1, 1))
        self.assertEqual((datums, koersen), ({}, []))

    def test_laatste_koers_update(self):
        from db import db_save_koersen, db_get_laatste_koers_update
        self.assertEqual(db_get_laatste_koers_update([]), (None, None))
        self.assertEqual(db_get_laatste_koers_update([T1]), (None, None))
        db_save_koersen([(T1, date(2021, 1, 25), 1.0), (T1, date(2021, 1, 26), 2.0)], {T1: {}})
        laatste, opgehaald = db_get_laatste_koers_update([T1])
        self.assertEqual(laatste, date(2021, 1, 26))
        self.assertIsNotNone(opgehaald)

    def test_prijscheck_koers_ook_mislukte_poging_en_upsert(self):
        from db import db_save_prijscheck_koers, db_get_cached_prijscheck_koers
        self.assertIsNone(db_get_cached_prijscheck_koers(T1, date(2021, 1, 26)))
        db_save_prijscheck_koers(T1, date(2021, 1, 26), None, None)
        self.assertEqual(db_get_cached_prijscheck_koers(T1, date(2021, 1, 26)), (None, None, None, None))
        db_save_prijscheck_koers(T1, date(2021, 1, 26), 2.51, "USD", 2.6, 2.27)
        self.assertEqual(db_get_cached_prijscheck_koers(T1, date(2021, 1, 26)), (2.51, "USD", 2.6, 2.27))


if __name__ == "__main__":
    unittest.main()
