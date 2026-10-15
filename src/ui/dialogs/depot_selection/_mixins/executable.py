"""Pre-flight check that the selected depots actually contain the game's
launch executable."""

import logging
import re

from pathlib import Path

from PyQt6.QtCore import Qt

from PyQt6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


class ExecutableVerifyMixin:
    """Pre-flight check that the selection contains the game's launch executable."""

    def _verify_executable_selection(self, selected_depots: list) -> bool:
        """
        Feature B Pre-flight guard:
        Verify that at least one selected depot contains the primary launch executable.
        If missing, prompts user with option to automatically select the depot and continue.
        """
        if not self.app_id or not selected_depots:
            return True

        appid_str = str(self.app_id).strip()
        exe_name = None

        # 1. Try resolving launch executable from game_data
        if hasattr(self, "game_data") and isinstance(self.game_data, dict):
            exe_name = self.game_data.get("executable") or self.game_data.get("launch_executable")

        # 2. Try resolving from local Steam appinfo.vdf
        if not exe_name:
            try:
                import struct
                for p in [
                    Path.home() / ".local/share/Steam/appcache/appinfo.vdf",
                    Path.home() / ".steam/steam/appcache/appinfo.vdf",
                    Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/appcache/appinfo.vdf",
                ]:
                    if p.is_file():
                        with open(p, "rb") as f:
                            f.seek(16)
                            appid_num = int(appid_str)
                            while True:
                                buf = f.read(4)
                                if len(buf) < 4:
                                    break
                                aid = struct.unpack("<I", buf)[0]
                                if aid == 0:
                                    break
                                size = struct.unpack("<I", f.read(4))[0]
                                f.seek(48, 1)
                                vdf_data = f.read(size - 60)
                                if aid == appid_num:
                                    m = re.search(rb"executable\x00([^\x00]+)\x00", vdf_data)
                                    if m:
                                        exe_name = m.group(1).decode("latin1", errors="ignore").replace("\\", "/")
                                        break
                        if exe_name:
                            break
            except Exception as e:
                logger.debug(f"[DepotSelection] appinfo.vdf exe lookup error: {e}")

        if not exe_name:
            return True

        exe_filename = Path(exe_name).name.lower()
        if not exe_filename:
            return True

        # 3. Check which depot holds this executable
        depot_with_exe = None
        target_bytes = exe_filename.encode("latin1", errors="ignore")

        # Check in local hubcap zip bundle if present
        from utils.helpers import get_base_path
        zip_candidates = [
            Path(get_base_path()) / "hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
            Path.home() / ".local/share/ACCELA/hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
        ]
        import zipfile
        for z_path in zip_candidates:
            if z_path.is_file():
                try:
                    with zipfile.ZipFile(z_path, "r") as zf:
                        for mf in zf.namelist():
                            if mf.endswith(".manifest"):
                                raw = zf.read(mf)
                                if target_bytes in raw.lower():
                                    depot_with_exe = mf.split("_")[0]
                                    break
                except Exception:
                    pass
            if depot_with_exe:
                break

        # Check loose manifests in cache
        if not depot_with_exe:
            manifest_dirs = [
                Path(get_base_path()) / "manifests",
                Path.home() / ".local/share/Steam/steamapps/depotcache",
            ]
            for m_dir in manifest_dirs:
                if m_dir.is_dir():
                    try:
                        for mf in m_dir.glob("*.manifest"):
                            parts = mf.stem.split("_")
                            if len(parts) >= 1:
                                raw = mf.read_bytes()
                                if target_bytes in raw.lower():
                                    depot_with_exe = parts[0]
                                    break
                    except Exception:
                        pass
                if depot_with_exe:
                    break

        # If a depot containing the executable is found, check if it's selected
        if depot_with_exe and str(depot_with_exe) not in [str(d) for d in selected_depots]:
            reply = QMessageBox.warning(
                self,
                "Missing Game Executable",
                f"The launch executable '{Path(exe_name).name}' was detected in Depot {depot_with_exe}, "
                f"which is currently NOT selected.\n\n"
                f"Without this depot, Steam will fail to launch the game ('Missing Executable').\n\n"
                f"Would you like to select Depot {depot_with_exe} and proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Ignore | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.table_widget.blockSignals(True)
                for i in range(self.table_widget.rowCount()):
                    id_item = self.table_widget.item(i, 0)
                    if id_item and str(id_item.data(Qt.ItemDataRole.UserRole)) == str(depot_with_exe):
                        id_item.setCheckState(Qt.CheckState.Checked)
                        break
                self.table_widget.blockSignals(False)
                self._update_table_headers()
                return True
            elif reply == QMessageBox.StandardButton.Ignore:
                return True
            else:
                return False

        return True
