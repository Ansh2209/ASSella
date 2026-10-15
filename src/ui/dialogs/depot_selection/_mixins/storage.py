"""The storage-library picker in the bottom bar and the selected-storage
accessor."""

import logging
import os

from pathlib import Path

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QSizePolicy,
)

logger = logging.getLogger(__name__)


class StorageMixin:
    """The storage-library picker and the selected-storage accessor."""

    def _setup_storage_buttons(self, layout: QHBoxLayout) -> None:
        import shutil
        from core.steam_helpers import get_steam_libraries, find_steam_install
        from utils.paths import is_valid_download_directory

        storage_paths = []
        try:
            raw_libs = get_steam_libraries() or []
            for p in raw_libs:
                if p and is_valid_download_directory(p):
                    real_p = os.path.realpath(p)
                    if real_p not in storage_paths:
                        storage_paths.append(real_p)
        except Exception as e:
            logger.warning(f"Error discovering Steam storage libraries: {e}")

        def_dir = self._settings.value("default_download_directory", "", type=str) if self._settings else ""
        if def_dir and is_valid_download_directory(def_dir):
            real_def = os.path.realpath(def_dir)
            if real_def not in storage_paths:
                storage_paths.append(real_def)

        if self.preferred_library_path and is_valid_download_directory(self.preferred_library_path):
            real_pref = os.path.realpath(self.preferred_library_path)
            if real_pref not in storage_paths:
                storage_paths.append(real_pref)

        self._storage_paths = storage_paths
        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)

        if not storage_paths:
            layout.addStretch(1)
            return

        def _format_storage_info(path_str: str):
            p = Path(path_str)
            try:
                free_bytes = shutil.disk_usage(path_str).free
                if free_bytes >= 1024**4:
                    free_str = f"{free_bytes / (1024**4):.1f} TB free"
                elif free_bytes >= 1024**3:
                    free_str = f"{free_bytes / (1024**3):.1f} GB free"
                elif free_bytes >= 1024**2:
                    free_str = f"{free_bytes / (1024**2):.0f} MB free"
                else:
                    free_str = f"{free_bytes} B free"
            except Exception:
                free_str = ""

            steam_root = find_steam_install()
            p_str_lower = path_str.lower()

            if steam_root and os.path.realpath(path_str) == os.path.realpath(steam_root):
                label = "Primary"
            elif "/.local/share/steam" in p_str_lower or "/.steam/steam" in p_str_lower:
                label = "Primary"
            elif "sdcard" in p_str_lower or "sd_card" in p_str_lower or "mmcblk" in p_str_lower or "/sd" in p_str_lower:
                label = "SD Card"
            else:
                label = p.name
                if label.lower() in ("steamlibrary", "steamapps", "common") and len(p.parts) > 1:
                    label = p.parts[-2]
                if len(label) > 16:
                    label = label[:14] + "…"

            tooltip = f"Storage: {path_str}" + (f"\nAvailable: {free_str}" if free_str else "")
            return label, free_str, tooltip

        # Locked storage display (verify / update / DLC flows over installed
        # games): greyed-out label showing the install location, no choice.
        # Fresh installs (lock_storage unset) always get the selectable UI.
        from ui.dialogs.storage_lock import (
            create_locked_storage_button,
            resolve_locked_target,
        )
        _locked_target = resolve_locked_target(
            self.parent(),
            getattr(self, "app_id", ""),
            self.preferred_library_path,
            storage_paths,
            bool(getattr(self, "lock_storage", False)),
        )
        if _locked_target:
            lbl_text, free_str, _ = _format_storage_info(_locked_target)
            btn = create_locked_storage_button(_locked_target, lbl_text, free_str, height=28, radius=4)
            layout.addWidget(btn, 1)
            self.selected_storage_path = _locked_target
            return

        def _get_storage_btn_style():
            from utils.color_utils import get_best_foreground_color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
            return f"""
                QPushButton {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 8.5pt;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
                QPushButton:checked {{
                    background-color: {self.accent_color} !important;
                    color: {text_hex} !important;
                    border: 1px solid {self.accent_color} !important;
                    font-weight: bold;
                }}
            """

        self._storage_buttons = {}
        self._more_storage_combo = None

        total_storages = len(storage_paths)
        max_direct_buttons = 3

        for i in range(min(total_storages, max_direct_buttons)):
            spath = storage_paths[i]
            lbl_text, free_str, tip_text = _format_storage_info(spath)
            btn_text = f"{lbl_text} ({free_str})" if (total_storages <= 2 and free_str) else lbl_text
            btn = QPushButton(btn_text)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setToolTip(tip_text)
            btn.setStyleSheet(_get_storage_btn_style())
            self._storage_btn_group.addButton(btn, i)
            self._storage_buttons[spath] = btn
            layout.addWidget(btn, 1)

        def _on_storage_btn_clicked(btn_id: int):
            if 0 <= btn_id < len(storage_paths):
                target_path = storage_paths[btn_id]
                self.selected_storage_path = target_path
                logger.info(f"Storage library selected via button [{btn_id}]: {target_path}")
                if self._more_storage_combo:
                    self._more_storage_combo.blockSignals(True)
                    self._more_storage_combo.setCurrentIndex(0)
                    self._more_storage_combo.blockSignals(False)

        self._storage_btn_group.idClicked.connect(_on_storage_btn_clicked)

        if len(storage_paths) > max_direct_buttons:
            self._more_storage_combo = QComboBox()
            self._more_storage_combo.setFixedHeight(28)
            self._more_storage_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._more_storage_combo.addItem("More Drives ▾", None)
            self._more_storage_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 8.5pt;
                }}
                QComboBox:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1a24;
                    color: #FFFFFF;
                    selection-background-color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            for i in range(max_direct_buttons, len(storage_paths)):
                spath = storage_paths[i]
                lbl_text, free_str, tip_text = _format_storage_info(spath)
                item_text = f"{lbl_text} ({free_str})" if free_str else lbl_text
                self._more_storage_combo.addItem(item_text, spath)

            def _on_combo_changed(index):
                if index > 0:
                    chosen_path = self._more_storage_combo.itemData(index)
                    if chosen_path:
                        self.selected_storage_path = chosen_path
                        logger.info(f"Storage library selected via dropdown: {chosen_path}")
                        checked_btn = self._storage_btn_group.checkedButton()
                        if checked_btn:
                            self._storage_btn_group.setExclusive(False)
                            checked_btn.setChecked(False)
                            self._storage_btn_group.setExclusive(True)

            self._more_storage_combo.currentIndexChanged.connect(_on_combo_changed)
            layout.addWidget(self._more_storage_combo, 1)

        # Check if the game is already installed in any of the detected Steam libraries
        installed_target = None
        if self.app_id and str(self.app_id) not in ("0", "N/A", "unknown"):
            parent = self.parent()
            gm = getattr(parent, "game_manager", None) if parent else None
            if gm and hasattr(gm, "get_game"):
                igame = gm.get_game(str(self.app_id))
                if igame and igame.get("library_path"):
                    real_installed = os.path.realpath(igame["library_path"])
                    if real_installed in storage_paths:
                        installed_target = real_installed
                        logger.info(f"[DepotSelection] Pre-selecting existing install library for AppID {self.app_id}: {installed_target}")

        # Pre-select priority:
        # 1. Explicitly preferred library path (from Zip confirmation or job metadata)
        # 2. Existing install library
        # 3. Configured default directory (if valid and in storage_paths)
        # 4. First storage library
        real_pref = os.path.realpath(self.preferred_library_path) if self.preferred_library_path else ""
        real_def = os.path.realpath(def_dir) if def_dir else ""
        if real_pref and real_pref in storage_paths:
            default_target = real_pref
            logger.info(f"[DepotSelection] Pre-selecting preferred library path: {default_target}")
        elif installed_target:
            default_target = installed_target
        elif real_def and real_def in storage_paths:
            default_target = real_def
        else:
            default_target = storage_paths[0]

        self.selected_storage_path = default_target
        if default_target in self._storage_buttons:
            self._storage_buttons[default_target].setChecked(True)
        elif self._more_storage_combo:
            for idx in range(1, self._more_storage_combo.count()):
                if self._more_storage_combo.itemData(idx) == default_target:
                    self._more_storage_combo.setCurrentIndex(idx)
                    break

    def get_selected_storage(self) -> str:
        """Returns the selected storage destination path, or None."""
        return self.selected_storage_path
