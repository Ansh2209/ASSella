#!/usr/bin/env python3
"""
test_fix_path_safety.py - Guards against identifiers escaping the directories
they are meant to live in.

The lua.tools API hands us the fix id, and that id ends up in a directory name
and in a path recorded inside the fix receipt. A fix id of ".." used to resolve
to the game folder itself, and the cleanup step deleted the whole install.
"""

import http.server
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core import lua_tools_api
from core.lua_tools_api import LuaToolsAPI
from managers.fix_manager import FixManager, FIX_DIR
from utils.safe_paths import contained_path, is_within, safe_path_component


class TestSafePathComponent(unittest.TestCase):
    def test_dot_names_are_not_returned_verbatim(self):
        for value in ("..", ".", "", "  ", "...", " "):
            component = safe_path_component(value, fallback_prefix="fix")
            self.assertNotIn(component, ("", ".", ".."))
            self.assertTrue(component)

    def test_separators_and_control_characters_are_removed(self):
        for value in ("a/b", "a\\b", "a\x00b", "a\nb"):
            component = safe_path_component(value)
            self.assertNotIn("/", component)
            self.assertNotIn("\\", component)
            self.assertNotIn("\x00", component)
            self.assertNotIn("\n", component)

    def test_normal_ids_are_preserved(self):
        self.assertEqual(safe_path_component("1716751"), "1716751")
        self.assertEqual(safe_path_component("v1.2.3-hotfix1"), "v1.2.3-hotfix1")

    def test_distinct_inputs_get_distinct_components(self):
        self.assertNotEqual(safe_path_component(".."), safe_path_component("."))
        self.assertNotEqual(safe_path_component(""), safe_path_component(" "))

    def test_long_values_are_truncated_but_stay_unique(self):
        a = safe_path_component("x" * 300 + "a")
        b = safe_path_component("x" * 300 + "b")
        self.assertLessEqual(len(a), 100)
        self.assertLessEqual(len(b), 100)
        self.assertNotEqual(a, b)


class TestContainedPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "base"
        self.base.mkdir()
        (self.base / "child").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_inside_is_allowed(self):
        self.assertIsNotNone(contained_path(self.base, "child"))
        self.assertTrue(is_within(self.base, self.base / "child"))

    def test_escape_is_refused(self):
        self.assertIsNone(contained_path(self.base, ".."))
        self.assertIsNone(contained_path(self.base, "..", "sibling"))
        self.assertIsNone(contained_path(self.base, "/etc"))
        self.assertFalse(is_within(self.base, self.base.parent))

    def test_symlink_escape_is_refused(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.base / "link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        self.assertIsNone(contained_path(self.base, "link"))
        self.assertIsNone(contained_path(self.base, "link", "file.txt"))


class TestFixManagerPathSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game_dir = self.root / "game"
        self.game_dir.mkdir()
        self.exe = self.game_dir / "game.exe"
        self.exe.write_bytes(b"ORIGINAL")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_zip(self) -> Path:
        zip_path = self.root / "fix.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game.exe", b"PATCHED")
        return zip_path

    def test_traversal_fix_id_does_not_touch_the_game_folder(self):
        """A fix id of '..' must not let the cleanup step delete the game."""
        zip_path = self._make_zip()
        ok, msg = FixManager.apply_fix(
            self.game_dir, "470220", "..", "Evil fix", zip_path
        )
        self.assertTrue(ok, msg)
        self.assertTrue(self.exe.is_file(), "game file vanished during apply")

        ok, msg = FixManager.remove_fix(self.game_dir, fix_id="..")
        self.assertTrue(ok, msg)
        self.assertTrue(self.exe.is_file(), "game file vanished during restore")
        self.assertEqual(self.exe.read_bytes(), b"ORIGINAL")

    def test_dot_fix_id_is_contained(self):
        zip_path = self._make_zip()
        ok, msg = FixManager.apply_fix(
            self.game_dir, "470220", ".", "Dot fix", zip_path
        )
        self.assertTrue(ok, msg)
        self.assertTrue(self.exe.is_file())

        # The backup must live inside .lfix, not in the game folder itself.
        backups = list((self.game_dir / FIX_DIR).rglob("*.bak"))
        self.assertTrue(backups)
        for backup in backups:
            self.assertTrue(is_within(self.game_dir / FIX_DIR, backup))

    def test_empty_fix_id_is_rejected(self):
        zip_path = self._make_zip()
        ok, msg = FixManager.apply_fix(self.game_dir, "470220", "", "No id", zip_path)
        self.assertFalse(ok)
        self.assertIn("identifier", msg.lower())
        self.assertEqual(self.exe.read_bytes(), b"ORIGINAL")

    def test_tampered_backup_path_does_not_restore_from_outside(self):
        """A receipt pointing at a backup outside .lfix must not restore it."""
        zip_path = self._make_zip()
        ok, _ = FixManager.apply_fix(self.game_dir, "470220", "fix1", "Fix", zip_path)
        self.assertTrue(ok)

        # Point the receipt at a file outside the fix directory.
        outside = self.root / "outside.bin"
        outside.write_bytes(b"EVIL")
        receipt_file = next((self.game_dir / FIX_DIR).glob("*.json"))
        import json
        receipt = json.loads(receipt_file.read_text())
        receipt["files"][0]["backup_path"] = "../outside.bin"
        receipt_file.write_text(json.dumps(receipt))

        preview = FixManager.preview_restore(self.game_dir, fix_id="fix1")
        self.assertFalse(preview["can_restore"])
        self.assertTrue(preview["conflicts"])

        ok, msg = FixManager.remove_fix(self.game_dir, fix_id="fix1")
        self.assertFalse(ok)
        self.assertEqual(self.exe.read_bytes(), b"PATCHED")
        self.assertEqual(outside.read_bytes(), b"EVIL")

    def test_tampered_relative_path_does_not_write_outside_the_game(self):
        """A receipt with a path that leaves the game folder must be ignored."""
        zip_path = self._make_zip()
        ok, _ = FixManager.apply_fix(self.game_dir, "470220", "fix2", "Fix", zip_path)
        self.assertTrue(ok)

        victim = self.root / "victim.txt"
        victim.write_text("PRECIOUS")

        receipt_file = next((self.game_dir / FIX_DIR).glob("*.json"))
        import json
        receipt = json.loads(receipt_file.read_text())
        receipt["files"][0]["relative_path"] = "../victim.txt"
        receipt_file.write_text(json.dumps(receipt))

        preview = FixManager.preview_restore(self.game_dir, fix_id="fix2")
        self.assertFalse(preview["can_restore"])

        FixManager.remove_fix(self.game_dir, fix_id="fix2", force=True)
        self.assertEqual(victim.read_text(), "PRECIOUS")


class _TruncatingHandler(http.server.BaseHTTPRequestHandler):
    """Serves a body that is shorter than the announced Content-Length."""

    payload = b""
    announced_length = 0

    def do_GET(self):  # noqa: N802 - stdlib naming
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(self.announced_length))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):  # silence the test output
        pass


class TestLuaToolsDownloadIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir()
        self._orig_cache_dir = lua_tools_api._get_fixes_cache_dir
        lua_tools_api._get_fixes_cache_dir = lambda: self.cache_dir
        self._orig_resolve = LuaToolsAPI.resolve_fix_download
        LuaToolsAPI.resolve_fix_download = classmethod(
            lambda cls, fix_id, slot="fix": (None, "not stubbed")
        )

    def tearDown(self):
        LuaToolsAPI.resolve_fix_download = self._orig_resolve
        lua_tools_api._get_fixes_cache_dir = self._orig_cache_dir
        self.tmp.cleanup()

    def _make_zip(self) -> Path:
        zip_path = self.root / "payload.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game.exe", b"PATCHED")
        return zip_path

    def test_cache_path_cannot_escape_the_cache_directory(self):
        zip_path = self._make_zip()
        LuaToolsAPI.resolve_fix_download = classmethod(
            lambda cls, fix_id, slot="fix": (zip_path.as_uri(), None)
        )

        result, err = LuaToolsAPI.download_fix_zip("../../escape", "123/..")
        self.assertIsNotNone(result, err)
        self.assertEqual(result.parent, self.cache_dir)
        self.assertNotIn("/", result.name)
        self.assertNotIn("\\", result.name)
        self.assertNotIn(result.name, (".", ".."))
        self.assertTrue(result.is_file())

    def test_corrupt_cache_entry_is_refetched(self):
        zip_path = self._make_zip()
        LuaToolsAPI.resolve_fix_download = classmethod(
            lambda cls, fix_id, slot="fix": (zip_path.as_uri(), None)
        )
        target = self.cache_dir / "123_fix1.zip"
        target.write_bytes(b"not a zip at all" * 200)

        result, err = LuaToolsAPI.download_fix_zip("fix1", "123")
        self.assertIsNotNone(result, err)
        with zipfile.ZipFile(result) as zf:
            self.assertIn("game.exe", zf.namelist())

    def test_stale_cache_entry_is_refetched(self):
        import os
        import time

        zip_path = self._make_zip()
        LuaToolsAPI.resolve_fix_download = classmethod(
            lambda cls, fix_id, slot="fix": (zip_path.as_uri(), None)
        )
        target = self.cache_dir / "123_fix2.zip"
        shutil.copy2(zip_path, target)
        old = time.time() - lua_tools_api.FIX_ZIP_CACHE_TTL - 60
        os.utime(target, (old, old))

        result, err = LuaToolsAPI.download_fix_zip("fix2", "123")
        self.assertIsNotNone(result, err)
        self.assertGreater(result.stat().st_mtime, old)

    def test_truncated_download_is_rejected(self):
        zip_path = self._make_zip()
        payload = zip_path.read_bytes()

        handler = type(
            "Handler",
            (_TruncatingHandler,),
            {"payload": payload, "announced_length": len(payload) + 500},
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            LuaToolsAPI.resolve_fix_download = classmethod(
                lambda cls, fix_id, slot="fix": (
                    f"http://127.0.0.1:{server.server_address[1]}/fix.zip",
                    None,
                )
            )
            result, err = LuaToolsAPI.download_fix_zip("fix3", "123")
            self.assertIsNone(result)
            self.assertIn("Download failed", err)
            # Nothing usable may be left behind in the cache.
            self.assertEqual(
                [p for p in self.cache_dir.iterdir() if p.suffix == ".zip"], []
            )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
