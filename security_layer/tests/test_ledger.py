import unittest

from security_layer.ledger import MerkleAuditLog


class TestMerkleAuditLog(unittest.TestCase):
    def test_flush_empty_returns_none(self):
        log = MerkleAuditLog()
        self.assertIsNone(log.flush())

    def test_single_record_proof_verifies(self):
        log = MerkleAuditLog()
        rid = log.append({"device_id": "d1", "seq": 1, "value": 200})
        log.flush()
        receipt = log.get_receipt(rid)
        self.assertTrue(log.verify_receipt(receipt))

    def test_multi_record_batch_all_verify(self):
        log = MerkleAuditLog()
        ids = [log.append({"device_id": "d1", "seq": i, "value": 200 + i}) for i in range(7)]
        log.flush()
        for rid in ids:
            receipt = log.get_receipt(rid)
            self.assertTrue(log.verify_receipt(receipt), f"record {rid} failed to verify")

    def test_forged_leaf_hash_fails(self):
        log = MerkleAuditLog()
        rid = log.append({"device_id": "d1", "seq": 1, "value": 200})
        log.flush()
        receipt = log.get_receipt(rid)
        receipt["leaf_hash"] = "0" * 64
        self.assertFalse(log.verify_receipt(receipt))

    def test_forged_root_fails(self):
        log = MerkleAuditLog()
        rid = log.append({"device_id": "d1", "seq": 1, "value": 200})
        log.flush()
        receipt = log.get_receipt(rid)
        receipt["root"] = "f" * 64
        self.assertFalse(log.verify_receipt(receipt))

    def test_proof_from_wrong_record_fails(self):
        log = MerkleAuditLog()
        a = log.append({"device_id": "d1", "seq": 1, "value": 200})
        b = log.append({"device_id": "d1", "seq": 2, "value": 205})
        log.flush()
        receipt_a = log.get_receipt(a)
        receipt_b = log.get_receipt(b)
        mixed = dict(receipt_a)
        mixed["leaf_hash"] = receipt_b["leaf_hash"]
        self.assertFalse(log.verify_receipt(mixed))

    def test_chain_integrity_across_batches(self):
        log = MerkleAuditLog()
        log.append({"seq": 1})
        log.flush()
        log.append({"seq": 2})
        log.flush()
        ok, _ = log.verify_chain_integrity()
        self.assertTrue(ok)

    def test_chain_integrity_detects_tampering(self):
        log = MerkleAuditLog()
        log.append({"seq": 1})
        log.flush()
        log.append({"seq": 2})
        log.flush()
        log._batches[0]["root"] = "e" * 64  # simulate a rewritten earlier batch
        ok, _ = log.verify_chain_integrity()
        self.assertFalse(ok)

    def test_get_receipt_for_unflushed_record_is_none(self):
        log = MerkleAuditLog()
        rid = log.append({"seq": 1})
        self.assertIsNone(log.get_receipt(rid))


if __name__ == "__main__":
    unittest.main()
