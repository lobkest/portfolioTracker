"""Unit tests voor _sector_naam() in ticker_classificatie.py (weergave van Yahoo-sectorsleutels)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ticker_classificatie import _sector_naam


class TestSectorNaam(unittest.TestCase):
    def test_yahoo_sleutels(self):
        self.assertEqual(_sector_naam("realestate"), "Real Estate")
        self.assertEqual(_sector_naam("consumer_cyclical"), "Consumer Cyclical")
        self.assertEqual(_sector_naam("communication_services"), "Communication Services")
        self.assertEqual(_sector_naam("technology"), "Technology")

    def test_al_opgemaakte_naam_uit_cache(self):
        self.assertEqual(_sector_naam("Realestate"), "Real Estate")
        self.assertEqual(_sector_naam("Real Estate"), "Real Estate")
        self.assertEqual(_sector_naam("Consumer Cyclical"), "Consumer Cyclical")


if __name__ == "__main__":
    unittest.main()
