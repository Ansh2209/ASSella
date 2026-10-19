"""
Plugin Games Manager Module

Provides high-level management for games installed or downloaded via SLSsteam plugins
(Steam native downloader). Counterpart to psyche's library database (.psyche-library.json),
tracking which AppIDs, DepotIDs, and AES DecryptionKeys belong to which game so they
can be injected smartly without queuing unintended games.
"""

import json
import logging
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from utils.helpers import get_base_path
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    SHARED_REDISTS,
    _get_section_bounds,
    add_additional_app,
    add_additional_depot,
    add_decryption_key,
    batch_config_edit,
    get_user_config_path,
    remove_additional_app,
    remove_additional_depot,
    remove_decryption_key,
)

logger = logging.getLogger(__name__)

PLUGIN_LIBRARY_FILENAME = "plugin_library.json"


def get_plugin_library_path() -> Path:
    """Return the path to ACCELA's plugin_library.json database."""
    from utils.helpers import get_data_file_path
    return get_data_file_path(PLUGIN_LIBRARY_FILENAME)


def load_plugin_library() -> Dict[str, Any]:
    """Load the plugin-managed games library from disk."""
    lib_path = get_plugin_library_path()
    if not lib_path.exists():
        return {}
    try:
        with open(lib_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.error(f"[PluginGames] Failed to read {lib_path}: {e}")
    return {}


def save_plugin_library(data: Dict[str, Any]) -> bool:
    """Save the plugin-managed games library to disk."""
    lib_path = get_plugin_library_path()
    try:
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = lib_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, lib_path)
        return True
    except Exception as e:
        logger.error(f"[PluginGames] Failed to save {lib_path}: {e}")
        return False


def is_valid_plugin_record(record: Optional[Dict[str, Any]]) -> bool:
    """Check if a plugin library record represents an active AT0-M / plugin-native game."""
    if not record or not isinstance(record, dict):
        return False
    if record.get("is_atom") is False or record.get("is_accela") is True or record.get("mode") == "accela":
        return False
    return True


def get_plugin_game(appid: Union[str, int], active_only: bool = True) -> Optional[Dict[str, Any]]:
    """Retrieve metadata for a specific plugin-managed game."""
    lib = load_plugin_library()
    rec = lib.get(str(appid))
    if active_only and not is_valid_plugin_record(rec):
        return None
    return rec


def is_plugin_game(appid: Union[str, int]) -> bool:
    """Check if an AppID is currently registered as an active AT0-M plugin game."""
    return get_plugin_game(appid, active_only=True) is not None


def get_all_plugin_games(active_only: bool = True) -> Dict[str, Any]:
    """Return all currently registered plugin-managed games."""
    lib = load_plugin_library()
    if not active_only:
        return lib
    return {aid: g for aid, g in lib.items() if is_valid_plugin_record(g)}


def get_game_for_depot(depot_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Find which registered plugin game owns a given depot_id."""
    did_str = str(depot_id).strip()
    lib = load_plugin_library()
    for g in lib.values():
        if did_str in g.get("depots", []) or did_str in g.get("keys", {}):
            return g
    return None


def build_dlc_reverse_map() -> Dict[str, str]:
    """
    Build a reverse mapping {dlc_appid: base_appid} for all DLC-only registered games.
    Used by the library scanner to discover base games when only DLC AppIDs are in
    AdditionalApps (AT0-M DLC-only mode).
    """
    result: Dict[str, str] = {}
    for appid_str, record in load_plugin_library().items():
        for dlc_id in record.get("dlc_appids", []):
            result[str(dlc_id)] = appid_str
    return result


# SHARED_REDISTS is imported above canonically from utils.yaml_config_manager


def register_plugin_game(
    appid: Union[str, int],
    name: str,
    depot_ids: List[Union[str, int]],
    decryption_keys: Optional[Dict[Union[str, int], str]] = None,
    installdir: str = "",
    depot_names: Optional[Dict[Union[str, int], str]] = None,
    dlc_appids: Optional[List[Union[str, int]]] = None,
) -> bool:
    """
    Register a newly installed or downloaded game as plugin-managed.
    AppIDs are NEVER added to AdditionalDepots or DecryptionKeys.
    """
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        logger.warning(f"[PluginGames] Invalid AppID '{appid}' for registration")
        return False

    # Normalise DLC AppIDs (if DLC-only mode was used)
    clean_dlc_appids = [str(d).strip() for d in (dlc_appids or []) if str(d).strip().isdigit()]
    is_dlc_only = bool(clean_dlc_appids)

    # AdditionalDepots: Base AppIDs and DLC AppIDs must NEVER be in clean_depots
    if is_dlc_only:
        from utils.dlc_helpers import filter_dlc_depots_only
        clean_depots = filter_dlc_depots_only(depot_ids, appid_str, dlc_appids=clean_dlc_appids)
    else:
        clean_depots = [
            str(d).strip() for d in depot_ids
            if str(d).strip().isdigit() and str(d).strip() != appid_str and str(d).strip() not in clean_dlc_appids
        ]
    clean_keys = {}
    if decryption_keys:
        for did, key in decryption_keys.items():
            did_str = str(did).strip()
            key_str = str(key).strip().lower()
            if did_str.isdigit() and len(key_str) == 64:
                clean_keys[did_str] = key_str

    # Safeguard AT0-M mode: Only include depots that have decryption keys (or are shared redists)
    valid_depot_keys = set(clean_keys.keys()) | SHARED_REDISTS
    excluded_unkeyed = [d for d in clean_depots if d not in valid_depot_keys]
    if excluded_unkeyed:
        logger.warning(
            f"[PluginGames] Excluded {len(excluded_unkeyed)} unkeyed depot(s) from AdditionalDepots for {appid_str}: {excluded_unkeyed}"
        )
        clean_depots = [d for d in clean_depots if d in valid_depot_keys]

    clean_depot_names = {}
    if depot_names:
        for did, dname in depot_names.items():
            did_str = str(did).strip()
            if did_str.isdigit() and dname and did_str != appid_str:
                clean_depot_names[did_str] = str(dname).strip()

    lib = load_plugin_library()
    game_record = {
        "appid": appid_str,
        "name": name,
        "installdir": installdir,
        "depots": clean_depots,
        "keys": clean_keys,
        "depot_names": clean_depot_names,
        "dlc_only": is_dlc_only,
        "dlc_appids": clean_dlc_appids,
        "updated_at": int(time.time()),
        "source": "plugin_native",
    }
    lib[appid_str] = game_record
    if not save_plugin_library(lib):
        return False

    # Smart injection into SLSsteam config.yaml
    cfg_path = get_user_config_path()
    if cfg_path.exists():
        with batch_config_edit(cfg_path) as editor:
            if is_dlc_only:
                # DLC-only: add DLC AppIDs to AdditionalApps, NOT the base game appid.
                for dlc_id in clean_dlc_appids:
                    editor.add_app(dlc_id, comment=f"{name} DLC ({dlc_id})")
            else:
                # Normal mode: add base AppID
                editor.add_app(appid_str, comment=name)

            # Add only the required depots with clear comments (never AppIDs!)
            for did in clean_depots:
                if did in SHARED_REDISTS:
                    depot_comment = "Steamworks Shared"
                else:
                    d_name = clean_depot_names.get(did, "")
                    depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
                editor.add_depot(did, comment=depot_comment)

            # Add decryption keys with game comment
            for did, key in clean_keys.items():
                if did == appid_str:
                    key_comment = f"{name} [AppKey] ({appid_str})"
                elif did in SHARED_REDISTS:
                    key_comment = "Steamworks Shared"
                else:
                    d_name = clean_depot_names.get(did, "")
                    key_comment = f"{name} - {d_name}" if d_name else name
                editor.add_key(did, key, comment=key_comment)

        # Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    mode_str = "DLC-only" if is_dlc_only else "normal"
    logger.info(
        f"[PluginGames] Successfully registered '{name}' ({appid_str}) "
        f"in {mode_str} mode with {len(clean_depots)} depot(s)"
    )
    return True


def unregister_plugin_game(appid: Union[str, int], keep_in_additional_apps: bool = False) -> bool:
    """
    Unregister a plugin-managed game and clean up its entries in config.yaml.
    Depots, keys, and DLC AppIDs shared with other registered games are safely preserved.

    If keep_in_additional_apps is True (e.g. converting from AT0-M to ACCELA mode):
      - The game's AppID is kept in AdditionalApps.
      - Game-specific depots and keys are pruned from AdditionalDepots and DecryptionKeys.
    """
    appid_str = str(appid).strip()
    lib = load_plugin_library()
    target_game = lib.pop(appid_str, None)
    if target_game:
        save_plugin_library(lib)
    else:
        target_game = {"appid": appid_str, "name": ""}

    # Collect depots, keys, and DLC AppIDs still required by remaining games
    remaining_depots: Set[str] = set()
    remaining_keys: Set[str] = set()
    remaining_dlc_appids: Set[str] = set()
    for g in lib.values():
        remaining_depots.update(str(d) for d in g.get("depots", []))
        remaining_keys.update(str(k) for k in g.get("keys", {}).keys())
        remaining_dlc_appids.update(str(a) for a in g.get("dlc_appids", []))

    # Discover all candidate depots and keys for the target game
    target_depots: Set[str] = {str(d) for d in target_game.get("depots", [])}
    target_keys: Set[str] = {str(d) for d in target_game.get("keys", {}).keys()}
    target_keys.add(appid_str)  # Root AppKey in DecryptionKeys

    # Also discover depots from local SQLite DB for this appid
    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager.get_instance()
        local_keys = dkm.get_keys_for_app(appid_str) or {}
        for did in local_keys.keys():
            did_str = str(did).strip()
            if did_str:
                target_depots.add(did_str)
                target_keys.add(did_str)
    except Exception:
        pass

    cfg_path = get_user_config_path()
    if cfg_path.exists():
        # Scan config.yaml comments for depots/keys mentioning appid_str or game_name
        try:
            txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
            gname = (target_game.get("name") or "").strip().lower()
            for sec in ("AdditionalDepots", "DecryptionKeys"):
                bounds = _get_section_bounds(txt, sec)
                if not bounds:
                    continue
                for line in txt[bounds[1]:bounds[2]].splitlines():
                    m = re.match(r"^[ \t]*-[ \t]*['\"]?(\d+)['\"]?[ \t]*(?:#[ \t]*(.*))?$", line)
                    if not m:
                        m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$", line)
                    if m:
                        item_id, comment = m.group(1), (m.group(2) or "").lower()
                        if appid_str in comment or (gname and len(gname) > 3 and gname in comment):
                            if sec == "AdditionalDepots":
                                target_depots.add(item_id)
                            else:
                                target_keys.add(item_id)
        except Exception as e:
            logger.debug(f"[PluginGames] Error scanning config comments: {e}")

        with batch_config_edit(cfg_path) as editor:
            if keep_in_additional_apps:
                # Ensure the game remains/is added in AdditionalApps for ACCELA mode
                editor.add_app(appid_str, comment=target_game.get("name", ""))
                # Prune DLC AppIDs so regular ACCELA mode doesn't leave unnecessary DLC AppIDs
                for dlc_id in target_game.get("dlc_appids", []):
                    if dlc_id not in remaining_dlc_appids:
                        editor.remove_app(dlc_id)
            else:
                # Full unregistration/uninstall: remove from AdditionalApps
                # 1a. Remove DLC AppIDs from AdditionalApps (DLC-only mode)
                for dlc_id in target_game.get("dlc_appids", []):
                    if dlc_id not in remaining_dlc_appids:
                        editor.remove_app(dlc_id)

                # 1b. Remove base AppID from AdditionalApps (normal mode only)
                if not target_game.get("dlc_only"):
                    editor.remove_app(appid_str)

                # 1c. Remove FakeAppIds, AppTokens, LaunchOptions, and DlcData
                editor.remove_fake_app_id(appid_str)
                editor.remove_app_token(appid_str)
                editor.remove_launch_option(appid_str)
                editor.remove_dlc(appid_str)
                for dlc_id in target_game.get("dlc_appids", []):
                    editor.remove_fake_app_id(dlc_id)
                    editor.remove_app_token(dlc_id)
                    editor.remove_launch_option(dlc_id)

                # 1d. Remove secondary sections (GameTitles, ManifestIds, CDKeys, etc.)
                for sec in (
                    "GameTitles",
                    "ManifestIds",
                    "DepotBlacklist",
                    "CDKeys",
                    "FakeOffline",
                    "SubscriptionTimestamps",
                    "DenuvoGames",
                ):
                    editor.remove_secondary_entry(sec, appid_str)
                    for dlc_id in target_game.get("dlc_appids", []):
                        editor.remove_secondary_entry(sec, dlc_id)
                    for did in target_depots:
                        editor.remove_secondary_entry(sec, did)

            # 2. Remove depots that are not shared with any other registered game
            for did in target_depots:
                if did not in remaining_depots:
                    editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)

            # 3. Remove decryption keys that are not shared
            for did in target_keys:
                if did not in remaining_keys:
                    editor.remove_key(did, check_shared=True, excluding_appid=appid_str)

        # 4. Remove any pinned build manifests for this game's depots
        try:
            from core.native_steam.steam_manifest_pinning import remove_manifest_ids
            remove_manifest_ids(cfg_path, list(target_depots))
        except Exception as e:
            logger.debug(f"[PluginGames] Error removing pinned manifests for {appid_str}: {e}")

        # 5. Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    game_label = target_game.get("name") or appid_str
    logger.info(f"[PluginGames] Successfully unregistered '{game_label}' ({appid_str}) [keep_apps={keep_in_additional_apps}]")
    return True


def convert_plugin_game_to_accela(appid: Union[str, int]) -> bool:
    """
    Convert a game from AT0-M (plugin mode) to ACCELA Managed mode.
    - AppID is PRESERVED in AdditionalApps (ensuring the game stays unlocked in Steam).
    - Game-specific depots and keys are removed from AdditionalDepots and DecryptionKeys.
    - Shared depots/keys (used by other registered games or common redistributables) are preserved.
    - Unregisters the game from plugin_library.json.
    """
    return unregister_plugin_game(appid, keep_in_additional_apps=True)


def sync_all_plugin_games_to_config() -> None:
    """Ensure all registered plugin games have their AppID, depots, and keys in config.yaml with comments."""
    lib = load_plugin_library()
    if not lib:
        return

    cfg_path = get_user_config_path()
    if not cfg_path.exists():
        return

    with batch_config_edit(cfg_path) as editor:
        for appid_str, game in lib.items():
            name = game.get("name", "")
            depot_names = game.get("depot_names", {})

            if game.get("dlc_only") and game.get("dlc_appids"):
                # DLC-only: sync DLC AppIDs (not base appid) to AdditionalApps
                for dlc_id in game["dlc_appids"]:
                    editor.add_app(dlc_id, comment=f"{name} DLC ({dlc_id})")
            else:
                editor.add_app(appid_str, comment=name)

            for did in game.get("depots", []):
                if str(did) == appid_str:
                    continue  # Never add AppID to AdditionalDepots
                if str(did) in SHARED_REDISTS:
                    depot_comment = "Steamworks Shared"
                else:
                    d_name = depot_names.get(did, "")
                    depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
                editor.add_depot(did, comment=depot_comment)

            for did, key in game.get("keys", {}).items():
                did_str = str(did)
                if did_str == appid_str:
                    key_comment = f"{name} [AppKey] ({appid_str})"
                elif did_str in SHARED_REDISTS:
                    key_comment = "Steamworks Shared"
                else:
                    d_name = depot_names.get(did_str, "")
                    key_comment = f"{name} - {d_name}" if d_name else name
                editor.add_key(did_str, key, comment=key_comment)

        if editor.has_changes:
            SLSBridge.notify_reload()
            logger.info("[PluginGames] Synced all registered plugin games to config.yaml in a single batch")


def get_atom_game_install_info(appid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """
    Locate where a game is installed (or currently downloading) when managed via AT0-M mode.
    Since download and installation are handled directly by Steam, this inspects:
      1. All Steam library folders from libraryfolders.vdf.
      2. The appmanifest_<appid>.acf file in each library.
      3. The 'installdir' property pointing to steamapps/common/<installdir>.
      4. Staged downloads in steamapps/downloading/<appid>.

    Returns a dict with install paths, state flags, size, build ID, and download state,
    or None if not found in any Steam library.
    """
    aid_str = str(appid).strip()
    if not aid_str or not aid_str.isdigit():
        return None

    try:
        from core.steam_helpers import get_steam_libraries
        libs = get_steam_libraries()
    except Exception:
        libs = []

    fallback_libs = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]
    for fb in fallback_libs:
        if fb.is_dir() and fb not in libs:
            libs.append(fb)

    for lib_path in libs:
        lib = Path(lib_path)
        steamapps = lib / "steamapps"
        if not steamapps.is_dir():
            continue

        acf_path = steamapps / f"appmanifest_{aid_str}.acf"
        common_dir = steamapps / "common"
        downloading_dir = steamapps / "downloading" / aid_str

        installdir = ""
        game_name = ""
        state_flags = 0
        size_on_disk = 0
        buildid = ""
        bytes_downloaded = 0
        bytes_to_download = 0

        if acf_path.is_file():
            try:
                content = acf_path.read_text(encoding="utf-8", errors="replace")
                m_dir = re.search(r'"installdir"\s+"([^"]+)"', content)
                if m_dir:
                    installdir = m_dir.group(1).strip()
                m_name = re.search(r'"name"\s+"([^"]+)"', content)
                if m_name:
                    game_name = m_name.group(1).strip()
                m_state = re.search(r'"StateFlags"\s+"(\d+)"', content)
                if m_state:
                    state_flags = int(m_state.group(1))
                m_size = re.search(r'"SizeOnDisk"\s+"(\d+)"', content)
                if m_size:
                    size_on_disk = int(m_size.group(1))
                m_build = re.search(r'"buildid"\s+"([^"]+)"', content)
                if m_build:
                    buildid = m_build.group(1).strip()
                m_b_dl = re.search(r'"BytesDownloaded"\s+"(\d+)"', content)
                if m_b_dl:
                    bytes_downloaded = int(m_b_dl.group(1))
                m_b_td = re.search(r'"BytesToDownload"\s+"(\d+)"', content)
                if m_b_td:
                    bytes_to_download = int(m_b_td.group(1))
            except Exception as e:
                logger.debug(f"[PluginGames] Error reading {acf_path}: {e}")

        # If installdir wasn't in ACF, check plugin_library record
        if not installdir:
            record = get_plugin_game(aid_str)
            if record:
                installdir = record.get("installdir") or ""
                if not game_name:
                    game_name = record.get("name") or ""

        target_folder = (common_dir / installdir) if installdir else None
        folder_exists = bool(target_folder and target_folder.is_dir())
        is_dl = bool(downloading_dir.is_dir() or (state_flags & 1024 != 0))
        is_installed = bool((state_flags & 4 != 0) and folder_exists)

        if folder_exists or is_dl or acf_path.is_file():
            resolved_install_path = str(target_folder) if folder_exists else (str(downloading_dir) if downloading_dir.is_dir() else None)
            return {
                "appid": aid_str,
                "game_name": game_name or installdir or aid_str,
                "installed": is_installed,
                "is_downloading": is_dl,
                "install_path": resolved_install_path,
                "common_path": str(target_folder) if target_folder else None,
                "downloading_path": str(downloading_dir) if downloading_dir.is_dir() else None,
                "library_path": str(lib),
                "appmanifest_path": str(acf_path) if acf_path.is_file() else None,
                "installdir": installdir,
                "state_flags": state_flags,
                "size_on_disk": size_on_disk,
                "buildid": buildid,
                "bytes_downloaded": bytes_downloaded,
                "bytes_to_download": bytes_to_download,
            }

    return None


def get_atom_game_install_path(appid: Union[str, int]) -> Optional[str]:
    """Return the absolute filesystem path where the AT0-M game is installed, or None."""
    info = get_atom_game_install_info(appid)
    if info:
        return info.get("install_path")
    return None


def _atomic_write_acf(acf_path: str, content: str) -> bool:
    """Back up and atomically replace a Steam ACF."""
    path = Path(acf_path)
    if not path.exists():
        return False
    try:
        stamp = int(time.time())
        backup = path.with_suffix(path.suffix + f".assella-bak-{stamp}")
        shutil.copy2(path, backup)
    except Exception as e:
        logger.warning(f"[PluginGames] Could not back up ACF {acf_path}: {e}")
        return False

    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning(f"[PluginGames] Atomic ACF write failed for {acf_path}: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _ensure_acf_has_installed_depots(acf_path: str, appid: str, depot_ids: list, game_data: dict) -> None:
    """Make Steam adopt an already-downloaded install by adding InstalledDepots to ACF."""
    if not acf_path or not os.path.exists(acf_path):
        return
    try:
        with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        needs_size = False
        m_size = re.search(r'"SizeOnDisk"\s*"(\d+)"', content)
        if m_size and m_size.group(1) == "0":
            needs_size = True

        install_path = game_data.get("install_path")
        if not install_path and game_data.get("library_path"):
            folder_name = game_data.get("installdir") or game_data.get("game_name")
            if folder_name:
                cand_install = Path(game_data["library_path"]) / "steamapps" / "common" / folder_name
                if cand_install.is_dir():
                    install_path = str(cand_install)

        actual_size = 0
        if install_path and os.path.isdir(install_path):
            try:
                for root, _, files in os.walk(install_path):
                    for file in files:
                        try:
                            actual_size += os.path.getsize(os.path.join(root, file))
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"[PluginGames] Error calculating size: {e}")

        wanted = [str(d).strip() for d in (depot_ids or []) if str(d).strip() and str(d).strip() != str(appid)]
        has_entries = bool(re.search(r'"InstalledDepots"\s*\{[^}]*"\d+"\s*\n\s*\{', content))

        if not has_entries and wanted:
            depots_lines = []
            per_depot = actual_size // max(1, len(wanted))
            for did_str in wanted:
                dsize = actual_size if len(wanted) == 1 else per_depot
                depots_lines.append(
                    f'\t\t"{did_str}"\n\t\t{{\n\t\t\t"manifest"\t\t"0"\n\t\t\t"size"\t\t"{dsize}"\n\t\t}}'
                )
            if depots_lines:
                new_block = '\t"InstalledDepots"\n\t{\n' + "\n".join(depots_lines) + '\n\t}'
                if re.search(r'"InstalledDepots"\s*\{\s*\}', content):
                    content = re.sub(
                        r'"InstalledDepots"\s*\{\s*\}', new_block.replace("\\", "\\\\"), content, count=1
                    )
                else:
                    content = re.sub(
                        r'("AppState"\s*\{)', rf'\1\n{new_block}', content, count=1
                    )

        if (needs_size or not m_size) and actual_size > 0:
            if m_size:
                content = re.sub(r'"SizeOnDisk"\s*"\d+"', f'"SizeOnDisk"\t\t"{actual_size}"', content)
            else:
                content = re.sub(r'("AppState"\s*\{)', rf'\1\n\t"SizeOnDisk"\t\t"{actual_size}"', content)

        if not _atomic_write_acf(acf_path, content):
            return
        logger.info(
            f"[PluginGames] Updated ACF {acf_path}: {len(wanted)} depot(s) listed"
        )
    except Exception as e:
        logger.warning(f"[PluginGames] Graceful fallback on updating ACF {acf_path}: {e}")


def collect_game_depots_and_keys(
    appid: Union[str, int],
    game_data: Optional[Dict[str, Any]] = None,
    allow_hubcap_fetch: bool = True,
) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """
    Collect all known depot IDs, decryption keys, and depot names for a game.
    Queries DatabaseManager, local .depot files, SQLite DepotKeyManager,
    cached luas, manifest zips, and falls back to Hubcap API manifest fetch if needed.
    """
    from utils.lua_parsing import iter_live_matches

    appid_str = str(appid).strip()
    depot_ids: List[str] = []
    decryption_keys: Dict[str, str] = {}
    depot_names: Dict[str, str] = {}

    game_dict = game_data or {}

    # 1. DatabaseManager lookup
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(appid_str, bypass_expiration=True)
        if app_info and app_info.get("depots"):
            for did, dinfo in app_info["depots"].items():
                did_str = str(did)
                if did_str not in depot_ids:
                    depot_ids.append(did_str)
                if isinstance(dinfo, dict) and dinfo.get("name"):
                    depot_names[did_str] = str(dinfo["name"])
    except Exception as e:
        logger.debug(f"[PluginGames] DB lookup error for {appid_str}: {e}")

    # 2. Local .depot files
    depot_file = get_base_path() / "depots" / f"{appid_str}.depot"
    if depot_file.exists():
        try:
            with open(depot_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split(":")
                    if parts and parts[0].strip().isdigit():
                        did = parts[0].strip()
                        if did not in depot_ids:
                            depot_ids.append(did)
                        if len(parts) > 1 and parts[1].strip() and not parts[1].strip().isdigit():
                            depot_names[did] = parts[1].strip()
        except Exception as e:
            logger.debug(f"[PluginGames] Could not read .depot file for {appid_str}: {e}")

    # 3. Authoritative local SQLite DepotKeyManager
    try:
        from managers.depot_key_manager import DepotKeyManager
        stored_keys = DepotKeyManager.get_instance().get_depot_keys(appid_str)
        for did, key in stored_keys.items():
            did_str = str(did).strip()
            key_str = str(key).strip().lower()
            if key_str and len(key_str) == 64:
                decryption_keys[did_str] = key_str
                if did_str != appid_str and did_str not in depot_ids:
                    depot_ids.append(did_str)
    except Exception as e:
        logger.debug(f"[PluginGames] DepotKeyManager lookup error for {appid_str}: {e}")

    # 4. Extract from cached Lua or manifest zip
    def _extract_keys_from_lua(lua_text: str):
        for m in iter_live_matches(lua_text, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
            did, key = m.group(1), m.group(2).lower()
            decryption_keys[did] = key
            if did != appid_str and did not in depot_ids:
                depot_ids.append(did)

    lua_file = get_base_path() / "cached_luas" / f"{appid_str}.lua"
    if lua_file.exists():
        try:
            with open(lua_file, "r", encoding="utf-8", errors="ignore") as lf:
                _extract_keys_from_lua(lf.read())
        except Exception as e:
            logger.debug(f"[PluginGames] Could not read cached Lua for {appid_str}: {e}")

    zip_file = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid_str}.zip"
    if zip_file.exists():
        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".lua"):
                        _extract_keys_from_lua(zf.read(name).decode("utf-8", errors="ignore"))
        except Exception as e:
            logger.debug(f"[PluginGames] Could not read manifest zip for {appid_str}: {e}")

    # 5. Respect user's saved depot selection if available
    from utils.dlc_helpers import get_saved_depot_selection
    saved_selection = get_saved_depot_selection(appid_str)
    if not saved_selection and game_dict:
        saved_selection = game_dict.get("selected_depots") or (
            game_dict.get("metadata", {}).get("selected_depots_list")
        )
    if saved_selection:
        saved_depot_set = {str(d) for d in saved_selection}
        user_depots = [d for d in depot_ids if str(d) in saved_depot_set]
        if user_depots:
            depot_ids = user_depots

    # 6. Filter out appid itself from depot_ids
    depot_ids = [d for d in depot_ids if str(d) != appid_str]

    # 7. If any depot is missing keys, attempt emergency Hubcap fetch
    missing_key_depots = [d for d in depot_ids if str(d) not in decryption_keys]
    if missing_key_depots and allow_hubcap_fetch:
        try:
            logger.info(f"[PluginGames] Missing keys for depot(s) {missing_key_depots} of {appid_str}. Fetching from Hubcap...")
            from core import morrenus_api
            zip_res, _ = morrenus_api.download_manifest(appid_str, force_update=True)
            if zip_res and os.path.exists(zip_res):
                fresh_keys = {}
                with zipfile.ZipFile(zip_res, "r") as zf:
                    for name in zf.namelist():
                        if name.endswith(".lua"):
                            lua_txt = zf.read(name).decode("utf-8", errors="ignore")
                            for m in iter_live_matches(lua_txt, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
                                did_f, k_f = m.group(1), m.group(2).lower()
                                fresh_keys[did_f] = k_f
                if fresh_keys:
                    from managers.depot_key_manager import DepotKeyManager
                    DepotKeyManager.get_instance().save_depot_keys(appid_str, fresh_keys)
                    for did_f, k_f in fresh_keys.items():
                        decryption_keys[did_f] = k_f
                        if did_f != appid_str and did_f not in depot_ids:
                            depot_ids.append(did_f)
                    logger.info(f"[PluginGames] Recovered {len(fresh_keys)} fresh key(s) from Hubcap for {appid_str}.")
        except Exception as e:
            logger.warning(f"[PluginGames] Hubcap emergency key recovery failed for {appid_str}: {e}")

    return depot_ids, decryption_keys, depot_names


def batch_convert_games_to_atom(
    games: List[Dict[str, Any]],
    progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Batch convert multiple eligible games from ASSella to AT0-M (Steam Native) mode.
    - Resolves depots and keys per game.
    - Excludes unkeyed depots to safeguard against Steam DepotWithoutKey errors.
    - Skips games with zero decryption keys.
    - Removes .DepotDownloader / legacy markers from install folders.
    - Seeds Steam central depotcache.
    - Prepares InstalledDepots in Steam appmanifest ACF files.
    - Injects all games, depots, and keys into SLSsteam config.yaml in a single atomic batch.
    - Emits SLSBridge.notify_reload() once.
    - Strict: Does NOT send SLSsteam install API commands.
    """
    from utils.settings import get_settings

    migrated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    staged_records: List[Dict[str, Any]] = []

    total = len(games)
    lib = load_plugin_library()
    settings = get_settings()

    for idx, game_data in enumerate(games):
        if cancel_check and cancel_check():
            logger.info("[PluginGames] Batch AT0-M conversion canceled by user.")
            break

        step_num = idx + 1
        appid_str = str(game_data.get("appid", "")).strip()
        game_name = game_data.get("game_name") or f"App {appid_str}"

        if progress_callback:
            progress_callback(step_num, total, game_name, "Collecting depots and decryption keys...")

        # 1. Collect depots, keys, and names
        depot_ids, decryption_keys, depot_names = collect_game_depots_and_keys(
            appid_str, game_data=game_data, allow_hubcap_fetch=True
        )

        # 2. Exclude unkeyed depots
        valid_keys_and_redists = set(decryption_keys.keys()) | SHARED_REDISTS
        depot_ids = [d for d in depot_ids if d in valid_keys_and_redists]

        # 3. Guard against games with zero decryption keys
        if not decryption_keys or not depot_ids:
            logger.warning(
                f"[PluginGames] Skipping '{game_name}' ({appid_str}): 0 valid decryption keys or depots available."
            )
            failed.append({
                "appid": appid_str,
                "name": game_name,
                "reason": "No valid decryption keys or depots found",
            })
            continue

        if progress_callback:
            progress_callback(step_num, total, game_name, "Cleaning legacy markers & updating metadata...")

        # 4. Remove ASSella markers from install directory
        install_path = game_data.get("install_path")
        if not install_path and game_data.get("library_path"):
            folder_name = game_data.get("installdir") or game_data.get("game_name")
            if folder_name:
                cand = Path(game_data["library_path"]) / "steamapps" / "common" / folder_name
                if cand.is_dir():
                    install_path = str(cand)

        if install_path and os.path.isdir(install_path):
            for marker_name in (".assella", ".ASSELLA", ".ACCELA", ".accela", ".DepotDownloader", ".depotdownloader"):
                m_path = os.path.join(install_path, marker_name)
                if os.path.exists(m_path):
                    try:
                        if os.path.isdir(m_path):
                            shutil.rmtree(m_path, ignore_errors=True)
                        else:
                            os.remove(m_path)
                    except Exception as e:
                        logger.debug(f"[PluginGames] Marker cleanup failed for {m_path}: {e}")

        # 5. Update settings
        settings.setValue(f"exclude_from_update_all/{appid_str}", True)
        settings.setValue(f"selected_branch/{appid_str}", "public")
        settings.setValue(f"installed_branch/{appid_str}", "public")

        # 6. Update ACF InstalledDepots
        acf_path = game_data.get("appmanifest_path")
        if not acf_path and game_data.get("library_path"):
            cand_acf = Path(game_data["library_path"]) / "steamapps" / f"appmanifest_{appid_str}.acf"
            if cand_acf.exists():
                acf_path = str(cand_acf)
        if not acf_path:
            try:
                from core.steam_helpers import get_steam_libraries
                for slib in (get_steam_libraries() or []):
                    cand_acf = Path(slib) / "steamapps" / f"appmanifest_{appid_str}.acf"
                    if cand_acf.exists():
                        acf_path = str(cand_acf)
                        break
            except Exception:
                pass

        if acf_path and os.path.exists(acf_path):
            _ensure_acf_has_installed_depots(acf_path, appid_str, depot_ids, game_data)

        # 7. Seed Steam central depotcache
        try:
            from core.native_steam.native_steam_handoff import sync_manifests_to_depotcache
            dest_lib = game_data.get("library_path") or install_path or ""
            sync_manifests_to_depotcache(appid_str, dest_lib)
        except Exception as e:
            logger.debug(f"[PluginGames] Depotcache sync failed for {appid_str}: {e}")

        # 8. Stage game record for plugin_library
        installdir = game_data.get("installdir") or game_data.get("game_name") or ""
        game_record = {
            "appid": appid_str,
            "name": game_name,
            "installdir": installdir,
            "depots": depot_ids,
            "keys": decryption_keys,
            "depot_names": depot_names,
            "dlc_only": False,
            "dlc_appids": [],
            "updated_at": int(time.time()),
            "source": "plugin_native",
        }
        staged_records.append(game_record)
        migrated.append({
            "appid": appid_str,
            "name": game_name,
            "depots": len(depot_ids),
            "keys": len(decryption_keys),
        })

    # Save to plugin_library.json
    if staged_records:
        if progress_callback:
            progress_callback(total, total, "SLSsteam Config", "Applying atomic batch configuration updates...")

        for rec in staged_records:
            lib[rec["appid"]] = rec
        save_plugin_library(lib)

        # Atomic batch edit to config.yaml
        cfg_path = get_user_config_path()
        if cfg_path.exists():
            with batch_config_edit(cfg_path) as editor:
                for rec in staged_records:
                    aid = rec["appid"]
                    name = rec["name"]
                    editor.add_app(aid, comment=name)

                    for did in rec["depots"]:
                        if did in SHARED_REDISTS:
                            depot_comment = "Steamworks Shared"
                        else:
                            d_name = rec["depot_names"].get(did, "")
                            depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
                        editor.add_depot(did, comment=depot_comment)

                    for did, key in rec["keys"].items():
                        if did == aid:
                            key_comment = f"{name} [AppKey] ({aid})"
                        elif did in SHARED_REDISTS:
                            key_comment = "Steamworks Shared"
                        else:
                            d_name = rec["depot_names"].get(did, "")
                            key_comment = f"{name} - {d_name}" if d_name else name
                        editor.add_key(did, key, comment=key_comment)

            # Reload SLSsteam
            SLSBridge.notify_reload()
            logger.info(
                f"[PluginGames] Batch AT0-M migration successfully applied for {len(staged_records)} game(s)."
            )

    return {"migrated": migrated, "failed": failed}


