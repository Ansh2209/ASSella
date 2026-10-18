#!/usr/bin/env python3
"""Test how DLC mode is shown to the user.

Two pieces are covered here:

* The selector lists the DLCs that own no depot at all. Ship 1 records those by
  name when the user saves DLC mode, so they must be visible before the
  confirmation, not discovered afterwards. They are informational rows: they
  cannot be ticked and they must not count towards "Selected: X of Y".
* The library card shows the mode as an accented chip instead of the plain
  white " [DLC MODE]" text that used to ride along inside the game name.

    python3 scripts/test_dlc_mode_ui.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


DEPOTS = {
    "111": {"dlcappid": "111", "name": "DL One", "desc": "DL One", "size": 5000},
    "222": {"dlcappid": "222", "name": "DL Two", "desc": "DL Two", "size": 10},
}
CATALOG = {"111": "DL One", "222": "DL Two", "333": "Name Only"}


def main() -> int:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from ui.dialogs.dlc_depot_selection import DlcDepotSelectionDialog
    from ui.dialogs.library.widgets import (
        DLC_MODE_SUFFIX,
        GameItemWidget,
        format_game_display_name,
        split_dlc_mode,
    )
    from utils.settings import get_settings

    # Keep the reference: a temporary QApplication is collected immediately and
    # Qt then refuses to construct widgets ("Must construct a QApplication").
    app = QApplication.instance() or QApplication([])
    settings = get_settings()
    probe_appid = "990001"

    # ------------------------------------------------------------------ marker
    print("=== the mode marker is split off the name ===")
    check(split_dlc_mode(f"Probe Game{DLC_MODE_SUFFIX}") == ("Probe Game", True),
          "marker detected and stripped")
    check(split_dlc_mode("Probe Game") == ("Probe Game", False),
          "a plain name is left alone")
    check(split_dlc_mode(DLC_MODE_SUFFIX.strip()) == (DLC_MODE_SUFFIX.strip(), False),
          "the bare marker is not a name suffix")
    check(split_dlc_mode(f"Probe {DLC_MODE_SUFFIX.strip()} Game") ==
          (f"Probe {DLC_MODE_SUFFIX.strip()} Game", False),
          "a marker in the middle does not count")

    # ---------------------------------------------------------------- selector
    print("=== DLCs without a depot are listed but not selectable ===")
    dialog = DlcDepotSelectionDialog(
        probe_appid, "Probe Game", DEPOTS, dlc_catalog=CATALOG
    )
    try:
        rows = dialog.table.rowCount()
        check(rows == 3, f"two depot rows plus one name-only row (got {rows})")

        info_rows = [
            r
            for r in range(rows)
            if dialog.table.item(r, 0).flags() == Qt.ItemFlag.NoItemFlags
        ]
        check(len(info_rows) == 1, "exactly one informational row")
        info = info_rows[0]
        check(dialog.table.item(info, 1).text().endswith("(no depot)"),
              "the row says it has no depot")
        check("Name Only" in dialog.table.item(info, 1).text(),
              "the catalogue name is shown")
        check(dialog.table.item(info, 0).text() == "—",
              "the id column does not pretend to be a depot")
        check(dialog.summary_lbl.text().startswith("Selected: 2 of 2"),
              f"informational rows are not counted (got {dialog.summary_lbl.text()!r})")
        check(dialog.get_depotless_dlc_catalog() == {"333": "Name Only"},
              "the depotless catalogue is available to the caller")
        check(dialog.get_selected_dlc_appids() == ["111", "222"],
              "checked depots map back to their DLC app ids")

        dialog._toggle_select_all()
        check(dialog.get_selected_depots() == [], "deselect-all leaves nothing ticked")
        check(dialog.download_btn.text() == "Save DLC Mode (1 DLC Without Depot)",
              f"config-only stays reachable (got {dialog.download_btn.text()!r})")
        check(dialog.download_btn.isEnabled(), "the save button is enabled")
    finally:
        dialog.deleteLater()

    # -------------------------------------------------------------------- card
    print("=== the library card shows an accented chip ===")
    settings.setValue(f"dlc_only_mode/{probe_appid}", True)
    try:
        display = format_game_display_name(
            {"appid": probe_appid, "game_name": "Probe Game"}
        )
        check(display == f"Probe Game{DLC_MODE_SUFFIX}",
              f"the shared name still carries the marker (got {display!r})")

        card = GameItemWidget(
            {"appid": probe_appid, "game_name": "Probe Game"},
            "1 GiB",
            "#a1c9fd",
            "#111318",
        )
        check(card.dlc_mode_badge is not None, "a DLC-mode card gets a badge")
        check(card.name_label.text() == "Probe Game",
              f"the marker is not part of the elided name (got {card.name_label.text()!r})")
        check("DLC MODE" in (card.dlc_mode_badge.text() if card.dlc_mode_badge else ""),
              "the badge says DLC MODE")
        check("#a1c9fd" in (card.dlc_mode_badge.styleSheet() if card.dlc_mode_badge else ""),
              "the badge uses the accent colour")
        card.deleteLater()
    finally:
        settings.remove(f"dlc_only_mode/{probe_appid}")

    plain = GameItemWidget({"appid": "990002", "game_name": "Plain Game"},
                           "1 GiB", "#a1c9fd", "#111318")
    check(plain.dlc_mode_badge is None, "a normal card gets no badge")
    plain.deleteLater()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
