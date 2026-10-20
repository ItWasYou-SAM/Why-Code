"""Candidate detection contracts.

Detection is deterministic and explainable; these tests pin the weights so
a silent rule change cannot quietly widen who reaches the LLM.
"""

from intent_helpers import REPO, records

from app.intent.selection.candidate_detector import CandidateClass, CandidateDetector
from app.intent.selection.evidence_selector import EvidenceSelector


def bundle(fixture_name: str):
    return EvidenceSelector(REPO).select(records(fixture_name))[0]


def signals_for(fixture_name: str) -> dict[str, int]:
    candidates = CandidateDetector().detect(bundle(fixture_name))
    signals: dict[str, int] = {}
    for candidate in candidates:
        for signal in candidate.signals:
            signals[signal.name] = signal.weight
    return signals


class TestRetryPr:
    def test_retry_pr_yields_decision_and_constraint(self) -> None:
        classes = {
            candidate.candidate_class
            for candidate in CandidateDetector().detect(bundle("retry_pr_evidence.json"))
        }
        assert CandidateClass.DECISION_CANDIDATE in classes
        assert CandidateClass.CONSTRAINT_CANDIDATE in classes
        assert CandidateClass.LOW_SIGNAL not in classes

    def test_plan_weights_are_pinned(self) -> None:
        signals = signals_for("retry_pr_evidence.json")
        assert signals["merged_pr"] == 2
        assert signals["multiple_review_participants"] == 3
        assert signals["line_anchored_review_comment"] == 4
        assert signals["linked_issue"] == 3
        assert signals["issue_discussion"] == 3
        assert signals["pr_body_motivation"] == 2
        assert signals["commit_rationale"] == 2
        assert signals["high_value_path"] == 3
        assert signals["docs_or_adr_changed"] == 4

    def test_every_candidate_carries_evidence(self) -> None:
        for candidate in CandidateDetector().detect(bundle("retry_pr_evidence.json")):
            assert candidate.evidence_ids, candidate.candidate_class
            assert candidate.reasons


class TestReversalPr:
    def test_revert_title_produces_reversal_candidate(self) -> None:
        candidates = CandidateDetector().detect(bundle("timeout_pr_evidence.json"))
        classes = {candidate.candidate_class for candidate in candidates}
        assert CandidateClass.REVERSAL_CANDIDATE in classes
        assert CandidateClass.INCIDENT_CANDIDATE in classes
        assert CandidateClass.DECISION_CANDIDATE in classes

    def test_reversal_title_scores_four(self) -> None:
        assert signals_for("timeout_pr_evidence.json")["reversal_title"] == 4


class TestLowSignal:
    def test_merged_only_pr_is_low_signal(self) -> None:
        minimal = [
            {
                "record_id": "httpx:pr:1",
                "record_type": "pull_request",
                "repo": REPO,
                "pr_number": 1,
                "payload": {
                    "number": 1,
                    "title": "Tidy whitespace",
                    "author": "a",
                    "merged": True,
                    "merged_at": "2024-01-01T00:00:00Z",
                    "url": "https://example.test/1",
                    "body": "",
                },
                "content_hash": "x",
                "ingested_at": "2026-10-19T10:00:00+00:00",
            }
        ]
        candidates = CandidateDetector().detect(
            EvidenceSelector(REPO).select(minimal)[0]
        )
        assert len(candidates) == 1
        assert candidates[0].candidate_class is CandidateClass.LOW_SIGNAL
        assert candidates[0].score == 2
        assert not candidates[0].actionable
