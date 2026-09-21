import unittest

from security_layer import wire


class TestWireFormat(unittest.TestCase):
    def test_roundtrip(self):
        raw = wire.encode("dev1", "sess1", 42, "TEMP", 223, 8600, b"secret")
        fields = wire.parse(raw)
        self.assertEqual(fields["device_id"], "dev1")
        self.assertEqual(fields["session_id"], "sess1")
        self.assertEqual(fields["seq"], 42)
        self.assertEqual(fields["metric"], "TEMP")
        self.assertEqual(fields["scaled_value"], 223)
        self.assertEqual(fields["uptime_ms"], 8600)
        self.assertTrue(wire.verify(b"secret", fields["header"], fields["mac"]))

    def test_negative_scaled_value(self):
        raw = wire.encode("dev1", "sess1", 1, "TEMP", -55, 100, b"secret")
        fields = wire.parse(raw)
        self.assertEqual(fields["scaled_value"], -55)

    def test_tamper_breaks_hmac(self):
        raw = wire.encode("dev1", "sess1", 1, "TEMP", 200, 100, b"secret")
        forged = raw.replace(" 200 ", " 999 ")
        fields = wire.parse(forged)
        self.assertFalse(wire.verify(b"secret", fields["header"], fields["mac"]))

    def test_wrong_key_fails(self):
        raw = wire.encode("dev1", "sess1", 1, "TEMP", 200, 100, b"secret")
        fields = wire.parse(raw)
        self.assertFalse(wire.verify(b"wrong-key", fields["header"], fields["mac"]))

    def test_rejects_wrong_field_count(self):
        with self.assertRaises(wire.WireFormatError):
            wire.parse("T dev1 sess1 1 TEMP 200")

    def test_rejects_bad_grammar(self):
        with self.assertRaises(wire.WireFormatError):
            wire.parse("X dev1 sess1 1 TEMP 200 100 " + "a" * 64)

    def test_rejects_non_hex_mac(self):
        with self.assertRaises(wire.WireFormatError):
            wire.parse("T dev1 sess1 1 TEMP 200 100 not-a-hex-digest")

    def test_rejects_space_in_device_id(self):
        with self.assertRaises(wire.WireFormatError):
            wire.encode_header("dev 1", "sess1", 1, "TEMP", 200, 100)


if __name__ == "__main__":
    unittest.main()
