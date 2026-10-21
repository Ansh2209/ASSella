#!/usr/bin/env python3
"""Regression tests for the web UI status snapshot and manifest extraction.

The web server answers requests on its own threads, so it must never read
QWidget state. The GUI thread publishes a plain snapshot instead.
"""

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from utils.web_server import read_progress_snapshot
from utils.steam_manifest import safe_extract_zip


class _ExplodingWidget:
    """Any access to this object means a widget was touched off the GUI thread."""

    def __getattr__(self, name):
        raise AssertionError(f"widget attribute accessed from a non-GUI thread: {name}")


class _MainWinStub:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot
        self.progress_bar = _ExplodingWidget()
        self.speed_label = _ExplodingWidget()

    def get_web_status_snapshot(self):
        if self._snapshot is None:
            return {}
        return dict(self._snapshot)


class _LegacyMainWinStub:
    """A main window that never got the snapshot provider (e.g. old build)."""

    def __init__(self):
        self.progress_bar = _ExplodingWidget()
        self.speed_label = _ExplodingWidget()


class TestReadProgressSnapshot(unittest.TestCase):
    def test_reads_published_snapshot(self):
        main_win = _MainWinStub({
            "progress": 42,
            "progress_visible": True,
            "speed": "12.3 MB/s",
            "speed_visible": True,
        })
        self.assertEqual(read_progress_snapshot(main_win), (42, "12.3 MB/s"))

    def test_hidden_widgets_report_defaults(self):
        main_win = _MainWinStub({
            "progress": 42,
            "progress_visible": False,
            "speed": "12.3 MB/s",
            "speed_visible": False,
        })
        self.assertEqual(read_progress_snapshot(main_win), (0, ""))

    def test_missing_snapshot_provider_is_tolerated(self):
        main_win = _LegacyMainWinStub()
        self.assertEqual(read_progress_snapshot(main_win), (0, ""))

    def test_invalid_progress_value_falls_back_to_zero(self):
        main_win = _MainWinStub({
            "progress": "not-a-number",
            "progress_visible": True,
            "speed": "",
            "speed_visible": True,
        })
        self.assertEqual(read_progress_snapshot(main_win), (0, ""))


class TestPublisher(unittest.TestCase):
    """The GUI-thread side of the contract (needs PyQt6 importable)."""

    def setUp(self):
        try:
            from ui.main_window import MainWindow  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on the environment
            self.skipTest(f"PyQt6 main_window unavailable: {exc}")
        from ui.main_window import MainWindow

        self.MainWindow = MainWindow

    def _stub(self, progress_value=0, progress_visible=False, speed="", speed_visible=False):
        class _Bar:
            def isVisible(self):
                return progress_visible

            def value(self):
                return progress_value

        class _Speed:
            def isVisible(self):
                return speed_visible

            def text(self):
                return "elided..."

            full_text = property(lambda self: speed)

        stub = type("Stub", (), {})()
        stub.progress_bar = _Bar()
        stub.speed_label = _Speed()
        return stub

    def test_refresh_publishes_widget_state(self):
        stub = self._stub(progress_value=17, progress_visible=True, speed="5 MB/s", speed_visible=True)
        self.MainWindow._refresh_web_status_snapshot(stub)
        snapshot = self.MainWindow.get_web_status_snapshot(stub)
        self.assertEqual(snapshot["progress"], 17)
        self.assertTrue(snapshot["progress_visible"])
        self.assertEqual(snapshot["speed"], "5 MB/s")
        self.assertTrue(snapshot["speed_visible"])

    def test_snapshot_is_a_copy(self):
        stub = self._stub(progress_value=3, progress_visible=True)
        self.MainWindow._refresh_web_status_snapshot(stub)
        snapshot = self.MainWindow.get_web_status_snapshot(stub)
        snapshot["progress"] = 999
        self.assertEqual(self.MainWindow.get_web_status_snapshot(stub)["progress"], 3)


class TestSafeExtractZip(unittest.TestCase):
    def test_returns_member_count_and_extracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "bundle.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("manifest/1.manifest", "a")
                zf.writestr("manifest/2.manifest", "b")
            dest = os.path.join(tmp, "out")
            self.assertEqual(safe_extract_zip(zip_path, dest), 2)
            self.assertTrue(os.path.exists(os.path.join(dest, "manifest", "1.manifest")))

    def test_does_not_leak_file_descriptors(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "bundle.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("manifest/1.manifest", "a")
            dest = os.path.join(tmp, "out")
            safe_extract_zip(zip_path, dest)
            before = len(os.listdir("/proc/self/fd"))
            for _ in range(100):
                safe_extract_zip(zip_path, dest)
            after = len(os.listdir("/proc/self/fd"))
            self.assertLessEqual(after, before + 2, "safe_extract_zip leaked file descriptors")

    def test_still_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "evil.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../evil.txt", "a")
            with self.assertRaises(ValueError):
                safe_extract_zip(zip_path, os.path.join(tmp, "out"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
