"""Everything that ticks or unticks rows: cell clicks, select-all, smart
select, the platform and edition presets, DLC-only mode and its
auto-selection rules."""

import logging

from PyQt6.QtCore import Qt, QPoint

from PyQt6.QtWidgets import (
    QApplication,
)

try:
    from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning
except ImportError:
    show_dlc_mode_warning = None

from ui.dialogs.depot_selection import selection_policy

logger = logging.getLogger(__name__)


class SelectionMixin:
    """Everything that ticks or unticks rows, including the presets and DLC-only mode."""

    def _apply_dlc_auto_selection(self):
        """
        Auto-selects DLC depots when DLC-only mode is active.

        The rules themselves (which depots count as DLC, the Windows/Linux
        conflict, the >= 64 bypass and the 1 KB stub filter) live in
        selection_policy.dlc_auto_selection; this gathers what they need.
        """
        if not getattr(self, "_dlc_only_mode", False):
            return
        if getattr(self, "_has_saved_selection", False) or getattr(self, "_user_interacted", False):
            return

        facts = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            data = selection_policy.depot_data(self.depots, depot_id)
            desc = str(data.get("desc") or data.get("name") or "")
            cfg_item = self.table_widget.item(i, 1)
            cfg_text = cfg_item.text() if cfg_item else ""
            size = int(data.get("size") or 0)
            if not size:
                size_item = self.table_widget.item(i, 2)
                size = getattr(size_item, "sort_value", 0) or 0
            facts.append(selection_policy.DepotRowFacts(depot_id, desc, cfg_text, size, data))

        selection = selection_policy.dlc_auto_selection(facts, self.app_id)
        self._apply_depot_checks(selection.ids)

    def _apply_depot_checks(self, wanted) -> None:
        """Tick exactly these depot ids, and untick every other selectable row."""
        wanted = {str(d) for d in wanted}
        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in wanted:
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
        self._apply_depot_checks(selection_policy.recommended_depots(self.app_id, self.depots, platform="linux"))

    def _select_platform(self, platform: str):
        """Smart select depots matching a platform (linux/windows), including shared depots and filtering out bonus media/32-bit."""
        self._user_interacted = True
        self._has_saved_selection = True
        self._apply_depot_checks(
            selection_policy.recommended_depots(self.app_id, self.depots, platform=platform, use_steam=False)
        )

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

        groups = selection_policy.edition_menu(
            self._available_platforms,
            self._multi_platform_editions,
            self.get_selected_depots(),
        )
        for group in groups:
            sub_menu = menu.addMenu(group.label) if group.label else menu
            if group.label:
                sub_menu.setStyleSheet(menu.styleSheet())
            for choice in group.choices:
                action = sub_menu.addAction(choice.name)
                action.setCheckable(True)
                if choice.checked:
                    action.setChecked(True)
                action.triggered.connect(lambda checked, target_ed=choice.edition: self._apply_edition_selection(target_ed))

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

        multi_platform = bool(getattr(self, "_available_platforms", None)) and len(self._available_platforms) > 1
        if hasattr(self, "edition_button"):
            self.edition_button.setText(
                selection_policy.edition_button_text(edition, multi_platform=multi_platform)
            )
            self.edition_button.setToolTip(selection_policy.edition_button_tooltip(edition))

        self._apply_depot_checks({str(d) for d in edition.get("depot_ids", set())})

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
