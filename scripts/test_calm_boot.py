#!/usr/bin/env python3
"""Regression tests for the calm-boot changes.

Boot used to be able to freeze the window: the status panel refreshed on
every dashboard rebuild and one of its checks reached out to GitHub from the
GUI thread, so a slow network meant a full timeout inside the event loop.
These tests pin that behaviour down.

    python3 scripts/test_calm_boot.py
"""

import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils import slssteam_integration


class _FakeTimer:
    """Records the coalescing calls without needing a Qt event loop."""

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class _Signal:
    """A pyqtSignal stand-in for the stubs in these tests.

    It records the emitted payloads and the connected slots, and it dispatches
    to the connected slots so a test can drive the real chain of signals.
    """

    def __init__(self):
        self.emitted = []
        self.connected = []

    def connect(self, slot, *_args, **_kwargs):
        self.connected.append(slot)

    def disconnect(self, *_args, **_kwargs):
        pass

    def emit(self, *args):
        self.emitted.append(args)
        for slot in list(self.connected):
            slot(*args)


class _SettingsStub:
    def __init__(self, mode="auto"):
        self._mode = mode

    def value(self, key, default=None, type=None):
        if key == "isp_bypass_mode":
            return self._mode
        return default


class TestSlsBinaryVersionCheck(unittest.TestCase):
    """check_slssteam_binary_is_latest() must never block the GUI thread."""

    def setUp(self):
        slssteam_integration._binary_version_cache = {}
        slssteam_integration._binary_version_cache_lock = threading.Lock()
        self._paths = {
            "dir": "/tmp/sls",
            "so_path": "/tmp/sls/SLSsteam.so",
            "inject_path": "/tmp/sls/library-inject.so",
            "version_file": "/tmp/sls/version",
            "is_system_wide": False,
            "detected": True,
        }

    def test_cached_only_does_not_touch_the_network(self):
        def explode(*args, **kwargs):
            raise AssertionError("cached_only must not reach the network")

        with mock.patch("urllib.request.urlopen", explode):
            result = slssteam_integration.check_slssteam_binary_is_latest(cached_only=True)

        self.assertEqual(result["status"], "checking")
        self.assertIsNone(result["error"])

    def test_cached_only_returns_the_cached_answer(self):
        slssteam_integration._binary_version_cache = {
            "timestamp": time.time(),
            "data": {"status": "up_to_date", "release_tag": "20260903114323"},
        }

        def explode(*args, **kwargs):
            raise AssertionError("cached_only must not reach the network")

        with mock.patch("urllib.request.urlopen", explode):
            result = slssteam_integration.check_slssteam_binary_is_latest(cached_only=True)

        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(result["release_tag"], "20260903114323")

    def test_failed_lookup_is_negatively_cached(self):
        attempts = []

        def failing_urlopen(*args, **kwargs):
            attempts.append(args)
            raise OSError("network is down")

        with mock.patch("ui.dialogs.settings_sls.get_sls_paths", return_value=self._paths), \
                mock.patch("ui.dialogs.settings_sls.is_headcrab_installed", return_value=False), \
                mock.patch("os.path.exists", return_value=True), \
                mock.patch("os.path.getmtime", return_value=1000.0), \
                mock.patch("urllib.request.urlopen", failing_urlopen):
            first = slssteam_integration.check_slssteam_binary_is_latest()
            second = slssteam_integration.check_slssteam_binary_is_latest()

        self.assertEqual(first["status"], "error")
        self.assertEqual(second["status"], "error")
        self.assertEqual(
            len(attempts),
            1,
            "a failed lookup must be cached, otherwise every dashboard rebuild "
            "pays the full network timeout again",
        )

    def test_error_expires_before_a_successful_lookup(self):
        self.assertLess(
            slssteam_integration._BINARY_VERSION_ERROR_TTL,
            slssteam_integration._BINARY_VERSION_CACHE_TTL,
        )


class TestStatusRefreshCoalescing(unittest.TestCase):
    """refresh_system_status() schedules; it does not run on every caller."""

    def setUp(self):
        from ui.main_window import MainWindow

        self.MainWindow = MainWindow
        self.applied = []
        self.stub = types.SimpleNamespace()
        self.stub._system_status_timer = _FakeTimer()
        self.stub._apply_system_status_refresh = lambda: self.applied.append(1)

    def test_refresh_is_deferred(self):
        self.MainWindow.refresh_system_status(self.stub)
        self.assertEqual(self.applied, [], "a refresh burst must not run per call")
        self.assertEqual(self.stub._system_status_timer.started, 1)

    def test_refresh_now_applies_immediately(self):
        self.MainWindow.refresh_system_status(self.stub)
        self.MainWindow.refresh_system_status_now(self.stub)
        self.assertEqual(self.applied, [1])
        self.assertEqual(self.stub._system_status_timer.stopped, 1)

    def test_worker_thread_hands_the_work_to_the_gui_thread(self):
        emitted = []

        class _Signal:
            def emit(self):
                emitted.append(1)

        self.stub.refresh_system_status_signal = _Signal()
        self.stub._system_status_timer = _FakeTimer()

        error = []

        def worker():
            try:
                self.MainWindow.refresh_system_status(self.stub)
            except Exception as exc:  # pragma: no cover - surfaced below
                error.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertEqual(error, [])
        self.assertEqual(emitted, [1], "a worker thread must not touch the widgets")
        self.assertEqual(self.applied, [])


class TestGuiStallWatchdog(unittest.TestCase):
    """The watchdog turns "the UI felt stuck" into a number in the log."""

    def setUp(self):
        from ui.main_window import MainWindow

        self.MainWindow = MainWindow

    def test_stall_is_reported(self):
        stub = types.SimpleNamespace(
            _gui_last_heartbeat=time.monotonic() - 1.2,
            _gui_phase="boot update check",
            _log_gui_stall_stack=lambda gap: None,
        )
        with self.assertLogs(level="WARNING") as captured:
            self.MainWindow._on_gui_heartbeat(stub)
        self.assertTrue(any("stalled" in line for line in captured.output))
        self.assertTrue(any("boot update check" in line for line in captured.output))

    def test_a_long_stall_also_reports_where_the_thread_was(self):
        stub = types.SimpleNamespace(
            _gui_last_heartbeat=time.monotonic() - 1.2,
            _gui_phase="idle",
            _log_gui_stall_stack=mock.Mock(),
        )
        with self.assertLogs(level="WARNING"):
            self.MainWindow._on_gui_heartbeat(stub)
        stub._log_gui_stall_stack.assert_called_once()

    def test_responsive_loop_is_silent(self):
        stub = types.SimpleNamespace(
            _gui_last_heartbeat=time.monotonic(),
            _gui_phase="idle",
            _log_gui_stall_stack=lambda gap: None,
        )
        with self.assertNoLogs(level="WARNING"):
            self.MainWindow._on_gui_heartbeat(stub)


class TestDirectProbeTimeout(unittest.TestCase):
    """The direct tier is a probe; the fallback tiers keep the full budget."""

    def test_direct_tier_uses_the_probe_budget(self):
        import requests

        from utils import isp_bypass

        isp_bypass._direct_connection_failed = False
        timeouts = []

        class _FakeSession:
            def request(self, method, url, headers=None, params=None, timeout=None, stream=False, **kwargs):
                timeouts.append(timeout)
                raise requests.exceptions.ConnectionError("blocked")

        with mock.patch.object(isp_bypass, "get_settings", return_value=_SettingsStub("auto")), \
                mock.patch.object(isp_bypass, "resolve_doh", return_value=None), \
                mock.patch.object(isp_bypass.TorManager, "start_tor_if_needed", return_value=False):
            with self.assertRaises(requests.exceptions.ConnectionError):
                isp_bypass.execute_hubcap_request(
                    _FakeSession(), "GET", "https://hubcapmanifest.com/api/v1/user/stats"
                )

        self.assertTrue(timeouts)
        self.assertEqual(
            timeouts[0],
            isp_bypass._DIRECT_PROBE_TIMEOUT,
            "the direct tier must not wait out the full budget before failing over",
        )
        self.assertGreater(
            max(timeouts),
            timeouts[0],
            "the fallback tiers must keep the caller's full timeout budget",
        )


class TestAt0mCheckRunsBehindTheManifestCheck(unittest.TestCase):
    """The AT0-M / DLC check must not share the boot window with the
    manifest check: both hammer the same Steam endpoints."""

    def setUp(self):
        from managers.game_manager import GameManager

        self.GameManager = GameManager

    def _stub_manager(self):
        stub = types.SimpleNamespace()
        stub.manifest_check_task = None
        stub.manifest_check_runner = None
        stub.manifest_check_runner = None
        stub.games = [{"appid": "10", "game_name": "Test"}]
        stub._games_to_check = []
        stub.parent = lambda: None
        stub.settings = None
        stub.update_check_started = _Signal()
        stub.update_check_progress = _Signal()
        stub._on_game_update_checked = lambda *a: None
        stub._on_update_check_progress = lambda *a: None
        stub._on_update_check_completed = lambda *a: None
        stub._on_update_check_error = lambda *a: None
        stub._on_manifest_check_runner_cleanup = lambda *a: None
        stub._start_at0m_update_check = mock.Mock()
        gm = self.GameManager
        stub._run_pending_at0m_check = lambda: gm._run_pending_at0m_check(stub)
        stub._pending_at0m_check = None
        stub.update_checks_finished = _Signal()
        stub._update_checks_finished_emitted = False
        stub._on_at0m_stage_finished = lambda: gm._on_at0m_stage_finished(stub)
        return stub

    def test_at0m_check_is_not_started_next_to_the_manifest_check(self):
        stub = self._stub_manager()

        with mock.patch("core.tasks.manifest_check_task.ManifestCheckTask"), \
                mock.patch("managers.game_manager.TaskRunner"), \
                mock.patch("managers.at0m_update_checker.At0mUpdateChecker"):
            self.GameManager.check_game_updates_async(stub)

        stub._start_at0m_update_check.assert_not_called()

    def test_queued_at0m_check_runs_when_the_manifest_check_finishes(self):
        stub = self._stub_manager()
        stub._pending_at0m_check = True

        self.GameManager._run_pending_at0m_check(stub)

        stub._start_at0m_update_check.assert_called_once_with(force_refresh=True)
        self.assertIsNone(
            stub._pending_at0m_check,
            "the queued check must not be run twice",
        )

    def test_cancelling_drops_the_queued_at0m_check(self):
        stub = self._stub_manager()
        stub._pending_at0m_check = True

        self.GameManager.cancel_update_checks(stub)

        self.assertIsNone(stub._pending_at0m_check)

    def test_the_check_is_over_only_after_the_at0m_stage(self):
        stub = self._stub_manager()
        stub._pending_at0m_check = True

        self.GameManager._run_pending_at0m_check(stub)

        self.assertEqual(
            stub.update_checks_finished.emitted,
            [],
            "the check is not over while the AT0-M stage is still running",
        )

        self.GameManager._on_at0m_stage_finished(stub)

        self.assertEqual(len(stub.update_checks_finished.emitted), 1)

    def test_the_finished_signal_is_emitted_once(self):
        stub = self._stub_manager()

        self.GameManager._on_at0m_stage_finished(stub)
        self.GameManager._on_at0m_stage_finished(stub)

        self.assertEqual(len(stub.update_checks_finished.emitted), 1)

    def test_cancelling_releases_the_waiters(self):
        stub = self._stub_manager()

        self.GameManager.cancel_update_checks(stub)

        self.assertEqual(
            len(stub.update_checks_finished.emitted),
            1,
            "a cancelled check must release whatever waits for it",
        )


class TestPostUpdateCheckQueue(unittest.TestCase):
    """Routine network work waits for the update check to finish."""

    def setUp(self):
        from ui.main_window import MainWindow

        self.MainWindow = MainWindow

    def _stub_window(self):
        stub = types.SimpleNamespace()
        stub._after_update_check_queue = None
        stub._after_update_check_connected = False
        stub.game_manager = types.SimpleNamespace(update_checks_finished=_Signal())
        stub._drain_after_update_check_queue = lambda: self.MainWindow._drain_after_update_check_queue(stub)
        return stub

    def test_queued_work_waits_for_the_update_check(self):
        stub = self._stub_window()
        ran = []

        self.MainWindow._queue_after_update_check(stub, lambda: ran.append("prefetch"))
        self.assertEqual(ran, [], "the queued work must not run immediately")

        self.MainWindow._drain_after_update_check_queue(stub)
        self.assertEqual(ran, ["prefetch"])

    def test_the_queue_is_released_by_the_finished_signal(self):
        stub = self._stub_window()
        ran = []

        self.MainWindow._queue_after_update_check(stub, lambda: ran.append("prefetch"))
        self.assertEqual(ran, [])

        stub.game_manager.update_checks_finished.emit()

        self.assertEqual(ran, ["prefetch"])

    def test_a_failing_queued_task_does_not_stop_the_others(self):
        stub = self._stub_window()
        ran = []

        def boom():
            raise RuntimeError("network down")

        self.MainWindow._queue_after_update_check(stub, boom)
        self.MainWindow._queue_after_update_check(stub, lambda: ran.append("second"))

        self.MainWindow._drain_after_update_check_queue(stub)

        self.assertEqual(ran, ["second"])


class TestDashboardRebuildCadence(unittest.TestCase):
    """A bulk update check must not repaint the dashboard hundreds of times."""

    def test_the_automatic_rebuild_is_debounced(self):
        source = (REPO_ROOT / "src" / "ui" / "widgets" / "simplified_terminal.py").read_text()
        self.assertIn(
            "self._stats_timer.start(500)",
            source,
            "the dashboard rebuild debounce should be half a second",
        )
        self.assertNotIn("self._stats_timer.start(120)", source)


class TestLogRedactionRunsOnce(unittest.TestCase):
    """The file handler redacts through its formatter only."""

    def test_the_file_handler_has_no_second_filter(self):
        from utils import logger as logger_module

        with mock.patch.object(logger_module, "get_base_path", return_value=Path("/tmp")):
            handler = logger_module._create_file_handler(Path("/tmp/calm_boot_log_test.log"))
        try:
            self.assertIsNotNone(handler)
            self.assertFalse(
                any(isinstance(f, logger_module.SensitiveDataFilter) for f in handler.filters),
                "the file handler must not redact a second time",
            )
        finally:
            if handler is not None:
                handler.close()
            try:
                Path("/tmp/calm_boot_log_test.log").unlink()
            except OSError:
                pass

    def test_the_file_handler_still_redacts(self):
        import logging

        from utils import logger as logger_module

        with mock.patch.object(logger_module, "get_base_path", return_value=Path("/tmp")):
            handler = logger_module._create_file_handler(Path("/tmp/calm_boot_log_test2.log"))
        try:
            record = logging.LogRecord(
                "test", logging.INFO, __file__, 1,
                "api_key=abcdef1234567890", None, None,
            )
            handler.emit(record)
            handler.flush()
            written = Path("/tmp/calm_boot_log_test2.log").read_text()
            self.assertNotIn("abcdef1234567890", written)
            self.assertIn("REDACTED", written)
        finally:
            if handler is not None:
                handler.close()
            try:
                Path("/tmp/calm_boot_log_test2.log").unlink()
            except OSError:
                pass


class TestAt0mConfigIndex(unittest.TestCase):
    """The AT0-M discovery used to re-scan the whole SLSsteam config per appid."""

    def setUp(self):
        from managers.at0m_update_checker import index_appids_in_key_comments

        self.index_appids_in_key_comments = index_appids_in_key_comments

    def test_it_maps_appids_to_the_key_they_sit_under(self):
        text = (
            "DecryptionKeys:\n"
            "  1234567890: aabbccdd  # 2230763\n"
            "  '987654321': eeff0011\n"
            "  555: dd # 111222\n"
        )
        index = self.index_appids_in_key_comments(text)
        self.assertEqual(index["2230763"], {"1234567890"})
        self.assertEqual(index["111222"], {"555"})
        self.assertNotIn("987654321", index)

    def test_a_long_config_is_indexed_in_one_pass(self):
        lines = ["DecryptionKeys:"]
        for i in range(5000):
            lines.append(f"  {i}: key{i:032x}")
        lines.append("  9999: key  # 42")
        text = "\n".join(lines)

        start = time.perf_counter()
        index = self.index_appids_in_key_comments(text)
        elapsed = time.perf_counter() - start

        self.assertEqual(index["42"], {"9999"})
        self.assertLess(
            elapsed,
            0.5,
            "indexing the config must not cost more than one pass over the file",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
