"""Options Configure menu visibility (Learn IR must stay available)."""

from custom_components.globalcache_itach.options_menu import (
    build_command_list_menu,
    build_options_init_menu,
)


def test_learn_ir_visible_without_remotes() -> None:
    menu = build_options_init_menu(remotes=[], relays=[], serials=[])
    assert "learn_ir" in menu
    assert menu["learn_ir"] == "Learn IR command (pinhole)"
    assert "add_remote" in menu
    assert "edit_remote" not in menu
    assert "remove_remote" not in menu


def test_learn_ir_visible_with_remotes() -> None:
    menu = build_options_init_menu(
        remotes=[{"remote_id": "r1"}],
        relays=[],
        serials=[],
    )
    assert "learn_ir" in menu
    assert "edit_remote" in menu
    assert "remove_remote" in menu


def test_relay_serial_edit_gated_on_empty() -> None:
    menu = build_options_init_menu(
        remotes=[],
        relays=[{"relay_id": "x"}],
        serials=[],
    )
    assert "edit_relay" in menu
    assert "remove_relay" in menu
    assert "edit_serial" not in menu
    assert "remove_serial" not in menu
    assert "learn_ir" in menu


def test_command_list_menu_hides_edit_until_commands_exist() -> None:
    empty = build_command_list_menu(has_commands=False, save_label="Save remote")
    assert list(empty) == ["add", "import_json", "save"]
    assert empty["save"] == "Save remote"
    full = build_command_list_menu(has_commands=True, save_label="Save serial port")
    assert list(full) == ["add", "edit", "remove", "import_json", "save"]
    assert full["save"] == "Save serial port"
