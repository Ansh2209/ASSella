#!/usr/bin/env python3
"""
Test Suite for AT0-M Move Complications & Invariants:
1. detect_migration_complications (clean game, pinned build, DLC-only, Goldberg, EOS proxy)
2. revert_goldberg_if_applied (restores .valve files, removes steam_settings and steam_appid.txt)
3. revert_eos_proxy_if_applied (calls EOSDetector.remove_proxy safely)
4. migrate_game_to_atom with ManifestIds (pinned build writes ManifestIds block in config.yaml)
5. migrate_game_to_atom DLC-only mode (only DLC AppIDs and DLC depots in config.yaml, no base AppID)
6. Verify install API command is completely eliminated (no slssteam_api_send("install|..."))
7. AtomMigrationComplicationsDialog UI validation
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to PYTHONPATH
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from ui.dialogs.atom_complications_dialog import AtomMigrationComplicationsDialog
from utils.plugin_games import (
    detect_migration_complications,
    migrate_game_to_atom,
    revert_eos_proxy_if_applied,
    revert_goldberg_if_applied,
)


class TestAtomMoveComplications(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="assella_test_atom_comp_")
        self.game_dir = Path(self.test_dir) / "TestGame"
        self.game_dir.mkdir(parents=True, exist_ok=True)

        self.config_path = Path(self.test_dir) / "config.yaml"
        self.config_path.write_text(
            "Plugins: yes\n"
            "SmartTickets: 0x1\n"
            "AdditionalApps:\n"
            "AdditionalDepots:\n"
            "DecryptionKeys:\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Complication Detection
    # -------------------------------------------------------------------------
    def test_detect_clean_game(self):
        """Clean game without complications should return has_complications=False."""
        game_data = {
            "appid": "12345",
            "game_name": "Clean Game",
            "install_path": str(self.game_dir),
            "buildid": 100,
        }
        res = detect_migration_complications(game_data, settings=None)
        self.assertFalse(res["has_complications"])
        self.assertIsNone(res["pinned"])
        self.assertIsNone(res["dlc_only"])
        self.assertFalse(res["eos"])
        self.assertFalse(res["goldberg"])

    def test_detect_pinned_build_from_metadata(self):
        """Game with pinned build in metadata should be detected."""
        game_data = {
            "appid": "12345",
            "game_name": "Pinned Game",
            "install_path": str(self.game_dir),
            "pin_build": True,
            "pinned_buildid": 500,
            "buildid": 500,
        }
        with patch("core.native_steam.steam_manifest_pinning.resolve_pinned_manifests", return_value={"12346": "999888777"}):
            res = detect_migration_complications(game_data, settings=None)
        self.assertTrue(res["has_complications"])
        self.assertIsNotNone(res["pinned"])
        self.assertEqual(res["pinned"]["build_id"], "500")
        self.assertEqual(res["pinned"]["manifest_map"], {"12346": "999888777"})

    def test_resolve_pinned_manifests_aggregates_multiple_depots(self):
        """resolve_pinned_manifests aggregates manifest IDs from overrides and depots dictionary."""
        from core.native_steam.steam_manifest_pinning import resolve_pinned_manifests

        game_data = {
            "appid": "70000",
            "pin_build": True,
            "manifest_overrides": {
                "70001": "111111111",
            },
            "depots": {
                "70002": {"manifest_id": "222222222"},
                "70003": {"gid": "333333333"},
            },
        }
        res = resolve_pinned_manifests(game_data, "70000")
        self.assertEqual(res.get("70001"), "111111111")
        self.assertEqual(res.get("70002"), "222222222")
        self.assertEqual(res.get("70003"), "333333333")

    def test_detect_dlc_only_game(self):
        """DLC-only game should report dlc_only info and list DLC AppIDs."""
        game_data = {
            "appid": "2879840",
            "game_name": "Base Game",
            "install_path": str(self.game_dir),
            "dlc_only": True,
            "is_dlc_only": True,
            "selected_depots": ["2879842"],
        }
        with patch("utils.dlc_helpers.resolve_dlc_mapping_for_selection", return_value=({"2879841": ["2879842"]}, {"2879842"})):
            res = detect_migration_complications(game_data, settings=None)
        self.assertTrue(res["has_complications"])
        self.assertIsNotNone(res["dlc_only"])
        self.assertIn("2879841", res["dlc_only"]["dlc_appids"])
        self.assertIn("2879842", res["dlc_only"]["depots"])

    def test_detect_goldberg_emulator(self):
        """Presence of .valve backup file should detect Goldberg emulator."""
        valve_file = self.game_dir / "steam_api64.dll.valve"
        valve_file.write_text("original valve dll", encoding="utf-8")
        settings_dir = self.game_dir / "steam_settings"
        settings_dir.mkdir(parents=True, exist_ok=True)

        game_data = {
            "appid": "12345",
            "game_name": "Goldberg Game",
            "install_path": str(self.game_dir),
        }
        res = detect_migration_complications(game_data, settings=None)
        self.assertTrue(res["has_complications"])
        self.assertTrue(res["goldberg"])

    def test_detect_eos_proxy(self):
        """Active EOS proxy should be detected."""
        game_data = {
            "appid": "12345",
            "game_name": "EOS Game",
            "install_path": str(self.game_dir),
        }
        with patch("utils.eos_detector.EOSDetector.get_proxy_status", return_value="active"):
            res = detect_migration_complications(game_data, settings=None)
        self.assertTrue(res["has_complications"])
        self.assertTrue(res["eos"])

    # -------------------------------------------------------------------------
    # 2. Goldberg Reversion
    # -------------------------------------------------------------------------
    def test_revert_goldberg(self):
        """revert_goldberg_if_applied restores .valve backups and deletes configs."""
        dll_file = self.game_dir / "steam_api64.dll"
        dll_file.write_text("goldberg modified dll", encoding="utf-8")
        valve_file = self.game_dir / "steam_api64.dll.valve"
        valve_file.write_text("original valve dll", encoding="utf-8")

        settings_dir = self.game_dir / "steam_settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.ini").write_text("offline=1", encoding="utf-8")
        appid_txt = self.game_dir / "steam_appid.txt"
        appid_txt.write_text("12345", encoding="utf-8")

        success = revert_goldberg_if_applied(str(self.game_dir))
        self.assertTrue(success)
        # .valve should be moved to .dll
        self.assertEqual(dll_file.read_text(encoding="utf-8"), "original valve dll")
        self.assertFalse(valve_file.exists())
        self.assertFalse(settings_dir.exists())
        self.assertFalse(appid_txt.exists())

    def test_revert_goldberg_handles_missing_dir(self):
        """revert_goldberg_if_applied safely returns True (clean no-op) if dir does not exist."""
        self.assertTrue(revert_goldberg_if_applied("/tmp/nonexistent_folder_abc_123"))

    # -------------------------------------------------------------------------
    # 3. EOS Proxy Reversion
    # -------------------------------------------------------------------------
    def test_revert_eos_proxy(self):
        """revert_eos_proxy_if_applied calls EOSDetector.remove_proxy safely."""
        with patch("utils.eos_detector.EOSDetector.get_proxy_status", return_value="active"), \
             patch("utils.eos_detector.EOSDetector.remove_proxy", return_value=True) as mock_remove:
            success = revert_eos_proxy_if_applied(str(self.game_dir))
            self.assertTrue(success)
            mock_remove.assert_called_once()

    def test_revert_eos_proxy_ignores_exceptions(self):
        """revert_eos_proxy_if_applied gracefully handles exceptions without raising."""
        with patch("utils.eos_detector.EOSDetector.get_proxy_status", side_effect=RuntimeError("detection error")):
            # Should not raise
            res = revert_eos_proxy_if_applied(str(self.game_dir))
            self.assertFalse(res)

    # -------------------------------------------------------------------------
    # 4. ManifestIds Written on Pinned Build Migration
    # -------------------------------------------------------------------------
    def test_migrate_game_to_atom_pinned_build_writes_manifest_ids(self):
        """Pinned build migration writes ManifestIds block into SLSsteam config.yaml."""
        game_data = {
            "appid": "55555",
            "game_name": "Pinned Game",
            "install_path": str(self.game_dir),
            "pin_build": True,
            "pinned_buildid": 9999,
            "buildid": 9999,
            "depots": ["55556"],
            "keys": {"55556": "f" * 64},
        }

        # Mock config path, keys, and manifest resolver
        with patch("utils.plugin_games.get_user_config_path", return_value=self.config_path), \
             patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(["55556"], {"55556": "f" * 64}, {"55556": "Depot"})), \
             patch("core.native_steam.steam_manifest_pinning.resolve_pinned_manifests", return_value={"55556": "123456789012345678"}), \
             patch("utils.steam_acf.find_appmanifest_path", return_value=None), \
             patch("utils.plugin_games.save_plugin_library", return_value=True):
            res = migrate_game_to_atom(game_data)
            self.assertTrue(res.get("success"), f"Migration failed: {res.get('error')}")

        # Check config.yaml contents
        cfg_text = self.config_path.read_text(encoding="utf-8")
        self.assertIn("ManifestIds:", cfg_text)
        self.assertIn("55556: 123456789012345678", cfg_text)
        self.assertIn("55555", cfg_text)  # Base AppID in AdditionalApps

    def test_migrate_game_to_atom_pinned_build_multiple_manifests(self):
        """Pinned build with multiple depots/manifests writes all of them to ManifestIds."""
        game_data = {
            "appid": "66666",
            "game_name": "Multi Manifest Game",
            "install_path": str(self.game_dir),
            "pin_build": True,
            "pinned_buildid": 8888,
            "buildid": 8888,
            "depots": ["66667", "66668", "66669"],
            "keys": {
                "66667": "1" * 64,
                "66668": "2" * 64,
                "66669": "3" * 64,
            },
        }

        multi_manifests = {
            "66667": "100000000000000001",
            "66668": "200000000000000002",
            "66669": "300000000000000003",
        }

        with patch("utils.plugin_games.get_user_config_path", return_value=self.config_path), \
             patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(["66667", "66668", "66669"], game_data["keys"], {})), \
             patch("core.native_steam.steam_manifest_pinning.resolve_pinned_manifests", return_value=multi_manifests), \
             patch("utils.steam_acf.find_appmanifest_path", return_value=None), \
             patch("utils.plugin_games.save_plugin_library", return_value=True):
            res = migrate_game_to_atom(game_data)
            self.assertTrue(res.get("success"), f"Migration failed: {res.get('error')}")

        cfg_text = self.config_path.read_text(encoding="utf-8")
        self.assertIn("ManifestIds:", cfg_text)
        self.assertIn("66667: 100000000000000001", cfg_text)
        self.assertIn("66668: 200000000000000002", cfg_text)
        self.assertIn("66669: 300000000000000003", cfg_text)
        self.assertIn("66666", cfg_text)

    # -------------------------------------------------------------------------
    # 5. DLC-Only Migration Invariants
    # -------------------------------------------------------------------------
    def test_migrate_game_to_atom_dlc_only(self):
        """DLC-only mode only adds DLC AppIDs and DLC depots to config.yaml (never base AppID)."""
        base_appid = "2879840"
        dlc_appid = "4575720"
        dlc_depot = "4575721"
        depot_key = "e" * 64

        game_data = {
            "appid": base_appid,
            "game_name": "DLC Base Game",
            "install_path": str(self.game_dir),
            "dlc_only": True,
            "is_dlc_only": True,
            "dlc_only_appid_mapping": {dlc_appid: [dlc_depot]},
            "selected_depots": [dlc_depot],
            "depots": [dlc_depot],
            "keys": {dlc_depot: depot_key},
            "depot_names": {dlc_depot: "DLC Depot 1"},
        }

        with patch("utils.plugin_games.get_user_config_path", return_value=self.config_path), \
             patch("utils.plugin_games.collect_game_depots_and_keys", return_value=([dlc_depot], {dlc_depot: depot_key}, {dlc_depot: "DLC Depot 1"})), \
             patch("utils.dlc_helpers.resolve_dlc_mapping_for_selection", return_value=({dlc_appid: "DLC 4575720"}, {dlc_depot})), \
             patch("utils.steam_acf.find_appmanifest_path", return_value=None), \
             patch("utils.plugin_games.save_plugin_library", return_value=True):
            res = migrate_game_to_atom(game_data)
            self.assertTrue(res.get("success"), f"DLC-only migration failed: {res.get('error')}")

        from utils.yaml.apps import get_additional_apps, get_additional_depots
        from utils.yaml.keys import get_decryption_keys

        apps = get_additional_apps(self.config_path)
        depots = get_additional_depots(self.config_path)
        keys = get_decryption_keys(self.config_path)

        # Base AppID MUST NOT be in AdditionalApps or AdditionalDepots
        self.assertNotIn(base_appid, apps, "Base AppID must NOT be added to AdditionalApps in DLC-only mode")
        self.assertNotIn(base_appid, depots, "Base AppID must NOT be in AdditionalDepots")

        # DLC AppID MUST be in AdditionalApps
        self.assertIn(dlc_appid, apps, "DLC AppID must be present in AdditionalApps")
        # DLC Depot MUST be in AdditionalDepots and DecryptionKeys
        self.assertIn(dlc_depot, depots, "DLC Depot must be present in AdditionalDepots")
        self.assertIn(dlc_depot, keys, "DLC Depot Key must be present in DecryptionKeys")

    # -------------------------------------------------------------------------
    # 6. Install API Command Elimination
    # -------------------------------------------------------------------------
    def test_no_install_api_command_called(self):
        """migrate_game_to_atom must never call slssteam_api_send('install|...')."""
        game_data = {
            "appid": "77777",
            "game_name": "API Test Game",
            "install_path": str(self.game_dir),
            "depots": ["77778"],
            "keys": {"77778": "b" * 64},
        }

        with patch("utils.plugin_games.get_user_config_path", return_value=self.config_path), \
             patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(["77778"], {"77778": "b" * 64}, {"77778": "Depot"})), \
             patch("utils.steam_acf.find_appmanifest_path", return_value=None), \
             patch("utils.plugin_games.save_plugin_library", return_value=True), \
             patch("core.steam_helpers.slssteam_api_send") as mock_api1, \
             patch("utils.slssteam_integration._slssteam_api_send") as mock_api2:
            res = migrate_game_to_atom(game_data)
            self.assertTrue(res.get("success"))
            mock_api1.assert_not_called()
            mock_api2.assert_not_called()

    # -------------------------------------------------------------------------
    # 7. UI Dialog Rendering
    # -------------------------------------------------------------------------
    def test_complications_dialog_initialization(self):
        """AtomMigrationComplicationsDialog renders properly with all complication cards."""
        complications = {
            "has_complications": True,
            "pinned": {
                "build_id": "1234",
                "manifest_map": {"101": "999888777666"},
            },
            "dlc_only": {
                "dlc_appids": ["201", "202"],
                "depots": ["301"],
                "key_depot_ids": ["301"],
            },
            "eos": True,
            "goldberg": True,
        }
        dlg = AtomMigrationComplicationsDialog(
            parent=None,
            game_name="Complex Game",
            appid="1000",
            complications=complications,
        )
        self.assertEqual(dlg.windowTitle(), "AT0-M Migration Review")
        self.assertIsNotNone(dlg.proceed_btn)
        self.assertIsNotNone(dlg.cancel_btn)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
