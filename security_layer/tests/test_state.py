import unittest

from security_layer.state import (
    CorroborationBuffer,
    FreshnessTracker,
    ReplayWindow,
    SessionStore,
    TrustLedger,
    TRUST_FLOOR,
    TRUST_MAX,
    TRUST_START,
)


class TestTrustLedger(unittest.TestCase):
    def test_unknown_device_rejected(self):
        t = TrustLedger()
        ok, rec, reason = t.check("ghost")
        self.assertFalse(ok)
        self.assertIn("unknown", reason)

    def test_below_floor_rejected(self):
        t = TrustLedger()
        t.register("d1", b"k", initial_score=TRUST_FLOOR - 1)
        ok, rec, reason = t.check("d1")
        self.assertFalse(ok)

    def test_reward_clamped_to_max(self):
        t = TrustLedger()
        t.register("d1", b"k", initial_score=TRUST_MAX - 1)
        t.reward("d1", amount=50)
        self.assertEqual(t.get("d1").trust_score, TRUST_MAX)

    def test_penalize_clamped_to_zero(self):
        t = TrustLedger()
        t.register("d1", b"k", initial_score=3)
        t.penalize("d1", amount=50)
        self.assertEqual(t.get("d1").trust_score, 0)

    def test_revoked_overrides_score(self):
        t = TrustLedger()
        t.register("d1", b"k", initial_score=TRUST_START)
        t.revoke("d1")
        ok, rec, reason = t.check("d1")
        self.assertFalse(ok)
        self.assertIn("revoked", reason)


class TestSessionStore(unittest.TestCase):
    def test_issue_then_valid(self):
        s = SessionStore()
        sid = s.issue("d1")
        self.assertTrue(s.is_valid("d1", sid))

    def test_unknown_session_invalid(self):
        s = SessionStore()
        self.assertFalse(s.is_valid("d1", "anything"))


class TestReplayWindow(unittest.TestCase):
    def test_increasing_seq_ok(self):
        w = ReplayWindow()
        for seq in range(1, 6):
            ok, _ = w.check_and_mark("d1", seq)
            self.assertTrue(ok)

    def test_duplicate_rejected(self):
        w = ReplayWindow()
        w.check_and_mark("d1", 5)
        ok, reason = w.check_and_mark("d1", 5)
        self.assertFalse(ok)
        self.assertIn("replay", reason)

    def test_old_within_window_but_unseen_ok(self):
        w = ReplayWindow()
        w.check_and_mark("d1", 10)
        ok, _ = w.check_and_mark("d1", 9)  # out of order, but unseen and within window
        self.assertTrue(ok)

    def test_too_old_rejected(self):
        w = ReplayWindow()
        w.check_and_mark("d1", 100)
        ok, reason = w.check_and_mark("d1", 1)  # 99 behind, window is 64
        self.assertFalse(ok)

    def test_devices_are_independent(self):
        w = ReplayWindow()
        w.check_and_mark("d1", 5)
        ok, _ = w.check_and_mark("d2", 5)
        self.assertTrue(ok)


class TestFreshnessTracker(unittest.TestCase):
    def test_first_message_establishes_baseline(self):
        f = FreshnessTracker()
        ok, reason = f.check("d1", uptime_ms=1000, received_at_ms=500000, max_delay_ms=5000)
        self.assertTrue(ok)

    def test_in_sync_clocks_pass(self):
        f = FreshnessTracker()
        f.check("d1", uptime_ms=1000, received_at_ms=500000, max_delay_ms=5000)
        ok, _ = f.check("d1", uptime_ms=2000, received_at_ms=501000, max_delay_ms=5000)
        self.assertTrue(ok)

    def test_held_and_replayed_message_flagged(self):
        f = FreshnessTracker()
        f.check("d1", uptime_ms=1000, received_at_ms=500000, max_delay_ms=5000)
        # uptime only advanced 1s but wall clock advanced 20s — held somewhere in transit
        ok, reason = f.check("d1", uptime_ms=2000, received_at_ms=520000, max_delay_ms=5000)
        self.assertFalse(ok)


class TestCorroborationBuffer(unittest.TestCase):
    def test_nothing_pending_returns_none(self):
        b = CorroborationBuffer()
        self.assertIsNone(b.resolve("d1", 100))

    def test_close_reading_confirms(self):
        b = CorroborationBuffer()
        b.hold("d1", seq=5, value=200, fields={"seq": 5})
        result = b.resolve("d1", 210)
        self.assertIsNotNone(result)
        held, confirmed = result
        self.assertTrue(confirmed)
        self.assertEqual(held.seq, 5)

    def test_far_reading_denies(self):
        b = CorroborationBuffer()
        b.hold("d1", seq=5, value=200, fields={"seq": 5})
        _, confirmed = b.resolve("d1", 400)
        self.assertFalse(confirmed)

    def test_resolve_clears_the_hold(self):
        b = CorroborationBuffer()
        b.hold("d1", seq=5, value=200, fields={"seq": 5})
        b.resolve("d1", 205)
        self.assertFalse(b.has_pending("d1"))


if __name__ == "__main__":
    unittest.main()
