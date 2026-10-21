"""
fix_manager.py - Manages applying, backing up, and removing game fixes.
Implements LuaTools-compatible 4-phase process with SHA-256 conflict detection.
Backups stored in .lfix/ directory (with legacy .luafix_backup/ support).
"""

import hashlib
import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Current backup directory name
FIX_DIR = ".lfix"
# Legacy backup directory name (for migration)
LEGACY_FIX_DIR = ".luafix_backup"
# Receipt file name
RECEIPT_FILE = ".luafix_receipt.json"
# Index file to track all applied fixes across games
INDEX_FILE = ".luafix_index.json"


class FixManager:
    """
    Manages application, receipts, and clean restoration of game fixes.
    
    Implements a 4-phase process:
    1. PLAN - Scan ZIP, determine modified vs added files
    2. COMMIT - Write provisional record before any changes
    3. APPLY - Backup, extract, compute hashes
    4. SETTLE - Rewrite record with actual results
    
    SHA-256 hashing enables conflict detection during revert.
    """

    # ──────────────────────────────────────────────────────────────
    # Configuration
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _get_fix_dir(cls, game_path: Path) -> Path:
        """Get the .lfix directory, checking and migrating legacy path/receipt first."""
        target_dir = Path(game_path)
        fix_dir = target_dir / FIX_DIR
        legacy_dir = target_dir / LEGACY_FIX_DIR
        legacy_receipt = target_dir / RECEIPT_FILE

        if legacy_dir.exists() or legacy_receipt.exists():
            logger.info(f"[FixManager] Migrating legacy fix data to {FIX_DIR}")
            fix_dir.mkdir(parents=True, exist_ok=True)
            cls._migrate_legacy_backup(target_dir, fix_dir)
            return fix_dir

        fix_dir.mkdir(parents=True, exist_ok=True)
        return fix_dir

    @classmethod
    def _migrate_legacy_backup(cls, target_dir: Path, new_dir: Path) -> None:
        """Migrate legacy .luafix_backup and .luafix_receipt.json structure to .lfix."""
        legacy_dir = target_dir / LEGACY_FIX_DIR
        legacy_receipt_path = target_dir / RECEIPT_FILE

        rec = {}
        if legacy_receipt_path.is_file():
            try:
                with open(legacy_receipt_path, "r", encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception as e:
                logger.warning(f"[FixManager] Error reading legacy receipt: {e}")

        fix_id = rec.get("fix_id") or "legacy"
        fix_key = cls._safe_fix_key(fix_id)
        fix_subdir = new_dir / fix_key
        fix_subdir.mkdir(parents=True, exist_ok=True)

        # Migrate backed up files
        if legacy_dir.is_dir():
            for bak_file in legacy_dir.rglob("*"):
                if bak_file.is_file():
                    rel_path = bak_file.relative_to(legacy_dir)
                    dest_file = fix_subdir / f"{rel_path}.bak"
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bak_file, dest_file)
            shutil.rmtree(legacy_dir, ignore_errors=True)

        # Build converted record
        files_list = []
        backed_up = rec.get("backed_up_files", [])
        extracted = rec.get("extracted_files", [])
        for rel_str in backed_up:
            files_list.append({
                "relative_path": str(rel_str),
                "action": "modified",
                "backup_path": f"{fix_key}/{rel_str}.bak",
                "hash_before": None,
                "hash_after": None,
            })
        backed_up_set = set(backed_up)
        for rel_str in extracted:
            if rel_str not in backed_up_set:
                files_list.append({
                    "relative_path": str(rel_str),
                    "action": "added",
                    "backup_path": None,
                    "hash_before": None,
                    "hash_after": None,
                })

        new_rec = {
            "appid": str(rec.get("appid", "")),
            "fix_id": fix_id,
            "fix_title": rec.get("fix_title", "Legacy Fix"),
            "applied_at": rec.get("applied_at", time.strftime("%Y-%m-%d %H:%M:%S")),
            "files": files_list,
            "status": "completed",
            "failed_count": 0,
        }
        record_path = new_dir / f"{fix_key}.json"
        try:
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(new_rec, f, indent=2)
            if legacy_receipt_path.is_file():
                legacy_receipt_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"[FixManager] Failed to write migrated record: {e}")

    @classmethod
    def _safe_fix_key(cls, fix_id: str) -> str:
        """Sanitize fix_id for use as directory name."""
        invalid_chars = '<>:"/\\|?*'
        key = fix_id
        for ch in invalid_chars:
            key = key.replace(ch, "_")
        return key

    @classmethod
    def _sha256_file(cls, filepath: Path) -> Optional[str]:
        """Compute SHA-256 hash of a file."""
        try:
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"[FixManager] Failed to hash {filepath}: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # Phase 1: PLAN
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def plan_fix(
        cls,
        game_path: str | Path,
        zip_path: str | Path,
        selected_files: Optional[Set[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Phase 1: Plan the fix application.
        
        Returns:
            (plan_entries, modified_count, added_count)
        
        plan_entries: List of dicts with keys:
            - relative_path: str
            - dest_path: str (full path)
            - action: "modified" or "added"
            - zip_entry: ZipFile instance
        """
        target_dir = Path(game_path)
        archive = Path(zip_path)
        
        plan_entries = []
        modified_count = 0
        added_count = 0

        with zipfile.ZipFile(archive, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue

                # Guard against path traversal / zip slip attacks
                member_path = Path(member.filename)
                if member_path.is_absolute() or any(part.startswith("..") or part == ".." for part in member_path.parts):
                    logger.warning(f"[FixManager] Skipping malicious path: {member.filename}")
                    continue

                # Resolve destination and check containment
                dest_file = target_dir / member_path
                try:
                    dest_full = dest_file.resolve()
                    target_full = target_dir.resolve()
                    if not str(dest_full).startswith(str(target_full) + os.sep) and dest_full != target_full:
                        logger.warning(f"[FixManager] Path escapes game directory: {member.filename}")
                        continue
                except Exception:
                    continue

                # Check if user wants to skip this file
                if selected_files is not None and member.filename not in selected_files:
                    continue

                is_existing = dest_file.exists()
                if is_existing:
                    modified_count += 1
                else:
                    added_count += 1

                plan_entries.append({
                    "relative_path": member.filename,
                    "dest_path": str(dest_file),
                    "action": "modified" if is_existing else "added",
                    "zip_entry": member,
                })

        return plan_entries, modified_count, added_count

    # ──────────────────────────────────────────────────────────────
    # Phase 2: COMMIT
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _commit_provisional_record(
        cls,
        game_path: Path,
        appid,
        fix_id: str,
        fix_title: str,
        plan_entries: List[Dict[str, Any]],
    ) -> Optional[Path]:
        """
        Phase 2: Write provisional record BEFORE any changes.
        Returns path to record file, or None on failure.
        """
        fix_dir = cls._get_fix_dir(game_path)
        fix_key = cls._safe_fix_key(fix_id)
        record_path = fix_dir / f"{fix_key}.json"

        # Build provisional record
        record = {
            "appid": str(appid),
            "fix_id": fix_id,
            "fix_title": fix_title,
            "applied_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": [],
            "status": "provisional",
        }

        for entry in plan_entries:
            rel_path = entry["relative_path"]
            record["files"].append({
                "relative_path": rel_path,
                "action": entry["action"],
                "backup_path": f"{fix_key}/{rel_path}.bak" if entry["action"] == "modified" else None,
                "hash_before": None,  # Will be computed in Phase 3
                "hash_after": None,   # Will be computed in Phase 3
            })

        try:
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            return record_path
        except Exception as e:
            logger.error(f"[FixManager] Failed to write provisional record: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # Phase 3: APPLY
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _apply_files(
        cls,
        game_path: Path,
        fix_id: str,
        plan_entries: List[Dict[str, Any]],
        zip_path: Path,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Phase 3: Apply the fix files.
        
        Returns:
            (applied_entries, failed_count)
        """
        fix_dir = cls._get_fix_dir(game_path)
        fix_key = cls._safe_fix_key(fix_id)
        fix_subdir = fix_dir / fix_key
        applied_entries = []
        failed_count = 0

        with zipfile.ZipFile(zip_path, "r") as zf:
            for i, entry in enumerate(plan_entries):
                rel_path = entry["relative_path"]
                dest_file = Path(entry["dest_path"])
                zip_member = entry["zip_entry"]

                try:
                    hash_before = None
                    backup_rel = None

                    # Backup existing file if modified
                    if entry["action"] == "modified":
                        backup_file = fix_subdir / f"{rel_path}.bak"
                        backup_file.parent.mkdir(parents=True, exist_ok=True)
                        if not backup_file.exists():
                            shutil.copy2(dest_file, backup_file)
                        backup_rel = f"{fix_key}/{rel_path}.bak"
                        
                        # Hash the original before overwrite
                        hash_before = cls._sha256_file(dest_file)

                    # Extract file
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(zip_member) as src, open(dest_file, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                    # Set permissions if present
                    if zip_member.external_attr > 0:
                        mode = zip_member.external_attr >> 16
                        if mode > 0:
                            os.chmod(dest_file, mode)

                    # Hash the new file
                    hash_after = cls._sha256_file(dest_file)

                    applied_entries.append({
                        "relative_path": rel_path,
                        "action": entry["action"],
                        "backup_path": backup_rel,
                        "hash_before": hash_before,
                        "hash_after": hash_after,
                    })

                    if progress_cb:
                        progress_cb(f"Applying {i+1}/{len(plan_entries)}: {Path(rel_path).name}")

                except Exception as e:
                    logger.warning(f"[FixManager] Failed to apply {rel_path}: {e}")
                    failed_count += 1

        return applied_entries, failed_count

    # ──────────────────────────────────────────────────────────────
    # Phase 4: SETTLE
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _settle_record(
        cls,
        record_path: Path,
        appid,
        fix_id: str,
        fix_title: str,
        applied_entries: List[Dict[str, Any]],
        failed_count: int,
    ) -> bool:
        """
        Phase 4: Rewrite record with actual results.
        """
        record = {
            "appid": str(appid),
            "fix_id": fix_id,
            "fix_title": fix_title,
            "applied_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": applied_entries,
            "status": "completed",
            "failed_count": failed_count,
        }

        try:
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"[FixManager] Failed to write settled record: {e}")
            return False

    # ──────────────────────────────────────────────────────────────
    # Main Apply Interface
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def apply_fix(
        cls,
        game_path: str | Path,
        appid: str | int,
        fix_id: str,
        fix_title: str,
        zip_path: str | Path,
        selected_files: Optional[Set[str]] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Apply a fix archive using the 4-phase process.
        
        Args:
            game_path: Path to game directory
            appid: Steam AppID
            fix_id: Fix identifier
            fix_title: Human-readable fix title
            zip_path: Path to downloaded fix ZIP
            selected_files: Optional set of relative paths to apply (None = all)
            progress_cb: Optional progress callback
        """
        target_dir = Path(game_path)
        if not target_dir.is_dir():
            return False, f"Game directory not found: {target_dir}"

        archive = Path(zip_path)
        if not archive.is_file():
            return False, f"Fix archive not found: {archive}"

        # Check for existing fix and remove if different
        existing_receipt = cls.get_receipt(target_dir, fix_id)
        if existing_receipt:
            if existing_receipt.get("fix_id") == fix_id:
                return True, "This fix is already applied to the game."
            logger.info(f"[FixManager] Removing existing fix: {existing_receipt.get('fix_id')}")
            cls.remove_fix(target_dir)

        # ── Phase 1: PLAN ──
        if progress_cb:
            progress_cb("Planning fix application...")
        
        plan_entries, modified_count, added_count = cls.plan_fix(
            target_dir, archive, selected_files
        )

        if not plan_entries:
            return False, "No files to apply (all selected files may already be up to date)."

        logger.info(f"[FixManager] Planning: {modified_count} modified, {added_count} added files")

        # ── Phase 2: COMMIT ──
        if progress_cb:
            progress_cb("Writing provisional record...")

        record_path = cls._commit_provisional_record(
            target_dir, appid, fix_id, fix_title, plan_entries
        )
        if not record_path:
            return False, "Failed to initialize fix record."

        # ── Phase 3: APPLY ──
        if progress_cb:
            progress_cb(f"Applying {len(plan_entries)} files...")

        applied_entries, failed_count = cls._apply_files(
            target_dir, fix_id, plan_entries, archive, progress_cb
        )

        if not applied_entries:
            if record_path.exists():
                record_path.unlink()
            return False, f"Failed to apply any files from fix ({failed_count} errors)."

        # ── Phase 4: SETTLE ──
        if progress_cb:
            progress_cb("Finalizing fix record...")

        success = cls._settle_record(
            record_path, appid, fix_id, fix_title, applied_entries, failed_count
        )

        if not success:
            return False, "Failed to write fix record. Backup files are preserved."

        # Build success message
        msg_parts = [f"Applied {len(applied_entries)} files"]
        if failed_count > 0:
            msg_parts.append(f"({failed_count} failed)")
        msg = " ".join(msg_parts)

        logger.info(f"[FixManager] Fix applied successfully: {fix_id}")
        return True, msg

    # ──────────────────────────────────────────────────────────────
    # Receipt Management
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def get_receipt(cls, game_path: str | Path, fix_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Read the active fix receipt from a game directory.
        
        Args:
            game_path: Path to game directory
            fix_id: Optional specific fix_id to look for
        
        Returns:
            Receipt dict or None
        """
        target_dir = Path(game_path)
        if not target_dir.is_dir():
            return None

        receipts = cls.get_all_receipts(target_dir)
        if not receipts:
            return None

        if fix_id:
            for r in receipts:
                if r.get("fix_id") == fix_id:
                    return r
            return None

        receipts.sort(key=lambda r: str(r.get("applied_at", "")), reverse=True)
        return receipts[0]

    @classmethod
    def get_all_receipts(cls, game_path: str | Path) -> List[Dict[str, Any]]:
        """Get all fix receipts for a game directory."""
        target_dir = Path(game_path)
        if not target_dir.is_dir():
            return []

        # Check and migrate legacy directory / receipt if found
        if (target_dir / LEGACY_FIX_DIR).is_dir() or (target_dir / RECEIPT_FILE).is_file():
            cls._get_fix_dir(target_dir)

        fix_dir = target_dir / FIX_DIR
        if not fix_dir.is_dir():
            return []

        receipts = []
        for record_file in fix_dir.glob("*.json"):
            try:
                with open(record_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("fix_id"):
                    receipts.append(data)
            except Exception as e:
                logger.warning(f"[FixManager] Error reading receipt {record_file}: {e}")

        return receipts

    # ──────────────────────────────────────────────────────────────
    # Remove/Revert Fix
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def remove_fix(
        cls,
        game_path: str | Path,
        fix_id: Optional[str] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Remove an applied fix, restoring original files and cleaning up.
        Uses SHA-256 hashes for conflict detection.
        """
        target_dir = Path(game_path)
        if not target_dir.is_dir():
            return False, f"Game directory not found: {target_dir}"

        # Find the receipt
        receipt = cls.get_receipt(target_dir, fix_id)
        if not receipt:
            return False, "No active fix receipt found in game directory."

        fix_id = receipt.get("fix_id")
        fix_dir = cls._get_fix_dir(target_dir)
        fix_key = cls._safe_fix_key(fix_id)
        fix_subdir = fix_dir / fix_key
        
        restored_count = 0
        deleted_count = 0
        conflicts = 0
        errors = 0
        conflict_files = []

        try:
            for entry in receipt.get("files", []):
                rel_path = entry.get("relative_path")
                action = entry.get("action")
                hash_after = entry.get("hash_after")
                hash_before = entry.get("hash_before")
                backup_path = entry.get("backup_path")
                
                dest_file = target_dir / rel_path
                if not dest_file.exists():
                    # If modified file was missing, try restoring from backup
                    if action == "modified" and backup_path:
                        backup_file = fix_dir / backup_path
                        if backup_file.is_file():
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup_file, dest_file)
                            restored_count += 1
                    continue

                try:
                    if action == "modified":
                        # Check for conflicts
                        if hash_after or hash_before:
                            current_hash = cls._sha256_file(dest_file)
                            if current_hash not in (hash_after, hash_before):
                                conflicts += 1
                                conflict_files.append(rel_path)
                                continue
                        
                        # Restore from backup
                        if backup_path:
                            backup_file = fix_dir / backup_path
                        else:
                            backup_file = fix_subdir / f"{rel_path}.bak"

                        if backup_file.is_file():
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup_file, dest_file)
                            restored_count += 1
                            if progress_cb:
                                progress_cb(f"Restoring: {Path(rel_path).name}")
                        else:
                            errors += 1

                    elif action == "added":
                        # Only delete if we wrote it (hash match) or if no hash info recorded
                        if hash_after:
                            current_hash = cls._sha256_file(dest_file)
                            if current_hash == hash_after:
                                dest_file.unlink()
                                deleted_count += 1
                                if progress_cb:
                                    progress_cb(f"Removing: {Path(rel_path).name}")
                            else:
                                conflicts += 1
                                conflict_files.append(rel_path)
                        else:
                            dest_file.unlink()
                            deleted_count += 1
                            if progress_cb:
                                progress_cb(f"Removing: {Path(rel_path).name}")

                except Exception as e:
                    logger.warning(f"[FixManager] Error handling {rel_path}: {e}")
                    errors += 1

            if conflicts > 0:
                # IMPORTANT: DO NOT delete backups or record on conflict!
                return False, f"Conflict detected on {len(conflict_files)} file(s) (e.g. '{conflict_files[0]}'). Another fix or update changed the file. Backups were preserved."

            # Clean up backup directory and record
            if fix_subdir.is_dir():
                shutil.rmtree(fix_subdir, ignore_errors=True)
            
            record_path = fix_dir / f"{fix_key}.json"
            if record_path.exists():
                record_path.unlink()

            # Clean up .lfix directory if empty
            try:
                if fix_dir.is_dir() and not any(fix_dir.iterdir()):
                    fix_dir.rmdir()
            except Exception as e:
                logger.debug(f"[FixManager] Could not remove empty fix directory {fix_dir}: {e}")

            # Build result message
            msg_parts = [f"Fix restored: {restored_count} restored, {deleted_count} deleted"]
            if errors > 0:
                msg_parts.append(f"({errors} errors)")
            msg = " ".join(msg_parts)
            
            return True, msg

        except Exception as e:
            logger.error(f"[FixManager] Error removing fix: {e}", exc_info=True)
            return False, f"Error removing fix: {e}"

    # ──────────────────────────────────────────────────────────────
    # Utility Methods
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def get_fix_status(cls, game_path: str | Path) -> Dict[str, Any]:
        """
        Get the current fix status for a game.
        
        Returns:
            Dict with keys:
            - has_fix: bool
            - fix_id: str or None
            - fix_title: str or None
            - applied_at: str or None
            - file_count: int
            - conflicts: list of (path, reason) tuples
        """
        receipt = cls.get_receipt(game_path)
        if not receipt:
            return {
                "has_fix": False,
                "fix_id": None,
                "fix_title": None,
                "applied_at": None,
                "file_count": 0,
                "conflicts": [],
            }

        # Check for conflicts
        conflicts = []
        for entry in receipt.get("files", []):
            rel_path = entry.get("relative_path")
            hash_after = entry.get("hash_after")
            if hash_after:
                dest_file = Path(game_path) / rel_path
                if dest_file.exists():
                    current_hash = cls._sha256_file(dest_file)
                    if current_hash != hash_after:
                        conflicts.append((rel_path, "file_modified_since_apply"))

        return {
            "has_fix": True,
            "fix_id": receipt.get("fix_id"),
            "fix_title": receipt.get("fix_title"),
            "applied_at": receipt.get("applied_at"),
            "file_count": len(receipt.get("files", [])),
            "conflicts": conflicts,
        }

    @classmethod
    def preview_fix(
        cls,
        game_path: str | Path,
        zip_path: str | Path,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Preview what files would be affected by a fix without applying it.
        
        Returns:
            (entries, modified_count, added_count)
        """
        return cls.plan_fix(game_path, zip_path, selected_files=None)
