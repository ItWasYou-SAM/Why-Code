"""Regression: property accessors are distinct symbols.

Real files (httpx/_client.py among them) define a property and its setter
under the same bare name. A naive ``path@sha:qualname`` identity makes the
two collide, which fails validation and silently drops one of them from the
graph. These tests pin the fix.
"""

from __future__ import annotations

from structural_helpers import COMMIT, REPO

from app.structural.extraction.models import Language
from app.structural.extraction.symbol_extractor import extract_symbols
from app.structural.languages.registry import LanguageRegistry

SOURCE = b'''\
class BaseClient:
    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value

    def send(self):
        return 1
'''


def symbols():
    tree = LanguageRegistry().parse(Language.PYTHON, SOURCE)
    return extract_symbols(
        tree, SOURCE, repo=REPO, path="httpx/_client.py", commit_sha=COMMIT
    )


def test_getter_and_setter_do_not_collide() -> None:
    ids = [symbol.symbol_id for symbol in symbols()]
    assert len(ids) == len(set(ids)), "property and setter must have distinct IDs"


def test_both_accessors_are_kept() -> None:
    found = [symbol for symbol in symbols() if symbol.name == "timeout"]
    assert len(found) == 2
    assert {tuple(symbol.decorators) for symbol in found} == {
        ("property",),
        ("timeout.setter",),
    }


def test_plain_symbol_ids_are_unchanged() -> None:
    send = next(symbol for symbol in symbols() if symbol.name == "send")
    assert send.symbol_id == f"httpx:symbol:httpx/_client.py@{COMMIT}:BaseClient.send"


def test_accessor_role_only_applies_to_accessors() -> None:
    ids = {symbol.name: symbol.symbol_id for symbol in symbols()}
    assert ids["send"].endswith("BaseClient.send")
    assert not ids["send"].endswith(":setter")
    timeout_ids = {
        symbol.symbol_id
        for symbol in symbols()
        if symbol.name == "timeout"
    }
    assert any(identifier.endswith(":setter") for identifier in timeout_ids)
    assert any(not identifier.endswith(":setter") for identifier in timeout_ids)


def test_regions_stay_attached_to_the_right_accessor() -> None:
    from app.structural.extraction.region_extractor import regions_for_symbols

    found = symbols()
    regions = regions_for_symbols(
        found, repo=REPO, path="httpx/_client.py", commit_sha=COMMIT
    )
    spans = {(region.start_line, region.end_line) for region in regions}
    getter = next(symbol for symbol in found if symbol.decorators == ["property"])
    setter = next(symbol for symbol in found if symbol.decorators == ["timeout.setter"])
    assert (getter.start_line, getter.end_line) in spans
    assert (setter.start_line, setter.end_line) in spans
    assert getter.start_line != setter.start_line
