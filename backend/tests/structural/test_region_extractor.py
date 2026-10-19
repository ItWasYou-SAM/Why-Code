"""Region + reference contracts: bounded, owned, honest."""

from structural_helpers import COMMIT, REPO, fixture_bytes

from app.structural.extraction.file_extractor import extract_file
from app.structural.extraction.models import Language


def entry():
    return extract_file(
        repo=REPO,
        path="httpx/_client.py",
        commit_sha=COMMIT,
        source=fixture_bytes("sample_client.py").decode(),
        language=Language.PYTHON,
        known_paths={"httpx/_config.py", "httpx/_types.py"},
    )


class TestRegions:
    def test_every_symbol_has_a_region(self) -> None:
        result = entry()
        assert len(result.regions) == len(result.symbols)

    def test_region_ids_are_path_commit_and_line_scoped(self) -> None:
        result = entry()
        region = next(r for r in result.regions if r.symbol_qualname == "Client")
        assert region.region_id.startswith(
            f"httpx:region:httpx/_client.py@{COMMIT}:"
        )
        assert region.start_line >= 1
        assert region.end_line >= region.start_line

    def test_regions_are_unique(self) -> None:
        ids = [region.region_id for region in entry().regions]
        assert len(ids) == len(set(ids))

    def test_config_regions_use_the_declaration_line(self) -> None:
        result = extract_file(
            repo=REPO,
            path="config.yaml",
            commit_sha=COMMIT,
            source="default:\n  timeout: 5\n",
            language=Language.YAML,
        )
        assert {region.kind for region in result.regions} == {"config_key"}
        assert all(region.start_line == region.end_line for region in result.regions)


class TestReferences:
    def test_same_file_call_creates_a_reference(self) -> None:
        result = entry()
        pairs = {(ref.source_qualname, ref.name) for ref in result.references}
        assert ("Client.send", "_send") in pairs
        assert ("build_client", "Client") in pairs

    def test_reference_target_exists_in_the_file(self) -> None:
        result = entry()
        symbol_ids = {symbol.symbol_id for symbol in result.symbols}
        for reference in result.references:
            if reference.target_symbol_id is not None:
                assert reference.target_symbol_id in symbol_ids
            else:
                # Imported-but-cross-file: carried as path + qualname until
                # the batch resolver sees the defining file.
                assert reference.target_path
                assert reference.target_qualname
                assert reference.resolution == "imported"

    def test_builtins_do_not_create_references(self) -> None:
        result = entry()
        assert all(ref.name not in {"print", "len", "str"} for ref in result.references)

    def test_no_reference_without_a_call(self) -> None:
        result = entry()
        assert all(ref.name != "DEFAULT_TIMEOUT_CONFIG" for ref in result.references)
