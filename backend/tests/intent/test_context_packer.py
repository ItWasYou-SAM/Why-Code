"""Context packing contracts: hard budget, exact evidence IDs, whole lines."""

from intent_helpers import REPO, records

from app.intent.context.context_packer import ContextPacker
from app.intent.models import TargetOption, TargetType
from app.intent.selection.candidate_detector import CandidateDetector
from app.intent.selection.evidence_selector import EvidenceSelector


def retry_bundle():
    return EvidenceSelector(REPO).select(records("retry_pr_evidence.json"))[0]


def retry_candidate():
    return CandidateDetector().detect(retry_bundle())[0]


class TestPacking:
    def test_pack_includes_exact_evidence_ids(self) -> None:
        bundle = retry_bundle()
        pack = ContextPacker().pack(bundle, retry_candidate())
        assert set(pack.evidence_ids()) <= set(bundle.evidence_ids())
        assert bundle.pr.record_id in pack.evidence_ids()
        text = pack.text()
        for evidence_id in pack.evidence_ids():
            assert evidence_id in text

    def test_pack_respects_the_token_budget(self) -> None:
        packer = ContextPacker(max_tokens=500)
        pack = packer.pack(retry_bundle(), retry_candidate())
        assert pack.estimated_tokens <= 500
        assert pack.estimated_tokens == packer.budget.estimate(pack.text())

    def test_omitted_items_are_reported_not_hidden(self) -> None:
        packer = ContextPacker(max_tokens=250)
        pack = packer.pack(retry_bundle(), retry_candidate())
        assert pack.omitted_ids
        assert not set(pack.omitted_ids) & set(pack.evidence_ids())

    def test_diff_hunks_are_rendered_with_headers(self) -> None:
        pack = ContextPacker().pack(retry_bundle(), retry_candidate())
        assert "@@ -100,6 +100,12 @@" in pack.text()
        assert "httpx/_client.py" in pack.text()

    def test_target_catalogue_is_copy_only(self) -> None:
        options = [
            TargetOption(
                type=TargetType.SYMBOL,
                identifier="httpx._client.Client._send_handling_redirects",
                graph_key="httpx:symbol:httpx/_client.py@aaa:Client._send_handling_redirects",
                path="httpx/_client.py",
                start_line=100,
                end_line=140,
            ),
            TargetOption(
                type=TargetType.CONFIG_KEY,
                identifier="retries",
                graph_key="httpx:config:httpx/_config.py:retries",
                path="httpx/_config.py",
            ),
        ]
        pack = ContextPacker().pack(
            retry_bundle(), retry_candidate(), target_options=options
        )
        assert "TARGETS" in pack.text()
        assert "httpx:symbol:httpx/_client.py@aaa:Client._send_handling_redirects" in pack.text()
        assert "ConfigKey: retries" in pack.text()

    def test_irrelevant_targets_are_dropped(self) -> None:
        options = [
            TargetOption(
                type=TargetType.FILE,
                identifier="other/file.py",
                graph_key="httpx:file:other/file.py@b",
                path="other/file.py",
            )
        ]
        pack = ContextPacker().pack(
            retry_bundle(), retry_candidate(), target_options=options
        )
        assert "other/file.py" not in pack.text()
