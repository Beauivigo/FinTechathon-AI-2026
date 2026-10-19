"""D 问卷评分接入验收：边界、拒绝、幂等与绿色写直通。"""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request
from shared.contracts import validate_result

LETTERS = {1: "a", 2: "b", 3: "c", 4: "d"}


def answers(scores):
    return {f"q{i}": f"q{i}_{LETTERS[score]}" for i, score in enumerate(scores, 1)}


BOUNDARIES = [
    ((1, 1, 1, 1, 1), "R1"),
    ((1, 2, 1, 2, 2), "R1"),
    ((2, 2, 1, 2, 2), "R2"),
    ((3, 3, 2, 2, 2), "R2"),
    ((3, 3, 3, 2, 2), "R3"),
    ((3, 3, 3, 3, 4), "R3"),
    ((4, 4, 3, 3, 3), "R4"),
    ((4, 4, 4, 3, 3), "R4"),
    ((4, 4, 4, 4, 3), "R5"),
    ((4, 4, 4, 4, 4), "R5"),
]


class WealthAssessTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "assess.sqlite3")
        initialize(self.store)
        self.now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)

    def request(self, payload, key=None):
        return adapt_request("wealth.assess", payload, request_id="req_" + uuid4().hex,
                             trace_id="trace_" + uuid4().hex, idempotency_key=key or "assess_" + uuid4().hex)

    def assess(self, payload, key=None):
        request = self.request(payload, key)
        result = self.g.execute(request, principal_id="demo_user_a")
        validate_result(request, result)
        return result

    def test_boundaries_map_to_levels(self):
        for scores, level in BOUNDARIES:
            with self.subTest(scores=scores):
                result = self.assess({"questionnaire_version": "wpq_v1", "answers": answers(scores)})
                self.assertEqual(result["status"], "succeeded", result)
                self.assertEqual(result["data"]["risk_level"], level)
                self.assertRegex(result["data"]["risk_profile_id"], r"^profile_[0-9a-f]{32}$")

    def test_green_write_executes_without_challenge(self):
        result = self.assess({"questionnaire_version": "wpq_v1", "answers": answers((3, 3, 3, 2, 2))})
        self.assertEqual(result["status"], "succeeded")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM challenges").fetchone()[0], 0)
            row = c.execute("SELECT * FROM risk_profiles WHERE profile_id=?",
                            (result["data"]["risk_profile_id"],)).fetchone()
        self.assertEqual(row["total_score"], 13)
        self.assertEqual(row["risk_level"], "R3")
        self.assertEqual(row["principal_id"], "demo_user_a")

    def test_missing_unknown_and_version_rejected(self):
        bad = answers((2, 2, 2, 2, 2))
        missing = dict(bad); missing.pop("q5")
        unknown_choice = dict(bad, q1="q1_z")
        extra = dict(bad, q6="q6_a")
        for payload in (
            {"questionnaire_version": "wpq_v1", "answers": missing},
            {"questionnaire_version": "wpq_v1", "answers": unknown_choice},
            {"questionnaire_version": "wpq_v1", "answers": extra},
            {"questionnaire_version": "wpq_v2", "answers": bad},
        ):
            with self.subTest(payload=payload):
                result = self.assess(payload)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM risk_profiles").fetchone()[0], 0)

    def test_same_key_replays_profile(self):
        request = self.request({"questionnaire_version": "wpq_v1", "answers": answers((4, 4, 4, 4, 4))},
                               key="fixed_assess")
        first = self.g.execute(request, principal_id="demo_user_a")
        second = self.g.execute(request, principal_id="demo_user_a")
        self.assertEqual(first, second)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM risk_profiles").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
