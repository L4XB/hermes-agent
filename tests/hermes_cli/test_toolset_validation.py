"""Unit tests for hermes_cli.toolset_validation (see #38798).

Pure logic — the validity predicate is injected, so these tests need neither the
tool registry nor a running Hermes.
"""

import pytest

from hermes_cli.toolset_validation import validate_platform_toolsets

# A representative set of real toolset names. `hermes` is deliberately absent —
# that is the corruption #38798 reported (`hermes-cli` rewritten to `hermes`).
_KNOWN = {
    "hermes-cli",
    "hermes-telegram",
    "hermes-discord",
    "hermes-whatsapp",
    "discord",
    "terminal",
    "web",
}


def _is_valid(name):
    return name in _KNOWN




def test_38798_corruption_warns_and_suggests_correct_name():
    # The exact reported shape: cli holds 'hermes' instead of 'hermes-cli'.
    warnings = validate_platform_toolsets({"cli": ["hermes"]}, _is_valid)
    unknown = [w for w in warnings if "unknown toolset 'hermes'" in w]
    assert len(unknown) == 1
    # Actionable: points at the valid name the entry should have been.
    assert "did you mean 'hermes-cli'?" in unknown[0]
    # And the zero-valid-toolsets safety net fires.
    assert any("zero valid toolsets" in w for w in warnings)


def test_mixed_valid_and_invalid_flags_only_the_invalid():
    cfg = {"cli": ["hermes-cli"], "discord": ["bogus"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)
    # One valid entry exists, so no zero-valid warning.
    assert not any("zero valid toolsets" in w for w in warnings)
    assert any(
        "platform 'discord'" in w and "unknown toolset 'bogus'" in w
        for w in warnings
    )
    assert any(
        "platform 'discord'" in w and "no valid toolsets" in w
        for w in warnings
    )






def test_empty_list_on_a_platform_warns_even_when_others_are_valid():
    # The #89050 shape: the active platform is wiped to [] while every other
    # platform stays populated. The global zero-valid-toolsets net does not fire
    # (telegram/discord are valid), so without a per-platform check this config
    # produces no warning at all and the agent silently starts with no tools.
    cfg = {
        "cli": [],
        "telegram": ["hermes-telegram"],
        "discord": ["hermes-discord"],
    }
    warnings = validate_platform_toolsets(cfg, _is_valid)

    empty = [w for w in warnings if "empty toolset list" in w]
    assert len(empty) == 1
    assert "platform 'cli'" in empty[0]
    assert "no tools" in empty[0]
    # Populated platforms must not be implicated.
    assert "telegram" not in empty[0]
    # The global net is genuinely suppressed here — the per-platform warning is
    # the only thing standing between the user and a silent zero-tool agent.
    assert not any("zero valid toolsets" in w for w in warnings)


def test_empty_list_warns_for_each_affected_platform():
    cfg = {"cli": [], "discord": [], "telegram": ["hermes-telegram"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    empty = [w for w in warnings if "empty toolset list" in w]
    assert len(empty) == 2
    assert {"cli", "discord"} == {
        p for p in ("cli", "discord") if any(f"platform '{p}'" in w for w in empty)
    }


def test_empty_list_does_not_mask_unknown_names_on_other_platforms():
    cfg = {"cli": [], "discord": ["bogus"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    assert any("empty toolset list" in w and "platform 'cli'" in w for w in warnings)
    assert any("unknown toolset 'bogus'" in w for w in warnings)
    # Nothing valid anywhere, so the global net still fires too.
    assert any("zero valid toolsets" in w for w in warnings)


def test_null_platform_warns_even_when_others_are_valid():
    # YAML's ``cli:`` parses as None. The resolver treats it as absent and uses
    # the platform default, so the warning must not claim that tools disappear.
    cfg = {"cli": None, "telegram": ["hermes-telegram"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    assert any(
        "platform 'cli'" in w
        and "null toolset value" in w
        and "falling back to 'hermes-cli'" in w
        for w in warnings
    )
    assert not any("zero valid toolsets" in w for w in warnings)


def test_null_platform_uses_canonical_default_for_alias():
    cfg = {"whatsapp_cloud": None, "telegram": ["hermes-telegram"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    assert any(
        "platform 'whatsapp_cloud'" in w
        and "falling back to 'hermes-whatsapp'" in w
        for w in warnings
    )
    assert not any("zero valid toolsets" in w for w in warnings)


def test_scalar_platform_value_warns_but_uses_platform_default():
    cfg = {"cli": "bogus", "telegram": ["hermes-telegram"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    assert any(
        "platform 'cli'" in w
        and "invalid toolset value 'bogus'" in w
        and "falling back to 'hermes-cli'" in w
        for w in warnings
    )
    assert not any("zero valid toolsets" in w for w in warnings)


def test_platform_restricted_toolset_warns_when_other_platform_is_valid():
    cfg = {"telegram": ["discord"], "cli": ["hermes-cli"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    assert any(
        "platform 'telegram'" in w
        and "toolset 'discord'" in w
        and "not available" in w
        for w in warnings
    )
    assert not any("zero valid toolsets" in w for w in warnings)


def test_null_plugin_platform_uses_synthetic_default():
    from gateway.platform_registry import PlatformEntry, platform_registry
    from toolsets import resolve_toolset

    platform = "toolset_validation_plugin"
    platform_registry.register(
        PlatformEntry(
            name=platform,
            label="Toolset Validation Plugin",
            adapter_factory=lambda _config: object(),
            check_fn=lambda: True,
        )
    )
    try:
        cfg = {platform: None, "telegram": ["hermes-telegram"]}
        warnings = validate_platform_toolsets(cfg, _is_valid)

        assert resolve_toolset(f"hermes-{platform}")
        assert any(
            f"platform '{platform}'" in w
            and f"falling back to 'hermes-{platform}'" in w
            for w in warnings
        )
        assert not any("zero valid toolsets" in w for w in warnings)
    finally:
        platform_registry.unregister(platform)


def test_all_invalid_platform_warns_even_when_others_are_valid():
    cfg = {"cli": ["bogus"], "telegram": ["hermes-telegram"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)

    assert any("unknown toolset 'bogus'" in w for w in warnings)
    assert any(
        "platform 'cli'" in w and "no valid toolsets" in w for w in warnings
    )
    assert not any("zero valid toolsets" in w for w in warnings)


def test_populated_platforms_produce_no_empty_list_warning():
    cfg = {"cli": ["hermes-cli"], "telegram": ["hermes-telegram"]}
    warnings = validate_platform_toolsets(cfg, _is_valid)
    assert warnings == []


# --- MCP server names in platform_toolsets (#109791) ---------------------------------
#
# A name in that list is not always a toolset: `tools_config._merge_mcp_servers` reads the
# MCP server names out of it as the platform's ALLOWLIST. The validator only knew about
# native toolsets, so every MCP name drew an "unknown toolset" warning suggesting
# `hermes-<platform>` — and taking that suggestion empties the allowlist, which falls back
# to every globally enabled MCP server, changing the platform's tool surface.

_MCP_SERVERS = {"drawio", "github"}


def test_mcp_server_name_is_not_an_unknown_toolset():
    warnings = validate_platform_toolsets(
        {"cli": ["hermes-cli", "drawio", "github"]}, _is_valid, extra_valid_names=_MCP_SERVERS
    )
    assert warnings == []


def test_a_platform_listing_only_mcp_servers_has_tools():
    # The allowlist alone is a working tool surface, so neither the per-platform net nor the
    # global one should fire.
    warnings = validate_platform_toolsets(
        {"cli": ["drawio"]}, _is_valid, extra_valid_names=_MCP_SERVERS
    )
    assert warnings == []


def test_a_typo_in_a_toolset_name_still_warns_with_mcp_names_present():
    warnings = validate_platform_toolsets(
        {"cli": ["kanbn", "drawio"]}, _is_valid, extra_valid_names=_MCP_SERVERS
    )
    assert [w for w in warnings if "unknown toolset 'kanbn'" in w]
    assert not any("drawio" in w for w in warnings)


def test_a_typo_in_an_mcp_name_still_warns():
    # `drawioo` is neither a toolset nor a registered server, so it is exactly the mistake
    # the warning exists for.
    warnings = validate_platform_toolsets(
        {"cli": ["hermes-cli", "drawioo"]}, _is_valid, extra_valid_names=_MCP_SERVERS
    )
    assert [w for w in warnings if "unknown toolset 'drawioo'" in w]


def test_without_extra_valid_names_behaviour_is_unchanged():
    # The parameter is optional, and every existing caller that omits it sees what it saw.
    warnings = validate_platform_toolsets({"cli": ["hermes-cli", "drawio"]}, _is_valid)
    assert [w for w in warnings if "unknown toolset 'drawio'" in w]
