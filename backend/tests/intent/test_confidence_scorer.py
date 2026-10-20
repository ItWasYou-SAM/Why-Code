"""Confidence scoring contracts: deterministic, categorical, auditable."""

from app.intent.models import Confidence
from app.intent.validation.confidence_scorer import (
    ConfidenceScorer,
    display_label,
)


def types(*values: str) -> list[str]:
    return list(values)


def item(record_type: str, author: str | None = None, **extra: object) -> dict:
    row: dict = {"record_type": record_type, **extra}
    if author is not None:
        row["author"] = author
    return row


class TestConfidenceScorer:
    def setup_method(self) -> None:
        self.scorer = ConfidenceScorer()

    def test_review_comment_alone_is_low(self) -> None:
        result = self.scorer.score(types("review_comment"))
        assert result.level is Confidence.LOW
        assert result.score == 0.20

    def test_merged_pr_comment_commit_and_diff_are_high(self) -> None:
        result = self.scorer.score(
            types("pull_request", "review_comment", "commit", "changed_file")
        )
        assert result.level is Confidence.HIGH
        assert result.score == 0.75
        assert set(result.source_types) == {
            "pull_request",
            "review_comment",
            "commit",
            "changed_file",
        }

    def test_comment_commit_and_diff_are_medium(self) -> None:
        result = self.scorer.score(
            types("review_comment", "commit", "changed_file")
        )
        assert result.level is Confidence.MEDIUM
        assert result.score == 0.55

    def test_commit_plus_diff_is_low(self) -> None:
        result = self.scorer.score(types("commit", "changed_file"))
        assert result.level is Confidence.LOW
        assert result.score == 0.35

    def test_issue_comment_commit_and_doc_are_low(self) -> None:
        result = self.scorer.score(types("issue_comment", "commit", "doc_file"))
        assert result.level is Confidence.LOW
        assert result.score == 0.40

    def test_pr_body_alone_is_low(self) -> None:
        result = self.scorer.score(types("pull_request"))
        assert result.level is Confidence.LOW
        assert result.score == 0.20

    def test_single_commit_is_low(self) -> None:
        result = self.scorer.score(types("commit"))
        assert result.level is Confidence.LOW
        assert result.score == 0.15

    def test_no_evidence_is_insufficient(self) -> None:
        result = self.scorer.score([])
        assert result.level is Confidence.INSUFFICIENT
        assert result.score == 0.0
        assert "no usable evidence" in result.reasons[0]

    def test_unmerged_pull_request_does_not_earn_merged_pr_points(self) -> None:
        result = self.scorer.score(
            [
                {
                    "record_type": "pull_request",
                    "merged": False,
                    "author": "someone",
                }
            ]
        )
        assert result.level is Confidence.INSUFFICIENT
        assert result.score == 0.0

    def test_independent_authors_add_a_capped_bonus(self) -> None:
        two = self.scorer.score(
            [
                item("pull_request", "alice", merged=True),
                item("changed_file", "bob"),
            ]
        )
        three = self.scorer.score(
            [
                item("pull_request", "alice", merged=True),
                item("changed_file", "bob"),
                item("review_comment", "carol"),
            ]
        )
        many = self.scorer.score(
            [
                item("pull_request", author)
                for author in ("alice", "bob", "carol", "dave", "erin")
            ]
        )
        assert two.score == 0.43
        assert three.score == 0.66
        # Bonus is capped at three extra authors even with five logins.
        assert many.score == 0.29

    def test_uncertain_status_discounts_the_score(self) -> None:
        evidence = types("pull_request", "review_comment", "changed_file")
        accepted = self.scorer.score(evidence, status="accepted")
        uncertain = self.scorer.score(evidence, status="uncertain")
        assert accepted.score == 0.60
        assert uncertain.score == 0.36
        assert uncertain.level is Confidence.LOW
        assert any("uncertain" in reason for reason in uncertain.reasons)

    def test_score_is_capped_below_one(self) -> None:
        result = self.scorer.score(
            types(
                "pull_request",
                "commit",
                "changed_file",
                "review_comment",
                "issue_comment",
                "doc_file",
            )
        )
        assert result.score == 0.98
        assert result.level is Confidence.HIGH

    def test_ui_labels_are_categorical_not_percentages(self) -> None:
        assert display_label(Confidence.HIGH) == "High confidence"
        assert display_label(Confidence.MEDIUM) == "Medium confidence"
        assert display_label(Confidence.LOW) == "Low confidence"
        assert display_label(Confidence.INSUFFICIENT) == "Insufficient evidence"

    def test_confidence_is_not_accepted_from_the_model(self) -> None:
        # The scorer has no parameter for a model-supplied value; this test
        # documents the API shape rather than a runtime check.
        import inspect

        signature = inspect.signature(self.scorer.score)
        assert "confidence" not in signature.parameters
        assert "model_confidence" not in signature.parameters
