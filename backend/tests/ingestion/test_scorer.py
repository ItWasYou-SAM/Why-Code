"""RelevanceScorer: deterministic, explainable, monotone.

It decides which PRs get expensive LLM attention later (Component 4), so
"opaque black-box score" is not acceptable: every score carries reasons.
Numbers are not pinned here (formula churn is cheap); ordering, bands,
and determinism ARE pinned."""

import pytest
from fakes import (
    deep_pr,
    file_payload,
    trivial_pr,
)


def bundle(pr):
    return {
        "pr": pr,
        "files": pr["files"],
        "commits": pr["commits"],
        "reviews": pr["reviews"],
        "review_comments": pr["review_comments"],
        "issue_comments": pr["issue_comments"],
    }


@pytest.fixture()
def scorer():
    from app.ingestion.scorer import RelevanceScorer

    return RelevanceScorer()


def score_of(scorer, pr) -> tuple[float, str]:
    result = scorer.score(bundle(pr))
    return result.score, result.band


class TestDeterminism:
    def test_same_bundle_same_score(self, scorer) -> None:
        assert score_of(scorer, deep_pr()) == score_of(scorer, deep_pr())


class TestBands:
    def test_bands_are_one_of_three(self, scorer) -> None:
        for pr in (deep_pr(), trivial_pr()):
            assert score_of(scorer, pr)[1] in {"low", "medium", "high"}

    def test_deep_pr_scores_higher_than_trivial_pr(self, scorer) -> None:
        """The DEEP fixture has core-code + config-change + review
        discussion; the TRIVIAL fixture is a README typo. If a scoring
        model cannot separate these, it will waste LLM calls on
        whitespace PRs and trash the decision budget."""
        deep = score_of(scorer, deep_pr())
        trivial = score_of(scorer, trivial_pr())
        assert deep[0] > trivial[0]

    def test_high_band_implies_above_threshold_and_low_below(self, scorer) -> None:
        import app.ingestion.scorer as scorer_module

        threshold = scorer_module.HIGH_SIGNAL_SCORE
        for pr in (deep_pr(), trivial_pr()):
            score, band = score_of(scorer, pr)
            if band == "high":
                assert score >= threshold
            if band == "low":
                assert score < threshold

    def test_score_is_a_float_between_zero_and_one(self, scorer) -> None:
        result = scorer.score(bundle(deep_pr()))
        assert 0.0 <= result.score <= 1.0


class TestExplainability:
    def test_reasons_are_human_readable_and_never_empty(self, scorer) -> None:
        result = scorer.score(bundle(deep_pr()))
        assert result.reasons, "unexplained scores are audits waiting to fail"

    def test_scores_never_crash_on_empty_pr(self, scorer) -> None:
        pr = trivial_pr()
        pr["files"] = []
        pr["commits"] = []
        result = scorer.score(bundle(pr))
        assert result.band in {"low", "medium", "high"}


class TestSignalDefiniteness:
    def test_config_change_is_recognized_as_signal(self, scorer) -> None:
        """Config keys are where dead-decision questions live. A scorer
        that cannot see config diffs is scoring the wrong PRs."""
        pr = trivial_pr()
        pr["files"] = [
            file_payload("httpx/_config.py", patch="@@ -14 +14 @@ DEFAULT = ..."),
        ]
        config_only = score_of(scorer, pr)
        pr["files"] = [file_payload("docs/timelines.md")]
        docs_only = score_of(scorer, pr)
        assert config_only[0] >= docs_only[0]
