#!/usr/bin/env python3
"""Regression tests for preserving cached Hubcap bundles during refresh."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.morrenus_api import download_manifest


class TestManifestRefreshAtomic(unittest.TestCase):
    def _run_download(self, cache_path: Path, response_or_error):
        request_side_effect = (
            response_or_error
            if isinstance(response_or_error, BaseException)
            else lambda *args, **kwargs: response_or_error
        )
        with patch("core.morrenus_api._get_headers", return_value={"Authorization": "test"}), patch(
            "core.morrenus_api.get_manifest_zip_path", return_value=cache_path
        ), patch("core.morrenus_api.get_settings", return_value=MagicMock()), patch(
            "utils.isp_bypass.execute_hubcap_request", side_effect=request_side_effect
        ):
            return download_manifest("123", force_update=True)

    def test_failed_refresh_preserves_previous_cached_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "accela_fetch_123.zip"
            cache_path.write_bytes(b"previous valid bundle")

            path, error = self._run_download(cache_path, RuntimeError("offline"))

            self.assertIsNone(path)
            self.assertTrue(error)
            self.assertEqual(cache_path.read_bytes(), b"previous valid bundle")
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

    def test_successful_refresh_atomically_replaces_cached_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "accela_fetch_123.zip"
            cache_path.write_bytes(b"previous bundle")
            response = MagicMock()
            response.iter_content.return_value = [b"complete new bundle"]

            path, error = self._run_download(cache_path, response)

            self.assertIsNone(error)
            self.assertEqual(Path(path), cache_path)
            self.assertEqual(cache_path.read_bytes(), b"complete new bundle")
            self.assertEqual(list(Path(tmp).glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
