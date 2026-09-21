"""
run_tests.py — regression suite for the security_layer gateway.
Usage:
    python -m security_layer.run_tests
"""

import sys
import unittest


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 70)
    print("security_layer | Gateway Regression Test Suite")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.discover("security_layer/tests", pattern="test_*.py", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors
    print("=" * 70)
    print(f"Total: {total}  Passed: {passed}  Failures: {failures}  Errors: {errors}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
