#!/usr/bin/env python3
"""Tests for the depot dialog's Hubcap key states.

A depot that Steam lists but that the local Hubcap bundle does not carry can
be in three states, and they must not be collapsed into one:

    available - Hubcap holds a decryption key for it  -> "[Key Ready]"
    pending   - Hubcap knows it, the key is not out   -> "[No Key]"
    unknown   - Hubcap has not indexed it at all      -> "[Missing]"

The dialog is shared between the ASSela-managed and the Steam-managed (AT0-M)
library, so these tags are what the user sees in both.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem  # noqa: E402

from ui.dialogs.depot_selection._mixins.metadata import DepotMetadataMixin  # noqa: E402
from ui.dialogs.depot_selection._mixins.table import DepotTableMixin  # noqa: E402


class _Panel(DepotMetadataMixin, DepotTableMixin):
    """The parts of the dialog the tag rendering touches."""

    def __init__(self):
        self.app_id = "12345"
        self.branch = "public"
        self.depots = {}
        self.missing_hubcap_depots = ["111", "222", "333"]
        self.missing_depots_info = {
            "111": {"name": "Windows content"},
            "222": {"name": "Linux content"},
            "333": {"name": "macOS content"},
        }
        self.selected_depots = []
        self._recovered_depots_map = {}
        self._is_checking_missing_contents = True

        self.table_widget = QTableWidget(3, 4)
        self.table_widget.setHorizontalHeaderLabels(["ID", "Config", "Size", "Flags"])
        for row, did in enumerate(self.missing_hubcap_depots):
            item = QTableWidgetItem(did)
            item.setData(Qt.ItemDataRole.UserRole, did)
            item.setData(Qt.ItemDataRole.UserRole + 2, "missing")
            self.table_widget.setItem(row, 0, item)
            self.table_widget.setItem(row, 1, QTableWidgetItem(f"[Missing]  Depot {did}"))
            self.table_widget.setItem(row, 2, QTableWidgetItem("0 B"))
            self.table_widget.setItem(row, 3, QTableWidgetItem(""))


class TestDepotDialogKeyStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tags(self, panel):
        return [
            panel.table_widget.item(row, 1).text().split("  ")[0]
            for row in range(panel.table_widget.rowCount())
        ]

    def test_a_depot_with_a_key_is_marked_as_key_ready(self):
        panel = _Panel()
        panel._on_missing_contents_check_finished(
            {
                "recovered": {},
                "key_states": {"111": "available", "222": "pending", "333": "unknown"},
            }
        )
        self.assertEqual(self._tags(panel), ["[Key Ready]", "[No Key]", "[Missing]"])

    def test_the_tooltip_explains_the_state(self):
        panel = _Panel()
        panel._on_missing_contents_check_finished(
            {"recovered": {}, "key_states": {"111": "available", "222": "pending", "333": "unknown"}}
        )
        tips = [
            panel.table_widget.item(row, 1).toolTip()
            for row in range(panel.table_widget.rowCount())
        ]
        self.assertIn("decryption key on Hubcap", tips[0])
        self.assertIn("has not published", tips[1])
        self.assertIn("does not know this depot", tips[2])

    def test_an_old_payload_still_renders(self):
        """The signal used to carry the recovered dict alone."""
        panel = _Panel()
        panel._on_missing_contents_check_finished({})
        self.assertEqual(self._tags(panel), ["[Missing]", "[Missing]", "[Missing]"])

    def test_a_key_ready_depot_is_not_reported_as_missing(self):
        panel = _Panel()
        panel._on_missing_contents_check_finished(
            {"recovered": {}, "key_states": {"111": "available"}}
        )
        self.assertNotEqual(self._tags(panel)[0], "[Missing]")

    def test_the_stale_tag_is_stripped_when_the_row_is_rebuilt(self):
        """A re-render must not end up as '[Key Ready]  [Key Ready]  ...'."""
        panel = _Panel()
        text = panel._build_config_text(
            "111", {"desc": "[Key Ready]  Windows content - Depot 111"}
        )
        self.assertNotIn("[Key Ready]", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
