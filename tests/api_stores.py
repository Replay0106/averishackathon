"""Keep API tests off the real audit ledger and amendment outbox.

api.main creates its `ledger` (audit_ledger.json) and `outbox` (.cache/amendments.db) at import time, and
ensure_amendments() records into them whenever a dataset is loaded. Tests that go through the app call
isolate_api_stores() in setUp so those writes land in a temporary directory instead.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sdoc_amendment import AmendmentOutbox
from sdoc_security import TamperEvidentAuditLedger


def isolate_api_stores(test: unittest.TestCase, tmp: Path = None) -> Path:
    """Patch api.main.ledger and api.main.outbox with fresh local instances for the rest of `test`.
    Returns the directory holding them (a new temporary one, removed on cleanup, unless `tmp` is given)."""
    import api.main as m

    if tmp is None:
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for name, value in (("ledger", TamperEvidentAuditLedger(str(tmp / "ledger.json"))),
                        ("outbox", AmendmentOutbox(str(tmp / "amendments.db")))):
        p = mock.patch.object(m, name, value)
        p.start()
        test.addCleanup(p.stop)
    return tmp
