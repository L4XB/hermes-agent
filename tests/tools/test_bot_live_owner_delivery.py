"""Durable mailbox invariants, using real disk and exec boundaries."""
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("terminal_status", ["settled", "failed", "cancelled"])
def test_delivery_is_idempotent_fenced_and_permanent(tmp_path, terminal_status):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    delivery_id = "a" * 32
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id=delivery_id)
    assert queued["status"] == "queued"
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id=delivery_id) == queued
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "different", delivery_id=delivery_id)
    assert mailbox.claim_pending_delivery(tmp_path, dict(owner, lease_id="other")) is None
    assert mailbox.claim_pending_delivery(tmp_path, dict(owner, live_session_id="other")) is None
    script = (
        "import json,sys; from tools.bot_live_delivery import claim_pending_delivery; "
        "print(json.dumps(claim_pending_delivery(sys.argv[1],json.loads(sys.argv[2]))))"
    )
    children = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), json.dumps(owner)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = []
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        results.append(json.loads(out))
    claims = [r for r in results if r is not None]
    assert len(claims) == 1 and claims[0]["message"] == "hello"
    assert mailbox.read_delivery_result(tmp_path, delivery_id)["status"] == "claimed"
    assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    receipt = mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="answer")
    assert mailbox.read_delivery_result(tmp_path, delivery_id) == receipt
    assert mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="answer") == receipt
    with pytest.raises(ValueError):
        mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="rewrite")
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id=delivery_id) == receipt
    assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    if os.name != "nt":
        for path in (tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME).iterdir():
            assert path.stat().st_mode & 0o077 == 0


def test_fifo_survives_clock_rollback(tmp_path, monkeypatch):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    for timestamp, message in ((100, "first"), (90, "second")):
        monkeypatch.setattr(mailbox.time, "time_ns", lambda: timestamp)
        mailbox.deliver_to_live_owner(tmp_path, owner, message)
    assert mailbox.claim_pending_delivery(tmp_path, owner)["message"] == "first"
    assert mailbox.claim_pending_delivery(tmp_path, owner)["message"] == "second"


@pytest.mark.parametrize("capable", [True, False])
def test_only_canonical_capable_owner_receives_across_compression(tmp_path, capable):
    from hermes_state import SessionDB
    from hermes_cli.active_sessions import try_acquire_active_session, transfer_active_session
    from tools import bot_live_delivery as mailbox

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="chat", source="cli")
    db.set_session_title("chat", "Bot Chat")
    meta = dict(live_session_id="live", bot_live_delivery_consumer=capable)
    lease, refusal = try_acquire_active_session(session_id="chat", surface="desktop", config={},
                                               registry_home=tmp_path, metadata=meta)
    assert refusal is None
    try:
        owner = mailbox.find_canonical_live_owner(tmp_path)
        if not capable:
            assert owner is None
            return
        assert owner["lease_id"] == lease.lease_id
        queued = mailbox.deliver_to_live_owner(tmp_path, owner, "before compression")
        db.end_session("chat", "compression")
        db.create_session(session_id="tip", source="cli", parent_session_id="chat")
        assert transfer_active_session(lease, session_id="tip", metadata=meta)
        current = mailbox.find_canonical_live_owner(tmp_path)
        assert current["session_id"] == "tip"
        claim = mailbox.claim_pending_delivery(tmp_path, current)
        assert claim["delivery_id"] == queued["delivery_id"]
        assert claim["session_id"] == "chat"
        assert mailbox.claim_pending_delivery(tmp_path, current) is None
    finally:
        lease.release()
        db.close()


def test_delivery_keeps_the_sender_and_refuses_a_different_one_under_the_same_id(tmp_path):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat", lease_id="lease", live_session_id="live")
    author = {"id": "bot:coder", "name": "coder", "is_bot": True}
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id="b" * 32, author=author)
    assert queued["author"] == author
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id="b" * 32, author=author) == queued
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id="b" * 32, author={**author, "id": "bot:other"})
    assert "author" not in mailbox.deliver_to_live_owner(tmp_path, owner, "no sender", delivery_id="c" * 32)


# --- one unreadable ticket must not wedge the pipeline (#109820) ----------------------
#
# `_read` tolerated only FileNotFoundError, and both bulk scans went through it: the
# sender's sequence high-water scan and the receiver's pending scan. One unreadable
# ticket therefore stopped every sender (admissions came back `ambiguous`) and crashed
# the receiver's poll on every cycle, while every other ticket in the directory was fine.

def _owner_for(tmp_path):
    return dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                lease_id="lease", live_session_id="live")


def _root_of(tmp_path):
    from tools import bot_live_delivery as mailbox

    return mailbox._root(tmp_path)


@pytest.mark.parametrize("flavour", ["corrupt-json", "directory", "unreadable"])
def test_one_unreadable_ticket_does_not_wedge_the_pipeline(tmp_path, flavour):
    from tools import bot_live_delivery as mailbox

    owner = _owner_for(tmp_path)
    # A first real delivery, so the directory (and the lock) exist.
    first = mailbox.deliver_to_live_owner(tmp_path, owner, "first", delivery_id="a" * 32)
    root = _root_of(tmp_path)

    intruder = root / f"{'b' * 32}.json"
    if flavour == "corrupt-json":
        intruder.write_text("{not json", encoding="utf-8")  # ValueError on read
    elif flavour == "directory":
        intruder.mkdir()  # IsADirectoryError / PermissionError, an OSError either way
    else:
        if os.name == "nt" or os.geteuid() == 0:
            pytest.skip("chmod does not deny the owner here")
        intruder.write_text("{}", encoding="utf-8")
        os.chmod(intruder, 0o000)

    try:
        # Sender: the high-water scan walks the whole directory, including the intruder.
        second = mailbox.deliver_to_live_owner(tmp_path, owner, "second", delivery_id="c" * 32)
        assert second["status"] == "queued"

        # Receiver: the pending scan does too, and still finds the oldest queued ticket.
        claimed = mailbox.claim_pending_delivery(tmp_path, owner)
        assert claimed is not None
        assert claimed["delivery_id"] == first["delivery_id"]
    finally:
        if flavour == "unreadable":
            os.chmod(intruder, 0o600)


def test_a_targeted_read_of_a_broken_ticket_is_still_an_error(tmp_path):
    # The other half of the contract: only the SCANS are tolerant. Treating an
    # unreadable ticket as absent in a targeted read would let a delivery overwrite a
    # live admission, or report one that exists as not found.
    from tools import bot_live_delivery as mailbox

    owner = _owner_for(tmp_path)
    delivery_id = "d" * 32
    mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id=delivery_id)
    (_root_of(tmp_path) / f"{delivery_id}.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError):  # json.JSONDecodeError, not FileNotFoundError
        mailbox.complete_delivery(tmp_path, delivery_id, status="settled", reply="hi")
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "hello", delivery_id=delivery_id)
