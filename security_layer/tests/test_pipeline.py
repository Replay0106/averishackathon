import unittest

from security_layer import wire
from security_layer.pipeline import Gateway
from security_layer.state import TRUST_FLOOR


def bootstrap(gw: Gateway, device_id: str, secret: bytes, seq: int = 1) -> str:
    """Sends one message with an invalid session to obtain a real one, and
    returns the session id the device should now use."""
    raw = wire.encode(device_id, "s-invalid", seq, "TEMP", 200, 1000, secret)
    decision = gw.ingest(raw, "10.0.0.1")
    assert not decision.accepted
    assert decision.new_session_id is not None
    return decision.new_session_id


class TestPipelineBasics(unittest.TestCase):
    def test_unknown_device_rejected_at_gate2(self):
        gw = Gateway()
        raw = wire.encode("ghost", "s1", 1, "TEMP", 200, 1000, b"whatever")
        decision = gw.ingest(raw, "10.0.0.1")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failed_gate, "trust_ledger_check")

    def test_malformed_message_rejected_before_device_known(self):
        gw = Gateway()
        decision = gw.ingest("not a valid wire message", "10.0.0.1")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failed_gate, "canonical_parser")
        self.assertIsNone(decision.device_id)

    def test_missing_session_reissues_without_penalty(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        before = gw.trust.get("d1").trust_score
        raw = wire.encode("d1", "s-invalid", 1, "TEMP", 200, 1000, b"secret")
        decision = gw.ingest(raw, "10.0.0.1")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failed_gate, "session_check")
        self.assertEqual(gw.trust.get("d1").trust_score, before)  # no penalty

    def test_full_round_trip_commits_and_is_provable(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        sid = bootstrap(gw, "d1", b"secret")

        raw = wire.encode("d1", sid, 2, "TEMP", 205, 2000, b"secret")
        decision = gw.ingest(raw, "10.0.0.1")
        self.assertTrue(decision.accepted)

        gw.flush_ledger()
        receipt = gw.ledger.get_receipt(decision.record_id)
        self.assertTrue(gw.ledger.verify_receipt(receipt))

    def test_below_floor_device_always_rejected(self):
        gw = Gateway()
        gw.register_device("d1", b"secret", initial_trust=TRUST_FLOOR - 1)
        raw = wire.encode("d1", "s-invalid", 1, "TEMP", 200, 1000, b"secret")
        decision = gw.ingest(raw, "10.0.0.1")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failed_gate, "trust_ledger_check")


class TestPipelineAttacks(unittest.TestCase):
    def test_replay_is_dropped_and_does_not_double_commit(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        sid = bootstrap(gw, "d1", b"secret")
        raw = wire.encode("d1", sid, 2, "TEMP", 200, 2000, b"secret")

        first = gw.ingest(raw, "10.0.0.1")
        second = gw.ingest(raw, "10.0.0.1")

        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.failed_gate, "replay_window")
        self.assertEqual(len(gw.accepted), 1)

    def test_tampered_payload_fails_hmac(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        sid = bootstrap(gw, "d1", b"secret")
        raw = wire.encode("d1", sid, 2, "TEMP", 200, 2000, b"secret")
        forged = raw.replace(" 200 ", " 999 ")

        decision = gw.ingest(forged, "10.0.0.1")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failed_gate, "hmac_verify")

    def test_gate_failure_penalizes_trust_score(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        sid = bootstrap(gw, "d1", b"secret")
        before = gw.trust.get("d1").trust_score
        raw = wire.encode("d1", sid, 2, "TEMP", 200, 2000, b"secret")
        forged = raw.replace(" 200 ", " 999 ")
        gw.ingest(forged, "10.0.0.1")
        self.assertLess(gw.trust.get("d1").trust_score, before)

    def test_ip_flood_rate_limited(self):
        gw = Gateway()
        results = [gw.ingest("garbage", "1.2.3.4") for _ in range(25)]
        self.assertTrue(any(r.failed_gate == "rate_limit_ip" for r in results))


class TestCorroborationInPipeline(unittest.TestCase):
    def test_confirmed_spike_eventually_commits(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        sid = bootstrap(gw, "d1", b"secret")

        gw.ingest(wire.encode("d1", sid, 2, "TEMP", 200, 2000, b"secret"), "10.0.0.1")
        held = gw.ingest(wire.encode("d1", sid, 3, "TEMP", 290, 3000, b"secret"), "10.0.0.1")
        self.assertFalse(held.accepted)
        self.assertEqual(held.failed_gate, "plausibility_hold")

        confirm = gw.ingest(wire.encode("d1", sid, 4, "TEMP", 285, 4000, b"secret"), "10.0.0.1")
        self.assertTrue(confirm.accepted)
        self.assertEqual(confirm.resolved_hold["outcome"], "confirmed")

        gw.flush_ledger()
        self.assertEqual(len(gw.accepted), 2)  # baseline + the confirmed spike

    def test_denied_spike_never_commits(self):
        gw = Gateway()
        gw.register_device("d1", b"secret")
        sid = bootstrap(gw, "d1", b"secret")

        gw.ingest(wire.encode("d1", sid, 2, "TEMP", 200, 2000, b"secret"), "10.0.0.1")
        gw.ingest(wire.encode("d1", sid, 3, "TEMP", 290, 3000, b"secret"), "10.0.0.1")
        # 230 is close enough to the held 290 to pass on its own merits (slew 60),
        # but far enough from it (60 > TOLERANCE 25) to deny the held spike as a blip.
        deny = gw.ingest(wire.encode("d1", sid, 4, "TEMP", 230, 4000, b"secret"), "10.0.0.1")

        self.assertTrue(deny.accepted)  # this message itself is fine
        self.assertEqual(deny.resolved_hold["outcome"], "rejected")
        gw.flush_ledger()
        self.assertEqual(len(gw.accepted), 2)  # baseline + this one; the spike never committed


if __name__ == "__main__":
    unittest.main()
