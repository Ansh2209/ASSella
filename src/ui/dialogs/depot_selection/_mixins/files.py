"""Browse Files: dumping a depot's manifest and opening the file-selection
tree."""

import logging
import re
import os
import tempfile
import subprocess

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal

from PyQt6.QtWidgets import (
    QMessageBox,
    QProgressDialog,
)

from utils.lua_parsing import find_live

logger = logging.getLogger(__name__)


class FileCustomisationMixin:
    """Browse Files: manifest dump and the file-selection tree."""

    def _on_select_files_clicked(self):
        # 1. Get chosen depots
        chosen_depots = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is not None and id_item.checkState() == Qt.CheckState.Checked:
                role = id_item.data(Qt.ItemDataRole.UserRole + 2)
                if role in ("missing", "expander"):
                    continue
                depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
                chosen_depots.append(depot_id)

        if not chosen_depots:
            QMessageBox.warning(self, "Warning", "Please select at least one depot first.")
            return

        # Use the first checked depot for file list customization
        target_depot = chosen_depots[0]

        # 2. Locate the manifest zip for this app
        from utils.helpers import get_base_path
        app_id = self.app_id

        manifests_dir = get_base_path() / "hubcap_manifests"
        zips = list(manifests_dir.glob(f"accela_fetch_{app_id}.zip")) + \
               list(manifests_dir.glob(f"accela_fetch_{app_id}_*.zip"))
        if not zips:
            QMessageBox.critical(self, "Error", f"No manifest zip file found for AppID {app_id} in {manifests_dir}.")
            return
        zips.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        zip_path = str(zips[0])

        # 3. Extract target manifests
        import zipfile
        temp_dir = os.path.join(tempfile.gettempdir(), f"selective_manifests_{app_id}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            from utils.steam_manifest import safe_extract_zip
            safe_extract_zip(zip_path, temp_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract manifest zip: {e}")
            return

        # Extract key from LUA config or fallback to depot_keys.db
        depot_key = None
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            try:
                with open(str(lua_files[0]), "r", encoding="utf-8") as lf:
                    lua_content = lf.read()
                    match = find_live(
                        lua_content,
                        r"addappid\(\s*" + re.escape(target_depot) + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)",
                    )
                    if match:
                        depot_key = match.group(1)
            except Exception as e:
                logger.warning(f"Failed to parse LUA for depot keys: {e}")

        # Fallback to depot_keys.db if key was not in LUA file (e.g. smart generate bundle)
        if not depot_key:
            try:
                from managers.depot_key_manager import DepotKeyManager
                dkm = DepotKeyManager()
                cached = dkm.get_depot_keys(app_id)
                if target_depot in cached:
                    depot_key = cached[target_depot]
            except Exception as dkm_e:
                logger.warning(f"Failed to load key from depot_keys.db for depot {target_depot}: {dkm_e}")

        if not depot_key:
            QMessageBox.critical(self, "Error", f"Could not find depot key for depot {target_depot} in LUA config or local key database.")
            return

        # Create depot keys file
        keys_path = os.path.join(temp_dir, "depot.keys")
        try:
            with open(keys_path, "w") as kf:
                kf.write(f"{target_depot};{depot_key}\n")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to write keys file: {e}")
            return

        # Locate the manifest file and manifest ID
        manifest_files = list(Path(temp_dir).glob(f"{target_depot}_*.manifest"))
        if not manifest_files:
            # Fallback check if it was zipped without depot ID prefix
            manifest_files = list(Path(temp_dir).glob("*.manifest"))

        if not manifest_files:
            QMessageBox.critical(self, "Error", f"No manifest file (*.manifest) found for depot {target_depot} in the extracted manifest bundle.")
            return

        manifest_path = manifest_files[0]
        manifest_file = str(manifest_path)

        filename = manifest_path.name
        stem = filename.replace(".manifest", "")
        if "_" in stem:
            manifest_id = stem.split("_", 1)[1]
        else:
            manifest_id = stem

        # Dump manifest files using DDM in background progress
        progress_dialog = QProgressDialog("Loading file list from manifest...", "Cancel", 0, 0, self)
        progress_dialog.setWindowTitle("Loading Manifest")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.show()

        # Define command args
        from utils.helpers import get_dotnet_path, resource_path
        dotnet_path = get_dotnet_path()
        dll_path = resource_path(os.path.join("deps", "DepotDownloader.dll"))

        cmd = [
            dotnet_path,
            dll_path,
            "-app", str(app_id),
            "-depot", str(target_depot),
            "-manifest", str(manifest_id),
            "-manifestfile", manifest_file,
            "-depotkeys", keys_path,
            "-manifest-only",
            "-dir", temp_dir
        ]

        class DumpThread(QThread):
            finished_signal = pyqtSignal(bool, str)
            def run(self):
                try:
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    self.finished_signal.emit(True, "")
                except Exception as ex:
                    self.finished_signal.emit(False, str(ex))

        self.dump_thread = DumpThread()

        def on_dump_finished(success, err):
            progress_dialog.close()

            if not success:
                if os.path.exists(keys_path):
                    try:
                        os.remove(keys_path)
                    except OSError:
                        pass
                QMessageBox.critical(self, "Error", f"Failed to load file list: {err}")
                return

            txt_path = os.path.join(temp_dir, f"manifest_{target_depot}_{manifest_id}.txt")
            if not os.path.exists(txt_path):
                if os.path.exists(keys_path):
                    try:
                        os.remove(keys_path)
                    except OSError:
                        pass
                QMessageBox.critical(self, "Error", "Failed to locate generated file list text file.")
                return

            # Open File Selection Tree Dialog
            from ui.dialogs.fileselection import FileSelectionDialog
            sel_dialog = FileSelectionDialog(
                app_id=app_id,
                depot_id=target_depot,
                manifest_txt_path=txt_path,
                parent=self,
                manifest_file=manifest_file,
                keys_path=keys_path,
                manifest_id=manifest_id,
            )
            try:
                if sel_dialog.exec():
                    self.selected_files = sel_dialog.selected_files
                    QMessageBox.information(
                        self,
                        "Selection Confirmed",
                        f"Selected {len(self.selected_files)} file(s) for custom download.\nPress OK at the bottom to start installing."
                    )
            finally:
                if os.path.exists(keys_path):
                    try:
                        os.remove(keys_path)
                    except OSError:
                        pass

        self.dump_thread.finished_signal.connect(on_dump_finished)
        self.dump_thread.start()
