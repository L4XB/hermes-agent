from gateway.response_filters import (
    is_autonomous_silence_response,
    is_intentional_silence_agent_result,
    is_intentional_silence_response,
    silent_delivery_reply,
)


def test_exact_silence_tokens_are_intentional_silence():
    for token in ("[SILENT]", " SILENT ", "NO_REPLY", "no reply"):
        assert is_intentional_silence_response(token)


def test_autonomous_silence_accepts_marker_with_own_line_note():
    """The loose rule for cron/webhook lanes: marker + explanation suppresses."""
    assert is_autonomous_silence_response("[SILENT]")
    assert is_autonomous_silence_response("[SILENT]\n\nNothing new this tick.")
    assert is_autonomous_silence_response("2 deals filtered\n\n[SILENT]")
    assert is_autonomous_silence_response("no_reply\nduplicate inbound, already handled")
    assert is_autonomous_silence_response("[SILENT] No changes detected")


def test_silent_delivery_reply_drops_only_a_bare_marker():
    """The split every delivery path outside the gateway shares (#110782)."""
    for marker in ("NO_REPLY", " [SILENT] ", "no reply", "*NO_REPLY*"):
        assert silent_delivery_reply(marker) == ("", True), marker

    # A real answer survives byte-for-byte, mention of a marker included.
    for kept in ("pong", "I would say NO_REPLY if there were nothing to add.", "", "   "):
        assert silent_delivery_reply(kept) == (kept, False), kept


def test_silent_delivery_reply_handles_a_non_string_turn():
    assert silent_delivery_reply(None) == ("", False)
    assert silent_delivery_reply(7) == ("7", False)
