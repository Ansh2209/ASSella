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

    def _create_fix_zip(self, files: dict) -> Path:
        zip_path = self.root / "fix.zip"
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
        dlg._list_widgets[2]._cb.setChecked(False) # uncheck readme.txt
        dlg._on_apply()
        self.assertEqual(dlg.selected_files, {"bin/game.exe", "new_file.dll"})


if __name__ == "__main__":
    unittest.main()
