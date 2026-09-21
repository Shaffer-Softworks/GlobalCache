"""Options Configure menu visibility (Learn IR must stay available)."""

from custom_components.globalcache_itach.options_menu import build_options_init_menu


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
