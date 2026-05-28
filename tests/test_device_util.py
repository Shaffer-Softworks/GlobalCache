"""Device capability parsing."""

from custom_components.globalcache_itach.device_util import (
    format_unknown_command,
    infer_product_label,
    module_accepts_ir,
    parse_getdevices_lines,
)

GC100_12_LINES = [
    "device,1,1,SERIAL",
    "device,2,1,SERIAL",
    "device,3,3,RELAY",
    "device,4,3,IR",
    "device,5,3,IR",
    "endlistdevices",
]


def test_parse_gc100_12() -> None:
    mods = parse_getdevices_lines(GC100_12_LINES)
    assert len(mods) == 5
    assert mods[3] == {"module": 4, "ports": 3, "type": "IR"}


def test_module_accepts_ir() -> None:
    mods = parse_getdevices_lines(GC100_12_LINES)
    assert module_accepts_ir(mods, 4) is True
    assert module_accepts_ir(mods, 3) is False


def test_unknowncommand_21_message() -> None:
    msg = format_unknown_command("unknowncommand 21")
    assert "not an IR module" in msg


def test_infer_gc100_12() -> None:
    mods = parse_getdevices_lines(GC100_12_LINES)
    assert infer_product_label(mods, "version,0,3.2-12") == "GC-100-12"
