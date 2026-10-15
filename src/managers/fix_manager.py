"""
fix_manager.py - Manages applying, backing up, and removing game fixes.
Safe drop-in extraction with full rollback capabilities (.luafix_backup/ and .luafix_receipt.json).
"""

import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RECEIPT_FILE = ".luafix_receipt.json"
BACKUP_DIR = ".luafix_backup"


class FixManager:
    """Manages application, receipts, and clean restoration of game fixes."""

    @classmethod
    def get_receipt(cls, game_path: str | Path) -> Optional[Dict[str, Any]]:
        """Read the active fix receipt from a game directory if present."""
        if not game_path:
            return None
        receipt_path = Path(game_path) / RECEIPT_FILE
        if not receipt_path.is_file():
            return None
        try:
            with open(receipt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("fix_id"):
                return data
        except Exception as e:
            logger.warning(f"[FixManager] Error reading receipt from {receipt_path}: {e}")
        return None

    @classmethod
    def apply_fix(
        cls,
        game_path: str | Path,
        appid: str | int,
        fix_id: str,
        fix_title: str,
        zip_path: str | Path,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Safely apply a fix archive to a game directory.
        Existing files being replaced are preserved inside .luafix_backup/.
        """
        target_dir = Path(game_path)
        if not target_dir.is_dir():
            return False, f"Game directory not found: {target_dir}"

        archive = Path(zip_path)
        if not archive.is_file():
            return False, f"Fix archive not found: {archive}"

        # If an existing fix is already applied, remove it cleanly first
        existing_receipt = cls.get_receipt(target_dir)
        if existing_receipt:
            if existing_receipt.get("fix_id") == fix_id:
                return True, "This fix is already applied to the game."
            cls.remove_fix(target_dir)

        backup_root = target_dir / BACKUP_DIR
        backup_root.mkdir(parents=True, exist_ok=True)

        extracted_files: List[str] = []
        backed_up_files: List[str] = []

        try:
            with zipfile.ZipFile(archive, "r") as zf:
                total_files = len([info for info in zf.infolist() if not info.is_dir()])
                count = 0

                for member in zf.infolist():
                    if member.is_dir():
                        continue

                    # Guard against path traversal / zip slip attacks
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        logger.warning(f"[FixManager] Skipping malicious path in zip: {member.filename}")
                        continue

                    dest_file = target_dir / member_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)

                    # If file already exists, preserve it in the backup folder
                    if dest_file.exists():
                        backup_file = backup_root / member_path
                        backup_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dest_file, backup_file)
                        backed_up_files.append(str(member_path))
                        logger.debug(f"[FixManager] Backed up original file: {member_path}")

                    # Extract file
                    with zf.open(member) as source, open(dest_file, "wb") as target:
                        shutil.copyfileobj(source, target)

                    # Match file permissions if present
                    if member.external_attr > 0:
                        mode = member.external_attr >> 16
                        if mode > 0:
                            os.chmod(dest_file, mode)

                    extracted_files.append(str(member_path))
                    count += 1
                    if progress_cb:
                        progress_cb(f"Extracting {count}/{total_files}: {member_path.name}")

            # Write receipt
            receipt_data = {
                "appid": str(appid),
                "fix_id": fix_id,
                "fix_title": fix_title,
                "applied_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "extracted_files": extracted_files,
                "backed_up_files": backed_up_files,
            }

            receipt_file = target_dir / RECEIPT_FILE
            with open(receipt_file, "w", encoding="utf-8") as f:
                json.dump(receipt_data, f, indent=2)

            return True, f"Successfully applied {fix_title} ({len(extracted_files)} files installed)!"

        except Exception as e:
            logger.error(f"[FixManager] Failed to apply fix: {e}", exc_info=True)
            # Attempt rollback
            try:
                cls.remove_fix(target_dir)
            except Exception:
                pass
            return False, f"Failed to apply fix: {e}"

    @classmethod
    def remove_fix(
        cls,
        game_path: str | Path,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Remove an applied fix, restoring original backed-up files and deleting fix overlays.
        """
        target_dir = Path(game_path)
        receipt = cls.get_receipt(target_dir)
        if not receipt:
            return False, "No active fix receipt found in game directory."

        backup_root = target_dir / BACKUP_DIR
        extracted = receipt.get("extracted_files", [])
        backed_up = receipt.get("backed_up_files", [])

        restored_count = 0
        deleted_count = 0

        try:
            # 1. Restore backed up files
            for rel_str in backed_up:
                rel_path = Path(rel_str)
                backup_file = backup_root / rel_path
                dest_file = target_dir / rel_path
                if backup_file.is_file():
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, dest_file)
                    restored_count += 1
                    if progress_cb:
                        progress_cb(f"Restoring original: {rel_path.name}")

            # 2. Delete extracted fix files that were NOT originally present
            backed_up_set = set(backed_up)
            for rel_str in extracted:
                if rel_str not in backed_up_set:
                    dest_file = target_dir / Path(rel_str)
                    if dest_file.is_file():
                        dest_file.unlink()
                        deleted_count += 1
                        if progress_cb:
                            progress_cb(f"Removing overlay: {dest_file.name}")

            # 3. Clean up backup directory & receipt
            if backup_root.is_dir():
                shutil.rmtree(backup_root, ignore_errors=True)

            receipt_file = target_dir / RECEIPT_FILE
            if receipt_file.is_file():
                receipt_file.unlink()

            return True, f"Fix removed: {restored_count} original files restored, {deleted_count} overlay files cleaned."

        except Exception as e:
            logger.error(f"[FixManager] Error removing fix: {e}", exc_info=True)
            return False, f"Error removing fix: {e}"
