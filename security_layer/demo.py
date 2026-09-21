"""
End-to-end walkthrough: several simulated edge devices, one well-behaved and
several adversarial, sending through a single Gateway. Run with:

    python -m security_layer.demo
"""

import secrets
import shutil

from .edge import EdgeDevice
from .pipeline import Gateway

_DATA_DIR = "_data"


def line(char="-", n=78):
    print(char * n)


def show(label: str, decision):
    status = "ACCEPTED " if decision.accepted else "DROPPED  "
    gate = decision.failed_gate or "commit"
    hold_note = ""
    if decision.resolved_hold:
        hold_note = f"  [resolved earlier hold: seq={decision.resolved_hold['seq']} -> {decision.resolved_hold['outcome']}]"
    print(f"{label:<28} {status} gate={gate:<20} score={decision.trust_score_after!s:<4} {decision.reason}{hold_note}")


def send(gw: Gateway, device: EdgeDevice, source_ip: str, label: str, value=None, received_at_ms=None):
    msg = device.build_message(value=value)
    decision = gw.ingest(msg.raw_wire, source_ip, received_at_ms=received_at_ms)
    show(label, decision)
    if decision.new_session_id:
        device.adopt_session(decision.new_session_id)
    # Note: the queue is NOT cleared here. A device only deletes a message once
    # it holds a Merkle proof it has verified for itself, after the batch is
    # flushed and the root published — see step 6 below.
    return decision, msg


def main():
    # Fresh disk queues each run, so the "still queued" counts below are readable.
    # Real edge devices deliberately persist this directory across restarts.
    shutil.rmtree(_DATA_DIR, ignore_errors=True)

    gw = Gateway()

    good = EdgeDevice("dev-good", b"secret-good", "TEMP", base_value=220, noise=3)
    replay = EdgeDevice("dev-replay", b"secret-replay", "TEMP", base_value=200, noise=2)
    tamper = EdgeDevice("dev-tamper", b"secret-tamper", "TEMP", base_value=210, noise=2)
    noisy = EdgeDevice("dev-noisy", b"secret-noisy", "TEMP", base_value=200, noise=2)
    lowtrust = EdgeDevice("dev-lowtrust", b"secret-lowtrust", "TEMP", base_value=180, noise=2)

    gw.register_device(good.device_id, good.secret_key)
    gw.register_device(replay.device_id, replay.secret_key)
    gw.register_device(tamper.device_id, tamper.secret_key)
    gw.register_device(noisy.device_id, noisy.secret_key)
    gw.register_device(lowtrust.device_id, lowtrust.secret_key, initial_trust=15)  # below TRUST_FLOOR

    line("=")
    print("1) NORMAL DEVICE — session bootstrap, three clean readings")
    line("=")
    send(gw, good, "10.0.0.1", "good: first (no session)")
    send(gw, good, "10.0.0.1", "good: retry (session issued)")
    send(gw, good, "10.0.0.1", "good: reading #2")
    send(gw, good, "10.0.0.1", "good: reading #3")

    line("=")
    print("2) REPLAY ATTACK — a captured valid message resent verbatim")
    line("=")
    send(gw, replay, "10.0.0.2", "replay: bootstrap")
    _, first_msg = send(gw, replay, "10.0.0.2", "replay: reading #1 (legit)")
    resend_decision = gw.ingest(first_msg.raw_wire, "10.0.0.2")
    show("replay: same message again", resend_decision)

    line("=")
    print("3) TAMPERED MESSAGE — payload altered after signing")
    line("=")
    send(gw, tamper, "10.0.0.3", "tamper: bootstrap")
    msg = tamper.build_message(value=210)
    forged = msg.raw_wire.replace(" 210 ", " 999 ")  # value changed, HMAC now invalid
    show("tamper: forged value", gw.ingest(forged, "10.0.0.3"))

    line("=")
    print("4) LOW-TRUST DEVICE — score already below the floor")
    line("=")
    send(gw, lowtrust, "10.0.0.4", "lowtrust: bootstrap")
    send(gw, lowtrust, "10.0.0.4", "lowtrust: reading")

    line("=")
    print("5) CORROBORATION BUFFER — a borderline jump, then confirmed by the next reading")
    line("=")
    send(gw, noisy, "10.0.0.5", "noisy: bootstrap")
    send(gw, noisy, "10.0.0.5", "noisy: baseline", value=200)
    send(gw, noisy, "10.0.0.5", "noisy: SPIKE (held)", value=290)
    send(gw, noisy, "10.0.0.5", "noisy: stays near spike (confirms)", value=285)

    print()
    print("...and the same buffer rejecting a spike that turns out to be a blip:")
    send(gw, noisy, "10.0.0.5", "noisy: SPIKE #2 (held)", value=180)
    send(gw, noisy, "10.0.0.5", "noisy: reverts to normal (denies)", value=220)

    line("=")
    print("6) COMMIT — flush the batch, then each device verifies its own receipt")
    line("=")
    batch = gw.flush_ledger()
    print(f"batch flushed: {batch['size']} records, root={batch['root'][:16]}..., prev_root={batch['prev_root'][:16]}...")

    devices = {d.device_id: d for d in (good, replay, tamper, noisy, lowtrust)}
    accepted_ids = [r.record_id for r in gw.ledger._records if r.batch_index is not None]
    for record_id in accepted_ids:
        record = next(r for r in gw.ledger._records if r.record_id == record_id)
        receipt = gw.ledger.get_receipt(record_id)
        verified = gw.ledger.verify_receipt(receipt)
        device = devices.get(record.payload["device_id"])
        if verified and device:
            device.queue.confirm(record.payload["seq"])  # only now does the edge drop it

    sample_id = accepted_ids[0]
    receipt = gw.ledger.get_receipt(sample_id)
    print(f"record {sample_id} verifies against the published root: {gw.ledger.verify_receipt(receipt)}")

    tampered_receipt = dict(receipt)
    tampered_receipt["leaf_hash"] = secrets.token_hex(32)
    print(f"a forged receipt for the same root verifies: {gw.ledger.verify_receipt(tampered_receipt)}")

    ok, msg_integrity = gw.ledger.verify_chain_integrity()
    print(f"full chain integrity: {ok} — {msg_integrity}")

    print()
    for dev in (good, replay, tamper, noisy, lowtrust):
        print(f"  {dev.device_id:<14} still queued (unconfirmed): {len(dev.queue.pending())}")

    line("=")
    print("FINAL TRUST SCORES")
    line("=")
    for dev in (good, replay, tamper, noisy, lowtrust):
        rec = gw.trust.get(dev.device_id)
        print(f"  {dev.device_id:<14} score={rec.trust_score:<4} revoked={rec.revoked}")

    print()
    print(f"total accepted: {len(gw.accepted) or len(accepted_ids)}   total quarantined: {len(gw.quarantine)}")


if __name__ == "__main__":
    main()
