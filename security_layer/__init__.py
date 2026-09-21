"""
security_layer — a software-only, zero-trust ingest gateway for IoT telemetry.

An edge device signs and locally queues every reading, sends it in a canonical
plaintext+HMAC wire format, and only drops it from its queue once it holds a
Merkle inclusion proof it has verified for itself. The gateway runs every
message through nine sequential validation gates before a tenth step commits
it to a hash-chained, provable audit log.

See pipeline.Gateway for the entry point, or run `python -m security_layer.demo`
for an end-to-end walkthrough.
"""
