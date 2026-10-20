"""Tests for the parts that must not break: filing, ledger, undo, fallbacks.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slothd import clerk  # noqa: E402
from slothd.runner import Runner, scan, slugify  # noqa: E402


class TestSlugify(unittest.TestCase):
    def test_makes_a_filename(self):
        self.assertEqual(slugify("Invoice: Akshat / Domain"), "invoice-akshat-domain")

    def test_never_empty(self):
        self.assertEqual(slugify("///"), "file")

    def test_truncates(self):
        self.assertLessEqual(len(slugify("x" * 200)), 48)


class TestParse(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(clerk._parse('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(clerk._parse('```json\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_leading_prose(self):
        self.assertEqual(clerk._parse('Sure! {"a": 1} hope that helps'), {"a": 1})

    def test_garbage_is_none(self):
        self.assertIsNone(clerk._parse("I could not decide."))


class TestClean(unittest.TestCase):
    def test_clamps_unknown_category(self):
        out = clerk._clean({"title": "x", "category": "urgent", "summary": "s"})
        self.assertEqual(out["category"], "reference")

    def test_clamps_confidence(self):
        out = clerk._clean({"title": "x", "category": "money", "confidence": 9})
        self.assertEqual(out["confidence"], 1.0)

    def test_survives_missing_fields(self):
        out = clerk._clean({})
        self.assertEqual(out["title"], "Untitled")
        self.assertEqual(out["category"], "reference")


class TestFallback(unittest.TestCase):
    def test_invoice_is_money(self):
        self.assertEqual(clerk.fallback("Invoice 899 INR due", "x.txt")["category"], "money")

    def test_code_is_code(self):
        self.assertEqual(clerk.fallback("import os\ndef main(): pass", "a.py")["category"], "code")

    def test_unknown_is_reference(self):
        self.assertEqual(clerk.fallback("zzz qqq", "a.txt")["category"], "reference")


class TestLedgerAndUndo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.inbox = os.path.join(self.tmp, "inbox")
        os.makedirs(self.inbox)
        self.var = os.path.join(self.tmp, "var")
        os.makedirs(self.var)

    def test_scan_dedupes_by_content(self):
        with open(os.path.join(self.inbox, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.inbox, "b.txt"), "w") as f:
            f.write("hello")
        queue = scan(self.inbox, set())
        self.assertEqual(len({q["hash"] for q in queue}), 1)
        self.assertEqual(len(queue), 2)

    def test_scan_skips_seen(self):
        path = os.path.join(self.inbox, "a.txt")
        with open(path, "w") as f:
            f.write("hello")
        first = scan(self.inbox, set())
        second = scan(self.inbox, {first[0]["hash"]})
        self.assertEqual(second, [])

    def test_undo_puts_the_file_back(self):
        runner = Runner(self.inbox, self.var, model="none")
        src = os.path.join(self.inbox, "receipt.txt")
        with open(src, "w") as f:
            f.write("receipt total 100 INR")
        item = scan(self.inbox, set())[0]

        # stand in for the model so the test needs no inference
        runner.store.start_run()
        runner.run_id = runner.store.last_run_entry()["id"]
        dest_dir = os.path.join(runner.outbox, "money")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "receipt-abc123.txt")
        os.replace(src, dest)
        runner.store.append({"run": runner.run_id, "hash": item["hash"], "src": src,
                             "dst": dest, "name": "receipt.txt", "category": "money",
                             "title": "Receipt", "summary": "s", "confidence": 0.9,
                             "source": "model", "seconds": 1.0})

        self.assertFalse(os.path.exists(src))
        restored = runner.store.undo_last_run()
        self.assertEqual(restored, 1)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))
        self.assertEqual(runner.store.entries(), [])

    def test_digest_states_the_payoff(self):
        runner = Runner(self.inbox, self.var, model="none")
        runner.store.append({"run": 1, "hash": "h", "src": "/tmp/x", "dst": "/tmp/y",
                             "name": "x", "category": "junk", "title": "x",
                             "summary": "s", "confidence": 0.9, "seconds": 1.0})
        lines = runner.store.digest()
        self.assertEqual(len(lines), 2)
        self.assertIn("1", lines[0])
        self.assertIn("minute", lines[0])


if __name__ == "__main__":
    unittest.main()
