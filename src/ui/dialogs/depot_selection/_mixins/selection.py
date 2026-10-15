"""Everything that ticks or unticks rows: cell clicks, select-all, smart
select, the platform and edition presets, DLC-only mode and its
auto-selection rules."""

import logging
import re

from PyQt6.QtCore import Qt, QPoint

from PyQt6.QtWidgets import (
    QApplication,
    QPushButton,
)

try:
    from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning
except ImportError:
    show_dlc_mode_warning = None

from ui.dialogs.depot_selection.rules import get_smart_default_depots

logger = logging.getLogger(__name__)


class SelectionMixin:
    """Everything that ticks or unticks rows, including the presets and DLC-only mode."""

    def _apply_dlc_auto_selection(self):
        """
        Auto-selects DLC depots when DLC-only mode is active.
        Rules:
        1. Non-DLC (base game / redist) depots are unchecked.
        2. If DLC depots exist for both Windows and Linux, do NOT auto-select (leave unchecked).
        3. If total DLC count >= 64, auto-select ALL DLC depots (bypass the >1KB condition).
        4. If total DLC count < 64, auto-select DLC depots with size > 1024 bytes (skip stubs <= 1KB).
        """
        if not getattr(self, "_dlc_only_mode", False):
            return
        if getattr(self, "_has_saved_selection", False) or getattr(self, "_user_interacted", False):
            return

        from utils.dlc_helpers import is_base_game_main_depot

        dlc_depot_ids = set()
        has_win_dlc = False
        has_linux_dlc = False

        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            d_data = self.depots.get(depot_id) or self.depots.get(int(depot_id) if depot_id.isdigit() else 0) or {}

            desc = str(d_data.get("desc") or d_data.get("name") or "")
            cfg_item = self.table_widget.item(i, 1)
            cfg_text = cfg_item.text() if cfg_item else ""

            is_dlc = (
                d_data.get("is_dlc", False)
                or "[dlc]" in desc.lower()
                or "[dlc]" in cfg_text.lower()
                or bool(re.search(r"\bDLC\s+\d+", desc, re.IGNORECASE))
                or bool(re.search(r"\bDLC\s+\d+", cfg_text, re.IGNORECASE))
                or bool(d_data.get("dlcappid"))
            )

            if is_base_game_main_depot(depot_id, desc, str(self.app_id)):
                is_dlc = False

            if is_dlc:
                dlc_depot_ids.add(depot_id)
                oslist = (d_data.get("oslist") or "").lower()
                if not oslist and "[windows" in cfg_text.lower():
                    oslist = "windows"
                elif not oslist and "[linux" in cfg_text.lower():
                    oslist = "linux"

                if "windows" in oslist:
                    has_win_dlc = True
                if "linux" in oslist:
                    has_linux_dlc = True

        os_conflict = (has_win_dlc and has_linux_dlc)
        if os_conflict:
            logger.info(
                f"[DepotSelection] App {self.app_id}: DLC depots detected for both Windows and Linux — "
                "bypassing auto-selection so user can pick target platform manually."
            )

        total_dlcs = len(dlc_depot_ids)
        bypass_size_limit = (total_dlcs >= 64)

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            d_data = self.depots.get(depot_id) or self.depots.get(int(depot_id) if depot_id.isdigit() else 0) or {}

            if depot_id in dlc_depot_ids:
                if os_conflict:
                    id_item.setCheckState(Qt.CheckState.Unchecked)
                elif bypass_size_limit:
                    id_item.setCheckState(Qt.CheckState.Checked)
                else:
                    raw_size = int(d_data.get("size") or 0)
                    if not raw_size:
                        size_item = self.table_widget.item(i, 2)
                        raw_size = getattr(size_item, "sort_value", 0) or 0

                    if 0 < raw_size <= 1024:
                        id_item.setCheckState(Qt.CheckState.Unchecked)
                    else:
                        id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)

        self.table_widget.blockSignals(False)
        self.anchor_row = -1
        self._update_table_headers()

    def on_depot_cell_clicked(self, row, col):
        id_item = self.table_widget.item(row, 0)
        if id_item is None:
            return

        role = id_item.data(Qt.ItemDataRole.UserRole + 2)
        if role == "missing":
            return

        if role == "expander":
            self._hidden_depots_expanded = not getattr(self, "_hidden_depots_expanded", False)
            arrow = "▴" if self._hidden_depots_expanded else "▾"
            action_text = "[Click to collapse]" if self._hidden_depots_expanded else "[Click to expand]"

            id_item.setText(arrow)
            size_item = self.table_widget.item(row, 2)
            if size_item:
                size_item.setText(action_text)

            self.table_widget.blockSignals(True)
            for r in range(self.table_widget.rowCount()):
                it = self.table_widget.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole + 2) == "hidden_depot":
                    self.table_widget.setRowHidden(r, not self._hidden_depots_expanded)
            self.table_widget.blockSignals(False)
            return

        modifiers = QApplication.keyboardModifiers()
        current_state = id_item.checkState()
        new_state = (
            Qt.CheckState.Unchecked
            if current_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

        # Toggle the checkbox in the first column
        id_item.setCheckState(new_state)
        self._user_interacted = True
        self._has_saved_selection = True

        if modifiers == Qt.KeyboardModifier.ShiftModifier:
            if self.anchor_row == -1:
                self.anchor_row = row
            else:
                anchor_item = self.table_widget.item(self.anchor_row, 0)
                target_state = anchor_item.checkState() if anchor_item else new_state

                start_row = min(self.anchor_row, row)
                end_row = max(self.anchor_row, row)

                self.table_widget.blockSignals(True)
                for r in range(start_row, end_row + 1):
                    r_item = self.table_widget.item(r, 0)
                    if r_item is not None:
                        r_role = r_item.data(Qt.ItemDataRole.UserRole + 2)
                        if r_role in ("normal", "hidden_depot"):
                            r_item.setCheckState(target_state)
                self.table_widget.blockSignals(False)
        else:
            self.anchor_row = row

        self._update_table_headers()

    def _toggle_all_checkboxes(self, check=True):
        self._user_interacted = True
        self._has_saved_selection = True
        state = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is not None:
                role = id_item.data(Qt.ItemDataRole.UserRole + 2)
                if role in ("missing", "expander"):
                    continue
                if self.table_widget.isRowHidden(i):
                    continue
                id_item.setCheckState(state)
        self.table_widget.blockSignals(False)

        self.anchor_row = -1
        self._update_table_headers()

    def _on_smart_select_clicked(self):
        """Overwrites user selection with the depots Steam normally installs from package info / appcache."""
        self._user_interacted = True
        self._has_saved_selection = True

        try:
            from core.steam_package_info import get_steam_recommended_depots
            rec_depots = get_steam_recommended_depots(self.app_id, self.depots, target_platform="linux")
        except Exception as err:
            logger.warning(f"[DepotSelection] Smart Select error: {err}")
            rec_depots = []

        if not rec_depots:
            rec_depots = get_smart_default_depots(self.depots, target_platform="linux")

        smart_depots = set(str(d) for d in rec_depots)

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in smart_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self.anchor_row = -1
        self._update_table_headers()

    def _select_platform(self, platform: str):
        """Smart select depots matching a platform (linux/windows), including shared depots and filtering out bonus media/32-bit."""
        self._user_interacted = True
        self._has_saved_selection = True
        smart_depots = set(get_smart_default_depots(self.depots, target_platform=platform))

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in smart_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self.anchor_row = -1
        self._update_table_headers()

    def _show_edition_menu(self):
        """Displays drop-up/down menu of available editions grouped by OS if multiple exist."""
        from PyQt6.QtWidgets import QMenu
        from utils.edition_helpers import (
            get_available_platforms,
            resolve_multi_platform_editions,
        )

        if not hasattr(self, "_available_platforms") or not self._available_platforms:
            self._available_platforms = get_available_platforms(self.depots)
            self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e24;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px;
                color: #ffffff;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)

        current_sel = set(self.get_selected_depots())

        if len(self._available_platforms) > 1:
            for plat in self._available_platforms:
                plat_label = "Linux (Native)" if plat == "linux" else "Windows (Proton)"
                sub_menu = menu.addMenu(plat_label)
                sub_menu.setStyleSheet(menu.styleSheet())
                eds = self._multi_platform_editions.get(plat, [])
                for ed in eds:
                    action = sub_menu.addAction(ed["name"])
                    action.setCheckable(True)
                    ed_depots = {str(d) for d in ed.get("depot_ids", set())}
                    if current_sel == ed_depots:
                        action.setChecked(True)
                    action.triggered.connect(lambda checked, target_ed=ed: self._apply_edition_selection(target_ed))
        else:
            plat = self._available_platforms[0] if self._available_platforms else "windows"
            eds = self._multi_platform_editions.get(plat, [])
            for ed in eds:
                action = menu.addAction(ed["name"])
                action.setCheckable(True)
                ed_depots = {str(d) for d in ed.get("depot_ids", set())}
                if current_sel == ed_depots:
                    action.setChecked(True)
                action.triggered.connect(lambda checked, target_ed=ed: self._apply_edition_selection(target_ed))

        menu.adjustSize()
        menu_height = menu.sizeHint().height()
        pos = self.edition_button.mapToGlobal(QPoint(0, -menu_height - 2))
        if pos.y() < 0:
            pos = self.edition_button.mapToGlobal(QPoint(0, self.edition_button.height() + 2))
        menu.exec(pos)

    def _apply_edition_selection(self, edition: dict):
        """Apply the chosen edition to the depot checkboxes."""
        self._user_interacted = True
        self._has_saved_selection = True
        full_name = edition.get("name") or edition.get("short_name", "Edition")
        clean_name = re.sub(r"\s*\(All DLCs\)", "", full_name, flags=re.IGNORECASE).strip()
        clean_name = re.sub(r"\s*\(Base Game Only\)", "", clean_name, flags=re.IGNORECASE).strip()
        plat = edition.get("platform", "")
        if getattr(self, "_available_platforms", None) and len(self._available_platforms) > 1:
            plat_prefix = "Linux: " if plat == "linux" else "Win: "
            btn_text = f"{plat_prefix}{clean_name} ▾"
        else:
            btn_text = f"{clean_name} ▾"

        if len(btn_text) > 42:
            display_text = f"{btn_text[:38].rstrip()}... ▾"
        else:
            display_text = btn_text

        if hasattr(self, "edition_button"):
            self.edition_button.setText(display_text)
            self.edition_button.setToolTip(f"{plat.capitalize() + ': ' if plat else ''}{clean_name}")
        target_depots = {str(d) for d in edition.get("depot_ids", set())}

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in target_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self.anchor_row = -1
        self._update_table_headers()

    def _select_baseline_depots(self):
        """Legacy helper for selecting standard/baseline edition."""
        if hasattr(self, "_available_editions"):
            for ed in self._available_editions:
                if ed.get("id") == "standard":
                    self._apply_edition_selection(ed)
                    return
        self._on_smart_select_clicked()

    def _refresh_dlc_only_style(self) -> None:
        """Update the DLC Only button style to reflect its on/off state."""
        if not self._dlc_only_btn.isEnabled():
            self._dlc_only_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.03) !important;
                    color: rgba(255, 255, 255, 0.25) !important;
                    border: 1px solid rgba(255, 255, 255, 0.06) !important;
                    border-radius: 4px;
                    padding: 4px 10px;
                }
            """)
            return

        active = self._dlc_only_btn.isChecked()
        from utils.color_utils import get_best_foreground_color
        if active:
            # Active state: Solid accent background with high-contrast text color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")
            self._dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.accent_color} !important;
                    color: {text_hex} !important;
                    border: 1px solid {self.accent_color} !important;
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-weight: bold;
                }}
            """)
        else:
            # Inverted / outline style: Transparent background, accent hover
            self._dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.6);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 4px 10px;
                }}
                QPushButton:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
            """)

    def _on_dlc_only_toggled(self) -> None:
        """Toggle DLC Only mode, show 3-second lockout warning, and auto-select DLC depots."""
        new_state = self._dlc_only_btn.isChecked()
        if new_state:
            try:
                warn_fn = show_dlc_mode_warning
                if warn_fn is None:
                    try:
                        from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning as warn_fn
                    except ImportError:
                        warn_fn = None
                if warn_fn:
                    warn_fn(self)
            except Exception as e:
                logger.warning(f"DLC warning dialog error: {e}")

        self._dlc_only_mode = new_state
        self._refresh_dlc_only_style()
        if self._settings:
            self._settings.setValue(f"dlc_only_mode/{self.app_id}", self._dlc_only_mode)

        if self._dlc_only_mode:
            self._start_enrichment_async(force=True)
            if not getattr(self, "_has_saved_selection", False) and not getattr(self, "_user_interacted", False):
                self._apply_dlc_auto_selection()
        else:
            self._select_platform("windows")
