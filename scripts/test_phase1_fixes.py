#!/usr/bin/env python3
"""Targeted test suite for Phase 1 Integrity and Bug Fixes.

Verifies:
1. appinfo.vdf token validation (uint32/uint64) and rollback to .bak on write failure.
2. Token log redaction (raw token string never emitted at INFO).
3. CLI Linux permission finalizer recursive chmod execution.
4. CLI Steamless guard on Linux native depots.
5. Dynamic SteamEnv path resolution in MRC and Ticket managers.
"""

import os
import stat
import struct
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def test_appinfo_token_validation_and_rollback():
    print("-> Testing appinfo.vdf token validation & rollback...")
    from core.appinfo_editor import AppInfoEditor, add_token_to_appinfo

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        appinfo_file = tmp_path / "appinfo.vdf"

        # Create a minimal valid appinfo.vdf header
        # Header: magic (4), universe (4), offset (8)
        # Offset to string table = 20 (magic + universe + offset + footer 4)
        magic = 0x07564428
        universe = 1
        string_table_offset = 20
        with open(appinfo_file, "wb") as f:
            f.write(struct.pack("<I", magic))
            f.write(struct.pack("<I", universe))
            f.write(struct.pack("<Q", string_table_offset))
            f.write(struct.pack("<I", 0))  # footer (end of apps)
            f.write(b"")  # string table data

        original_bytes = appinfo_file.read_bytes()

        # 1. Test invalid negative token
        res = add_token_to_appinfo(appinfo_file, "1000", "-1")
        assert res is False, "Expected negative token to be rejected"
        assert appinfo_file.read_bytes() == original_bytes, "File modified on validation error"

        # 2. Test invalid too-large token (> 64-bit uint)
        res = add_token_to_appinfo(appinfo_file, "1000", str(2**64 + 10))
        assert res is False, "Expected too large token to be rejected"
        assert appinfo_file.read_bytes() == original_bytes, "File modified on validation error"

        # 3. Test non-numeric token
        res = add_token_to_appinfo(appinfo_file, "1000", "not_a_number")
        assert res is False, "Expected non-numeric token to be rejected"
        assert appinfo_file.read_bytes() == original_bytes, "File modified on validation error"

        # 4. Test valid token insertion
        valid_token = 123456789012345
        res = add_token_to_appinfo(appinfo_file, "1000", str(valid_token))
        assert res is True, "Expected valid token to succeed"
        assert appinfo_file.with_suffix(".vdf.bak").exists(), "Backup file not created"

        # 5. Test rollback on write failure
        # Mock write_appinfo to fail and verify original bytes are restored
        state_before_fail = appinfo_file.read_bytes()
        with patch.object(AppInfoEditor, "write_appinfo", return_value=False):
            res = add_token_to_appinfo(appinfo_file, "2000", "99999999")
            assert res is False, "Expected failure when write_appinfo fails"
            assert appinfo_file.read_bytes() == state_before_fail, "appinfo.vdf was not rolled back from backup!"

    print("   [OK] appinfo token validation and rollback tests passed.")


def test_token_logging_redaction():
    print("-> Testing PICS token log redaction...")
    from core.appinfo_editor import AppInfoEditor
    import logging

    editor = AppInfoEditor(Path("/dev/null"))
    editor.apps = []

    captured_logs = []
    class ListHandler(logging.Handler):
        def emit(self, record):
            captured_logs.append(record.getMessage())

    logger = logging.getLogger("core.appinfo_editor")
    handler = ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    secret_sentinel_token = 888777666555444333
    editor.set_token(12345, secret_sentinel_token)

    for log in captured_logs:
        assert str(secret_sentinel_token) not in log, f"Raw token leaked in log message: {log}"
        assert "token set" in log or "token redacted" in log, f"Expected redaction notice in log: {log}"

    logger.removeHandler(handler)
    print("   [OK] PICS token redaction tests passed.")


def test_cli_chmod_and_steamless():
    print("-> Testing CLI chmod execution & Steamless guard...")
    from managers.cli_manager import CLITaskManager

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        game_dir = tmp_path / "steamapps" / "common" / "MyGame"
        game_dir.mkdir(parents=True)

        # Create mock linux script and binary
        test_script = game_dir / "start.sh"
        test_script.write_text("#!/bin/sh\necho hi\n")
        os.chmod(test_script, 0o644)

        test_bin = game_dir / "game.x86_64"
        test_bin.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 2000)
        os.chmod(test_bin, 0o644)

        mgr = CLITaskManager(MagicMock(), MagicMock())
        mgr.game_data = {"appid": "100", "game_name": "MyGame"}
        mgr.current_dest_path = str(tmp_path)

        # Call _set_linux_binary_permissions
        mgr._set_linux_binary_permissions()

        # Check that permissions were updated to executable
        script_mode = os.stat(test_script).st_mode
        bin_mode = os.stat(test_bin).st_mode
        assert (script_mode & stat.S_IXUSR), f"start.sh not made executable: {oct(script_mode)}"
        assert (bin_mode & stat.S_IXUSR), f"game.x86_64 not made executable: {oct(bin_mode)}"

        # Test _is_current_job_linux
        mgr.game_data = {
            "appid": "100",
            "selected_depots_list": ["101"],
            "depots": {
                "101": {"oslist": "linux", "desc": "Linux Binaries"}
            }
        }
        assert mgr._is_current_job_linux() is True, "Expected Linux depot to return True"

        mgr.game_data["depots"]["101"]["oslist"] = "windows"
        mgr.game_data["depots"]["101"]["desc"] = "Windows Binaries"
        assert mgr._is_current_job_linux() is False, "Expected Windows depot to return False"

    print("   [OK] CLI chmod and Steamless guard tests passed.")


def test_steam_env_path_resolution():
    print("-> Testing Dynamic SteamEnv resolution in MRC & Ticket managers...")
    from managers.mrc_config_manager import get_sls_config_dir as get_mrc_sls_dir
    from utils.ticket_manager import get_sls_config_dir as get_ticket_sls_dir, get_tickets_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_sls_dir = Path(tmpdir) / "FlatpakSLS"
        mock_env = MagicMock()
        mock_env.sls_config_dir = mock_sls_dir

        with patch("core.steam_helpers.get_steam_env", return_value=mock_env):
            assert get_mrc_sls_dir() == mock_sls_dir
            assert get_ticket_sls_dir() == mock_sls_dir
            assert get_tickets_dir() == mock_sls_dir / "Tickets"
            assert (mock_sls_dir / "Tickets").exists()

    print("   [OK] SteamEnv dynamic resolution tests passed.")


def test_depot_tag_formatting_and_arch_detection():
    print("-> Testing Depot Tag Formatting & Arch Detection...")
    from utils.depot_tag_helpers import extract_arch_code, format_depot_row_label
    from ui.dialogs.depot_selection.rules import get_smart_default_depots

    # 1. Arch detection
    assert extract_arch_code({"osarch": "64"}) == "x64"
    assert extract_arch_code({"config": {"osarch": "64"}}) == "x64"
    assert extract_arch_code({"osarch": "32"}) == "x86"
    assert extract_arch_code({}, "Game Win64 Binaries") == "x64"
    assert extract_arch_code({}, "Game 32-bit Binaries") == "x86"
    assert extract_arch_code({}, "Original Soundtrack") is None

    # 2. Tag hierarchy formatting [OS] [ARCH] [LANG] [DLC]
    row1 = format_depot_row_label("286691", {"oslist": "windows", "osarch": "64"}, "Metro 2033 Redux Content")
    assert row1 == "[WINDOWS] [x64]  Metro 2033 Redux Content", f"Got: {row1}"

    row2 = format_depot_row_label(
        "101",
        {"oslist": "windows", "osarch": "64", "language": "french", "is_dlc": True, "dlcappid": "500"},
        "French Voice Pack",
    )
    assert row2 == "[WINDOWS] [x64] [DLC] [FR]  French Voice Pack", f"Got: {row2}"

    # 3. Smart depot selection filters 32-bit when 64-bit exists
    mock_depots = {
        "1001": {"name": "Game Content 64-bit", "oslist": "windows", "osarch": "64"},
        "1002": {"name": "Game Content 32-bit", "oslist": "windows", "osarch": "32"},
    }
    selected = get_smart_default_depots(mock_depots, target_platform="windows")
    assert "1001" in selected, "Expected 64-bit depot 1001 to be selected"
    assert "1002" not in selected, "Expected 32-bit depot 1002 to be filtered out"

    print("   [OK] Depot Tag Formatting and Smart Arch Selection passed.")


if __name__ == "__main__":
    test_appinfo_token_validation_and_rollback()
    test_token_logging_redaction()
    test_cli_chmod_and_steamless()
    test_steam_env_path_resolution()
    test_depot_tag_formatting_and_arch_detection()
    print("\n=== ALL PHASE 1 INTEGRITY TESTS PASSED ===")
