"""Configure (options) menu labels for Global Caché iTach."""

from __future__ import annotations

from typing import Any


def build_options_init_menu(
    *,
    remotes: list[Any],
    relays: list[Any],
    serials: list[Any],
) -> dict[str, str]:
    """Build the Configure menu; Learn IR stays visible even with no remotes.

    Edit/remove entries are omitted when the matching list is empty. Learn IR
    must remain available so iTach users (no infrared receiver) can still
    capture codes via the pinhole learner without first adding a remote.
    """
    menu = {
        "ir_defaults": "IR defaults",
        "timeouts": "Timeouts",
        "add_remote": "Add remote",
        "edit_remote": "Edit remote",
        "remove_remote": "Remove remote",
        "learn_ir": "Learn IR command (pinhole)",
        "add_relay": "Add relay",
        "edit_relay": "Edit relay",
        "remove_relay": "Remove relay",
        "add_serial": "Add serial port",
        "edit_serial": "Edit serial port",
        "remove_serial": "Remove serial port",
    }
    if not remotes:
        menu.pop("edit_remote", None)
        menu.pop("remove_remote", None)
    if not relays:
        menu.pop("edit_relay", None)
        menu.pop("remove_relay", None)
    if not serials:
        menu.pop("edit_serial", None)
        menu.pop("remove_serial", None)
    return menu


def build_command_list_menu(*, has_commands: bool, save_label: str) -> dict[str, str]:
    """Actions for the per-command editor. Edit and remove need an existing command."""
    menu: dict[str, str] = {"add": "Add command"}
    if has_commands:
        menu["edit"] = "Edit command"
        menu["remove"] = "Remove command"
    menu["import_json"] = "Import JSON"
    menu["save"] = save_label
    return menu


IR_FORMAT_LABELS: dict[str, str] = {
    "pronto": "Pronto hex",
    "gc_pairs": "Global Caché pulse pairs",
    "full_sendir": "Full sendir line",
}
