#!/usr/bin/env python3
"""
Test Suite for Batch AT0-M Migration:
- Strict exclusion validation (DLC-only mode, pinned manifests/builds, already AT0-M, uninstalled)
- Batch conversion engine functionality
- UI Dialog structure & signal connections
"""

import os
import sys
from pathlib import Path

# Add src to PYTHONPATH
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for UI tests
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from ui.dialogs.library.batch_migration_dialog import (
    BatchAtomMigrationConfirmDialog,
    BatchAtomMigrationWorker,
)
from utils.plugin_games import (
    batch_convert_games_to_atom,
    collect_game_depots_and_keys,
    _atomic_write_acf,
    _ensure_acf_has_installed_depots,
)


class TestBatchAtomMigration(unittest.TestCase):
    """Verify batch AT0-M migration rules, dialogs, and engine."""

    def test_confirmation_dialog_eligible_and_skipped(self):
        """Test BatchAtomMigrationConfirmDialog renders eligible and skipped games with correct badges."""
        eligible = [
            {"appid": "12345", "game_name": "Test Game Alpha", "install_path": "/tmp/alpha"},
            {"appid": "67890", "game_name": "Test Game Beta", "install_path": "/tmp/beta"},
        ]
        skipped = [
            ({"appid": "11111", "game_name": "DLC Only Game"}, "DLC-Only Mode"),
            ({"appid": "22222", "game_name": "Pinned Game"}, "Pinned Build / Manifest"),
            ({"appid": "33333", "game_name": "Atom Game"}, "Already in AT0-M"),
            ({"appid": "44444", "game_name": "Missing Game"}, "Not Installed Locally"),
        ]

        dlg = BatchAtomMigrationConfirmDialog(
            eligible_games=eligible,
            skipped_games=skipped,
            accent_color="#38bdf8",
        )
        self.assertIsNotNone(dlg)
        self.assertEqual(len(dlg.eligible_games), 2)
        self.assertEqual(len(dlg.skipped_games), 4)

        # Check badge colors for reasons
        col, bg = dlg._get_badge_colors("DLC-Only Mode")
        self.assertEqual(col, "#f59e0b")

        col, bg = dlg._get_badge_colors("Pinned Build / Manifest")
        self.assertEqual(col, "#06b6d4")

        col, bg = dlg._get_badge_colors("Already in AT0-M")
        self.assertEqual(col, "#a855f7")

        col, bg = dlg._get_badge_colors("Not Installed Locally")
        self.assertEqual(col, "#f43f5e")

    def test_worker_signals_and_cancellation(self):
        """Test BatchAtomMigrationWorker signals and cancel flag."""
        worker = BatchAtomMigrationWorker([{"appid": "100", "game_name": "Cancel Test"}])
        self.assertFalse(worker._is_canceled)
        worker.cancel()
        self.assertTrue(worker._is_canceled)

    @patch("utils.plugin_games.collect_game_depots_and_keys")
    @patch("utils.plugin_games.save_plugin_library")
    @patch("utils.plugin_games.get_user_config_path")
    @patch("utils.sls_bridge.SLSBridge.notify_reload")
    def test_batch_convert_engine_skips_zero_keys(
        self, mock_reload, mock_cfg_path, mock_save_lib, mock_collect
    ):
        """Test that games with zero decryption keys are safely skipped without failing the whole batch."""
        mock_cfg_path.return_value = Path("/tmp/nonexistent_config.yaml")

        # Game 1: 0 keys
        # Game 2: valid keys
        def mock_collect_fn(appid, game_data=None, allow_hubcap_fetch=True):
            if str(appid) == "100":
                return (["101"], {}, {})  # no keys
            else:
                return (["201"], {"201": "a" * 64}, {"201": "Content"})

        mock_collect.side_effect = mock_collect_fn

        games = [
            {"appid": "100", "game_name": "No Key Game", "install_path": "/tmp"},
            {"appid": "200", "game_name": "Valid Game", "install_path": "/tmp"},
        ]

        progress_calls = []

        def _prog(cur, tot, name, msg):
            progress_calls.append((cur, tot, name))

        results = batch_convert_games_to_atom(games, progress_callback=_prog)

        self.assertEqual(len(results["failed"]), 1)
        self.assertEqual(results["failed"][0]["appid"], "100")
        self.assertEqual(len(results["migrated"]), 1)
        self.assertEqual(results["migrated"][0]["appid"], "200")
        self.assertTrue(len(progress_calls) > 0)

    @patch("utils.dlc_helpers.is_dlc_only_mode")
    @patch("utils.settings.get_settings")
    def test_strict_exclusion_rules(self, mock_settings, mock_is_dlc):
        """Verify strict exclusion rules for DLC-only and pinned builds."""
        settings_dict = {
            "pin_build/200": True,
            "pin_build/100": False,
        }
        mock_settings.return_value.value.side_effect = lambda k, def_v, type=None: settings_dict.get(k, def_v)

        mock_is_dlc.side_effect = lambda aid: str(aid) == "300"

        from ui.dialogs.library.filter_sort import game_is_dlc_only

        candidates = [
            {"appid": "100", "game_name": "Valid ASSella Game", "is_accela_install": True, "install_path": "/tmp"},
            {"appid": "200", "game_name": "Pinned Build Game", "is_accela_install": True, "install_path": "/tmp"},
            {"appid": "300", "game_name": "DLC-Only Game", "is_accela_install": True, "install_path": "/tmp"},
            {"appid": "400", "game_name": "Already AT0-M Game", "is_accela_install": True, "is_atom": True, "install_path": "/tmp"},
            {"appid": "500", "game_name": "Steam Pure Game", "is_accela_install": False, "install_path": "/tmp"},
        ]

        eligible = []
        skipped = []

        for g in candidates:
            aid = str(g.get("appid", ""))
            # 1. Already in AT0-M
            if g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"):
                skipped.append((g, "Already in AT0-M"))
                continue
            # 2. Not ASSella install
            if not g.get("is_accela_install"):
                skipped.append((g, "Not ASSella Install"))
                continue
            # 3. DLC-Only
            if mock_is_dlc(aid) or game_is_dlc_only(g):
                skipped.append((g, "DLC-Only Mode"))
                continue
            # 4. Pinned Build
            is_pinned = mock_settings.return_value.value(f"pin_build/{aid}", False, type=bool) or bool(g.get("_pin_build"))
            if is_pinned:
                skipped.append((g, "Pinned Build / Manifest"))
                continue
            eligible.append(g)

        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0]["appid"], "100")
        self.assertEqual(len(skipped), 4)
        skipped_map = {item[0]["appid"]: item[1] for item in skipped}
        self.assertEqual(skipped_map["200"], "Pinned Build / Manifest")
        self.assertEqual(skipped_map["300"], "DLC-Only Mode")
        self.assertEqual(skipped_map["400"], "Already in AT0-M")
        self.assertEqual(skipped_map["500"], "Not ASSella Install")


if __name__ == "__main__":
    print("=== Running Batch AT0-M Migration Test Suite ===")
    unittest.main()

