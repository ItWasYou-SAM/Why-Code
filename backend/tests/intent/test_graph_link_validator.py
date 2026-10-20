"""Target-resolution contracts for the Phase 3 target vocabulary."""

from __future__ import annotations

from intent_helpers import REPO, records, structural_records

from app.intent.llm.schemas import CandidateTarget
from app.intent.validation.graph_link_validator import GraphIndex, GraphLinkValidator


def validator() -> GraphLinkValidator:
    fixture = records("retry_pr_evidence.json")
    index = GraphIndex.from_sources(
        repo=REPO,
        normalized_records=fixture,
        structural_records=structural_records(),
    )
    return GraphLinkValidator(index)


class TestTargetVocabulary:
    def test_file_target_falls_back_to_the_changed_file_set(self) -> None:
        result = validator().validate_targets(
            [CandidateTarget(target_type="file", target_id="httpx/_client.py")],
            prefer_paths={"httpx/_client.py"},
        )
        assert not result.issues
        assert result.targets[0].graph_key == (
            "httpx:pr:1419:file:httpx/_client.py"
        )

    def test_symbol_and_config_key_targets_resolve_exactly(self) -> None:
        result = validator().validate_targets(
            [
                CandidateTarget(
                    target_type="symbol",
                    target_id="httpx._client.Client._send_handling_redirects",
                ),
                CandidateTarget(target_type="config_key", target_id="retries"),
            ],
            prefer_paths={"httpx/_config.py"},
        )
        assert not result.issues
        types = {target.type.value for target in result.targets}
        assert types == {"Symbol", "ConfigKey"}

    def test_unknown_symbol_is_rejected(self) -> None:
        result = validator().validate_targets(
            [CandidateTarget(target_type="symbol", target_id="Missing.send")]
        )
        assert result.issues
        assert result.dropped == ["symbol:Missing.send"]
