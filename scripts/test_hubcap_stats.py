#!/usr/bin/env python3
"""Unit tests for Hubcap API key expiry calculation and UTC midnight rollover."""

import datetime
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.morrenus_api import (
    compute_api_key_expiry_info,
    is_stats_cache_from_previous_utc_day,
)


class MockSettings:
    def __init__(self):
        self._data = {}

    def value(self, key, default=None, type=None):
        val = self._data.get(key, default)
        if type is not None and val is not None:
            try:
                return type(val)
            except Exception:
                return default
        return val

    def setValue(self, key, value):
        self._data[key] = value


class TestHubcapStats(unittest.TestCase):
    def setUp(self):
        self.settings = MockSettings()
        self.now_utc = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def test_lifetime_key(self):
        """None, empty, or 'Never' should display ∞ in Green."""
        for empty_val in [None, "", "never", "Never", "null"]:
            res = compute_api_key_expiry_info(
                empty_val,
                api_key="test_key",
                settings=self.settings,
                now_utc=self.now_utc,
            )
            self.assertEqual(res["text"], "∞")
            self.assertEqual(res["color"], "#10b981")
            self.assertTrue(res["is_lifetime"])
            self.assertFalse(res["is_expired"])
            self.assertEqual(res["formatted_date"], "Never")

    def test_expired_key(self):
        """Past date should display 0d in Red."""
        past_date = "2026-10-19T12:00:00Z"
        res = compute_api_key_expiry_info(
            past_date,
            api_key="test_key",
            settings=self.settings,
            now_utc=self.now_utc,
        )
        self.assertEqual(res["text"], "0d")
        self.assertEqual(res["color"], "#ef4444")
        self.assertTrue(res["is_expired"])
        self.assertFalse(res["is_lifetime"])

    def test_under_24_hours_displays_hours(self):
        """Expiry within 24 hours should switch from days to hours."""
        expiry_18h = (self.now_utc + datetime.timedelta(hours=18)).isoformat()
        res = compute_api_key_expiry_info(
            expiry_18h,
            api_key="test_key",
            settings=self.settings,
            now_utc=self.now_utc,
        )
        self.assertEqual(res["text"], "18h")
        self.assertEqual(res["hours"], 18)
        self.assertEqual(res["days"], 0)

    def test_color_thresholds_percentage(self):
        """Test Green (>=75%), Orange (25-74%), and Red (<25%) based on baseline."""
        # 1. First fetch establishes baseline of 100 days
        expiry_100d = (self.now_utc + datetime.timedelta(days=100)).isoformat()
        res1 = compute_api_key_expiry_info(
            expiry_100d,
            api_key="user_key_1",
            settings=self.settings,
            now_utc=self.now_utc,
        )
        self.assertEqual(res1["text"], "100d")
        self.assertAlmostEqual(res1["percentage"], 100.0, places=1)
        self.assertEqual(res1["color"], "#10b981")  # Green (>= 75%)

        # 2. Later check with 50 days remaining out of 100 days -> 50% (Orange)
        later_now = self.now_utc + datetime.timedelta(days=50)
        res2 = compute_api_key_expiry_info(
            expiry_100d,
            api_key="user_key_1",
            settings=self.settings,
            now_utc=later_now,
        )
        self.assertEqual(res2["text"], "50d")
        self.assertAlmostEqual(res2["percentage"], 50.0, places=1)
        self.assertEqual(res2["color"], "#f59e0b")  # Orange (25-74%)

        # 3. Later check with 10 days remaining out of 100 days -> 10% (Red)
        much_later = self.now_utc + datetime.timedelta(days=90)
        res3 = compute_api_key_expiry_info(
            expiry_100d,
            api_key="user_key_1",
            settings=self.settings,
            now_utc=much_later,
        )
        self.assertEqual(res3["text"], "10d")
        self.assertAlmostEqual(res3["percentage"], 10.0, places=1)
        self.assertEqual(res3["color"], "#ef4444")  # Red (< 25%)

    def test_key_change_rebaselines(self):
        """When user enters a new API key, baseline resets to the new key's duration."""
        # First key: 30 days
        exp_30 = (self.now_utc + datetime.timedelta(days=30)).isoformat()
        res_key1 = compute_api_key_expiry_info(exp_30, api_key="key_A", settings=self.settings, now_utc=self.now_utc)
        self.assertAlmostEqual(res_key1["percentage"], 100.0, places=1)

        # Second key: 800 days
        exp_800 = (self.now_utc + datetime.timedelta(days=800)).isoformat()
        res_key2 = compute_api_key_expiry_info(exp_800, api_key="key_B", settings=self.settings, now_utc=self.now_utc)
        self.assertEqual(res_key2["text"], "800d")
        self.assertAlmostEqual(res_key2["percentage"], 100.0, places=1)
        self.assertEqual(res_key2["color"], "#10b981")

    def test_utc_midnight_rollover(self):
        """Check that previous UTC day cache is properly flagged as expired."""
        yesterday_cache = {
            "timestamp": "2026-10-19T23:59:00Z",
            "daily_usage": 55,
            "daily_limit": 55,
        }
        self.assertTrue(is_stats_cache_from_previous_utc_day(yesterday_cache))

        today_cache = {
            "timestamp": "2026-10-20T00:01:00Z",
            "daily_usage": 2,
            "daily_limit": 55,
        }
        # In test setup, current UTC time is 2026-10-20 12:00:00 (same day)
        # Note: is_stats_cache_from_previous_utc_day checks against real now_utc
        # We test with today's real date
        real_today = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.assertFalse(is_stats_cache_from_previous_utc_day({"timestamp": real_today}))


if __name__ == "__main__":
    unittest.main()
