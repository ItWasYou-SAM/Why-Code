"""Language registry: one parser per supported language, no surprises.

The registry owns the Tree-sitter Language objects because constructing
them is the only version-sensitive step. Everything downstream receives a
parsed tree, not a parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.errors import InvalidRecordError
from app.structural.extraction.models import Language


@dataclass(frozen=True)
class LanguageParser:
    language: Language
    parser: object

    def parse(self, source: bytes) -> object:
        tree = self.parser.parse(source)  # type: ignore[attr-defined]
        if tree.root_node.has_error:
            raise InvalidRecordError(
                "tree-sitter reported a syntax error",
                component="structural",
            )
        return tree


@lru_cache(maxsize=1)
def python_parser() -> LanguageParser:
    try:
        import tree_sitter_python
        from tree_sitter import Language as TsLanguage
        from tree_sitter import Parser
    except ImportError as error:  # pragma: no cover - dependency is declared
        raise InvalidRecordError(
            "tree-sitter-python is not installed", component="structural"
        ) from error
    parser = Parser(TsLanguage(tree_sitter_python.language()))
    return LanguageParser(language=Language.PYTHON, parser=parser)


class LanguageRegistry:
    """Routes a path to a parser; unknown languages are a hard skip."""

    def __init__(self) -> None:
        self._parsers = {Language.PYTHON: python_parser}

    def supports(self, language: Language) -> bool:
        return language in self._parsers

    def parser_for(self, language: Language) -> LanguageParser:
        try:
            factory = self._parsers[language]
        except KeyError as error:
            raise InvalidRecordError(
                f"no parser registered for {language}", component="structural"
            ) from error
        return factory()

    def parse(self, language: Language, source: bytes) -> object:
        return self.parser_for(language).parse(source)
