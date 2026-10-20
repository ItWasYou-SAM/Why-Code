"""Phase 3 schema-gate contracts.

The provider schema is pinned directly: required keys, enums, length limits
and ``additionalProperties: false`` at every level. Cross-field rules that
JSON Schema cannot express are checked through ``SchemaValidator``.
"""

from __future__ import annotations

from app.intent.llm.schemas import CandidateExtraction, ExtractedCandidate
from app.intent.validation.schema_validator import SchemaValidator


def valid_candidate() -> dict:
    return {
        "claim": "Retries are limited to connection errors.",
        "decision_type": "design_decision",
        "status": "accepted",
        "targets": [{"target_type": "symbol", "target_id": "Client.send"}],
        "evidence_ids": ["httpx:review_comment:123"],
        "participants": [
            {
                "login": "reviewer",
                "role": "reviewer",
                "evidence_ids": ["httpx:review_comment:123"],
            }
        ],
        "rationale": "A retried read can duplicate side effects.",
        "temporal_scope": {"valid_from": "2024-04-02", "valid_until": None},
    }


def valid_payload() -> dict:
    return {"candidates": [valid_candidate()], "missing_evidence": []}


class TestProviderSchema:
    def test_top_level_shape_is_strict(self) -> None:
        schema = CandidateExtraction.model_json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"candidates", "missing_evidence"}
        assert schema["properties"]["candidates"]["type"] == "array"

    def test_candidate_shape_is_strict(self) -> None:
        schema = ExtractedCandidate.model_json_schema()
        assert schema["additionalProperties"] is False
        assert all(
            definition["additionalProperties"] is False
            for definition in schema["$defs"].values()
        )
        assert set(schema["required"]) == {
            "claim",
            "decision_type",
            "status",
            "targets",
            "evidence_ids",
            "participants",
            "rationale",
            "temporal_scope",
        }
        assert schema["properties"]["claim"]["maxLength"] == 240
        assert schema["properties"]["rationale"]["maxLength"] == 800
        assert schema["properties"]["evidence_ids"]["minItems"] == 1

    def test_decision_type_enum_matches_the_plan(self) -> None:
        schema = ExtractedCandidate.model_json_schema()
        assert schema["$defs"]["CandidateTarget"]["additionalProperties"] is False
        assert set(
            schema["properties"]["decision_type"]["enum"]
        ) == {
            "design_decision",
            "constraint",
            "reversal",
            "incident_response",
            "api_contract",
            "operational_policy",
        }

    def test_status_and_role_enums_match_the_plan(self) -> None:
        schema = ExtractedCandidate.model_json_schema()
        assert set(schema["properties"]["status"]["enum"]) == {
            "accepted",
            "rejected",
            "reversed",
            "revised",
            "uncertain",
        }
        role_schema = schema["$defs"]["CandidateParticipant"]["properties"]["role"]
        assert set(role_schema["enum"]) == {
            "author",
            "reviewer",
            "advocate",
            "objector",
            "maintainer",
            "commenter",
        }
        assert set(
            schema["$defs"]["CandidateTarget"]["properties"]["target_type"]["enum"]
        ) == {"file", "symbol", "config_key", "code_region"}


class TestSchemaValidator:
    def test_valid_payload_passes(self) -> None:
        result = SchemaValidator().validate(CandidateExtraction, valid_payload())
        assert result.ok
        assert result.model is not None

    def test_model_supplied_confidence_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["confidence"] = "high"
        result = SchemaValidator().validate(CandidateExtraction, payload)
        assert not result.ok
        assert any("confidence" in issue for issue in result.issues)

    def test_model_supplied_graph_key_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["graph_key"] = "httpx:symbol:x"
        assert not SchemaValidator().validate(CandidateExtraction, payload).ok

    def test_empty_evidence_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["evidence_ids"] = []
        assert not SchemaValidator().validate(CandidateExtraction, payload).ok

    def test_duplicate_evidence_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["evidence_ids"] = [
            "httpx:commit:a",
            "httpx:commit:a",
        ]
        assert not SchemaValidator().validate(CandidateExtraction, payload).ok

    def test_unknown_target_type_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["targets"] = [
            {"target_type": "database", "target_id": "users"}
        ]
        assert not SchemaValidator().validate(CandidateExtraction, payload).ok

    def test_claim_over_240_characters_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["claim"] = "x" * 241
        assert not SchemaValidator().validate(CandidateExtraction, payload).ok

    def test_rationale_restating_claim_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["rationale"] = payload["candidates"][0]["claim"]
        result = SchemaValidator().validate(CandidateExtraction, payload)
        assert not result.ok
        assert any("restating" in issue for issue in result.issues)

    def test_citing_an_intent_id_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["evidence_ids"] = ["dec_httpx_retry_abc"]
        result = SchemaValidator().validate(CandidateExtraction, payload)
        assert not result.ok
        assert any("intent" in issue or "wrapper" in issue for issue in result.issues)

    def test_participant_evidence_outside_the_candidate_is_rejected(self) -> None:
        payload = valid_payload()
        payload["candidates"][0]["participants"] = [
            {
                "login": "reviewer",
                "role": "reviewer",
                "evidence_ids": ["httpx:review_comment:999"],
            }
        ]
        result = SchemaValidator().validate(CandidateExtraction, payload)
        assert not result.ok
        assert any("not in" in issue for issue in result.issues)

    def test_missing_evidence_is_required(self) -> None:
        payload = {"candidates": [valid_candidate()]}
        result = SchemaValidator().validate(CandidateExtraction, payload)
        assert not result.ok
        assert any("missing_evidence" in issue for issue in result.issues)
