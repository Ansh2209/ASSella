#!/usr/bin/env python3
"""
test_fix_manager_workflow.py - Comprehensive test suite for FixManager,
FixPreviewDialog, and backup/restore workflows.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from managers.fix_manager import FixManager, FIX_DIR, LEGACY_FIX_DIR, RECEIPT_FILE
from PyQt6.QtWidgets import QApplication
from ui.dialogs.game_details.fix_preview_dialog import FixPreviewDialog, show_fix_preview_dialog


class TestFixManagerWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name)
        self.game_dir = self.root / "game"
        self.game_dir.mkdir()

        # Create some original files in game directory
        self.orig_bin = self.game_dir / "bin" / "game.exe"
        self.orig_bin.parent.mkdir(parents=True, exist_ok=True)
        self.orig_bin.write_bytes(b"ORIGINAL_EXE_V1")

        self.orig_cfg = self.game_dir / "settings.ini"
        self.orig_cfg.write_text("fullscreen=0\nresolution=1080p")

    def tearDown(self):
        self.test_dir.cleanup()

    def _create_fix_zip(self, files: dict, filename: str = "fix.zip") -> Path:
        zip_path = self.root / filename
        with zipfile.ZipFile(zip_path, "w") as zf:
            for rel_path, content in files.items():
                if isinstance(content, str):
                    zf.writestr(rel_path, content.encode("utf-8"))
                else:
                    zf.writestr(rel_path, content)
        return zip_path

    def test_plan_fix_detection(self):
        """Test planning accurately categorizes modified vs added files."""
        zip_path = self._create_fix_zip({
            "bin/game.exe": b"FIXED_EXE_V2",
            "bin/fix_plugin.dll": b"NEW_DLL_CONTENT",
            "new_folder/readme.txt": "Fix documentation",
        })

        entries, mod_count, add_count = FixManager.plan_fix(self.game_dir, zip_path)
        self.assertEqual(mod_count, 1)
        self.assertEqual(add_count, 2)
        self.assertEqual(len(entries), 3)

        by_path = {e["relative_path"]: e["action"] for e in entries}
        self.assertEqual(by_path["bin/game.exe"], "modified")
        self.assertEqual(by_path["bin/fix_plugin.dll"], "added")
        self.assertEqual(by_path["new_folder/readme.txt"], "added")

    def test_apply_and_restore_clean_roundtrip(self):
        """Test apply backs up modified files and restore restores them cleanly."""
        zip_path = self._create_fix_zip({
            "bin/game.exe": b"FIXED_EXE_V2",
            "fix_overlay.dll": b"NEW_OVERLAY",
        })

        ok, msg = FixManager.apply_fix(
            game_path=self.game_dir,
            appid="1000",
            fix_id="test_fix_1",
            fix_title="Test Fix 1",
            zip_path=zip_path,
        )
        self.assertTrue(ok, f"apply_fix failed: {msg}")
        self.assertIn("Applied 2 files", msg)

        # Verify applied files
        self.assertEqual(self.orig_bin.read_bytes(), b"FIXED_EXE_V2")
        self.assertTrue((self.game_dir / "fix_overlay.dll").exists())

        # Verify backup directory
        fix_dir = self.game_dir / FIX_DIR
        self.assertTrue(fix_dir.is_dir())
        record = FixManager.get_receipt(self.game_dir)
        self.assertIsNotNone(record)
        self.assertEqual(record["fix_id"], "test_fix_1")
        self.assertEqual(record["status"], "completed")

        # Now remove fix
        ok_rem, msg_rem = FixManager.remove_fix(self.game_dir)
        self.assertTrue(ok_rem, f"remove_fix failed: {msg_rem}")

        # Verify original file restored
        self.assertEqual(self.orig_bin.read_bytes(), b"ORIGINAL_EXE_V1")
        # Verify overlay file deleted
        self.assertFalse((self.game_dir / "fix_overlay.dll").exists())
        # Verify receipt is gone
        self.assertIsNone(FixManager.get_receipt(self.game_dir))

    def test_conflict_detection_preserves_backups(self):
        """Test that if a modified file was changed by an update or external edit, restore detects conflict and preserves backups."""
        zip_path = self._create_fix_zip({
            "bin/game.exe": b"FIXED_EXE_V2",
        })

        ok, msg = FixManager.apply_fix(
            game_path=self.game_dir,
            appid="1000",
            fix_id="test_fix_conflict",
            fix_title="Conflict Test",
            zip_path=zip_path,
        )
        self.assertTrue(ok)

        # Simulate external edit / Steam update overwriting the file with V3
        self.orig_bin.write_bytes(b"UPDATE_EXE_V3")

        # Attempt to remove fix
        ok_rem, msg_rem = FixManager.remove_fix(self.game_dir)
        self.assertFalse(ok_rem)
        self.assertIn("Conflict detected", msg_rem)

        # Backups MUST still be preserved!
        record = FixManager.get_receipt(self.game_dir)
        self.assertIsNotNone(record, "Record should not be deleted on conflict!")
        backup_file = self.game_dir / FIX_DIR / "test_fix_conflict" / "bin" / "game.exe.bak"
        self.assertTrue(backup_file.exists(), "Backup file should be preserved on conflict!")
        self.assertEqual(backup_file.read_bytes(), b"ORIGINAL_EXE_V1")

    def test_selective_file_application(self):
        """Test applying only a subset of files selected by user."""
        zip_path = self._create_fix_zip({
            "bin/game.exe": b"FIXED_EXE_V2",
            "settings.ini": "fullscreen=1\nresolution=4k",
            "extra_bonus.txt": "Bonus file",
        })

        # Select only bin/game.exe and extra_bonus.txt, skip settings.ini
        selected = {"bin/game.exe", "extra_bonus.txt"}
        ok, msg = FixManager.apply_fix(
            game_path=self.game_dir,
            appid="1000",
            fix_id="selective_fix",
            fix_title="Selective Fix",
            zip_path=zip_path,
            selected_files=selected,
        )
        self.assertTrue(ok)

        # game.exe modified
        self.assertEqual(self.orig_bin.read_bytes(), b"FIXED_EXE_V2")
        # settings.ini UNCHANGED
        self.assertEqual(self.orig_cfg.read_text(), "fullscreen=0\nresolution=1080p")
        # extra_bonus.txt added
        self.assertTrue((self.game_dir / "extra_bonus.txt").exists())

    def test_legacy_migration(self):
        """Test automatic migration from legacy .luafix_backup and .luafix_receipt.json."""
        # Setup legacy structure
        legacy_dir = self.game_dir / LEGACY_FIX_DIR
        legacy_bin = legacy_dir / "bin" / "game.exe"
        legacy_bin.parent.mkdir(parents=True, exist_ok=True)
        legacy_bin.write_bytes(b"ORIGINAL_EXE_V1")

        # Apply overlay manually
        self.orig_bin.write_bytes(b"FIXED_EXE_LEGACY")
        (self.game_dir / "legacy_overlay.dll").write_bytes(b"OVERLAY_LEGACY")

        legacy_receipt = {
            "appid": "1000",
            "fix_id": "legacy_fix_42",
            "fix_title": "Legacy Test Fix",
            "applied_at": "2026-01-01 12:00:00",
            "extracted_files": ["bin/game.exe", "legacy_overlay.dll"],
            "backed_up_files": ["bin/game.exe"],
        }
        with open(self.game_dir / RECEIPT_FILE, "w", encoding="utf-8") as f:
            json.dump(legacy_receipt, f)

        # Now query get_receipt - it should auto-migrate!
        rec = FixManager.get_receipt(self.game_dir)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["fix_id"], "legacy_fix_42")

        # Verify old files migrated
        self.assertFalse((self.game_dir / LEGACY_FIX_DIR).exists())
        self.assertFalse((self.game_dir / RECEIPT_FILE).exists())

        # Now remove fix - should restore cleanly
        ok_rem, msg_rem = FixManager.remove_fix(self.game_dir)
        self.assertTrue(ok_rem, f"remove_fix failed: {msg_rem}")
        self.assertEqual(self.orig_bin.read_bytes(), b"ORIGINAL_EXE_V1")
        self.assertFalse((self.game_dir / "legacy_overlay.dll").exists())

    def test_fix_preview_dialog_filtering_and_selection(self):
        """Test FixPreviewDialog does not crash and filters accurately."""
        zip_path = self._create_fix_zip({
            "bin/game.exe": b"FIXED_EXE_V2",
            "new_file.dll": b"NEW_DLL",
            "readme.txt": "Notes",
        })

        dlg = FixPreviewDialog(
            parent=None,
            zip_path=zip_path,
            fix_title="Preview Test",
            game_name="Test Game",
            game_path=self.game_dir,
        )

        # Check modified vs added detection
        actions = {f["relative_path"]: f["action"] for f in dlg._all_files}
        self.assertEqual(actions["bin/game.exe"], "modified")
        self.assertEqual(actions["new_file.dll"], "added")
        self.assertEqual(actions["readme.txt"], "added")

        # Test filtering by modified
        dlg._filter_files("modified")
        vis_modified = [w._file_info["relative_path"] for w in dlg._list_widgets if not w.isHidden()]
        self.assertEqual(vis_modified, ["bin/game.exe"])

        # Test filtering by added
        dlg._filter_files("added")
        vis_added = [w._file_info["relative_path"] for w in dlg._list_widgets if not w.isHidden()]
        self.assertEqual(sorted(vis_added), ["new_file.dll", "readme.txt"])

        # Test resetting filter to All
        dlg._filter_files(None)
        vis_all = [w._file_info["relative_path"] for w in dlg._list_widgets if not w.isHidden()]
        self.assertEqual(len(vis_all), 3)

        # Test Select All / Deselect All
        dlg._on_deselect_all()
        checked_count = sum(1 for w in dlg._list_widgets if w._cb.isChecked())
        self.assertEqual(checked_count, 0)

        dlg._on_select_all()
        checked_count = sum(1 for w in dlg._list_widgets if w._cb.isChecked())
        self.assertEqual(checked_count, 3)

        # Test apply collection
        dlg._list_widgets[2]._cb.setChecked(False)  # uncheck readme.txt
        dlg._on_apply()
        self.assertEqual(dlg.selected_files, {"bin/game.exe", "new_file.dll"})

    def test_preview_restore_and_preflight_conflict(self):
        """Test preview_restore dry-run and preflight conflict blocking."""
        zip_path = self._create_fix_zip({
            "bin/game.exe": b"FIXED_EXE_V2",
            "fix_overlay.dll": b"NEW_OVERLAY",
        })
        ok, msg = FixManager.apply_fix(
            game_path=self.game_dir,
            appid="1000",
            fix_id="preview_test_fix",
            fix_title="Preview Test",
            zip_path=zip_path,
        )
        self.assertTrue(ok)

        # 1. Clean preview
        pre = FixManager.preview_restore(self.game_dir, "preview_test_fix")
        self.assertTrue(pre["can_restore"])
        self.assertEqual(len(pre["conflicts"]), 0)
        self.assertEqual(pre["total_files"], 2)

        # 2. Introduce conflict by editing file externally
        self.orig_bin.write_bytes(b"EXTERNALLY_ALTERED_CONTENT")

        # 3. Preview should report conflict
        pre_conflict = FixManager.preview_restore(self.game_dir, "preview_test_fix")
        self.assertFalse(pre_conflict["can_restore"])
        self.assertIn("bin/game.exe", pre_conflict["conflicts"])

        # 4. remove_fix should abort without modifying any files
        ok_rem, msg_rem = FixManager.remove_fix(self.game_dir, "preview_test_fix")
        self.assertFalse(ok_rem)
        self.assertIn("Conflict detected", msg_rem)
        # Verify overlay is STILL present (zero files modified on abort)
        self.assertTrue((self.game_dir / "fix_overlay.dll").exists())

    def test_multi_fix_per_fix_restore(self):
        """Test that multiple fixes can be recorded and restored independently."""
        zip1 = self._create_fix_zip({"mod1.dll": b"MOD1_DATA"}, "fix1.zip")
        zip2 = self._create_fix_zip({"mod2.dll": b"MOD2_DATA"}, "fix2.zip")

        ok1, _ = FixManager.apply_fix(self.game_dir, "1000", "fix_one", "Fix 1", zip1)
        ok2, _ = FixManager.apply_fix(self.game_dir, "1000", "fix_two", "Fix 2", zip2)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

        receipts = FixManager.get_all_receipts(self.game_dir)
        fix_ids = {r["fix_id"] for r in receipts}
        self.assertEqual(fix_ids, {"fix_one", "fix_two"})

        # Remove Fix 1 only
        ok_rem1, _ = FixManager.remove_fix(self.game_dir, fix_id="fix_one")
        self.assertTrue(ok_rem1)
        self.assertFalse((self.game_dir / "mod1.dll").exists())
        self.assertTrue((self.game_dir / "mod2.dll").exists())

        remaining = FixManager.get_all_receipts(self.game_dir)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["fix_id"], "fix_two")

    def test_restore_preview_dialog_ui(self):
        """Test FixPreviewDialog in restore mode renders and handles filter buttons."""
        preview_data = {
            "fix_id": "test_restore",
            "fix_title": "Test Restore Fix",
            "files": [
                {"relative_path": "bin/game.exe", "action": "revert", "status": "ok", "size": 1024},
                {"relative_path": "extra.dll", "action": "delete", "status": "ok", "size": 512},
                {"relative_path": "modded.cfg", "action": "revert", "status": "conflict", "conflict_reason": "Modified", "size": 128},
            ],
            "conflicts": ["modded.cfg"],
            "can_restore": False,
            "has_legacy_files": False,
            "revert_count": 2,
            "delete_count": 1,
        }

        dlg = FixPreviewDialog(
            parent=None,
            fix_title="Test Restore Fix",
            game_name="Test Game",
            game_path=self.game_dir,
            mode="restore",
            preview_data=preview_data,
        )
        self.assertEqual(len(dlg._all_files), 3)
        # Action button should be disabled due to conflict
        self.assertFalse(dlg.action_btn.isEnabled())

        # Test filter pill click
        dlg._on_filter_pill_clicked("delete")
        visible_paths = [w._file_info["relative_path"] for w in dlg._list_widgets if not w.isHidden()]
        self.assertEqual(visible_paths, ["extra.dll"])


class TestVersionHelpers(unittest.TestCase):
    def test_version_parsing_and_comparison(self):
        from utils.version import parse_version, extract_semver, get_effective_update_channel

        self.assertEqual(extract_semver("v3.0.0"), "3.0.0")
        self.assertEqual(extract_semver("20261021+ASSella-3.0.0"), "3.0.0")
        self.assertEqual(extract_semver("v3.1.0-beta.1"), "3.1.0-beta.1")

        # Stable release > RC > Beta > Canary
        self.assertGreater(parse_version("3.0.0"), parse_version("3.0.0-rc1"))
        self.assertGreater(parse_version("3.0.0-rc1"), parse_version("3.0.0-beta.1"))
        self.assertGreater(parse_version("3.0.0-beta.1"), parse_version("3.0.0-canary.1"))
        self.assertGreater(parse_version("3.0.1"), parse_version("3.0.0"))

        # Channel resolution
        self.assertEqual(get_effective_update_channel("stable", "3.0.0"), "stable")
        self.assertEqual(get_effective_update_channel("", "3.0.0-canary.1"), "canary")
        self.assertEqual(get_effective_update_channel("", "3.0.0-beta.2"), "beta")
        self.assertEqual(get_effective_update_channel("", "3.0.0"), "stable")


if __name__ == "__main__":
    unittest.main()

