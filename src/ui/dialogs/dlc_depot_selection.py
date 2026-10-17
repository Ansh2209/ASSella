"""
dlc_depot_selection.py - ASSella dedicated DLC selection dialog for owned Steam games.
Features a compact split-panel layout, OS-aware depot tabs (Linux / Windows),
Steam-recommended DLC auto-selection (excluding base game depots),
clean depot row labels with OS icons, and zero emojis.
"""

import logging
import re
from typing import Dict, List, Optional, Any, Set
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QPixmap, QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QMenu,
    QWidget,
    QButtonGroup,
)

from ui.dialog_titlebar import DialogTitleBar

from ui.dialogs.depot_selection.rules import (
    _depot_matches_platform,
    _depot_is_macos,
    _depot_is_android,
    is_bonus_or_media_depot,
    get_smart_default_depots,
    format_size,
)
from utils.depot_tag_helpers import format_depot_row_label, get_os_icon
from utils.image_fetcher import ImageFetcher
from utils.settings import get_settings
from utils.dlc_helpers import is_dlc_depot, is_base_game_main_depot
from utils.edition_helpers import (
    get_available_platforms,
    get_default_platform,
    fetch_store_packages,
    clean_package_name,
)

logger = logging.getLogger(__name__)


class NumericTableWidgetItem(QTableWidgetItem):
    """Sorts table rows numerically using sort_val."""

    def __init__(self, text: str, sort_val: int = 0):
        super().__init__(text)
        self.sort_val = sort_val

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_val < other.sort_val
        return super().__lt__(other)


class DlcDepotSelectionDialog(QDialog):
    """
    Dedicated compact split-panel dialog for DLC-only depot selection on owned Steam games.
    - Left: Banner, Game Name, AppID, Selection Summary, and locked Steam Install Path at bottom.
    - Right: OS tabs (Linux / Windows), Edition selector (Steam Recommended / Real Store packages),
             compact table with Depot ID, DLC Name (with OS icon and [DLC] tag), and Size.
    """

    def __init__(
        self,
        app_id,
        game_name,
        depots,
        header_url=None,
        parent=None,
        selected_depots=None,
        show_storage=False,
        is_single_depot=False,
        missing_hubcap_depots=None,
        missing_depots_info=None,
        library_path=None,
        refetched_depots=None,
        branch="public",
        branches=None,
        current_build_id="",
        lock_storage=True,
        dlc_only_mode=True,
        lock_dlc_only=True,
        **kwargs,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)

        self.app_id = str(app_id).strip()
        self.game_name = str(game_name or f"App {app_id}")
        self.depots = dict(depots or {})
        self.header_url = header_url
        self.library_path = str(library_path or "")
        self.current_build_id = str(current_build_id or "").strip()
        self.missing_hubcap_depots = {str(d).strip() for d in (missing_hubcap_depots or []) if str(d).strip()}
        self.missing_depots_info = dict(missing_depots_info or {})
        self.initial_selected_depots = {str(d).strip() for d in (selected_depots or []) if str(d).strip()}

        self._drag_pos: Optional[QPoint] = None
        self._user_checked_depots: Set[str] = set(self.initial_selected_depots)
        self._store_packages: List[Dict[str, Any]] = []

        # Platform detection
        self._available_platforms = get_available_platforms(self.depots)
        self._active_platform = get_default_platform(self.depots)

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self.setMinimumSize(780, 520)
        self.resize(820, 530)

        self._fetch_store_packages_safe()
        self._build_ui()
        self._load_header_image()

        # If user didn't supply pre-selected depots, compute smart recommended DLCs for default OS
        if not self._user_checked_depots:
            self._apply_smart_recommended_for_platform(self._active_platform)
        else:
            self._repopulate_table_for_platform(self._active_platform)
            self._update_summary()

    def _fetch_store_packages_safe(self):
        """Query real store packages if any exist."""
        try:
            raw_pkgs = fetch_store_packages(self.app_id)
            for p in raw_pkgs:
                name = clean_package_name(p.get("name", ""))
                if name and not any(k in name.lower() for k in ("standard", "base game")):
                    self._store_packages.append(p)
        except Exception as e:
            logger.debug(f"[DlcDepotSelection] Could not query store packages: {e}")
            self._store_packages = []

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("dlcContainer")
        self.container.setStyleSheet(f"""
            QFrame#dlcContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
        """)
        root.addWidget(self.container)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 12)
        container_lay.setSpacing(8)

        # Frameless title bar
        self.title_bar = DialogTitleBar(
            self,
            title="Select DLCs to Download",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # Split content panel
        content_lay = QHBoxLayout()
        content_lay.setContentsMargins(14, 0, 14, 0)
        content_lay.setSpacing(14)

        # Left Panel (Thumbnail, Game Name, AppID, Summary, Locked Install Path at bottom)
        left_panel = self._build_left_panel()
        content_lay.addWidget(left_panel, 0)

        # Right Panel (OS tabs, Edition dropdown, Depots table)
        right_panel = self._build_right_panel()
        content_lay.addWidget(right_panel, 1)

        container_lay.addLayout(content_lay, 1)

        # Bottom Action Bar
        bottom_bar = self._build_bottom_bar()
        container_lay.addLayout(bottom_bar)

    def _build_left_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFixedWidth(260)
        panel.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # 1. Game banner image
        self.header_img_lbl = QLabel(panel)
        self.header_img_lbl.setFixedHeight(105)
        self.header_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_img_lbl.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            color: rgba(255, 255, 255, 0.6);
            font-size: 8.5pt;
        """)
        self.header_img_lbl.setText("Loading banner...")
        lay.addWidget(self.header_img_lbl)

        # 2. Game title
        self.title_lbl = QLabel(self.game_name, panel)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet("font-size: 10pt; font-weight: bold; color: #ffffff; border: none; background: transparent;")
        lay.addWidget(self.title_lbl)

        # 3. AppID only (clean, no "owned" badge)
        appid_lbl = QLabel(f"AppID: {self.app_id}", panel)
        appid_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7); border: none; background: transparent;")
        lay.addWidget(appid_lbl)

        # 4. Selection Summary
        self.summary_lbl = QLabel("Selected: 0 DLCs (0 B)", panel)
        self.summary_lbl.setStyleSheet(f"font-size: 8.5pt; font-weight: 600; color: {self.accent_color}; border: none; background: transparent;")
        lay.addWidget(self.summary_lbl)

        # Stretch moves install path to the very bottom
        lay.addStretch(1)

        # 5. Locked Steam Install Location at the very bottom
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("border: none; border-top: 1px solid rgba(255, 255, 255, 0.06);")
        lay.addWidget(div)

        path_title_lbl = QLabel("Install Path [Steam]:", panel)
        path_title_lbl.setStyleSheet("font-size: 7.5pt; font-weight: bold; color: rgba(255, 255, 255, 0.5); text-transform: uppercase; border: none; background: transparent;")
        lay.addWidget(path_title_lbl)

        display_path = self.library_path or "Detected from Steam library"
        self.path_lbl = QLabel(display_path, panel)
        self.path_lbl.setToolTip(self.library_path or "Steam default directory")
        self.path_lbl.setWordWrap(True)
        self.path_lbl.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.25);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 5px;
            color: rgba(255, 255, 255, 0.75);
            font-size: 8pt;
            padding: 5px 8px;
        """)
        lay.addWidget(self.path_lbl)

        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.015);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Header: OS Tabs & Edition Selector (Replacing "Available DLC Depots")
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        # OS Platform Tabs (Segmented)
        self.os_btn_group = QButtonGroup(self)
        self.os_tab_buttons: Dict[str, QPushButton] = {}

        has_linux = "linux" in self._available_platforms
        has_win = "windows" in self._available_platforms

        if has_linux and has_win:
            btn_linux = self._create_os_tab_btn("Linux (Native)", "linux")
            btn_win = self._create_os_tab_btn("Windows (Proton)", "windows")
            self.os_btn_group.addButton(btn_linux)
            self.os_btn_group.addButton(btn_win)
            header_row.addWidget(btn_linux)
            header_row.addWidget(btn_win)
            self.os_tab_buttons["linux"] = btn_linux
            self.os_tab_buttons["windows"] = btn_win
        elif has_linux:
            btn_linux = self._create_os_tab_btn("Linux (Native)", "linux")
            self.os_btn_group.addButton(btn_linux)
            header_row.addWidget(btn_linux)
            self.os_tab_buttons["linux"] = btn_linux
        else:
            btn_win = self._create_os_tab_btn("Windows (Proton)", "windows")
            self.os_btn_group.addButton(btn_win)
            header_row.addWidget(btn_win)
            self.os_tab_buttons["windows"] = btn_win

        # Edition Preset Dropdown Button
        self.edition_btn = QPushButton("Steam Recommended ▾", panel)
        self.edition_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edition_btn.setFixedHeight(28)
        self.edition_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #ffffff;
                font-size: 8pt;
                font-weight: 600;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.10);
                border-color: {self.accent_color};
            }}
        """)
        self.edition_btn.clicked.connect(self._show_edition_menu)
        header_row.addWidget(self.edition_btn)

        header_row.addStretch()

        # Select All / Deselect All Button
        self.select_all_btn = QPushButton("Select All", panel)
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.setFixedHeight(26)
        self.select_all_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                color: #e5e7eb;
                font-size: 8pt;
                padding: 0 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)
        self.select_all_btn.clicked.connect(self._toggle_select_all)
        header_row.addWidget(self.select_all_btn)

        lay.addLayout(header_row)

        # DLC Depots Table (3 columns: Depot ID, DLC Name, Size)
        self.table = QTableWidget(panel)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Depot ID", "DLC Name", "Size"])
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        # Remove row numbers
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Compact Depot ID column
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 85)

        # DLC Name column (stretches to fill)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Size column
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 85)

        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: transparent;
                border: none;
                gridline-color: transparent;
                outline: 0;
                color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 4px 6px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            }}
            QTableWidget::item:hover {{
                background-color: rgba(255, 255, 255, 0.05);
            }}
            QTableWidget::item:selected {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.7);
                padding: 4px 6px;
                border: none;
                font-size: 8pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            QTableWidget::indicator {{
                width: 14px;
                height: 14px;
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.35);
                border-radius: 3px;
            }}
            QTableWidget::indicator:checked {{
                background-color: {self.accent_color};
                border: 1px solid {self.accent_color};
            }}
            QTableWidget::indicator:disabled {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
            }}
        """)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        lay.addWidget(self.table, 1)

        # Update initial OS tab visual
        self._update_os_tab_styles()

        return panel

    def _create_os_tab_btn(self, label: str, platform_key: str) -> QPushButton:
        btn = QPushButton(label, self)
        btn.setCheckable(True)
        btn.setFixedHeight(28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self._on_os_tab_selected(platform_key))
        return btn

    def _update_os_tab_styles(self):
        for plat, btn in self.os_tab_buttons.items():
            is_active = (plat == self._active_platform)
            btn.setChecked(is_active)
            if is_active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: rgba(255, 255, 255, 0.14);
                        border: 1px solid {self.accent_color};
                        border-radius: 5px;
                        color: #ffffff;
                        font-size: 8pt;
                        font-weight: bold;
                        padding: 0 10px;
                    }}
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.04);
                        border: 1px solid rgba(255, 255, 255, 0.10);
                        border-radius: 5px;
                        color: rgba(255, 255, 255, 0.65);
                        font-size: 8pt;
                        padding: 0 10px;
                    }
                    QPushButton:hover {
                        background-color: rgba(255, 255, 255, 0.08);
                        color: #ffffff;
                    }
                """)

    def _on_os_tab_selected(self, platform_key: str):
        if self._active_platform == platform_key:
            return
        self._active_platform = platform_key
        self._update_os_tab_styles()
        self._apply_smart_recommended_for_platform(platform_key)

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 2, 14, 0)
        bar.setSpacing(10)

        bar.addStretch()

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setFixedHeight(30)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: #e5e7eb;
                font-size: 8.5pt;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        bar.addWidget(self.cancel_btn)

        self.download_btn = QPushButton("Download Selected DLCs", self)
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.setFixedHeight(30)
        self.download_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: 1px solid {self.accent_color};
                border-radius: 6px;
                color: #111318;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
                background-color: #ffffff;
                border-color: #ffffff;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.1);
                border-color: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.35);
            }}
        """)
        self.download_btn.clicked.connect(self.accept)
        bar.addWidget(self.download_btn)

        return bar

    def _load_header_image(self):
        target_url = self.header_url
        if not target_url:
            target_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{self.app_id}/header.jpg"

        def _on_fetched(data):
            if data:
                pm = QPixmap()
                pm.loadFromData(data)
                if not pm.isNull():
                    scaled = pm.scaled(236, 105, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    self.header_img_lbl.setPixmap(scaled)
                    return
            self.header_img_lbl.setText(self.game_name)

        self.img_fetcher = ImageFetcher(target_url)
        self.img_fetcher.finished.connect(_on_fetched)
        self.img_fetcher.start()

    # ─────────────────────────────────────────────────────────────────────────
    # DLC Auto-Selection & Platform Filtering
    # ─────────────────────────────────────────────────────────────────────────

    def _get_dlc_depots_for_platform(self, platform: str) -> List[str]:
        """
        Returns list of DLC depot IDs matching the given platform:
        Excludes base game depots, macOS-only, Android-only, and bonus/soundtrack depots.
        """
        matched = []
        sorted_keys = sorted(
            self.depots.keys(),
            key=lambda k: int(k) if str(k).isdigit() else 0
        )

        for did in sorted_keys:
            did_str = str(did).strip()
            d_data = self.depots.get(did, {})
            if not isinstance(d_data, dict):
                continue

            desc = str(d_data.get("desc") or "")

            # Exclude base game depot / parent appid
            if is_base_game_main_depot(did_str, desc, self.app_id, d_data) or did_str == self.app_id:
                continue

            # Confirm it is a DLC depot
            if not is_dlc_depot(did_str, desc, self.app_id, d_data):
                continue

            # Exclude macOS and Android
            if _depot_is_macos(d_data) or _depot_is_android(d_data):
                continue

            # Platform match (Linux vs Windows)
            if not _depot_matches_platform(d_data, platform):
                continue

            matched.append(did_str)

        return matched

    def _apply_smart_recommended_for_platform(self, platform: str):
        """
        Computes Steam's recommended DLC depots for the platform:
        Smart default selection from Steam rules minus the base game depots.
        """
        smart_defaults = set(get_smart_default_depots(self.depots, target_platform=platform))
        visible_dlcs = set(self._get_dlc_depots_for_platform(platform))

        # Recommended DLCs are smart defaults intersected with visible DLCs
        recommended = smart_defaults.intersection(visible_dlcs)
        if not recommended and visible_dlcs:
            # Fallback: if smart defaults filtered everything, select non-bonus visible DLCs
            recommended = {d for d in visible_dlcs if not is_bonus_or_media_depot(self.depots.get(d, {}))}

        self._user_checked_depots = set(recommended)
        self.edition_btn.setText("Steam Recommended ▾")
        self._repopulate_table_for_platform(platform)
        self._update_summary()

    def _repopulate_table_for_platform(self, platform: str):
        """Fills table with DLC depots matching the current platform."""
        self.table.setRowCount(0)
        self.table.blockSignals(True)

        dlcs = self._get_dlc_depots_for_platform(platform)

        for did_str in dlcs:
            d_data = self.depots.get(did_str, {})
            desc = str(d_data.get("desc") or f"Depot {did_str}")
            raw_size = int(d_data.get("size") or 0)
            is_missing = did_str in self.missing_hubcap_depots

            row = self.table.rowCount()
            self.table.insertRow(row)

            # Col 0: Checkbox + Depot ID (compact)
            id_item = NumericTableWidgetItem(did_str, int(did_str) if did_str.isdigit() else 0)
            id_item.setData(Qt.ItemDataRole.UserRole, did_str)

            if is_missing:
                id_item.setCheckState(Qt.CheckState.Unchecked)
                id_item.setFlags(Qt.ItemFlag.NoItemFlags)
                id_item.setForeground(QColor(130, 130, 130))
            else:
                is_checked = (did_str in self._user_checked_depots)
                id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)

            self.table.setItem(row, 0, id_item)

            # Col 1: DLC Name with OS icon and [DLC] tag
            tint = QColor(self.accent_color) if hasattr(self, "accent_color") and self.accent_color else None
            os_icon = get_os_icon(d_data.get("oslist"), tint_color=tint)
            clean_name = format_depot_row_label(
                did_str,
                d_data,
                desc,
                has_os_icon=bool(os_icon),
                show_os_badge=False,
            )
            name_item = QTableWidgetItem(clean_name)
            if os_icon:
                name_item.setIcon(os_icon)

            if is_missing:
                name_item.setToolTip(f"{desc}\n[Status: Missing on Hubcap]")
                name_item.setForeground(QColor(130, 130, 130))
            else:
                name_item.setToolTip(desc)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, name_item)

            # Col 2: Size
            size_str = format_size(raw_size) if raw_size > 0 else "-"
            size_item = NumericTableWidgetItem(size_str, raw_size)
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if is_missing:
                size_item.setForeground(QColor(130, 130, 130))
            self.table.setItem(row, 2, size_item)

        self.table.blockSignals(False)

    # ─────────────────────────────────────────────────────────────────────────
    # Edition Menu (Real Store Packages or Recommended / All / Custom)
    # ─────────────────────────────────────────────────────────────────────────

    def _show_edition_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px;
                color: #ffffff;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 8.5pt;
            }}
            QMenu::item:selected {{
                background-color: rgba(255, 255, 255, 0.12);
                color: {self.accent_color};
            }}
        """)

        # 1. Steam Recommended Action
        act_rec = menu.addAction("Steam Recommended")
        act_rec.triggered.connect(lambda: self._apply_smart_recommended_for_platform(self._active_platform))

        # 2. Real Store Packages (if available from Steam Store)
        if self._store_packages:
            menu.addSeparator()
            for pkg in self._store_packages:
                pkg_name = clean_package_name(pkg.get("name", "Package"))
                act_pkg = menu.addAction(pkg_name)
                act_pkg.triggered.connect(lambda checked, p=pkg, n=pkg_name: self._apply_store_package(p, n))

        # 3. All DLCs Action
        menu.addSeparator()
        act_all = menu.addAction("All DLCs")
        act_all.triggered.connect(lambda: self._apply_all_dlcs())

        pos = self.edition_btn.mapToGlobal(QPoint(0, self.edition_btn.height() + 2))
        menu.exec(pos)

    def _apply_store_package(self, pkg: dict, name: str):
        """Selects DLC depots associated with a real Steam store package."""
        self.edition_btn.setText(f"{name} ▾")
        visible_dlcs = set(self._get_dlc_depots_for_platform(self._active_platform))
        # Store package apps list
        apps = {str(a) for a in pkg.get("apps", [])}

        target_dlcs = set()
        for did in visible_dlcs:
            d_data = self.depots.get(did, {})
            dlc_app = str(d_data.get("dlcappid") or "")
            if (did in apps) or (dlc_app in apps) or (not apps):
                target_dlcs.add(did)

        if not target_dlcs:
            target_dlcs = visible_dlcs

        self._user_checked_depots = target_dlcs
        self._sync_checks_to_table()
        self._update_summary()

    def _apply_all_dlcs(self):
        self.edition_btn.setText("All DLCs ▾")
        visible_dlcs = set(self._get_dlc_depots_for_platform(self._active_platform))
        # Exclude missing hubcap depots from auto-selection
        valid_dlcs = {d for d in visible_dlcs if d not in self.missing_hubcap_depots}
        self._user_checked_depots = valid_dlcs
        self._sync_checks_to_table()
        self._update_summary()

    def _sync_checks_to_table(self):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if not id_item or (id_item.flags() == Qt.ItemFlag.NoItemFlags):
                continue
            did = str(id_item.data(Qt.ItemDataRole.UserRole))
            is_chk = did in self._user_checked_depots
            id_item.setCheckState(Qt.CheckState.Checked if is_chk else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)

    def _toggle_select_all(self):
        any_unchecked = False
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if id_item and (id_item.flags() != Qt.ItemFlag.NoItemFlags):
                if id_item.checkState() == Qt.CheckState.Unchecked:
                    any_unchecked = True
                    break

        if any_unchecked:
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                if id_item and (id_item.flags() != Qt.ItemFlag.NoItemFlags):
                    did = str(id_item.data(Qt.ItemDataRole.UserRole))
                    self._user_checked_depots.add(did)
                    id_item.setCheckState(Qt.CheckState.Checked)
            self.edition_btn.setText("All DLCs ▾")
        else:
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                if id_item and (id_item.flags() != Qt.ItemFlag.NoItemFlags):
                    did = str(id_item.data(Qt.ItemDataRole.UserRole))
                    self._user_checked_depots.discard(did)
                    id_item.setCheckState(Qt.CheckState.Unchecked)
            self.edition_btn.setText("Custom ▾")

        self._update_summary()

    def _on_table_cell_clicked(self, row: int, col: int):
        id_item = self.table.item(row, 0)
        if not id_item or (id_item.flags() == Qt.ItemFlag.NoItemFlags):
            return
        did = str(id_item.data(Qt.ItemDataRole.UserRole))
        curr = id_item.checkState()
        new_state = Qt.CheckState.Unchecked if curr == Qt.CheckState.Checked else Qt.CheckState.Checked
        id_item.setCheckState(new_state)

        if new_state == Qt.CheckState.Checked:
            self._user_checked_depots.add(did)
        else:
            self._user_checked_depots.discard(did)

        self.edition_btn.setText("Custom ▾")
        self._update_summary()

    def _update_summary(self):
        total_visible = self.table.rowCount()
        checked_count = 0
        checked_bytes = 0

        for r in range(total_visible):
            id_item = self.table.item(r, 0)
            if id_item and id_item.checkState() == Qt.CheckState.Checked:
                checked_count += 1
                size_item = self.table.item(r, 2)
                if size_item and hasattr(size_item, "sort_val"):
                    checked_bytes += int(size_item.sort_val or 0)

        size_text = format_size(checked_bytes)
        self.summary_lbl.setText(f"Selected: {checked_count} of {total_visible} DLCs ({size_text})")
        self.download_btn.setEnabled(checked_count > 0)
        self.select_all_btn.setText("Deselect All" if checked_count == total_visible and total_visible > 0 else "Select All")

    # ─────────────────────────────────────────────────────────────────────────
    # Public Downstream Interface Matching DepotSelectionDialog
    # ─────────────────────────────────────────────────────────────────────────

    def get_selected_depots(self) -> List[str]:
        """Returns the list of checked DLC depot ID strings."""
        result = []
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if id_item and id_item.checkState() == Qt.CheckState.Checked:
                did = id_item.data(Qt.ItemDataRole.UserRole)
                if did:
                    result.append(str(did))
        return result

    def get_selected_storage(self) -> str:
        """Returns the locked Steam installation library path."""
        return self.library_path

    def is_build_pinned(self) -> bool:
        return False

    def get_selected_build(self) -> str:
        return self.current_build_id

    def get_manifest_overrides(self) -> dict:
        return {}

    def _apply_build_selection(self, rec_bid, patch_depots):
        """No-op for DLC-only mode since build is tied to base game."""
        pass

    # Window Drag Support
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
