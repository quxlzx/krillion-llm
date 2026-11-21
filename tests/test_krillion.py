"""Offline tests for the blind Krillion engine — no network, no cheating.

The fake API records every call so the tests can assert the honesty
guarantees: answer-key endpoints are never touched before all 7 answers
are in, one shot per prompt, and live days never print or post.
"""
import base64
import datetime
import io
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import krillion  # noqa: E402


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def fake_today(resets_at):
    return {"date": "2099-01-01", "dayNumber": 999,
            "resetsAt": _iso(resets_at),
            "prompts": [{"id": f"p{i}", "text": f"prompt {i}"}
                        for i in range(1, 8)]}


def fake_receipt(score=60):
    payload = {"v": 2, "scope": "daily", "game": 999,
               "playerId": "a" * 32, "score": score, "issuedAt": 0}
    tok = base64.urlsafe_b64encode(
        json.dumps(payload).encode()).rstrip(b"=").decode()
    return tok + ".sig"


class FakeAPI:
    def __init__(self, resets_at):
        self.calls = []
        self.today = fake_today(resets_at)
        self.reject_ids = set()  # promptIds that come back as misses

    def __call__(self, url, data=None):
        self.calls.append((url, data))
        if url == "https://krillion.io/api/today":
            return self.today
        if url.startswith("https://krillion.io/api/submit"):
            pid = data["promptId"]
            if pid in self.reject_ids:
                return {"accepted": False, "receipt": fake_receipt(0)}
            return {"accepted": True, "answer": data["answer"].title(),
                    "tier": "rare", "score": 60,
                    "receipt": fake_receipt(60)}
        if url.startswith("https://krillion.io/api/today/complete"):
            return {"sample": 100, "betterThan": 50, "dist": [100]}
        if url.startswith("https://krillion.io/api/reveal"):
            return {"date": "2099-01-01", "prompts": [
                {"id": f"p{i}", "text": f"prompt {i}",
                 "answers": [{"answer": "Deepcut"}]} for i in range(1, 8)]}
        if url.startswith("https://krillion.io/api/answer-popularity"):
            return {"date": "2099-01-01", "asOf": "x",
                    "prompts": {f"p{i}": {"Deepcut": 1} for i in
                                range(1, 8)}}
        raise AssertionError(f"unexpected URL: {url}")

    def urls(self):
        return [c[0] for c in self.calls]

    def peeked(self):
        return [u for u in self.urls()
                if "reveal" in u or "answer-popularity" in u]


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._state = krillion.STATE
        krillion.STATE = self.tmp.name
        self._curl = krillion.curl

    def tearDown(self):
        krillion.STATE = self._state
        krillion.curl = self._curl
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _live_api(self):
        api = FakeAPI(datetime.datetime.now(
            datetime.timezone.utc) + datetime.timedelta(hours=1))
        krillion.curl = api
        return api

    def _answer_all(self, api):
        st = krillion._load()
        for i in range(1, 8):
            krillion.cmd_answer(st, f"p{i}", f"answer {i}")
            st = krillion._load()
        return st

    def test_runid_format(self):
        for _ in range(5):
            _, run = krillion._new_ids()
            self.assertRegex(run, r"^run_[0-9a-z]{10}_[0-9a-z]{24}$")

    def test_receipt_payload_decodes(self):
        pl = krillion._receipt_payload(fake_receipt(85))
        self.assertEqual(pl["score"], 85)
        self.assertEqual(pl["game"], 999)

    def test_one_shot_per_prompt(self):
        api = self._live_api()
        st = krillion._load()
        self.assertEqual(krillion.cmd_answer(st, "p1", "first"), 0)
        st = krillion._load()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = krillion.cmd_answer(st, "p1", "second attempt")
        finally:
            sys.stdout = old
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", buf.getvalue())
        submits = [c for c in api.calls if "submit" in c[0]]
        self.assertEqual(len(submits), 1, "engine must submit each prompt once")

    def test_miss_scores_zero(self):
        api = self._live_api()
        api.reject_ids = {"p3"}
        st = krillion._load()
        rc = krillion.cmd_answer(st, "p3", "gibberish zzz")
        self.assertEqual(rc, 0)
        st = krillion._load()
        self.assertEqual(st["days"]["2099-01-01"]["answers"]["p3"]["score"], 0)
        self.assertFalse(st["days"]["2099-01-01"]["answers"]["p3"]["accepted"])

    def test_summary_locks_answer_key_until_complete(self):
        api = self._live_api()
        st = self._answer_all(api)
        before = len(api.peeked())
        self.assertEqual(before, 0,
                         "answer-key endpoints touched before game complete")
        krillion.cmd_summary(st)
        # after completion the key may be fetched exactly once each
        self.assertEqual(len(api.peeked()), 2)

    def test_summary_locked_before_seven(self):
        api = self._live_api()
        st = krillion._load()
        krillion.cmd_answer(st, "p1", "one")
        st = krillion._load()
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = krillion.cmd_summary(st)
        finally:
            sys.stdout = old
        self.assertEqual(rc, 1)
        self.assertIn("LOCKED", buf.getvalue())
        self.assertEqual(api.peeked(), [])

    def test_recap_refuses_live_day(self):
        api = self._live_api()
        self._answer_all(api)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = krillion.cmd_recap(krillion._load())
        finally:
            sys.stdout = old
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", buf.getvalue())
        self.assertIn("still LIVE", buf.getvalue())

    def test_recap_prints_closed_day(self):
        api = self._live_api()
        st = self._answer_all(api)
        # flip the day to closed by moving resetsAt into the past
        st = krillion._load()
        d = st["days"]["2099-01-01"]
        d["resetsAt"] = _iso(datetime.datetime.now(
            datetime.timezone.utc) - datetime.timedelta(hours=1))
        krillion._save(st)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = krillion.cmd_recap(krillion._load())
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("420/700", out)  # 7 x 60
        self.assertIn("Deepcut", out)  # intended deepest cuts line

    def test_post_refuses_live_day(self):
        api = self._live_api()
        self._answer_all(api)
        buf = io.StringIO()
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO("recap text"), buf
        try:
            rc = krillion.cmd_post(krillion._load(),
                                   "https://example.invalid/webhook")
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", buf.getvalue())

    def test_commitment_binds_prompts(self):
        api = self._live_api()
        st = krillion._load()
        d = krillion._init_day(st)
        import hashlib
        ptexts = "|".join(p["text"] for p in d["prompts"])
        want = hashlib.sha256(
            (d["nonce"] + d["date"] + ptexts).encode()).hexdigest()
        self.assertEqual(d["commit"], want)


if __name__ == "__main__":
    unittest.main()
