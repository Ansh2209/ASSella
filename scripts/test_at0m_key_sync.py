#!/usr/bin/env python3
"""Tests for the AT0-M key-sync feature.

AT0-M games are not downloaded by ASSella: Steam downloads them, ASSella only
has to hand the decryption keys to SLSsteam. The feature therefore needs three
things to hold:

* a cheap, cached answer to "does Hubcap have a key for this depot?" (the
  /depot-keys payload is ~7 MB, so it must never be fetched on the GUI thread),
* a verdict per game that distinguishes "Hubcap has the key" from "Hubcap does
  not have the key yet" from "nobody could be asked",
* a UI that shows AT0-M games in the pending-updates panel with a "Sync Keys"
  action instead of a download.

    python3 scripts/test_at0m_key_sync.py
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import hubcap_key_index
from core.hubcap_key_index import (
    CACHE_TTL_SECONDS,
    STATE_AVAILABLE,
    STATE_PENDING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    HubcapKeyIndex,
)
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMainWindow


def _payload(existing, pending, etag="etag-1"):
    return {
        "ok": True,
        "not_modified": False,
        "data": {
            "existing_depot_ids": list(existing),
            "pending_depot_ids": list(pending),
        },
        "etag": etag,
        "error": None,
    }


class _IndexTestBase(unittest.TestCase):
    def setUp(self):
        hubcap_key_index.reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = Path(self._tmp.name) / "hubcap_depot_keys.json"
        patcher = mock.patch.object(
            hubcap_key_index, "get_cache_path", return_value=self.cache_path
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class TestIndexStates(_IndexTestBase):
    def test_the_four_states_stay_apart(self):
        index = HubcapKeyIndex(existing=["11", "12"], pending=["21"])
        self.assertEqual(index.state("11"), STATE_AVAILABLE)
        self.assertEqual(index.state("21"), STATE_PENDING)
        self.assertEqual(index.state("99"), STATE_UNKNOWN)
        self.assertTrue(index.has_key("11"))
        self.assertFalse(index.has_key("21"))
        self.assertFalse(index.has_key("99"))

    def test_an_unloadable_index_reports_unavailable_not_unknown(self):
        index = HubcapKeyIndex(error="no network")
        self.assertFalse(index.loaded)
        self.assertEqual(index.state("11"), STATE_UNAVAILABLE)
        self.assertEqual(index.to_dict(), {"error": "no network"})

    def test_lookups_for_every_at0m_game_are_a_set_hit(self):
        # 50 games x 6 depots must stay far below a millisecond.
        index = HubcapKeyIndex(existing=[str(i) for i in range(100000)])
        depots = [str(i) for i in range(6)]
        start = time.perf_counter()
        for _ in range(50):
            for did in depots:
                index.state(did)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.001, f"300 lookups took {elapsed * 1000:.2f} ms")


class TestIndexDiskCache(_IndexTestBase):
    def test_a_snapshot_survives_a_restart(self):
        index = HubcapKeyIndex(existing=["1", "2"], pending=["3"], etag="e1")
        hubcap_key_index._save_to_disk(index)
        self.assertTrue(self.cache_path.exists())

        loaded = hubcap_key_index._load_from_disk()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.existing, {"1", "2"})
        self.assertEqual(loaded.pending, {"3"})
        self.assertEqual(loaded.etag, "e1")

    def test_a_corrupt_cache_file_is_ignored(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(hubcap_key_index._load_from_disk())

    def test_a_cache_from_another_version_is_ignored(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"version": 999, "existing_depot_ids": ["1"]}), encoding="utf-8"
        )
        self.assertIsNone(hubcap_key_index._load_from_disk())

    def test_the_cache_is_written_atomically(self):
        index = HubcapKeyIndex(existing=["1"], pending=[])
        hubcap_key_index._save_to_disk(index)
        leftovers = list(self.cache_path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [], "a temporary file was left behind")


class TestIndexRefresh(_IndexTestBase):
    def setUp(self):
        super().setUp()
        # These tests exercise the refresh logic itself; the GUI-thread guard
        # has its own test below.
        patcher = mock.patch.object(hubcap_key_index, "_on_gui_thread", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_fresh_index_is_not_refetched(self):
        hubcap_key_index._index = HubcapKeyIndex(existing=["1"], fetched_at=time.time())
        with mock.patch(
            "core.morrenus_api.fetch_depot_keys_index", side_effect=AssertionError("refetched")
        ):
            index = hubcap_key_index.get_index(allow_network=True)
        self.assertTrue(index.loaded)

    def test_a_stale_index_is_refetched(self):
        hubcap_key_index._index = HubcapKeyIndex(
            existing=["1"], fetched_at=time.time() - CACHE_TTL_SECONDS - 1
        )
        with mock.patch(
            "core.morrenus_api.fetch_depot_keys_index", return_value=_payload(["1", "2"], ["3"])
        ) as fetch:
            index = hubcap_key_index.get_index(allow_network=True)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(index.existing, {"1", "2"})
        self.assertEqual(index.pending, {"3"})

    def test_a_not_modified_answer_keeps_the_snapshot(self):
        hubcap_key_index._index = HubcapKeyIndex(
            existing=["1"], fetched_at=time.time() - CACHE_TTL_SECONDS - 1, etag="e1"
        )
        with mock.patch(
            "core.morrenus_api.fetch_depot_keys_index",
            return_value={"ok": True, "not_modified": True, "data": None, "etag": "e1", "error": None},
        ) as fetch:
            index = hubcap_key_index.refresh()
        self.assertEqual(fetch.call_args.kwargs.get("etag"), "e1")
        self.assertTrue(index.loaded)
        self.assertEqual(index.existing, {"1"})
        self.assertTrue(index.is_fresh(), "the timestamp was not refreshed")

    def test_a_failed_fetch_keeps_the_snapshot_and_backs_off(self):
        hubcap_key_index._index = HubcapKeyIndex(
            existing=["1"], fetched_at=time.time() - CACHE_TTL_SECONDS - 1
        )
        with mock.patch(
            "core.morrenus_api.fetch_depot_keys_index",
            return_value={"ok": False, "not_modified": False, "data": None, "etag": None, "error": "boom"},
        ) as fetch:
            first = hubcap_key_index.refresh()
            second = hubcap_key_index.refresh()
        self.assertEqual(fetch.call_count, 1, "the failed fetch was retried")
        self.assertTrue(first.loaded, "the stale snapshot was thrown away")
        self.assertEqual(first.existing, {"1"})
        self.assertEqual(second.existing, {"1"})

    def test_without_network_permission_the_disk_cache_is_used(self):
        with mock.patch(
            "core.morrenus_api.fetch_depot_keys_index", side_effect=AssertionError("network used")
        ):
            index = hubcap_key_index.get_index(allow_network=False)
        self.assertFalse(index.loaded)
        self.assertIn("error", index.to_dict())

    def test_the_gui_thread_never_fetches_the_payload(self):
        with mock.patch.object(hubcap_key_index, "_on_gui_thread", return_value=True):
            with mock.patch(
                "core.morrenus_api.fetch_depot_keys_index", side_effect=AssertionError("fetched")
            ):
                index = hubcap_key_index.refresh(force=True)
        self.assertFalse(index.loaded)
        self.assertIn("error", index.to_dict())


class _FakeDb:
    """Just enough of DatabaseManager for the checker."""

    def __init__(self):
        self.rows = {}

    def get_at0m_update_status(self, appid):
        return self.rows.get(str(appid))

    def save_at0m_update_status(
        self,
        appid,
        game_title="",
        last_timeupdated=0,
        status="up_to_date",
        new_depots=None,
        cooldown_until=0,
    ):
        self.rows[str(appid)] = {
            "appid": str(appid),
            "game_title": game_title,
            "last_timeupdated": last_timeupdated,
            "last_checked": int(time.time()),
            "status": status,
            "new_depots": new_depots or [],
            "cooldown_until": cooldown_until,
        }

    def get_app_info(self, appid):
        return None


def _steam_info(depots, timeupdated=1000, name="Test Game"):
    return {
        "name": name,
        "timeupdated": timeupdated,
        "depots": {did: dict(data) for did, data in depots.items()},
    }


def _depot(name="Depot", oslist="windows", size=1024):
    return {"name": name, "oslist": oslist, "size": size, "manifests": {"public": {"download": "1"}}}


class _CheckerTestBase(unittest.TestCase):
    def setUp(self):
        from managers.at0m_update_checker import At0mUpdateChecker

        self.checker = At0mUpdateChecker()
        self.checker.db = _FakeDb()

    def _patch_steam(self, info):
        return mock.patch(
            "core.steam_api.fetch_steamcmd_info", return_value=info
        )

    def _patch_index(self, index):
        return mock.patch("core.hubcap_key_index.get_index", return_value=index)


class TestCheckerKeyDelta(_CheckerTestBase):
    def test_a_depot_without_a_key_counts_as_missing(self):
        # The depot id is known locally, but no key was ever stored for it.
        info = _steam_info({"111": _depot()})
        with self._patch_steam(info):
            with self._patch_index(HubcapKeyIndex(existing=["111"])):
                with mock.patch.object(
                    self.checker,
                    "get_locally_known_depots",
                    return_value=({"111", "222"}, {"222": "a" * 64}),
                ):
                    status, details = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "update_available")
        self.assertEqual([d["id"] for d in details["new_depots"]], ["111"])

    def test_a_fully_keyed_game_is_up_to_date(self):
        info = _steam_info({"111": _depot()})
        with self._patch_steam(info):
            with self._patch_index(HubcapKeyIndex(existing=["111"])):
                with mock.patch.object(
                    self.checker,
                    "get_locally_known_depots",
                    return_value=({"111"}, {"111": "a" * 64}),
                ):
                    status, _ = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "up_to_date")

    def test_a_depot_hubcap_does_not_know_yet_is_awaiting(self):
        info = _steam_info({"111": _depot()})
        with self._patch_steam(info):
            with self._patch_index(HubcapKeyIndex(existing=[], pending=["111"])):
                with mock.patch.object(
                    self.checker,
                    "get_locally_known_depots",
                    return_value=(set(), {}),
                ):
                    status, details = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "key_pending")
        self.assertTrue(details["awaiting_hubcap"])
        self.assertEqual([d["id"] for d in details["pending_depots"]], ["111"])

    def test_an_unavailable_index_is_cannot_determine(self):
        info = _steam_info({"111": _depot()})
        with self._patch_steam(info):
            with self._patch_index(HubcapKeyIndex(error="no network")):
                with mock.patch.object(
                    self.checker, "get_locally_known_depots", return_value=(set(), {})
                ):
                    status, details = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "cannot_determine")
        self.assertIn("no network", details["reason"])

    def test_a_silent_steam_is_cannot_determine_not_up_to_date(self):
        with self._patch_steam(None):
            status, details = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "cannot_determine")
        self.assertEqual(details["reason"], "Steam did not answer")

    def test_a_pending_game_is_promoted_when_the_key_shows_up(self):
        # The cache still says key_pending, but the index now has the key.
        self.checker.db.save_at0m_update_status(
            appid="999",
            game_title="Test Game",
            last_timeupdated=1000,
            status="key_pending",
            new_depots=[{"id": "111", "name": "Depot", "has_key": False}],
            cooldown_until=int(time.time()) + 3600,
        )
        with self._patch_index(HubcapKeyIndex(existing=["111"])):
            status, details = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "update_available")
        self.assertEqual([d["id"] for d in details["new_depots"]], ["111"])

    def test_the_verdict_survives_the_timeupdate_diff(self):
        # The build did not change, but the keys are still missing: the old
        # code overwrote the pending verdict with "up to date" here.
        self.checker.db.save_at0m_update_status(
            appid="999",
            game_title="Test Game",
            last_timeupdated=1000,
            status="key_pending",
            new_depots=[{"id": "111", "name": "Depot", "has_key": False}],
            cooldown_until=0,
        )
        info = _steam_info({"111": _depot()}, timeupdated=1000)
        with self._patch_steam(info):
            with self._patch_index(HubcapKeyIndex(existing=[], pending=["111"])):
                with mock.patch.object(
                    self.checker, "get_locally_known_depots", return_value=(set(), {})
                ):
                    status, _ = self.checker.check_game("999", "Test Game")
        self.assertEqual(status, "key_pending")


class TestInstallPlatformFilter(_CheckerTestBase):
    def test_a_proton_install_ignores_the_linux_depot(self):
        info = _steam_info(
            {
                "111": _depot(name="Windows", oslist="windows"),
                "222": _depot(name="Linux", oslist="linux"),
            }
        )
        with mock.patch.object(
            self.checker, "_installed_depot_ids", return_value={"111"}
        ):
            depots, _ddata, platform = self.checker._collect_steam_depots("999", info)
        self.assertEqual(platform, "windows")
        self.assertEqual(depots, {"111"})

    def test_without_an_install_every_platform_depot_counts(self):
        info = _steam_info(
            {
                "111": _depot(name="Windows", oslist="windows"),
                "222": _depot(name="Linux", oslist="linux"),
            }
        )
        with mock.patch.object(self.checker, "_installed_depot_ids", return_value=set()):
            depots, _ddata, platform = self.checker._collect_steam_depots("999", info)
        self.assertIsNone(platform)
        self.assertEqual(depots, {"111", "222"})


class TestPanelSplit(unittest.TestCase):
    """The dashboard has to tell the two kinds of work apart."""

    def _terminal_source(self):
        return (REPO_ROOT / "src" / "ui" / "widgets" / "simplified_terminal.py").read_text(
            encoding="utf-8"
        )

    def test_the_panel_keeps_atom_games_in_the_pending_list(self):
        source = self._terminal_source()
        self.assertIn("is_atom_managed", source)
        self.assertNotIn(
            'not (g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"))',
            source,
            "the atom games are still filtered out of the pending updates",
        )

    def test_the_action_is_called_sync_keys(self):
        source = self._terminal_source()
        self.assertIn("Sync Keys", source)
        self.assertIn("Awaiting Hubcap Key", source)

    def test_the_web_ui_does_not_try_to_download_atom_games(self):
        source = (REPO_ROOT / "src" / "utils" / "web_server.py").read_text(encoding="utf-8")
        self.assertIn("is_atom_managed", source)

    def test_the_update_all_button_reports_both_kinds_of_work(self):
        source = self._terminal_source()
        self.assertIn("games +", source)
        self.assertIn("keys)", source)


class _StubGameManager:
    """The bits of the game manager the dashboard widget talks to."""

    def __init__(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Signals(QObject):
            library_updated = pyqtSignal()
            game_update_status_changed = pyqtSignal(str, str)
            scan_complete = pyqtSignal(int)
            update_check_started = pyqtSignal()
            all_updates_checked = pyqtSignal()

        self._signals = _Signals()
        self.games = []
        self.is_scanning = False

    def __getattr__(self, name):
        return getattr(self._signals, name)


class _StubMainWindow(QMainWindow):
    """Just enough MainWindow for the dashboard widget."""

    def __init__(self):
        super().__init__()
        self.settings = QSettings("Tachibana Labs", "ACCELA_Test")
        self.game_manager = _StubGameManager()
        self.accent_color = "#C06C84"
        self._at0m_key_syncs_running = set()

    def run_update_all_flow(self):
        pass

    def force_check_all_updates(self):
        pass

    def refresh_system_status(self):
        pass

    def position_update_all_btn(self):
        pass


class TestDashboardRows(unittest.TestCase):
    """The panel has to show the AT0-M games next to the downloadable ones."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self, games):
        from ui.widgets.simplified_terminal import SimplifiedTerminalWidget
        from ui.widgets.updates_panel import UpdateItemWidget

        win = _StubMainWindow()
        win.game_manager.games = games
        widget = SimplifiedTerminalWidget(win)
        widget._do_update_stats()

        rows = []
        layout = widget.updates_scroll_layout
        for i in range(layout.count()):
            item = layout.itemAt(i)
            child = item.widget()
            if isinstance(child, UpdateItemWidget):
                rows.append(child)
        self.addCleanup(widget.deleteLater)
        return widget, rows

    def test_the_three_kinds_of_rows_are_rendered(self):
        games = [
            {"appid": "1", "game_name": "Download Me", "update_status": "update_available"},
            {
                "appid": "2",
                "game_name": "Sync Me",
                "update_status": "update_available",
                "is_atom": True,
            },
            {
                "appid": "3",
                "game_name": "Waiting",
                "update_status": "key_pending",
                "is_atom": True,
            },
        ]
        widget, rows = self._panel(games)
        self.assertEqual(len(rows), 3, "not every pending game showed up")
        by_appid = {r.appid: r for r in rows}
        self.assertIsNone(by_appid["1"].action_btn, "a download row got a key-sync button")
        self.assertEqual(by_appid["2"].action_btn.text(), "Sync Keys")
        self.assertEqual(by_appid["2"].badge.text(), "AT0-M")
        self.assertIsNone(by_appid["3"].action_btn, "a pending game got a sync button")
        self.assertEqual(by_appid["3"].badge.text(), "Awaiting Hubcap Key")

    def test_the_button_counts_both_kinds_of_work(self):
        games = [
            {"appid": "1", "game_name": "Download Me", "update_status": "update_available"},
            {
                "appid": "2",
                "game_name": "Sync Me",
                "update_status": "update_available",
                "is_atom": True,
            },
        ]
        widget, _rows = self._panel(games)
        self.assertIn("1 games + 1 keys", widget.update_all_btn.text())

    def test_a_pinned_game_is_hidden(self):
        settings = QSettings("Tachibana Labs", "ACCELA_Test")
        settings.setValue("exclude_from_update_all/1", True)
        settings.sync()
        games = [
            {"appid": "1", "game_name": "Download Me", "update_status": "update_available"},
            {
                "appid": "2",
                "game_name": "Sync Me",
                "update_status": "update_available",
                "is_atom": True,
            },
        ]
        _widget, rows = self._panel(games)
        self.assertEqual([r.appid for r in rows], ["2"])
        settings.remove("exclude_from_update_all/1")
        settings.sync()


if __name__ == "__main__":
    unittest.main(verbosity=2)
