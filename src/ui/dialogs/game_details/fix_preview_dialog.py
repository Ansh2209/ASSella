"""
fix_preview_dialog.py - Dialog for previewing and selecting fix files before application.
Shows all files in the fix archive with their type (modified/added) and size.
Allows user to select which files to apply.
"""

import logging
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QFrame,
    QScrollArea,
    QWidget,
    QCheckBox,
    QMessageBox,
)

logger = logging.getLogger(__name__)


class _FixFileItem(QListWidgetItem):
    """Custom list item that stores file metadata."""
    def __init__(self, relative_path: str, action: str, size: int, parent=None):
        super().__init__(parent)
        self._relative_path = relative_path
        self._action = action
        self._size = size
        self.setFlags(self.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.setCheckState(Qt.CheckState.Checked)

    @property
    def relative_path(self) -> str:
        return self._relative_path

    @property
    def action(self) -> str:
        return self._action

    @property
    def size(self) -> int:
        return self._size


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
    
    Args:
        parent: Parent widget
        zip_path: Path to the fix ZIP file
        fix_title: Title of the fix
        game_name: Name of the game
        game_path: Optional path to game directory
    
    Returns:
        Set of relative paths to apply, or None if cancelled
    """
    dialog = FixPreviewDialog(parent, zip_path, fix_title, game_name, game_path=game_path)
    result = dialog.exec()
    
    if result == QDialog.DialogCode.Accepted:
        return dialog.selected_files
    return None


class FixPreviewDialog(QDialog):
    """Dialog for previewing and selecting fix archive files."""

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
        
        self.setWindowTitle(f"Apply Fix: {fix_title}")
        self.setMinimumSize(600, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMaximizeButtonHint)
        
        self._setup_ui()
        self._load_files()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title_lbl = QLabel(f"🔧 {self.fix_title}")
        title_lbl.setStyleSheet("color: #FFFFFF; font-size: 12pt; font-weight: bold;")
        header.addWidget(title_lbl)
        
        game_lbl = QLabel(f"for {self.game_name}")
        game_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 9pt;")
        header.addWidget(game_lbl)
        header.addStretch()
        layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        divider.setStyleSheet("color: rgba(255, 255, 255, 0.1);")
        layout.addWidget(divider)

        # Controls
        controls = QHBoxLayout()
        
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #d1d5db;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """)
        self.select_all_btn.clicked.connect(self._on_select_all)
        controls.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deselect_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #d1d5db;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """)
        self.deselect_all_btn.clicked.connect(self._on_deselect_all)
        controls.addWidget(self.deselect_all_btn)

        self.show_all_btn = QPushButton("Show All")
        self.show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #d1d5db;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """)
        self.show_all_btn.clicked.connect(lambda: self._filter_files(None))
        controls.addWidget(self.show_all_btn)

        self.only_modified_btn = QPushButton("Show Modified Only")
        self.only_modified_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.only_modified_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #d1d5db;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """)
        self.only_modified_btn.clicked.connect(lambda: self._filter_files("modified"))
        controls.addWidget(self.only_modified_btn)

        self.only_new_btn = QPushButton("Show New Only")
        self.only_new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.only_new_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #d1d5db;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """)
        self.only_new_btn.clicked.connect(lambda: self._filter_files("added"))
        controls.addWidget(self.only_new_btn)

        controls.addStretch()
        layout.addLayout(controls)

        # File list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_widget = QWidget()
        self.file_list_layout = QVBoxLayout(scroll_widget)
        self.file_list_layout.setContentsMargins(0, 0, 0, 0)
        self.file_list_layout.setSpacing(2)
        scroll.setWidget(scroll_widget)

        layout.addWidget(scroll)

        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8pt;")
        layout.addWidget(self.status_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #d1d5db;
                font-size: 9pt;
                padding: 8px 16px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.apply_btn = QPushButton("Apply Selected")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background: #3b82f6;
                border: 1px solid #2563eb;
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
                padding: 8px 16px;
            }
            QPushButton:hover { background: #2563eb; }
            QPushButton:disabled { background: rgba(255, 255, 255, 0.1); color: rgba(255, 255, 255, 0.4); }
        """)
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)

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
            QMessageBox.critical(self, "Error", f"Failed to read fix archive:\n{e}")

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
        # Clear existing items
        while self.file_list_layout.count():
            item = self.file_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._list_widgets = []

        for file_info in self._all_files:
            widget = self._create_file_widget(file_info)
            self.file_list_layout.addWidget(widget)
            self._list_widgets.append(widget)

    def _create_file_widget(self, file_info: Dict[str, Any]) -> QWidget:
        """Create a widget for a single file entry."""
        container = QFrame()
        container._file_info = file_info
        container.setStyleSheet("""
            QFrame {
                background: transparent;
                border: none;
            }
        """)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(8)

        # Checkbox
        cb = QCheckBox()
        cb.setChecked(True)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.stateChanged.connect(self._update_status)
        container._cb = cb
        lay.addWidget(cb)

        # Type badge
        action = file_info["action"]
        if action == "modified":
            badge = QLabel("MOD")
            badge.setStyleSheet("""
                QLabel {
                    background: rgba(251, 191, 36, 0.15);
                    color: #fbbf24;
                    border: 1px solid rgba(251, 191, 36, 0.4);
                    border-radius: 3px;
                    font-size: 7pt;
                    font-weight: bold;
                    padding: 2px 4px;
                }
            """)
        else:
            badge = QLabel("NEW")
            badge.setStyleSheet("""
                QLabel {
                    background: rgba(34, 197, 94, 0.15);
                    color: #4ade80;
                    border: 1px solid rgba(34, 197, 94, 0.4);
                    border-radius: 3px;
                    font-size: 7pt;
                    font-weight: bold;
                    padding: 2px 4px;
                }
            """)
        lay.addWidget(badge)

        # File path
        path_lbl = QLabel(file_info["relative_path"])
        path_lbl.setStyleSheet("color: #d1d5db; font-size: 8.5pt;")
        path_lbl.setWordWrap(True)
        lay.addWidget(path_lbl, 1)

        # File size
        size_lbl = QLabel(_format_size(file_info["size"]))
        size_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8pt;")
        lay.addWidget(size_lbl)

        return container

    def _update_status(self):
        """Update the status label with selection info."""
        checked = sum(1 for w in self._list_widgets if getattr(w, "_cb", None) and w._cb.isChecked())
        total = len(self._list_widgets)
        self.status_label.setText(f"{checked} of {total} files selected")

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
