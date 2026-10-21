"""
fix_preview_dialog.py - Dialog for previewing and selecting fix files before application.
Cleanly lists all files to be installed with checkboxes.
Files replacing existing game files are shown in orange text; new files in normal text.
"""

import logging
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
    QCheckBox,
)
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """Format byte size to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def show_fix_preview_dialog(
    parent,
    zip_path: str | Path,
    fix_title: str,
    game_name: str,
    game_path: Optional[str | Path] = None,
) -> Optional[Set[str]]:
    """
    Show a dialog previewing all files in a fix archive.
    User can select which files to apply.
    """
    dialog = FixPreviewDialog(parent, zip_path, fix_title, game_name, game_path=game_path)
    result = dialog.exec()
    
    if result == QDialog.DialogCode.Accepted:
        return dialog.selected_files
    return None


class _FileRowFrame(QFrame):
    """Interactive row frame that toggles checkbox on row click."""

    def mousePressEvent(self, event):
        if hasattr(self, "_cb") and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            child = self.childAt(pos)
            if child != self._cb:
                self._cb.setChecked(not self._cb.isChecked())
        super().mousePressEvent(event)


class FixPreviewDialog(QDialog):
    """Sleek dialog for previewing and selecting files to install from a fix archive."""

    def __init__(
        self,
        parent,
        zip_path: str | Path,
        fix_title: str,
        game_name: str,
        game_path: Optional[str | Path] = None,
    ):
        super().__init__(parent)
        self.zip_path = Path(zip_path)
        self.fix_title = fix_title
        self.game_name = game_name
        self.game_path = Path(game_path) if game_path else None
        self.selected_files: Set[str] = set()
        self._all_files: List[Dict[str, Any]] = []
        self._list_widgets: List[QFrame] = []

        self.setWindowTitle(f"Apply Fix: {fix_title}")
        self.resize(680, 520)
        self.setMinimumSize(540, 420)

        self._setup_ui()
        self._load_files()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = install_themed_frame(
            self,
            title=f"Apply Fix: {self.fix_title}",
            margins=(20, 16, 20, 18),
            spacing=14,
        )

        # Header section
        header_vbox = QVBoxLayout()
        header_vbox.setSpacing(4)

        header_top = QHBoxLayout()
        header_top.setSpacing(8)

        title_lbl = QLabel(f"🔧 {self.fix_title}")
        title_lbl.setStyleSheet("color: #FFFFFF; font-size: 13pt; font-weight: bold;")
        header_top.addWidget(title_lbl)

        game_lbl = QLabel(f"for {self.game_name}")
        game_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 9.5pt;")
        header_top.addWidget(game_lbl)
        header_top.addStretch()

        header_vbox.addLayout(header_top)

        # Legend explanation
        legend_lbl = QLabel(
            '<span style="color: #fb923c; font-weight: 600;">●</span> '
            '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">Orange files replace existing game files</span>'
            '&nbsp;&nbsp;&nbsp;&nbsp;'
            '<span style="color: #e2e8f0; font-weight: 600;">●</span> '
            '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">White files are newly added</span>'
        )
        legend_lbl.setStyleSheet("margin-top: 2px;")
        header_vbox.addWidget(legend_lbl)

        layout.addLayout(header_vbox)

        # Controls bar (Select / Deselect)
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
                font-weight: 500;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.25);
            }
        """)
        self.select_all_btn.clicked.connect(self._on_select_all)
        controls.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deselect_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
                font-weight: 500;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.25);
            }
        """)
        self.deselect_all_btn.clicked.connect(self._on_deselect_all)
        controls.addWidget(self.deselect_all_btn)

        controls.addStretch()
        layout.addLayout(controls)

        # File list container frame
        list_container = QFrame()
        list_container.setStyleSheet("""
            QFrame {
                background: rgba(0, 0, 0, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        list_container_lay = QVBoxLayout(list_container)
        list_container_lay.setContentsMargins(6, 6, 6, 6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        self.file_list_layout = QVBoxLayout(scroll_widget)
        self.file_list_layout.setContentsMargins(2, 2, 2, 2)
        self.file_list_layout.setSpacing(3)
        self.file_list_layout.addStretch()

        scroll.setWidget(scroll_widget)
        list_container_lay.addWidget(scroll)
        layout.addWidget(list_container, 1)

        # Bottom section: status on left, cancel / apply on right
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
        bottom_row.addWidget(self.status_label, 1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 6px;
                color: #e5e7eb;
                font-size: 9pt;
                font-weight: 500;
                padding: 8px 18px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                border-color: rgba(255, 255, 255, 0.28);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(self.cancel_btn)

        self.apply_btn = QPushButton("Apply Selected")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background: #3b82f6;
                border: 1px solid #2563eb;
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background: #2563eb;
                border-color: #1d4ed8;
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.08);
                border-color: rgba(255, 255, 255, 0.12);
                color: rgba(255, 255, 255, 0.35);
            }
        """)
        self.apply_btn.clicked.connect(self._on_apply)
        bottom_row.addWidget(self.apply_btn)

        layout.addLayout(bottom_row)

    def _load_files(self):
        """Load and display all files from the ZIP archive."""
        try:
            with zipfile.ZipFile(self.zip_path, "r") as zf:
                entries = zf.infolist()

            self._all_files = []
            for member in entries:
                if member.is_dir():
                    continue

                size = member.file_size
                action = "added"
                if self.game_path and (self.game_path / member.filename).exists():
                    action = "modified"

                self._all_files.append({
                    "relative_path": member.filename,
                    "size": size,
                    "action": action,
                })

            self._render_file_list()
            self._update_status()

        except Exception as e:
            logger.error(f"[FixPreviewDialog] Failed to load ZIP: {e}")
            ThemedMessageBox.critical(self, "Error", f"Failed to read fix archive:\n{e}")

    def set_game_path(self, game_path: str | Path):
        """Set the game path to determine modified vs added files."""
        self.game_path = Path(game_path) if game_path else None
        if not self.game_path or not self.game_path.is_dir():
            return

        for file_info in self._all_files:
            file_path = self.game_path / file_info["relative_path"]
            file_info["action"] = "modified" if file_path.exists() else "added"

        self._render_file_list()
        self._update_status()

    def _render_file_list(self):
        """Render the file list in the dialog."""
        while self.file_list_layout.count() > 1:
            item = self.file_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._list_widgets = []

        for file_info in self._all_files:
            widget = self._create_file_widget(file_info)
            self.file_list_layout.insertWidget(len(self._list_widgets), widget)
            self._list_widgets.append(widget)

    def _create_file_widget(self, file_info: Dict[str, Any]) -> QWidget:
        """Create a clean, polished row for a single file entry."""
        container = _FileRowFrame()
        container.setCursor(Qt.CursorShape.PointingHandCursor)
        container._file_info = file_info
        container.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid transparent;
                border-radius: 6px;
            }
            QFrame:hover {
                background: rgba(255, 255, 255, 0.06);
                border-color: rgba(255, 255, 255, 0.1);
            }
        """)

        lay = QHBoxLayout(container)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(12)

        # Checkbox
        cb = QCheckBox()
        cb.setChecked(True)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.stateChanged.connect(self._update_status)
        container._cb = cb
        lay.addWidget(cb)

        # File path label
        action = file_info.get("action", "added")
        rel_path = file_info["relative_path"]
        path_lbl = QLabel(rel_path)
        path_lbl.setWordWrap(True)

        if action == "modified":
            # Orange text for modified / replaced files
            path_lbl.setStyleSheet("""
                color: #fb923c;
                font-size: 9pt;
                font-weight: 500;
                background: transparent;
                border: none;
            """)
            path_lbl.setToolTip(f"Replaces existing file: {rel_path}")
        else:
            # Normal clean text for newly added files
            path_lbl.setStyleSheet("""
                color: #f3f4f6;
                font-size: 9pt;
                font-weight: 400;
                background: transparent;
                border: none;
            """)
            path_lbl.setToolTip(f"New file: {rel_path}")

        lay.addWidget(path_lbl, 1)

        # File size label
        size_lbl = QLabel(_format_size(file_info["size"]))
        size_lbl.setStyleSheet("""
            color: rgba(255, 255, 255, 0.45);
            font-size: 8.5pt;
            background: transparent;
            border: none;
        """)
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(size_lbl)

        return container

    def _update_status(self):
        """Update the status label and apply button state."""
        checked = sum(1 for w in self._list_widgets if getattr(w, "_cb", None) and w._cb.isChecked())
        total = len(self._list_widgets)
        mod_selected = sum(
            1 for w in self._list_widgets
            if getattr(w, "_cb", None) and w._cb.isChecked() and getattr(w, "_file_info", {}).get("action") == "modified"
        )
        add_selected = checked - mod_selected

        if total == 0:
            self.status_label.setText("No files in archive")
            self.apply_btn.setEnabled(False)
            return

        detail_parts = []
        if mod_selected > 0:
            detail_parts.append(f"{mod_selected} to replace")
        if add_selected > 0:
            detail_parts.append(f"{add_selected} new")

        if detail_parts:
            detail_str = f" ({', '.join(detail_parts)})"
        else:
            detail_str = ""

        self.status_label.setText(f"{checked} of {total} files selected{detail_str}")
        self.apply_btn.setEnabled(checked > 0)

    def _on_select_all(self):
        """Select all visible files."""
        for w in self._list_widgets:
            if not w.isHidden() and hasattr(w, "_cb"):
                w._cb.setChecked(True)
        self._update_status()

    def _on_deselect_all(self):
        """Deselect all visible files."""
        for w in self._list_widgets:
            if not w.isHidden() and hasattr(w, "_cb"):
                w._cb.setChecked(False)
        self._update_status()

    def _filter_files(self, action_filter: Optional[str]):
        """Show only files of a specific action type, or all if None."""
        for w in self._list_widgets:
            file_info = getattr(w, "_file_info", None)
            if not file_info:
                continue
            if action_filter is None or file_info.get("action") == action_filter:
                w.setVisible(True)
            else:
                w.setVisible(False)
        self._update_status()

    def _on_apply(self):
        """Collect selected files and accept dialog."""
        self.selected_files = set()
        for w in self._list_widgets:
            if hasattr(w, "_cb") and w._cb.isChecked() and hasattr(w, "_file_info"):
                self.selected_files.add(w._file_info["relative_path"])

        self.accept()
