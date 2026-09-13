"""`_warn_invalid_platform_toolsets` hands the validator the MCP server names (#109791).

The validator itself is pure and injected — these cover the other half, the caller that
owns the config read. Without it the exemption exists but nothing ever supplies it.
"""

from unittest.mock import patch

from hermes_cli import config as config_module

_CONFIG = {
    "platform_toolsets": {"cli": ["hermes-cli", "drawio", "kanbn"]},
    "mcp_servers": {
        "drawio": {"command": "mcp-proxy", "args": ["drawio"]},
        "github": {"command": "mcp-proxy", "args": ["github"], "enabled": False},
    },
}


def _warnings_for(raw_config):
    results = {"warnings": []}
    with patch.object(config_module, "read_raw_config", return_value=raw_config):
        config_module._warn_invalid_platform_toolsets(results, quiet=True)
    return results["warnings"]


def test_an_enabled_mcp_server_name_draws_no_warning():
    warnings = _warnings_for(_CONFIG)
    assert not any("drawio" in w for w in warnings)


def test_a_genuine_typo_still_warns():
    # The same call, same config: the exemption is by name, not a blanket silence.
    warnings = _warnings_for(_CONFIG)
    assert any("unknown toolset 'kanbn'" in w for w in warnings)


def test_a_disabled_mcp_server_is_not_exempt():
    # `enabled_mcp_server_names` filters on the enabled flag, and a disabled server is not
    # part of any allowlist — so listing it is the mistake the warning is for.
    warnings = _warnings_for(
        {**_CONFIG, "platform_toolsets": {"cli": ["hermes-cli", "github"]}}
    )
    assert any("unknown toolset 'github'" in w for w in warnings)
