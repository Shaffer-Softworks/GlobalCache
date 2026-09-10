"""Rewrite learner sendir connector to a remote's module:port."""

import pytest

from custom_components.globalcache_itach.command_util import rewrite_sendir_connector


def test_rewrite_sendir_connector_replaces_module_port() -> None:
    line = "sendir,1:1,1,38000,1,1,100,100,100,100"
    assert rewrite_sendir_connector(line, 1, 3) == (
        "sendir,1:3,1,38000,1,1,100,100,100,100"
    )


def test_rewrite_sendir_connector_strips_whitespace() -> None:
    line = "  sendir,1:1,1,38000,1,1,10,20  "
    assert rewrite_sendir_connector(line, 2, 1) == "sendir,2:1,1,38000,1,1,10,20"


def test_rewrite_sendir_connector_rejects_non_sendir() -> None:
    with pytest.raises(ValueError, match="sendir"):
        rewrite_sendir_connector("completeir,1:1,1", 1, 1)


def test_rewrite_sendir_connector_rejects_short_line() -> None:
    with pytest.raises(ValueError, match="Malformed"):
        rewrite_sendir_connector("sendir,1:1", 1, 1)
