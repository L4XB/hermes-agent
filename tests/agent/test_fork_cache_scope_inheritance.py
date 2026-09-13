"""A same-model cache-parity fork must route on its parent's cache scope (#109964).

`build_cache_parity_fork` copies the parent's `session_id` so the outbound
request hits the prefix cache the parent warmed. The scope a provider routes
on is RESOLVED from the agent rather than copied, and the two attributes the
fork sets for persistence isolation both remove an input to that resolution:
`_persist_disabled` makes `declared_conversation_scope` fail closed, and
`_session_db = None` removes the lineage walk. A gateway parent declares
`gwk_<hash>` while its fork lands on the physical id — same session, two
scopes, and the first review request goes out cold.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.background_review import _inherit_parent_cache_scope
from agent.prompt_cache_scope import _INHERITED_ATTR, resolve_prompt_cache_scope
from hermes_state import SessionDB

SESSION = "gc_run_room7_default_Reviewer_11111111111141118111111111111111"
CHAT_KEY = "agent:main:telegram:group:-100123:456"


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield session_db
    finally:
        session_db.close()


def _gateway_parent(session_db):
    return SimpleNamespace(
        session_id=SESSION,
        _session_db=session_db,
        _gateway_session_key=CHAT_KEY,
    )


def _fork_of(parent):
    """The fork as `build_cache_parity_fork` leaves it: the parent's session_id,
    no session DB, persistence disabled."""
    return SimpleNamespace(
        session_id=parent.session_id,
        _session_db=None,
        _persist_disabled=True,
        _gateway_session_key=parent._gateway_session_key,
    )


def test_the_fork_lands_elsewhere_without_inheriting(db):
    """The reported state, pinned first: without it the assertions below could
    pass on a tree where the two scopes were never going to differ."""
    db.create_session(SESSION, source="api_server")
    parent = _gateway_parent(db)

    assert resolve_prompt_cache_scope(parent).startswith("gwk_")
    assert resolve_prompt_cache_scope(_fork_of(parent)) == SESSION


def test_an_inherited_scope_matches_the_parent(db):
    db.create_session(SESSION, source="api_server")
    parent = _gateway_parent(db)
    fork = _fork_of(parent)

    _inherit_parent_cache_scope(fork, parent)

    assert resolve_prompt_cache_scope(fork) == resolve_prompt_cache_scope(parent)


def test_inheriting_does_not_relax_the_fail_closed_rule(db):
    """Only an agent handed its parent's scope on purpose inherits one. A fork
    that was not — a routed one, or any other `_persist_disabled` agent — still
    falls back, so `/branch` and delegate children cannot merge onto a parent's
    key by acquiring `_persist_disabled`."""
    db.create_session(SESSION, source="api_server")
    parent = _gateway_parent(db)

    assert resolve_prompt_cache_scope(_fork_of(parent)) == SESSION


def test_the_inherited_scope_wins_over_a_memoized_one(db):
    """Resolution memoizes on the agent, and a fork can be resolved before the
    builder finishes wiring it. The pin has to be read ahead of the memo or the
    stale physical id sticks for the whole review."""
    db.create_session(SESSION, source="api_server")
    parent = _gateway_parent(db)
    fork = _fork_of(parent)

    assert resolve_prompt_cache_scope(fork) == SESSION  # memoized
    _inherit_parent_cache_scope(fork, parent)

    assert resolve_prompt_cache_scope(fork) == resolve_prompt_cache_scope(parent)


def test_a_parent_with_no_declared_scope_pins_nothing_surprising(db):
    """No gateway key: the parent resolves to the physical id, and the fork was
    already going to agree. Inheriting must not invent a scope."""
    db.create_session(SESSION, source="cli")
    parent = SimpleNamespace(
        session_id=SESSION, _session_db=db, _gateway_session_key=None
    )
    fork = _fork_of(parent)

    _inherit_parent_cache_scope(fork, parent)

    assert resolve_prompt_cache_scope(fork) == resolve_prompt_cache_scope(parent)


def test_inheriting_never_raises():
    """Best effort by contract: a review must run even if the parent's scope
    cannot be resolved."""
    broken = SimpleNamespace()
    fork = SimpleNamespace(session_id="s", _session_db=None, _persist_disabled=True)

    _inherit_parent_cache_scope(fork, broken)

    assert getattr(fork, _INHERITED_ATTR, None) in (None, "")


def test_only_the_same_model_path_inherits():
    """A routed fork talks to a different model, where the parent's scope names
    a cache that cannot serve it. The call therefore belongs inside the
    `if not _routed:` block, and nothing else in this file can see which block
    it sits in.
    """
    source = Path(
        __import__("agent.background_review", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    body = source[source.index("def build_cache_parity_fork(") :]
    same_model_branch = body[body.index("if not _routed:") :]
    # Everything up to the next statement at the function's own indentation.
    same_model_branch = same_model_branch[: same_model_branch.index("\n    _detach_fork_compression")]

    assert "_inherit_parent_cache_scope(review_agent, agent)" in same_model_branch
