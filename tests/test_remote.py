"""Tests for command helpers (no Home Assistant runtime)."""

from custom_components.globalcache_itach.command_util import activity_labels_from_spec


def test_activity_labels_dedupe_and_sort():
    spec = {
        "commands": [
            {"name": "power"},
            {"name": "Volume Up"},
            {"name": "POWER"},
            {"name": "mute"},
        ]
    }
    assert activity_labels_from_spec(spec) == ["mute", "power", "Volume Up"]


def test_activity_labels_empty():
    assert activity_labels_from_spec({}) == []
    assert activity_labels_from_spec({"commands": []}) == []
