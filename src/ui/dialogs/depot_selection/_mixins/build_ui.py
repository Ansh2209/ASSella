"""Static widget construction: the header (thumbnail, title, branch and
Build button), the depot table, the platform / DLC-only / Browse Files row
and the bottom OK / Cancel bar."""

import os

from PyQt6.QtCore import Qt, QSize

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QComboBox,
    QSizePolicy,
)

from utils.settings import get_settings

from ui.dialogs.depot_selection.views import (
    DepotCheckboxDelegate,
    DepotHeaderView,
)


class BuildUIMixin:
    """Static widget construction for the header, table, action row and bottom bar."""

    def _build_header_section(self, layout: QVBoxLayout) -> None:
        """Build the thumbnail, game title, branch selector and Build button."""
        # Header Layout
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(15, 10, 15, 10)
        header_layout.setSpacing(15)

        # Thumbnail Label
        self.header_label = QLabel()
        self.header_label.setFixedSize(120, 56)
        self.header_label.setScaledContents(True)
        self.header_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.04); border-radius: 6px;")
        header_layout.addWidget(self.header_label)

        # Title / Info Layout
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        display_title = (
            f"{self.game_name} ({self.app_id})"
            if self.app_id and str(self.app_id) not in ("0", "unknown", "None", "") and f"({self.app_id})" not in str(self.game_name)
            else self.game_name
        )
        self.title_label = QLabel(display_title)
        self.title_label.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        title_layout.addWidget(self.title_label)

        # --- Branch & Builds Row ---
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        controls_row.setContentsMargins(0, 4, 0, 0)

        # Check saved branch if not explicitly given
        if not self.branch or self.branch == "public":
            try:
                from utils.settings import get_settings
                saved_b = get_settings().value(f"selected_branch/{self.app_id}", "", type=str)
                if saved_b:
                    self.branch = saved_b
            except Exception:
                pass

        branch_list = sorted(self.branches.keys(), key=lambda k: (0 if k == "public" else 1, k)) if self.branches else ["public"]
        if "public" not in branch_list:
            branch_list.insert(0, "public")
        if self.branch and self.branch not in branch_list:
            branch_list.append(self.branch)

        has_other_branches = any(b.lower() != "public" for b in branch_list)

        if has_other_branches:
            branch_lbl = QLabel("Branch:")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            controls_row.addWidget(branch_lbl)

            self.branch_combo = QComboBox()
            self.branch_combo.setFixedHeight(28)
            self.branch_combo.setMinimumWidth(120)
            self.branch_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    padding: 2px 12px;
                    font-size: 8.5pt;
                    font-weight: 600;
                }}
                QComboBox:hover {{
                    border-color: rgba(255, 255, 255, 0.35);
                    background-color: rgba(255, 255, 255, 0.12);
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 18px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1c23;
                    color: #FFFFFF;
                    selection-background-color: transparent;
                    selection-color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            self.branch_combo.addItems(branch_list)
            self.branch_combo.currentTextChanged.connect(self._on_branch_changed)
            if self.branch in branch_list:
                self.branch_combo.setCurrentText(self.branch)
            controls_row.addWidget(self.branch_combo)
        else:
            self.branch_combo = None
            branch_lbl = QLabel(f"Branch: <b style='color: #FFFFFF;'>{self.branch or 'public'}</b>")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            controls_row.addWidget(branch_lbl)

        controls_row.addSpacing(6)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"Build: {display_bid}")
        self.builds_btn.setFixedHeight(28)
        self.builds_btn.setMinimumWidth(140)
        self.builds_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_build_btn_style()
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        controls_row.addWidget(self.builds_btn)

        self.show_hidden_chk = None
        self.smart_select_btn = None

        if self.branch and self.branch != "public":
            self._on_branch_changed(self.branch)

        controls_row.addStretch()
        title_layout.addLayout(controls_row)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Resolve missing depots info if needed (table will show them at bottom in greyscale)
        if self.missing_hubcap_depots:
            self._resolve_missing_depots_info()
            self._check_missing_contents_async()

        layout.addSpacing(5)

        self._fetch_header_image(self.app_id)

    def _build_depot_table(
        self, content_widget: QVBoxLayout, accent_r: int, accent_g: int, accent_b: int,
    ) -> None:
        """Create the depot table, its header view and the checkbox delegate."""
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels(["Select", "Configuration", "Size"])
        self.table_widget.setIconSize(QSize(76, 16))
        self.table_widget.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table_widget.horizontalHeader().setSectionsClickable(True)
        self.table_widget.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_widget.setShowGrid(False)
        self.table_widget.setAlternatingRowColors(True)

        self.table_widget.setStyleSheet(f"""
            QTableWidget {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.047);
                border-radius: 8px;
                gridline-color: transparent;
                outline: 0;
                color: #FFFFFF;
                selection-background-color: transparent;
                selection-color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.02);
                border: none;
            }}
            QTableWidget::item:hover {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.06);
                border: none;
            }}
            QTableWidget::item:selected {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.16) !important;
                border: none !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #FFFFFF;
                padding: 6px 10px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            {"" if (self._settings and self._settings.value("material_preset", "ocean", type=str) == "halloween" or str(self.accent_color).lower() in ("#ffb77d", "#ff7518") or (self._settings and self._settings.value("theme_checkbox_unlit", "", type=str) and os.path.exists(self._settings.value("theme_checkbox_unlit", "", type=str)))) else f"""
            QTableWidget::indicator, QTableView::indicator {{
                width: 14px;
                height: 14px;
                background: transparent;
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.47);
                border-radius: 4px;
            }}
            QTableWidget::indicator:unchecked, QTableView::indicator:unchecked {{
                background-color: transparent;
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.47);
            }}
            QTableWidget::indicator:unchecked:hover, QTableView::indicator:unchecked:hover {{
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 1.0);
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.078);
            }}
            QTableWidget::indicator:checked, QTableView::indicator:checked,
            QTableWidget::indicator:checked:hover, QTableView::indicator:checked:hover,
            QTableWidget::indicator:checked:selected, QTableView::indicator:checked:selected {{
                background-color: {self.accent_color} !important;
                border: 1.5px solid {self.accent_color} !important;
            }}
            """}
        """)

        self.header_view = DepotHeaderView(self)
        self.table_widget.setHorizontalHeader(self.header_view)
        self.header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(0, 130)
        self.header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.header_view.sectionClicked.connect(self._on_header_section_clicked)

        self.table_widget.setItemDelegateForColumn(0, DepotCheckboxDelegate(self))

        self._hidden_depots_expanded = False
        self._populate_table()

        content_widget.addWidget(self.table_widget)
 
        self.table_widget.cellClicked.connect(self.on_depot_cell_clicked)

    def _add_depot_rows(self, content_widget: QVBoxLayout) -> None:
        """Add the platform edition selector and Browse Files row.

        Skipped for a single-depot app, where there is no choice to make.
        """
        if not self.is_single_depot:
            # Platform Edition Selector + Browse Files sharing 50/50 space identically
            button_layout = QHBoxLayout()
            button_layout.setSpacing(10)
            button_layout.setContentsMargins(0, 0, 0, 0)

            btn_style = f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.07);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 4px 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.14);
                    border: 1px solid {self.accent_color};
                    color: #FFFFFF;
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 0.20);
                }}
            """

            self.edition_button = QPushButton("Complete ▾")
            self.edition_button.setFixedHeight(28)
            self.edition_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.edition_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edition_button.setStyleSheet(btn_style)
            self.edition_button.setToolTip("Select game edition and platform (Complete, Deluxe, Standard)")
            self.edition_button.clicked.connect(self._show_edition_menu)
            button_layout.addWidget(self.edition_button, 1)

            from utils.dlc_helpers import has_game_dlcs
            has_dlc = has_game_dlcs(str(self.app_id), self.depots)

            self._dlc_only_btn = QPushButton("DLC Only")
            self._dlc_only_btn.setFixedHeight(28)
            self._dlc_only_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._dlc_only_btn.setCheckable(True)

            if getattr(self, "lock_dlc_only", False):
                self._dlc_only_btn.setChecked(True)
                self._dlc_only_btn.setEnabled(False)
                self._dlc_only_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
                self._dlc_only_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.04) !important;
                        color: rgba(255, 255, 255, 0.45) !important;
                        border: 1px solid rgba(255, 255, 255, 0.08) !important;
                        border-radius: 6px;
                        padding: 4px 12px;
                        font-size: 8.5pt;
                        font-weight: 500;
                    }
                """)
                self._dlc_only_btn.setToolTip("DLC-Only mode is locked for this installation.")
                self._dlc_only_mode = True
            elif not has_dlc:
                self._dlc_only_btn.setEnabled(False)
                self._dlc_only_btn.setChecked(False)
                self._dlc_only_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.03) !important;
                        color: rgba(255, 255, 255, 0.25) !important;
                        border: 1px solid rgba(255, 255, 255, 0.06) !important;
                        border-radius: 6px;
                        padding: 4px 12px;
                        font-size: 8.5pt;
                    }
                """)
                self._dlc_only_btn.setToolTip("DLC-Only mode is unavailable because this title has no DLC.")
                self._dlc_only_mode = False
            else:
                self._dlc_only_btn.setChecked(self._dlc_only_mode)
                self._refresh_dlc_only_style()
                self._dlc_only_btn.toggled.connect(self._on_dlc_only_toggled)

            button_layout.addWidget(self._dlc_only_btn, 1)

            browse_files_button = QPushButton("Browse Files...")
            browse_files_button.setFixedHeight(28)
            browse_files_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            browse_files_button.setCursor(Qt.CursorShape.PointingHandCursor)
            browse_files_button.setStyleSheet(btn_style)
            browse_files_button.setToolTip("Customize downloaded files within the selected depots")
            browse_files_button.clicked.connect(self._on_select_files_clicked)
            button_layout.addWidget(browse_files_button, 1)
            content_widget.addLayout(button_layout)

            from utils.edition_helpers import (
                get_available_platforms,
                resolve_multi_platform_editions,
            )
            self._available_platforms = get_available_platforms(self.depots)
            self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)
            self._update_table_headers()

    def _build_bottom_bar(self, content_widget: QVBoxLayout) -> None:
        """Build the storage picker plus OK and Cancel."""
        # Bottom row: Storage Selection (Left, Expanding) + OK / Cancel (Right)
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 4, 0, 0)
        bottom_bar.setSpacing(6)

        if self.show_storage:
            self._setup_storage_buttons(bottom_bar)
        else:
            bottom_bar.addStretch(1)

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("ok_button")
        ok_btn.setFixedHeight(28)
        ok_btn.setMinimumWidth(80)
        ok_btn.clicked.connect(self.accept)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #111318;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
            }}
        """)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancel_button")
        cancel_btn.setFixedHeight(28)
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                color: #FFFFFF;
            }}
        """)

        bottom_bar.addWidget(ok_btn)
        bottom_bar.addWidget(cancel_btn)

        content_widget.addLayout(bottom_bar)
