"""
dlc_depot_selection.py - ASSella unified DLC selection dialog for owned Steam games.
Features:
- Full-width modern layout matching standard DepotSelectionDialog
- Offline local Steam AppOwnershipTicket detection: Steam-owned DLCs are greyscaled and non-toggleable
- SLSsteam config.yaml injection state detection
- Interactive filter chips (All, Unowned, Steam Owned, Injected, Depotless) with custom colors and clean text (no dots)
- Checkable depotless DLC entitlements (with null size in italics), not checked by default
- Rich configuration tags: [OS icon] [ARCH] [DLC] [LANG] [SOUNDTRACK] [ARTBOOK] without duplicate depot IDs
- Standard edition selection dropdown with base-game depot filter
- Three equal-width bottom buttons: [ DLC Data Injection ], [ Cancel ], [ Download Selected ]
- Byparr & Store DLC name enrichment integration
"""

import logging
import re
from typing import Dict, List, Optional, Any, Set, Tuple
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QSize
from PyQt6.QtGui import QColor, QPixmap, QMouseEvent, QPainter, QFont, QPen
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
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QApplication,
)

from ui.dialog_titlebar import DialogTitleBar
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.depot_selection.rules import (
    _depot_matches_platform,
    _depot_is_macos,
    _depot_is_android,
    is_bonus_or_media_depot,
    get_smart_default_depots,
    format_size,
)
from ui.dialogs.depot_selection import selection_policy
from utils.depot_tag_helpers import (
    get_os_icon,
    extract_arch_code,
    extract_language_code,
)
from utils.image_fetcher import ImageFetcher
from utils.settings import get_settings
from utils.dlc_helpers import (
    is_dlc_depot,
    is_base_game_main_depot,
    get_owned_dlcs_from_steam,
    get_injected_dlcs_from_config,
    enrich_dlc_names_with_byparr_or_store,
)
from utils.edition_helpers import (
    get_available_platforms,
    get_default_platform,
    resolve_multi_platform_editions,
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


class DlcSizeDelegate(QStyledItemDelegate):
    """Renders size column; for depotless DLCs renders 'null' in italics."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        is_null = index.data(Qt.ItemDataRole.UserRole + 11) == "null"
        if is_null:
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            widget = option.widget
            style = widget.style() if widget else QApplication.style()

            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)

            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            font = painter.font()
            font.setItalic(True)
            painter.setFont(font)
            painter.setPen(QColor(130, 130, 135))
            text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "null")
            painter.restore()
        else:
            super().paint(painter, option, index)


class DlcHeaderView(QHeaderView):
    """
    Table header view supporting a custom tri-state checkbox in column 0
    with visible selection counts '(x/y)'.
    """

    def __init__(self, parent: "DlcDepotSelectionDialog"):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.dialog = parent
        self._check_state = Qt.CheckState.Unchecked
        self._count_str = ""
        self.setSectionsClickable(True)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_check_state_and_count(self, state: Qt.CheckState, count_str: str):
        if self._check_state != state or self._count_str != count_str:
            self._check_state = state
            self._count_str = count_str
            self.viewport().update()

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int):
        painter.save()
        super().paintSection(painter, rect, logical_index)
        painter.restore()

        if logical_index == 0:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            accent = getattr(self.dialog, "accent_color", "#a1c9fd")
            accent_color = QColor(accent)

            box_size = 14
            bx = rect.x() + 10
            by = rect.y() + (rect.height() - box_size) // 2
            box_rect = QRectF(bx, by, box_size, box_size)

            if self._check_state == Qt.CheckState.Checked:
                painter.setBrush(accent_color)
                painter.setPen(QPen(accent_color, 1.2))
                painter.drawRoundedRect(box_rect, 3.0, 3.0)

                painter.setPen(QPen(QColor("#111318"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawLine(int(bx + 3), int(by + 7), int(bx + 6), int(by + 10))
                painter.drawLine(int(bx + 6), int(by + 10), int(bx + 11), int(by + 4))
            elif self._check_state == Qt.CheckState.PartiallyChecked:
                painter.setBrush(accent_color)
                painter.setPen(QPen(accent_color, 1.2))
                painter.drawRoundedRect(box_rect, 3.0, 3.0)

                painter.setPen(QPen(QColor("#111318"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(int(bx + 3), int(by + 7), int(bx + 11), int(by + 7))
            else:
                painter.setBrush(QColor(255, 255, 255, 15))
                painter.setPen(QPen(QColor(255, 255, 255, 70), 1.2))
                painter.drawRoundedRect(box_rect, 3.0, 3.0)

            if self._count_str:
                painter.setFont(self.font())
                painter.setPen(QColor("#A0AEC0"))
                # Right-aligned so the (x/y) count never sits on the label.
                text_rect = QRect(bx + box_size + 6, rect.y(), rect.width() - (box_size + 14), rect.height())
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._count_str)

            painter.restore()


class DlcDepotSelectionDialog(QDialog):
    """
    Dedicated unified full-width DLC selection dialog for owned Steam games.
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
        dlc_catalog=None,
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
        self.branch = str(branch or "public")
        self.current_build_id = str(current_build_id or "").strip()
        self.missing_hubcap_depots = {str(d).strip() for d in (missing_hubcap_depots or []) if str(d).strip()}
        self.missing_depots_info = dict(missing_depots_info or {})
        self.initial_selected_depots = {str(d).strip() for d in (selected_depots or []) if str(d).strip()}

        self._drag_pos: Optional[QPoint] = None
        self._user_checked_items: Set[str] = set()
        self._store_packages: List[Dict[str, Any]] = []
        self._active_filter = "all"
        self._injection_only = False
        self._config_written = False

        # Read offline Steam ownership and SLSsteam injection state
        self._steam_owned_dlcs = get_owned_dlcs_from_steam(self.app_id)
        self._injected_dlcs = get_injected_dlcs_from_config(self.app_id)

        # DLC Catalog: dlc_appid -> name
        self._dlc_catalog: Dict[str, str] = {}
        for dlc_appid, dlc_name in dict(dlc_catalog or {}).items():
            dlc_appid = str(dlc_appid or "").strip()
            if dlc_appid.isdigit() and dlc_appid != self.app_id:
                self._dlc_catalog[dlc_appid] = str(dlc_name or "")
        for meta in self.depots.values():
            if not isinstance(meta, dict):
                continue
            dlc_appid = str(meta.get("dlcappid") or "").strip()
            if dlc_appid.isdigit() and dlc_appid != self.app_id:
                name = str(meta.get("name") or meta.get("desc") or "")
                self._dlc_catalog.setdefault(dlc_appid, name.strip())

        # Enrich missing DLC names using Byparr or Steam Store API
        self._dlc_catalog = enrich_dlc_names_with_byparr_or_store(self.app_id, self._dlc_catalog)

        # DLCs that own no depot at all (entitlement only)
        self._depotless_dlcs: Set[str] = set(self._dlc_catalog)
        for meta in self.depots.values():
            if isinstance(meta, dict):
                dlc_appid = str(meta.get("dlcappid") or "").strip()
                self._depotless_dlcs.discard(dlc_appid)

        # Available platforms and multi-platform editions
        self._available_platforms = get_available_platforms(self.depots)
        self._active_platform = get_default_platform(self.depots)
        self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self.setMinimumSize(820, 560)
        self.resize(860, 580)

        self._fetch_store_packages_safe()
        self._build_ui()
        self._load_header_image()

        # Compute initial selection:
        # Depotless DLCs are NOT checked by default.
        # Only downloadable unowned DLC depots are pre-checked by default.
        self._init_default_selections()
        self._repopulate_table()
        self._update_filter_counts()
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

    def _init_default_selections(self):
        """
        Determines initial checked state for all DLCs and depots:
        - Downloadable Unowned DLCs: Pre-checked
        - Injected DLCs: Pre-checked
        - Steam-Owned DLCs: Unchecked & Disabled (greyscaled)
        - Depotless DLCs: Unchecked by default
        """
        self._user_checked_items.clear()

        # 1. Downloadable Depots
        for did_str, d_data in self.depots.items():
            did_str = str(did_str).strip()
            if not isinstance(d_data, dict):
                continue
            desc = str(d_data.get("desc") or "")
            if is_base_game_main_depot(did_str, desc, self.app_id, d_data) or did_str == self.app_id:
                continue
            if not is_dlc_depot(did_str, desc, self.app_id, d_data):
                continue
            if _depot_is_macos(d_data) or _depot_is_android(d_data):
                continue

            dlc_app = str(d_data.get("dlcappid") or "").strip()
            is_steam_owned = (dlc_app in self._steam_owned_dlcs) or (did_str in self._steam_owned_dlcs)
            is_injected = (dlc_app in self._injected_dlcs) or (did_str in self._injected_dlcs)

            if not is_steam_owned and did_str not in self.missing_hubcap_depots:
                self._user_checked_items.add(did_str)

        # Depotless DLCs are intentionally NOT checked by default per user request.

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
        container_lay.setContentsMargins(14, 10, 14, 14)
        container_lay.setSpacing(10)

        # 1. Frameless title bar
        self.title_bar = DialogTitleBar(
            self,
            title="Select DLCs to Download",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # 2. Top Header Banner (Artwork, Title, AppID, Branch/Build, Install Path)
        header_banner = self._build_header_banner()
        container_lay.addLayout(header_banner)

        # 3. Filter Chips Bar (All, Unowned, Steam Owned, Injected, Depotless)
        chips_bar = self._build_filter_chips_bar()
        container_lay.addLayout(chips_bar)

        # 4. Table Widget
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["DLC ID", "Configuration", "Size"])
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)

        # Custom Header View with interactive tri-state checkbox and visible counts
        self.custom_header = DlcHeaderView(self)
        self.table.setHorizontalHeader(self.custom_header)
        # The tri-state box is painted at the left of column 0; inset the
        # header label so the two never overlap.
        self.custom_header.setStyleSheet(
            "QHeaderView::section:first { padding-left: 34px; }"
        )
        self.custom_header.sectionClicked.connect(self._on_header_section_clicked)

        # Column widths and stretch
        self.custom_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 120)
        self.custom_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.custom_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 95)

        # Size Item Delegate for null size in italics
        self.size_delegate = DlcSizeDelegate(self)
        self.table.setItemDelegateForColumn(2, self.size_delegate)

        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                gridline-color: transparent;
                outline: 0;
                color: #FFFFFF;
                selection-background-color: transparent;
                selection-color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            }}
            QTableWidget::item:hover {{
                background-color: rgba(255, 255, 255, 0.05);
            }}
            QTableWidget::item:selected {{
                background-color: rgba(255, 255, 255, 0.08) !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.7);
                padding: 6px 10px;
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
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
        """)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        container_lay.addWidget(self.table, 1)

        # 5. Bottom Controls Bar
        bottom_bar = self._build_bottom_bar()
        container_lay.addLayout(bottom_bar)

    def _build_header_banner(self) -> QHBoxLayout:
        banner = QHBoxLayout()
        banner.setSpacing(14)

        # Game capsule image
        self.header_img_lbl = QLabel(self)
        self.header_img_lbl.setFixedSize(184, 86)
        self.header_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_img_lbl.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            color: rgba(255, 255, 255, 0.5);
            font-size: 8.5pt;
        """)
        self.header_img_lbl.setText("Loading banner...")
        banner.addWidget(self.header_img_lbl)

        # Metadata layout
        info_lay = QVBoxLayout()
        info_lay.setSpacing(4)
        info_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Title + AppID
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_lbl = QLabel(f"<b>{self.game_name}</b> <span style='color: rgba(255, 255, 255, 0.55); font-size: 9.5pt;'>({self.app_id})</span>", self)
        self.title_lbl.setStyleSheet("font-size: 11pt; color: #ffffff;")
        title_row.addWidget(self.title_lbl)
        title_row.addStretch()
        info_lay.addLayout(title_row)

        # Badges row: Branch, Build ID, Selection summary
        badges_row = QHBoxLayout()
        badges_row.setSpacing(8)

        branch_lbl = QLabel(f"Branch: <span style='color: #ffffff; font-weight: 600;'>{self.branch}</span>", self)
        branch_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
        badges_row.addWidget(branch_lbl)

        if self.current_build_id:
            build_badge = QLabel(f"Build: {self.current_build_id}", self)
            build_badge.setStyleSheet("""
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                color: #ffffff;
                font-size: 8pt;
                font-weight: 600;
                padding: 1px 6px;
            """)
            badges_row.addWidget(build_badge)

        self.summary_lbl = QLabel("Selected: 0 of 0 DLCs (0 B)", self)
        self.summary_lbl.setStyleSheet(f"font-size: 8.5pt; font-weight: 600; color: {self.accent_color};")
        badges_row.addWidget(self.summary_lbl)

        badges_row.addStretch()
        info_lay.addLayout(badges_row)

        # Install Path
        display_path = self.library_path or "Detected from Steam library"
        self.path_lbl = QLabel(f"Install Path: <span style='color: rgba(255, 255, 255, 0.8); font-family: monospace;'>{display_path}</span>", self)
        self.path_lbl.setToolTip(self.library_path or "Steam default directory")
        self.path_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8pt;")
        info_lay.addWidget(self.path_lbl)

        banner.addLayout(info_lay, 1)
        return banner

    def _build_filter_chips_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.filter_chips: Dict[str, QPushButton] = {}

        chip_definitions = [
            ("all", "All", "#FFFFFF", "rgba(255, 255, 255, 0.12)", "rgba(255, 255, 255, 0.3)"),
            ("missing", "Unowned", "#f09a2f", "rgba(240, 154, 47, 0.16)", "rgba(240, 154, 47, 0.45)"),
            ("owned", "Steam Owned", "#25a878", "rgba(37, 168, 120, 0.16)", "rgba(37, 168, 120, 0.45)"),
            ("injected", "Injected", "#16a6c7", "rgba(22, 166, 199, 0.16)", "rgba(22, 166, 199, 0.45)"),
            ("depotless", "Depotless", "#eab308", "rgba(234, 179, 8, 0.16)", "rgba(234, 179, 8, 0.45)"),
        ]

        self._chip_colors = {}
        for key, label, fg_hex, bg_hex, border_hex in chip_definitions:
            self._chip_colors[key] = (fg_hex, bg_hex, border_hex)
            btn = QPushButton(self)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("filter_key", key)
            btn.clicked.connect(lambda checked, k=key: self._on_filter_chip_clicked(k))
            self.filter_chips[key] = btn
            bar.addWidget(btn)

        bar.addStretch()
        return bar

    def _update_filter_chips_visual(self):
        for key, btn in self.filter_chips.items():
            is_active = (key == self._active_filter)
            btn.setChecked(is_active)
            fg_hex, bg_hex, border_hex = self._chip_colors.get(key, ("#FFFFFF", "rgba(255,255,255,0.1)", "rgba(255,255,255,0.2)"))

            if is_active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {bg_hex};
                        border: 1px solid {border_hex};
                        border-radius: 13px;
                        color: {fg_hex};
                        font-size: 8pt;
                        font-weight: 600;
                        padding: 0 12px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: rgba(255, 255, 255, 0.035);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 13px;
                        color: rgba(255, 255, 255, 0.65);
                        font-size: 8pt;
                        padding: 0 12px;
                    }}
                    QPushButton:hover {{
                        background-color: rgba(255, 255, 255, 0.08);
                        color: {fg_hex};
                        border-color: {border_hex};
                    }}
                """)

    def _update_filter_counts(self):
        counts = {"all": 0, "missing": 0, "owned": 0, "injected": 0, "depotless": 0}

        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if not id_item:
                continue
            counts["all"] += 1
            status = id_item.data(Qt.ItemDataRole.UserRole + 20)
            is_depotless = bool(id_item.data(Qt.ItemDataRole.UserRole + 21))

            if status == "missing":
                counts["missing"] += 1
            elif status == "owned":
                counts["owned"] += 1
            elif status == "injected":
                counts["injected"] += 1

            if is_depotless:
                counts["depotless"] += 1

        labels = {
            "all": f"All ({counts['all']})",
            "missing": f"Unowned ({counts['missing']})",
            "owned": f"Steam Owned ({counts['owned']})",
            "injected": f"Injected ({counts['injected']})",
            "depotless": f"Depotless ({counts['depotless']})",
        }

        for key, btn in self.filter_chips.items():
            btn.setText(labels.get(key, key.capitalize()))

        self._update_filter_chips_visual()

    def _on_filter_chip_clicked(self, filter_key: str):
        self._active_filter = filter_key
        self._update_filter_chips_visual()
        self._apply_row_filtering()
        self._update_summary()

    def _apply_row_filtering(self):
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if not id_item:
                continue
            status = id_item.data(Qt.ItemDataRole.UserRole + 20)
            is_depotless = bool(id_item.data(Qt.ItemDataRole.UserRole + 21))

            visible = True
            if self._active_filter == "all":
                visible = True
            elif self._active_filter == "missing":
                visible = (status == "missing")
            elif self._active_filter == "owned":
                visible = (status == "owned")
            elif self._active_filter == "injected":
                visible = (status == "injected")
            elif self._active_filter == "depotless":
                visible = is_depotless

            self.table.setRowHidden(r, not visible)

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        # 1. Standard Edition Dropdown Button (Bottom Left)
        self.edition_button = QPushButton("Complete Edition ▾", self)
        self.edition_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edition_button.setFixedHeight(32)
        self.edition_button.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: #ffffff;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.10);
                border-color: {self.accent_color};
            }}
        """)
        self.edition_button.clicked.connect(self._show_edition_menu)
        bar.addWidget(self.edition_button)

        bar.addStretch()

        # 2. Three equal-width action buttons on the right:
        # [ DLC Data Injection ]  [ Cancel ]  [ Download Selected ]
        btn_min_w = 175

        self.inject_btn = QPushButton("DLC Data Injection", self)
        self.inject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inject_btn.setMinimumWidth(btn_min_w)
        self.inject_btn.setFixedHeight(32)
        self.inject_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(22, 166, 199, 0.16);
                border: 1px solid #16a6c7;
                border-radius: 6px;
                color: #ffffff;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: rgba(22, 166, 199, 0.28);
                border-color: #38bdf8;
            }
        """)
        self.inject_btn.clicked.connect(self._handle_inject_btn_clicked)
        bar.addWidget(self.inject_btn)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setMinimumWidth(btn_min_w)
        self.cancel_btn.setFixedHeight(32)
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

        self.download_btn = QPushButton("Download Selected (0/0)", self)
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.setMinimumWidth(btn_min_w)
        self.download_btn.setFixedHeight(32)
        self.download_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: 1px solid {self.accent_color};
                border-radius: 6px;
                color: #111318;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0 16px;
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
        self.download_btn.clicked.connect(self._handle_download_btn_clicked)
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
                    scaled = pm.scaled(184, 86, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    self.header_img_lbl.setPixmap(scaled)
                    return
            self.header_img_lbl.setText(self.game_name)

        self.img_fetcher = ImageFetcher(target_url)
        self.img_fetcher.finished.connect(_on_fetched)
        self.img_fetcher.start()

    # ─────────────────────────────────────────────────────────────────────────
    # Table Population & Enrichment
    # ─────────────────────────────────────────────────────────────────────────

    def _build_enriched_config_label(self, did_str: str, d_data: dict, desc: str) -> Tuple[str, Optional[Any]]:
        """
        Builds enriched label matching DepotSelectionDialog:
        [OS icon] [ARCH] [DLC] [LANG] [SOUNDTRACK] [ARTBOOK] Name
        Does not duplicate the depot ID in the text since it is already in column 0.
        """
        tint = QColor(self.accent_color) if hasattr(self, "accent_color") and self.accent_color else None
        os_icon = get_os_icon(d_data.get("oslist"), tint_color=tint)

        # Base clean description
        clean_desc = desc
        for p in ("[Checking]", "[Missing]", "[Recovered]", "[No Key]", "[Missing from Hubcap]"):
            clean_desc = clean_desc.replace(p, "").strip()
        clean_desc = re.sub(r"\s*-\s*Depot\s*" + re.escape(did_str), "", clean_desc, flags=re.IGNORECASE)
        clean_desc = re.sub(r"\[(?:Windows|Linux|macOS|Android|iOS|All)\]\s*", "", clean_desc, flags=re.IGNORECASE)
        clean_desc = re.sub(r"\[DLC(?:\s+\d+)?\]\s*", "", clean_desc, flags=re.IGNORECASE)
        clean_desc = re.sub(r"^DLC\s+\d+\s*[-–:]?\s*", "", clean_desc, flags=re.IGNORECASE).strip()

        tags = []

        # 1. Architecture tag
        arch_code = extract_arch_code(d_data, desc)
        if arch_code:
            tags.append(f"[{arch_code}]")

        # 2. [DLC] tag
        tags.append("[DLC]")

        # 3. Language tag ([EN], [ZH-CN], etc.)
        lang_code = extract_language_code(d_data, desc)
        if lang_code:
            tags.append(f"[{lang_code}]")

        # 4. Media tags
        desc_lower = clean_desc.lower()
        if "soundtrack" in desc_lower or " ost" in desc_lower or "[ost]" in desc_lower:
            tags.append("[SOUNDTRACK]")
        elif "artbook" in desc_lower or "art book" in desc_lower:
            tags.append("[ARTBOOK]")
        elif "wallpaper" in desc_lower:
            tags.append("[WALLPAPER]")

        final_text = f"{' '.join(tags)}  {clean_desc}" if tags else clean_desc
        return final_text, os_icon

    def _repopulate_table(self):
        self.table.setRowCount(0)
        self.table.blockSignals(True)

        sorted_depot_keys = sorted(
            self.depots.keys(),
            key=lambda k: int(k) if str(k).isdigit() else 0,
        )

        # 1. Downloadable DLC depots, collapsed to one row per DLC. A DLC
        #    routinely ships several depots (platform variants, language
        #    packs), so a depot-per-row list buries the entitlement the user is
        #    choosing behind depot ids. A depot with no DLC id keeps its own row.
        groups: Dict[str, Dict[str, Any]] = {}
        for did in sorted_depot_keys:
            did_str = str(did).strip()
            d_data = self.depots.get(did, {})
            if not isinstance(d_data, dict):
                continue
            desc = str(d_data.get("desc") or "")

            if is_base_game_main_depot(did_str, desc, self.app_id, d_data) or did_str == self.app_id:
                continue
            if not is_dlc_depot(did_str, desc, self.app_id, d_data):
                continue
            if _depot_is_macos(d_data) or _depot_is_android(d_data):
                continue
            if not _depot_matches_platform(d_data, self._active_platform):
                continue

            dlc_app = str(d_data.get("dlcappid") or "").strip()
            if not dlc_app.isdigit() or dlc_app == self.app_id:
                dlc_app = did_str

            group = groups.get(dlc_app)
            if group is None:
                group = {
                    "depots": [],
                    "size": 0,
                    "first_id": did_str,
                    "first_data": d_data,
                    "first_desc": desc,
                }
                groups[dlc_app] = group
            group["depots"].append(did_str)
            group["size"] += int(d_data.get("size") or 0)

        for dlc_app, group in sorted(
            groups.items(),
            key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0,
        ):
            depot_ids: List[str] = group["depots"]
            first_id = group["first_id"]
            d_data = group["first_data"]
            desc = group["first_desc"]

            is_steam_owned = (
                dlc_app in self._steam_owned_dlcs
                or any(d in self._steam_owned_dlcs for d in depot_ids)
            )
            is_injected = (
                dlc_app in self._injected_dlcs
                or any(d in self._injected_dlcs for d in depot_ids)
            )
            is_missing_hubcap = all(d in self.missing_hubcap_depots for d in depot_ids)

            row = self.table.rowCount()
            self.table.insertRow(row)

            # Col 0: Checkbox + DLC AppID (the depots it stands for are in UserRole + 22)
            id_item = NumericTableWidgetItem(dlc_app, int(dlc_app) if dlc_app.isdigit() else 0)
            id_item.setData(Qt.ItemDataRole.UserRole, dlc_app)
            id_item.setData(Qt.ItemDataRole.UserRole + 1, dlc_app)
            id_item.setData(Qt.ItemDataRole.UserRole + 21, False)  # Not depotless
            id_item.setData(Qt.ItemDataRole.UserRole + 22, list(depot_ids))

            if is_injected:
                status_key = "injected"
            elif is_steam_owned:
                status_key = "owned"
            else:
                status_key = "missing"
            id_item.setData(Qt.ItemDataRole.UserRole + 20, status_key)

            if is_steam_owned:
                # Owned on Steam: greyscaled and not selectable at all.
                id_item.setCheckState(Qt.CheckState.Unchecked)
                id_item.setFlags(Qt.ItemFlag.NoItemFlags)
                id_item.setForeground(QColor(130, 130, 130))
                id_item.setToolTip("Owned legitimately on Steam (not needed to download)")
            elif is_missing_hubcap:
                id_item.setCheckState(Qt.CheckState.Unchecked)
                id_item.setFlags(Qt.ItemFlag.NoItemFlags)
                id_item.setForeground(QColor(130, 130, 130))
                id_item.setToolTip("Depot missing from Hubcap repository")
            else:
                is_checked = any(d in self._user_checked_items for d in depot_ids)
                id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)

            self.table.setItem(row, 0, id_item)

            # Col 1: DLC name, with the depot count when one DLC ships several.
            dlc_name = self._dlc_catalog.get(dlc_app) or ""
            if dlc_name:
                clean_name = f"[DLC]  {dlc_name}"
                if len(depot_ids) > 1:
                    clean_name += f"  ({len(depot_ids)} depots)"
                os_icon = get_os_icon(d_data.get("oslist"), tint_color=QColor(self.accent_color))
            else:
                clean_name, os_icon = self._build_enriched_config_label(first_id, d_data, desc)
            name_item = QTableWidgetItem(clean_name)
            if os_icon:
                name_item.setIcon(os_icon)
            if is_steam_owned or is_missing_hubcap:
                name_item.setForeground(QColor(130, 130, 130))
            if len(depot_ids) > 1:
                name_item.setToolTip(
                    "\n".join(
                        f"{d}: {str(self.depots.get(d, {}).get('desc') or '').strip()}"
                        for d in depot_ids
                    )
                )
            else:
                name_item.setToolTip(desc)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, name_item)

            # Col 2: Size (summed over the DLC's depots)
            raw_size = int(group["size"])
            size_item = NumericTableWidgetItem(format_size(raw_size) if raw_size > 0 else "0 B", raw_size)
            if is_steam_owned or is_missing_hubcap:
                size_item.setForeground(QColor(130, 130, 130))
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, size_item)

        # 2. Depotless DLCs
        for dlc_appid in sorted(
            self._depotless_dlcs,
            key=lambda d: (self._dlc_catalog.get(d, "").lower(), d),
        ):
            dlc_name = self._dlc_catalog.get(dlc_appid) or f"DLC {dlc_appid}"
            is_steam_owned = dlc_appid in self._steam_owned_dlcs
            is_injected = dlc_appid in self._injected_dlcs

            row = self.table.rowCount()
            self.table.insertRow(row)

            # Col 0: Checkbox + DLC AppID
            id_item = NumericTableWidgetItem(dlc_appid, int(dlc_appid) if dlc_appid.isdigit() else 0)
            id_item.setData(Qt.ItemDataRole.UserRole, dlc_appid)
            id_item.setData(Qt.ItemDataRole.UserRole + 1, dlc_appid)
            id_item.setData(Qt.ItemDataRole.UserRole + 21, True)  # Is depotless

            if is_injected:
                status_key = "injected"
            elif is_steam_owned:
                status_key = "owned"
            else:
                status_key = "missing"
            id_item.setData(Qt.ItemDataRole.UserRole + 20, status_key)

            if is_steam_owned:
                # Owned on Steam: Greyscaled and non-toggleable
                id_item.setCheckState(Qt.CheckState.Unchecked)
                id_item.setFlags(Qt.ItemFlag.NoItemFlags)
                id_item.setForeground(QColor(130, 130, 130))
                id_item.setToolTip("Owned legitimately on Steam")
            else:
                is_checked = (dlc_appid in self._user_checked_items)
                id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)

            self.table.setItem(row, 0, id_item)

            # Col 1: Clean DLC Name with [DLC] tag (no duplicate DLC AppID in text)
            clean_name = f"[DLC]  {dlc_name}"
            name_item = QTableWidgetItem(clean_name)
            if is_steam_owned:
                name_item.setForeground(QColor(130, 130, 130))
            name_item.setToolTip(f"DLC {dlc_appid}: entitlement without separate depot files.")
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, name_item)

            # Col 2: Size ('null' in italics rendered by DlcSizeDelegate)
            size_item = NumericTableWidgetItem("null", 0)
            size_item.setData(Qt.ItemDataRole.UserRole + 11, "null")
            if is_steam_owned:
                size_item.setForeground(QColor(130, 130, 130))
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, size_item)

        self.table.blockSignals(False)

    @staticmethod
    def _row_depot_ids(id_item: QTableWidgetItem) -> List[str]:
        """Depots a row stands for; a grouped row holds the whole DLC."""
        depot_ids = id_item.data(Qt.ItemDataRole.UserRole + 22)
        if depot_ids:
            return [str(d) for d in depot_ids]
        item_id = str(id_item.data(Qt.ItemDataRole.UserRole) or "")
        return [item_id] if item_id else []

    def _on_table_cell_clicked(self, row: int, col: int):
        id_item = self.table.item(row, 0)
        if not id_item or (id_item.flags() == Qt.ItemFlag.NoItemFlags):
            return
        item_ids = self._row_depot_ids(id_item)
        curr = id_item.checkState()
        new_state = Qt.CheckState.Unchecked if curr == Qt.CheckState.Checked else Qt.CheckState.Checked
        id_item.setCheckState(new_state)

        for item_id in item_ids:
            if new_state == Qt.CheckState.Checked:
                self._user_checked_items.add(item_id)
            else:
                self._user_checked_items.discard(item_id)

        self.edition_button.setText("Custom ▾")
        self._update_summary()

    def _on_header_section_clicked(self, logical_index: int):
        if logical_index != 0:
            return

        any_visible_unchecked = False
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                item = self.table.item(r, 0)
                if item and (item.flags() != Qt.ItemFlag.NoItemFlags):
                    if item.checkState() == Qt.CheckState.Unchecked:
                        any_visible_unchecked = True
                        break

        new_state = Qt.CheckState.Checked if any_visible_unchecked else Qt.CheckState.Unchecked

        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                item = self.table.item(r, 0)
                if item and (item.flags() != Qt.ItemFlag.NoItemFlags):
                    item.setCheckState(new_state)
                    for item_id in self._row_depot_ids(item):
                        if new_state == Qt.CheckState.Checked:
                            self._user_checked_items.add(item_id)
                        else:
                            self._user_checked_items.discard(item_id)
        self.table.blockSignals(False)

        self.edition_button.setText("Custom ▾")
        self._update_summary()

    def _update_summary(self):
        visible_total = 0
        visible_checked = 0
        total_checked = 0
        checked_bytes = 0

        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if not id_item or id_item.flags() == Qt.ItemFlag.NoItemFlags:
                continue

            is_hidden = self.table.isRowHidden(r)
            is_chk = (id_item.checkState() == Qt.CheckState.Checked)

            if not is_hidden:
                visible_total += 1
                if is_chk:
                    visible_checked += 1

            if is_chk:
                total_checked += 1
                size_item = self.table.item(r, 2)
                if size_item and hasattr(size_item, "sort_val"):
                    checked_bytes += int(size_item.sort_val or 0)

        # Update Header Checkbox and Count String
        if visible_checked == 0:
            header_state = Qt.CheckState.Unchecked
        elif visible_checked == visible_total:
            header_state = Qt.CheckState.Checked
        else:
            header_state = Qt.CheckState.PartiallyChecked

        count_str = f"({visible_checked}/{visible_total})" if visible_total > 0 else ""
        self.custom_header.set_check_state_and_count(header_state, count_str)

        # Update Banner Summary
        size_text = format_size(checked_bytes)
        self.summary_lbl.setText(f"Selected: {total_checked} of {self.table.rowCount()} DLCs ({size_text})")

        # Update Download Button Text
        self.download_btn.setText(f"Download Selected ({total_checked}/{self.table.rowCount()})")
        self.download_btn.setEnabled(total_checked > 0)
        self.inject_btn.setEnabled(total_checked > 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Standard Edition Selector (Grouped by Platform)
    # ─────────────────────────────────────────────────────────────────────────

    def _show_edition_menu(self):
        """Displays standard platform & edition menu, identical to DepotSelectionDialog."""
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

        # Store packages if available
        if self._store_packages:
            menu.addSeparator()
            for pkg in self._store_packages:
                pkg_name = clean_package_name(pkg.get("name", "Package"))
                act_pkg = menu.addAction(pkg_name)
                act_pkg.triggered.connect(lambda checked, p=pkg, n=pkg_name: self._apply_store_package(p, n))

        pos = self.edition_button.mapToGlobal(QPoint(0, self.edition_button.height() + 2))
        menu.exec(pos)

    def _apply_edition_selection(self, edition: dict):
        """Apply the chosen edition to the DLC checkboxes, strictly avoiding base game depots."""
        multi_platform = bool(self._available_platforms) and len(self._available_platforms) > 1
        self.edition_button.setText(
            selection_policy.edition_button_text(edition, multi_platform=multi_platform)
        )
        self.edition_button.setToolTip(selection_policy.edition_button_tooltip(edition))

        target_depots = {str(d) for d in edition.get("depot_ids", set())}
        self.table.blockSignals(True)
        self._user_checked_items.clear()

        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item or item.flags() == Qt.ItemFlag.NoItemFlags:
                continue
            if bool(item.data(Qt.ItemDataRole.UserRole + 21)):
                # Entitlements without a depot are never part of an edition.
                item.setCheckState(Qt.CheckState.Unchecked)
                continue

            depot_ids = self._row_depot_ids(item)
            dlc_app = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")

            if any(d in target_depots for d in depot_ids) or dlc_app in target_depots:
                for did in depot_ids:
                    self._user_checked_items.add(did)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)

        self.table.blockSignals(False)
        self._update_summary()

    def _apply_store_package(self, pkg: dict, name: str):
        self.edition_button.setText(f"{name} ▾")
        apps = {str(a) for a in pkg.get("apps", [])}
        self.table.blockSignals(True)
        self._user_checked_items.clear()

        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item or item.flags() == Qt.ItemFlag.NoItemFlags:
                continue
            if bool(item.data(Qt.ItemDataRole.UserRole + 21)):
                item.setCheckState(Qt.CheckState.Unchecked)
                continue

            depot_ids = self._row_depot_ids(item)
            dlc_app = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")

            if not apps or dlc_app in apps or any(d in apps for d in depot_ids):
                for did in depot_ids:
                    self._user_checked_items.add(did)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)

        self.table.blockSignals(False)
        self._update_summary()

    def _preset_deselect_all(self):
        self.edition_button.setText("Deselect All ▾")
        self.table.blockSignals(True)
        self._user_checked_items.clear()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.flags() != Qt.ItemFlag.NoItemFlags:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_summary()

    # ─────────────────────────────────────────────────────────────────────────
    # Action Button Handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_inject_btn_clicked(self):
        """Injects all currently checked DLCs into DlcData in SLSsteam config.yaml."""
        checked_dlcs = self._collect_checked_dlc_dict()
        if not checked_dlcs:
            ThemedMessageBox.warning(
                self,
                "No DLCs Selected",
                "Please select at least one DLC to inject into SLSsteam config.",
            )
            return

        try:
            from utils.yaml.dlc import add_dlc_data_batch
            from utils.yaml_config_manager import get_user_config_path, remove_additional_app

            config_path = get_user_config_path()
            if not config_path or not config_path.exists():
                raise RuntimeError("SLSsteam config.yaml could not be found.")

            add_dlc_data_batch(config_path, self.app_id, checked_dlcs)
            remove_additional_app(config_path, self.app_id)
            get_settings().setValue(f"dlc_only_mode/{self.app_id}", True)

            self._injection_only = True
            self._config_written = True
            ThemedMessageBox.information(
                self,
                "DLC Data Injected",
                f"✓ Successfully injected {len(checked_dlcs)} DLC(s) into DlcData for '{self.game_name}'.\n\n"
                f"SLSsteam will now explicitly report these DLCs as owned.",
            )
            self.accept()
        except Exception as exc:
            logger.warning("[DlcDepotSelection] Could not inject DLC data: %s", exc, exc_info=True)
            ThemedMessageBox.warning(
                self,
                "Injection Failed",
                f"Failed writing DLC data to SLSsteam config.yaml:\n\n{exc}",
            )

    def _handle_download_btn_clicked(self):
        """
        Processes 'Download Selected':
        - Automatically injects any checked depotless DLCs into config.yaml
        - Queues checked downloadable depots for download
        - If only depotless DLCs were checked, completes cleanly with notification
        """
        downloadable_depots = self.get_selected_depots()
        checked_depotless = self._collect_checked_depotless_dict()

        if not downloadable_depots and not checked_depotless:
            ThemedMessageBox.warning(
                self,
                "Nothing Selected",
                "Please select at least one DLC to download or inject.",
            )
            return

        # If depotless DLCs were checked, inject them into config.yaml
        if checked_depotless:
            try:
                from utils.yaml.dlc import add_dlc_data_batch
                from utils.yaml_config_manager import get_user_config_path, remove_additional_app

                config_path = get_user_config_path()
                if config_path and config_path.exists():
                    add_dlc_data_batch(config_path, self.app_id, checked_depotless)
                    remove_additional_app(config_path, self.app_id)
                    self._config_written = True
            except Exception as e:
                logger.warning("[DlcDepotSelection] Auto-injecting depotless DLCs failed: %s", e)

        if not downloadable_depots:
            ThemedMessageBox.information(
                self,
                "DLC Mode Saved",
                f"DLC mode is enabled for '{self.game_name}'.\n\n"
                f"✓ Injected {len(checked_depotless)} depotless DLC(s) into SLSsteam config.\n"
                f"No downloads were queued because these DLCs have no downloadable files.",
            )
            self._injection_only = True

        self.accept()

    def _collect_checked_dlc_dict(self) -> Dict[str, str]:
        """Collects DLC AppID -> Name mapping for ALL currently checked items."""
        result: Dict[str, str] = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item or item.checkState() != Qt.CheckState.Checked:
                continue
            item_id = str(item.data(Qt.ItemDataRole.UserRole))
            dlc_app = str(item.data(Qt.ItemDataRole.UserRole + 1) or item_id)
            if dlc_app.isdigit() and dlc_app != self.app_id:
                raw_name = self._dlc_catalog.get(dlc_app) or self.table.item(r, 1).text()
                name = re.sub(r"\[(?:DLC|SOUNDTRACK|ARTBOOK|WALLPAPER|EN|BR|AR|DE|FR|JA|KO|RU|ES|ZH-CN|ZH-TW)\]\s*", "", raw_name).strip()
                result[dlc_app] = name
        return result

    def _collect_checked_depotless_dict(self) -> Dict[str, str]:
        """Collects DLC AppID -> Name mapping for checked DEPOTLESS items only."""
        result: Dict[str, str] = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item or item.checkState() != Qt.CheckState.Checked:
                continue
            is_depotless = bool(item.data(Qt.ItemDataRole.UserRole + 21))
            if is_depotless:
                item_id = str(item.data(Qt.ItemDataRole.UserRole))
                raw_name = self._dlc_catalog.get(item_id) or f"DLC {item_id}"
                name = re.sub(r"\[DLC\]\s*", "", raw_name).strip()
                result[item_id] = name
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Public Downstream Interface Matching DepotSelectionDialog
    # ─────────────────────────────────────────────────────────────────────────

    def accept(self):
        try:
            get_settings().setValue(f"dlc_only_mode/{self.app_id}", True)
        except Exception:
            logger.warning("Could not record DLC mode for %s", self.app_id, exc_info=True)
        super().accept()

    def get_dlc_catalog(self) -> Dict[str, str]:
        return dict(self._dlc_catalog)

    def get_depotless_dlc_catalog(self) -> Dict[str, str]:
        """DLCs without a depot, as ``{dlc_appid: name}``.

        When the user ticked some of them, only those ticks are
        returned: an unticked depotless DLC is a deliberate exclusion,
        not something to save behind their back.
        """
        ticked = self._collect_checked_depotless_dict()
        if ticked:
            return ticked
        return {dlc: self._dlc_catalog.get(dlc, "") for dlc in sorted(self._depotless_dlcs)}

    def get_config_written(self) -> bool:
        """True when this dialog already wrote DlcData for the ticks."""
        return self._config_written

    def get_selected_dlc_appids(self) -> List[str]:
        selected: List[str] = []
        for depot_id in self.get_selected_depots():
            meta = self.depots.get(depot_id) or {}
            if not isinstance(meta, dict):
                continue
            dlc_appid = str(meta.get("dlcappid") or "").strip()
            if dlc_appid.isdigit() and dlc_appid != self.app_id and dlc_appid not in selected:
                selected.append(dlc_appid)
        return selected

    def get_selected_depots(self) -> List[str]:
        if self._injection_only:
            return []
        result = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item or item.checkState() != Qt.CheckState.Checked:
                continue
            is_depotless = bool(item.data(Qt.ItemDataRole.UserRole + 21))
            if not is_depotless:
                for did in self._row_depot_ids(item):
                    if did and did not in result:
                        result.append(did)
        return result

    def get_selected_storage(self) -> str:
        return self.library_path

    def is_build_pinned(self) -> bool:
        return False

    def get_selected_build(self) -> str:
        return self.current_build_id

    def get_manifest_overrides(self) -> dict:
        return {}

    def _apply_build_selection(self, rec_bid, patch_depots):
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
