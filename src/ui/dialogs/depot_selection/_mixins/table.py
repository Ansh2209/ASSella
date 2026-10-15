"""Rendering of the depot table: row population, row labels and sizes,
column headers, the hidden-depot expander and the hidden-depot filter."""

import logging
import re

from PyQt6.QtCore import Qt

from PyQt6.QtGui import QColor, QPalette

from PyQt6.QtWidgets import QTableWidgetItem

from ui.dialogs.depot_selection.items import (
    ConfigTableWidgetItem,
    NumericTableWidgetItem,
)
from ui.dialogs.depot_selection.rules import (
    _depot_is_android,
    _depot_is_macos,
    format_size,
    get_smart_default_depots,
)

logger = logging.getLogger(__name__)


class DepotTableMixin:
    """Fills and re-labels the depot table, and owns the hidden-depot expander."""

    def _on_show_hidden_toggled(self, checked: bool):
        self._show_hidden_depots = checked
        if hasattr(self, "_settings") and self._settings:
            self._settings.setValue("show_hidden_depots_in_selector", checked)
        self.selected_depots = self.get_selected_depots()
        self._populate_table()

    def _has_native_linux_depots(self) -> bool:
        """Check if any depot explicitly targets Linux."""
        all_depot_dicts = []
        if isinstance(self.depots, dict):
            all_depot_dicts.extend(self.depots.values())
        if hasattr(self, "missing_depots_info") and isinstance(self.missing_depots_info, dict):
            all_depot_dicts.extend(self.missing_depots_info.values())
        for d_data in all_depot_dicts:
            if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
                continue
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or d_data.get("name") or "").lower()
            is_lin = "linux" in oslist or "[linux]" in desc
            is_win = "windows" in oslist or "win" in oslist or "[windows]" in desc or ".exe" in desc
            if is_lin and not is_win:
                return True
        return False

    def _build_config_text(self, d_id, d_data, is_first=False) -> str:
        original_desc = d_data.get("desc") or d_data.get("name") or ""
        for p in ("[Checking]", "[Missing]", "[Recovered]", "[No Key]", "[Missing from Hubcap]", "[Unavailable on Hubcap (404)]"):
            original_desc = original_desc.replace(p, "").strip()
        original_desc = re.sub(
            r"\s*-\s*Depot\s*" + re.escape(str(d_id)),
            "",
            original_desc,
            flags=re.IGNORECASE,
        )
        tags = ""
        base_desc = original_desc.strip()
        tags_match = re.match(r"^((?:\[.*?]\s*)*)(.*)", original_desc)
        if tags_match:
            tags = tags_match.group(1).strip()
            base_desc = tags_match.group(2).strip()

        is_generic_fallback = not base_desc or bool(
            re.fullmatch(r"Depot \d+", base_desc, re.IGNORECASE)
        )

        if not is_generic_fallback:
            final_desc = base_desc
        else:
            d_name = str(d_data.get("name") or "").strip()
            # Ignore d_name if it only contains platform/arch tokens
            clean_name = re.sub(r"\b(windows|linux|macos|64-bit|32-bit|x64|x86)\b", "", d_name, flags=re.IGNORECASE).strip()
            if clean_name and not re.match(r"^[,\s\-_/]+$", clean_name):
                final_desc = d_name
            else:
                final_desc = f"Depot {d_id}"

        if not d_data.get("osarch"):
            try:
                from managers.db_manager import DatabaseManager
                e_map = DatabaseManager().get_depot_enrichments(str(self.app_id))
                if str(d_id) in e_map and e_map[str(d_id)].get("osarch"):
                    d_data["osarch"] = e_map[str(d_id)]["osarch"]
            except Exception:
                pass

        from utils.depot_tag_helpers import format_depot_row_label, get_os_icon
        os_icon = get_os_icon(d_data.get("oslist"))
        cfg_text = format_depot_row_label(
            str(d_id),
            d_data,
            final_desc,
            has_os_icon=bool(os_icon),
            show_os_badge=False,
        )
        return cfg_text

    def _populate_table(self):
        self.table_widget.setSortingEnabled(False)
        self.table_widget.clearContents()

        def get_sort_key(depot_item):
            _depot_id, data = depot_item

            os_val = data.get("oslist")
            if os_val is None:
                os_str = ""
            else:
                os_str = os_val.lower()

            has_win = "windows" in os_str or "win" in os_str
            has_lin = "linux" in os_str
            has_mac = "macosx" in os_str or "macos" in os_str or "apple" in os_str
            has_and = "android" in os_str
            plat_count = sum([bool(has_win), bool(has_lin), bool(has_mac), bool(has_and)])
            is_multi = plat_count >= 2 or "all" in os_str or "shared" in os_str

            # Common / multi-OS depots get priority 0 (top of list)
            if is_multi or os_val is None or not os_str:
                os_priority = 0
            elif has_win and not has_lin:
                os_priority = 1
            elif has_lin and not has_win:
                os_priority = 2
            elif has_mac:
                os_priority = 5
            else:
                os_priority = 4

            desc_str = data.get("desc", "").lower()
            lang_val = data.get("language")

            lang_priority = 3
            lang_sort_key = lang_val.lower() if lang_val else "zzzz"

            is_no_language = (
                lang_val is None
                and "english" not in desc_str
                and "japanese" not in desc_str
            )

            if "english" in desc_str:
                lang_priority = 1
                lang_sort_key = lang_val.lower() if lang_val else "english"
            elif is_no_language:
                lang_priority = 1
                lang_sort_key = "english"
            elif "japanese" in desc_str:
                lang_priority = 2
                lang_sort_key = "japanese"

            depot_num = int(_depot_id) if str(_depot_id).isdigit() else 999999999
            final_key = (os_priority, lang_priority, lang_sort_key, depot_num)
            return final_key

        sorted_depots = sorted(self.depots.items(), key=get_sort_key)

        # Separate depots into active (displayed) vs hidden (expandable)
        active_depots = []
        hidden_depots = []

        for depot_id, depot_data in sorted_depots:
            is_hidden = False
            if self._hide_macos and _depot_is_macos(depot_data):
                is_hidden = True
            elif self._hide_android and _depot_is_android(depot_data):
                is_hidden = True
            elif getattr(self, "_filter_soundtracks", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "soundtrack" in desc_l or "soundtrack" in name_l or "[ost]" in desc_l or " ost" in desc_l:
                    is_hidden = True
            if not is_hidden and getattr(self, "_hide_artbooks", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "artbook" in desc_l or "artbook" in name_l or "wallpaper" in desc_l or "extra content" in desc_l:
                    is_hidden = True
            if not is_hidden and getattr(self, "_hide_demos", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "demo" in desc_l or "trial" in desc_l or "playtest" in desc_l:
                    is_hidden = True
            if not is_hidden and getattr(self, "_hide_tools", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "dedicated server" in desc_l or "editor" in desc_l or "sdk" in desc_l or " tool" in desc_l:
                    is_hidden = True

            if is_hidden:
                hidden_depots.append((depot_id, depot_data))
            else:
                active_depots.append((depot_id, depot_data))

        # Fallback if all depots were filtered out
        if not active_depots and hidden_depots:
            active_depots = hidden_depots
            hidden_depots = []

        # Determine initial selection: default to highest Complete Edition for target OS
        if self.selected_depots is not None:
            pre_selected_set = set(str(d) for d in self.selected_depots)
        else:
            try:
                from utils.edition_helpers import (
                    get_available_platforms,
                    get_default_platform,
                    resolve_multi_platform_editions,
                )
                self._available_platforms = get_available_platforms(self.depots)
                self._default_platform = get_default_platform(self.depots)
                self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

                default_eds = self._multi_platform_editions.get(self._default_platform, [])
                highest_ed = default_eds[0] if default_eds else None
                if highest_ed and highest_ed.get("depot_ids"):
                    pre_selected_set = set(highest_ed["depot_ids"])
                    if hasattr(self, "edition_button"):
                        short = highest_ed.get("short_name", "Complete")
                        if len(self._available_platforms) > 1:
                            plat_prefix = "Linux: " if self._default_platform == "linux" else "Win: "
                            self.edition_button.setText(f"{plat_prefix}{short} ▾")
                        else:
                            self.edition_button.setText(f"{short} ▾")
                else:
                    pre_selected_set = set(get_smart_default_depots(self.depots, target_platform=self._default_platform))
            except Exception as _e:
                logger.debug(f"[DepotSelection] Complete edition initial lookup failed: {_e}")
                pre_selected_set = set(get_smart_default_depots(self.depots, target_platform="linux"))

        include_hidden = getattr(self, "_show_hidden_depots", False) and bool(hidden_depots)
        total_rows = len(active_depots) + len(self.missing_hubcap_depots)
        if include_hidden:
            total_rows += 1 + len(hidden_depots)
        self.table_widget.setRowCount(total_rows)

        row_idx = 0
        is_first_depot = True

        # 1. Tier 0: Populate Active Depots
        for depot_id, depot_data in active_depots:
            config_text = self._build_config_text(depot_id, depot_data, is_first=is_first_depot)
            is_first_depot = False

            size_str = ""
            if not depot_data.get("size") and isinstance(depot_data.get("manifests"), dict):
                manifests = depot_data["manifests"]
                b_entry = manifests.get(self.branch) or manifests.get("public")
                if isinstance(b_entry, dict):
                    raw_fb = b_entry.get("size") or b_entry.get("download")
                    if raw_fb:
                        depot_data["size"] = raw_fb

            if depot_data.get("size"):
                try:
                    size_bytes = int(depot_data["size"])
                    size_str = format_size(size_bytes)
                except (ValueError, TypeError):
                    size_str = "0.00 B"
            elif depot_data.get("size_str"):
                size_str = depot_data["size_str"]

            id_val = int(depot_id) if str(depot_id).isdigit() else 0
            id_item = NumericTableWidgetItem(str(depot_id), id_val, tier=0)
            id_item.setData(Qt.ItemDataRole.UserRole, str(depot_id))
            id_item.setData(Qt.ItemDataRole.UserRole + 1, str(depot_id))
            id_item.setData(Qt.ItemDataRole.UserRole + 2, "normal")

            is_checked = True if self.is_single_depot else (str(depot_id) in pre_selected_set)
            id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)

            if str(depot_id) in getattr(self, "refetched_depots", []):
                config_text = f"[Recovered]  {config_text}"
                try:
                    from managers.db_manager import DatabaseManager
                    DatabaseManager().clear_missing_hubcap_depot(str(self.app_id), str(depot_id))
                except Exception:
                    pass

            config_item = ConfigTableWidgetItem(config_text, tier=0)
            config_item.setFlags(config_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            active_tint = QColor(self.accent_color) if hasattr(self, "accent_color") and self.accent_color else self.palette().color(QPalette.ColorRole.Text)
            from utils.depot_tag_helpers import get_os_icon
            os_ic = get_os_icon(depot_data.get("oslist"), tint_color=active_tint)
            if os_ic:
                config_item.setIcon(os_ic)

            raw_size = int(depot_data.get("size") or 0)
            size_item = NumericTableWidgetItem(size_str, raw_size, tier=0)
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self.table_widget.setItem(row_idx, 0, id_item)
            self.table_widget.setItem(row_idx, 1, config_item)
            self.table_widget.setItem(row_idx, 2, size_item)
            row_idx += 1

        # 2. Tier 1: Populate Missing Depots from Hubcap (Darker Greyscale, Completely Untoggleable & Unclickable)
        darker_grey = QColor(135, 135, 135)
        for did in self.missing_hubcap_depots:
            minfo = self.missing_depots_info.get(did, {})
            mname = minfo.get("name")
            mraw_size = int(minfo.get("size") or 0)
            if not mraw_size and isinstance(minfo.get("manifests"), dict):
                manifests = minfo["manifests"]
                b_entry = manifests.get(self.branch) or manifests.get("public")
                if isinstance(b_entry, dict):
                    mraw_size = int(b_entry.get("size") or b_entry.get("download") or 0)
                if not mraw_size:
                    for m_val in manifests.values():
                        if isinstance(m_val, dict) and (m_val.get("size") or m_val.get("download")):
                            mraw_size = int(m_val.get("size") or m_val.get("download") or 0)
                            break
            msize_str = format_size(mraw_size) if mraw_size > 0 else "0 B"
            hubcap_status = minfo.get("hubcap_status")
            if getattr(self, "_is_checking_missing_contents", False):
                tag = "[Checking]"
            elif hubcap_status == "missing_key":
                tag = "[No Key]"
            else:
                tag = "[Missing]"

            base_mcfg = self._build_config_text(did, minfo)
            mconfig_text = f"{tag}  {base_mcfg}"

            mid_val = int(did) if str(did).isdigit() else 0
            mid_item = NumericTableWidgetItem(str(did), mid_val, tier=1)
            mid_item.setData(Qt.ItemDataRole.UserRole, str(did))
            mid_item.setData(Qt.ItemDataRole.UserRole + 1, str(did))
            mid_item.setData(Qt.ItemDataRole.UserRole + 2, "missing")
            mid_item.setCheckState(Qt.CheckState.Unchecked)
            mid_item.setFlags(Qt.ItemFlag.NoItemFlags)
            mid_item.setForeground(darker_grey)

            mconfig_item = ConfigTableWidgetItem(mconfig_text, tier=1)
            from utils.depot_tag_helpers import get_os_icon
            m_ic = get_os_icon(minfo.get("oslist"), tint_color=darker_grey)
            if m_ic:
                mconfig_item.setIcon(m_ic)
            mconfig_item.setFlags(Qt.ItemFlag.NoItemFlags)
            mconfig_item.setForeground(darker_grey)
            cfg_font = mconfig_item.font()
            cfg_font.setItalic(True)
            mconfig_item.setFont(cfg_font)

            if hubcap_status == "missing_key":
                tip = f"Depot {did} has a manifest but no AES decryption key available. It cannot be downloaded."
                mid_item.setToolTip(tip)
                mconfig_item.setToolTip(tip)

            msize_item = NumericTableWidgetItem(msize_str, mraw_size, tier=1)
            msize_item.setFlags(Qt.ItemFlag.NoItemFlags)
            msize_item.setForeground(darker_grey)
            sz_font = msize_item.font()
            sz_font.setItalic(True)
            msize_item.setFont(sz_font)

            self.table_widget.setItem(row_idx, 0, mid_item)
            self.table_widget.setItem(row_idx, 1, mconfig_item)
            self.table_widget.setItem(row_idx, 2, msize_item)
            row_idx += 1

        # 3. Tier 2 & 3: Populate Hidden Depots if enabled (Expander + Hidden Rows)
        brighter_grey = QColor(195, 195, 195)
        if include_hidden:
            is_expanded = getattr(self, "_hidden_depots_expanded", False)
            exp_id = NumericTableWidgetItem("▴" if is_expanded else "▾", 0, tier=2)
            exp_id.setData(Qt.ItemDataRole.UserRole, "__expander__")
            exp_id.setData(Qt.ItemDataRole.UserRole + 2, "expander")
            exp_id.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            exp_id.setForeground(brighter_grey)
            font_id = exp_id.font()
            font_id.setBold(True)
            exp_id.setFont(font_id)

            exp_cfg = ConfigTableWidgetItem(f"Hidden Depots ({len(hidden_depots)})", tier=2)
            exp_cfg.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            exp_cfg.setForeground(brighter_grey)
            font_cfg = exp_cfg.font()
            font_cfg.setBold(True)
            exp_cfg.setFont(font_cfg)

            action_text = "[Click to collapse]" if is_expanded else "[Click to expand]"
            exp_size = NumericTableWidgetItem(action_text, 0, tier=2)
            exp_size.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            exp_size.setForeground(brighter_grey)
            font_size = exp_size.font()
            font_size.setItalic(True)
            exp_size.setFont(font_size)

            self.table_widget.setItem(row_idx, 0, exp_id)
            self.table_widget.setItem(row_idx, 1, exp_cfg)
            self.table_widget.setItem(row_idx, 2, exp_size)
            row_idx += 1

            # Hidden depot rows (Tier 3)
            for depot_id, depot_data in hidden_depots:
                hconfig_text = self._build_config_text(depot_id, depot_data, is_first=False)
                hsize_str = ""
                if depot_data.get("size"):
                    try:
                        hsize_bytes = int(depot_data["size"])
                        hsize_str = format_size(hsize_bytes)
                    except (ValueError, TypeError):
                        hsize_str = "0.00 B"
                elif depot_data.get("size_str"):
                    hsize_str = depot_data["size_str"]

                hid_val = int(depot_id) if str(depot_id).isdigit() else 0
                hid_item = NumericTableWidgetItem(str(depot_id), hid_val, tier=3)
                hid_item.setData(Qt.ItemDataRole.UserRole, str(depot_id))
                hid_item.setData(Qt.ItemDataRole.UserRole + 1, str(depot_id))
                hid_item.setData(Qt.ItemDataRole.UserRole + 2, "hidden_depot")
                is_h_checked = str(depot_id) in pre_selected_set
                hid_item.setCheckState(Qt.CheckState.Checked if is_h_checked else Qt.CheckState.Unchecked)
                hid_item.setFlags(hid_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)
                hid_item.setForeground(brighter_grey)

                hconfig_item = ConfigTableWidgetItem(hconfig_text, tier=3)
                from utils.depot_tag_helpers import get_os_icon
                h_ic = get_os_icon(depot_data.get("oslist"), tint_color=brighter_grey)
                if h_ic:
                    hconfig_item.setIcon(h_ic)
                hconfig_item.setFlags(hconfig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                hconfig_item.setForeground(brighter_grey)

                hraw_size = int(depot_data.get("size") or 0)
                hsize_item = NumericTableWidgetItem(hsize_str, hraw_size, tier=3)
                hsize_item.setFlags(hsize_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                hsize_item.setForeground(brighter_grey)

                self.table_widget.setItem(row_idx, 0, hid_item)
                self.table_widget.setItem(row_idx, 1, hconfig_item)
                self.table_widget.setItem(row_idx, 2, hsize_item)
                self.table_widget.setRowHidden(row_idx, not is_expanded)
                row_idx += 1

        self.table_widget.setRowCount(row_idx)
        # Row order comes from get_sort_key() above. Click-to-sort is
        # intentionally NOT enabled: reordering rows under the cursor fought
        # the header select-all checkbox, so a tick-all looked like it had
        # missed depots.
        self.table_widget.setSortingEnabled(False)
        self._update_table_headers()

    def _update_table_headers(self):
        """Updates horizontal table header labels with selected/total depot counts and selected size."""
        if not hasattr(self, "table_widget") or not self.table_widget:
            return

        total_count = 0
        sel_count = 0
        total_bytes = 0

        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role == "expander":
                continue

            total_count += 1
            if id_item.checkState() == Qt.CheckState.Checked:
                sel_count += 1
                size_item = self.table_widget.item(r, 2)
                if size_item and hasattr(size_item, "sort_value"):
                    total_bytes += int(size_item.sort_value or 0)

        if total_count > 0 and sel_count == total_count:
            hdr_state = Qt.CheckState.Checked
        else:
            hdr_state = Qt.CheckState.Unchecked

        if hasattr(self, "header_view") and self.header_view:
            self.header_view.set_check_state_and_count(hdr_state, f"({sel_count}/{total_count})")

        id_header = ""
        config_header = "Configuration"
        size_header = f"Size ({format_size(total_bytes)})"

        for col, text in enumerate([id_header, config_header, size_header]):
            h_item = self.table_widget.horizontalHeaderItem(col)
            if not h_item:
                h_item = QTableWidgetItem(text)
                self.table_widget.setHorizontalHeaderItem(col, h_item)
            else:
                h_item.setText(text)
            if col == 0:
                h_item.setToolTip("Click to toggle Select All / Select None")
            h_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.table_widget.resizeColumnToContents(2)

        if hasattr(self, "edition_button"):
            if not hasattr(self, "_multi_platform_editions") or not self._multi_platform_editions:
                from utils.edition_helpers import (
                    get_available_platforms,
                    resolve_multi_platform_editions,
                )
                self._available_platforms = get_available_platforms(self.depots)
                self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

            from utils.edition_helpers import match_selection_to_edition
            current_sel = set(self.get_selected_depots())
            matched = match_selection_to_edition(current_sel, self._multi_platform_editions)
            if matched:
                plat = matched.get("platform", "")
                full_name = matched.get("name") or matched.get("short_name", "Edition")
                clean_name = re.sub(r"\s*\(All DLCs\)", "", full_name, flags=re.IGNORECASE).strip()
                clean_name = re.sub(r"\s*\(Base Game Only\)", "", clean_name, flags=re.IGNORECASE).strip()

                if len(getattr(self, "_available_platforms", [])) > 1:
                    plat_prefix = "Linux: " if plat == "linux" else "Win: "
                    btn_text = f"{plat_prefix}{clean_name} ▾"
                else:
                    btn_text = f"{clean_name} ▾"

                if len(btn_text) > 42:
                    display_text = f"{btn_text[:38].rstrip()}... ▾"
                else:
                    display_text = btn_text

                self.edition_button.setText(display_text)
                self.edition_button.setToolTip(f"{plat.capitalize() + ': ' if plat else ''}{clean_name}")
            else:
                self.edition_button.setText("Custom ▾")
                self.edition_button.setToolTip("Custom depot selection")

    def _on_header_section_clicked(self, logical_index: int):
        if logical_index == 0:
            total = 0
            checked = 0
            for r in range(self.table_widget.rowCount()):
                item = self.table_widget.item(r, 0)
                if item and item.data(Qt.ItemDataRole.UserRole + 2) != "expander":
                    total += 1
                    if item.checkState() == Qt.CheckState.Checked:
                        checked += 1
            if checked == total and total > 0:
                self._toggle_all_checkboxes(check=False)
            else:
                self._toggle_all_checkboxes(check=True)
