"""Registration of the checked depots / DLC entitlements into AT0-M mode."""

import logging

logger = logging.getLogger(__name__)


class MigrationMixin:
    """Move the game to AT0-M (Steam Native) plugin mode."""

    def _handle_move_to_atom_clicked(self):
        """Registers checked depots / DLC entitlements into SLSsteam AT0-M plugin mode."""
        selected_depots = self.get_selected_depots()
        checked_dlcs = self.get_selected_dlc_data()

        if not selected_depots and not checked_dlcs:
            from ui.dialogs.themed_message_box import ThemedMessageBox

            ThemedMessageBox.warning(
                self,
                "Nothing Selected",
                "Please select at least one depot or DLC to register into AT0-M mode.",
            )
            return

        is_dlc_only = bool(getattr(self, "_dlc_only_mode", False))

        from ui.dialogs.themed_message_box import ThemedMessageBox

        reply = ThemedMessageBox.question(
            self,
            "Move to AT0-M (Steam Native)",
            f"Are you sure you want to register '{self.game_name}' to AT0-M mode?\n\n"
            f"• Mode: {'DLC-Only (Only DLC AppIDs injected)' if is_dlc_only else 'Full Game Install'}\n"
            f"• Depots / DLCs: {len(selected_depots)} depot(s), {len(checked_dlcs)} DLC entitlement(s)\n"
            "• SLSsteam config.yaml will be updated and reloaded.\n\n"
            "Proceed with transition?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.Yes,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

        try:
            from utils.plugin_games import register_plugin_game
            from utils.yaml.dlc import add_dlc_data_batch
            from utils.yaml_config_manager import get_user_config_path

            config_path = get_user_config_path()

            # 1. Gather depot names and decryption keys
            depot_names = {}
            decryption_keys = {}
            for did in selected_depots:
                meta = (self.depots or {}).get(str(did)) or {}
                d_name = meta.get("desc") or meta.get("name") or ""
                if d_name:
                    depot_names[str(did)] = d_name
                d_key = meta.get("key") or meta.get("decryption_key") or ""
                if d_key and len(str(d_key).strip()) == 64:
                    decryption_keys[str(did)] = str(d_key).strip().lower()

            # 2. Gather DLC AppIDs
            dlc_appids = list(checked_dlcs.keys()) if checked_dlcs else []
            for did in selected_depots:
                meta = (self.depots or {}).get(str(did)) or {}
                dlc_id = str(meta.get("dlcappid") or "").strip()
                if dlc_id and dlc_id != str(self.app_id) and dlc_id not in dlc_appids:
                    dlc_appids.append(dlc_id)

            # 3. Register to plugin games database and sync SLS config
            success = register_plugin_game(
                appid=self.app_id,
                name=self.game_name,
                depot_ids=selected_depots,
                decryption_keys=decryption_keys,
                installdir=self.game_name,
                depot_names=depot_names,
                dlc_appids=dlc_appids if is_dlc_only else None,
            )

            if not success:
                # The AT0-M identity was not committed - do not claim it was.
                ThemedMessageBox.critical(
                    self,
                    "AT0-M Transition Failed",
                    f"Failed to write the AT0-M configuration for '{self.game_name}'.\n\n"
                    "Nothing was changed. See the log for details.",
                )
                return

            # 4. If DLC data was selected, ensure DlcData is also injected
            if checked_dlcs and config_path and config_path.exists():
                add_dlc_data_batch(config_path, self.app_id, checked_dlcs)

            if is_dlc_only:
                from utils.settings import get_settings

                get_settings().setValue(f"dlc_only_mode/{self.app_id}", True)

            self._atom_registered = True
            self._config_written = True

            ThemedMessageBox.information(
                self,
                "AT0-M Registered",
                f"✓ Successfully transitioned '{self.game_name}' to AT0-M mode!\n\n"
                f"SLSsteam plugin integration has been configured.",
            )
            super().accept()
        except Exception as exc:
            logger.error(f"[DepotSelectionDialog] Could not move to AT0-M: {exc}", exc_info=True)
            ThemedMessageBox.critical(
                self,
                "AT0-M Transition Failed",
                f"Failed to register AT0-M configuration:\n\n{exc}",
            )
