"""Import extraction contracts: resolution only when provable."""


from app.structural.extraction.import_extractor import extract_imports
from app.structural.extraction.models import ImportKind, Language
from app.structural.languages.registry import LanguageRegistry

KNOWN = {"httpx/_config.py", "httpx/_types.py", "httpx/__init__.py", "httpx/_client.py"}


def imports(source: str, *, path: str = "httpx/_client.py", known: set[str] | None = None):
    encoded = source.encode()
    tree = LanguageRegistry().parse(Language.PYTHON, encoded)
    return extract_imports(
        tree, encoded, path=path, known_paths=KNOWN if known is None else known
    )


class TestImportExtraction:
    def test_plain_import_is_absolute(self) -> None:
        ref = imports("import os\n")[0]
        assert ref.kind is ImportKind.ABSOLUTE
        assert ref.module == "os"
        assert ref.resolved_path is None

    def test_aliased_import_keeps_alias(self) -> None:
        ref = imports("import sys as system\n")[0]
        assert ref.module == "sys"
        assert ref.aliases == {"system": "sys"}

    def test_multiple_imports_on_one_line_are_split(self) -> None:
        refs = imports("import os, sys\n")
        assert [ref.module for ref in refs] == ["os", "sys"]

    def test_from_import_names_and_alias(self) -> None:
        ref = imports("from httpx._config import Timeout, Limits as L\n")[0]
        assert ref.module == "httpx._config"
        assert ref.names == ["Timeout", "Limits"]
        assert ref.aliases == {"L": "Limits"}
        assert ref.resolved_path == "httpx/_config.py"

    def test_relative_import_level(self) -> None:
        ref = imports("from ..base import Base\n", path="httpx/_client.py")[0]
        assert ref.kind is ImportKind.RELATIVE
        assert ref.level == 2
        assert ref.module == "base"

    def test_wildcard_import_is_marked(self) -> None:
        ref = imports("from httpx._types import *\n")[0]
        assert ref.wildcard is True

    def test_unresolvable_third_party_import_stays_external(self) -> None:
        ref = imports("import requests\n")[0]
        assert ref.resolved_path is None


class TestImportResolution:
    def test_absolute_module_resolves_to_file_in_batch(self) -> None:
        ref = imports("from httpx._config import Timeout\n")[0]
        assert ref.resolved_path == "httpx/_config.py"
        assert ref.resolved_module == "httpx._config"

    def test_relative_sibling_resolves(self) -> None:
        ref = imports("from . import _types\n", path="httpx/_client.py")[0]
        assert ref.resolved_path == "httpx/_types.py"

    def test_package_init_resolves(self) -> None:
        ref = imports("from httpx import Client\n", path="httpx/_client.py")[0]
        assert ref.resolved_path == "httpx/__init__.py"

    def test_no_known_paths_means_no_resolution(self) -> None:
        ref = imports("from httpx._config import Timeout\n", known=set())[0]
        assert ref.resolved_path is None

    def test_never_resolves_to_a_file_outside_the_known_set(self) -> None:
        ref = imports("from httpx._missing import X\n")[0]
        assert ref.resolved_path is None

    def test_line_numbers_are_one_based(self) -> None:
        refs = imports("import os\n\nimport sys\n")
        assert [ref.line for ref in refs] == [1, 3]
