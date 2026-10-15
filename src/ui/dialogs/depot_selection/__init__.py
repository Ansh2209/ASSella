"""Depot selection dialog, split into testable pieces.

    rules.py     pure classification rules (platform, media, defaults) - no Qt
    items.py     table cell items that carry byte counts
    views.py     the checkbox delegate and the header with the select-all box
    dialog.py    DepotSelectionDialog: routing, state, accessors, Qt events
    _mixins/     the dialog's behaviour, one module per capability

Browse Files is not a mixin-shaped capability: ``manifest_dump.py`` prepares a
dump (Qt-free) and ``dump_runner.py`` runs it, and both the multi-depot and the
single-depot dialog use them.

Both ``ui.dialogs.depot_selection`` and the legacy facade
``ui.dialogs.depotselection`` export the full public API.
"""

from ui.dialogs.depot_selection.dialog import DepotSelectionDialog
from ui.dialogs.depot_selection.items import (
    ConfigTableWidgetItem,
    NumericTableWidgetItem,
)
from ui.dialogs.depot_selection.rules import (
    _depot_is_android,
    _depot_is_macos,
    _depot_matches_platform,
    format_size,
    get_smart_default_depots,
    is_bonus_or_media_depot,
)
from ui.dialogs.depot_selection.views import DepotCheckboxDelegate, DepotHeaderView

__all__ = [
    "ConfigTableWidgetItem",
    "DepotCheckboxDelegate",
    "DepotHeaderView",
    "DepotSelectionDialog",
    "NumericTableWidgetItem",
    "_depot_is_android",
    "_depot_is_macos",
    "_depot_matches_platform",
    "format_size",
    "get_smart_default_depots",
    "is_bonus_or_media_depot",
]
