"""Project parsed structural facts onto the Phase 5 output contract.

The graph-facing ``StructuralFile`` still carries path+commit on every fact.
The parser-output models carry resolved IDs instead. This module is the one
place that maps between them, so a future parser can emit the output models
directly without changing the graph builder.

Rules encoded here (Phase 6):

* symbol names are module-qualified (``httpx._client.Client.send``);
* imports are split per bound name, never per statement;
* an import only claims ``resolved_internal`` when a file was resolved from
  the selected path set; relative imports never become external packages;
* config keys keep their literal value and type;
* code regions keep the file content hash that makes the span verifiable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.structural.extraction.ids import (
    config_key_id,
    extracted_import_id,
    file_id,
)
from app.structural.extraction.models import (
    ExtractedCodeRegion,
    ExtractedConfigKey,
    ExtractedImport,
    ExtractedSymbol,
    ExtractedSymbolKind,
    ImportKind,
    ImportRef,
    ImportResolutionStatus,
    StructuralFile,
    SymbolKind,
)

_SYMBOL_KINDS: dict[SymbolKind, ExtractedSymbolKind] = {
    SymbolKind.CLASS: "class",
    SymbolKind.FUNCTION: "function",
    SymbolKind.METHOD: "method",
    SymbolKind.CONSTANT: "constant",
    SymbolKind.TYPE: "type",
}


@dataclass(frozen=True)
class ExtractionOutputs:
    """The four parser-output lists for one file at one commit."""

    file_id: str
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    config_keys: list[ExtractedConfigKey] = field(default_factory=list)
    regions: list[ExtractedCodeRegion] = field(default_factory=list)


def build_extraction_outputs(entry: StructuralFile) -> ExtractionOutputs:
    return ExtractionOutputs(
        file_id=entry.file_id,
        symbols=_symbols(entry),
        imports=_imports(entry),
        config_keys=_config_keys(entry),
        regions=_regions(entry),
    )


def build_batch_outputs(entries: list[StructuralFile]) -> list[ExtractionOutputs]:
    return [build_extraction_outputs(entry) for entry in entries]


def _symbols(entry: StructuralFile) -> list[ExtractedSymbol]:
    by_qualname: dict[str, str] = {}
    for symbol in entry.symbols:
        by_qualname.setdefault(symbol.qualname, symbol.symbol_id)

    return [
        ExtractedSymbol(
            symbol_id=symbol.symbol_id,
            file_id=entry.file_id,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            kind=_SYMBOL_KINDS[symbol.kind],
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            parent_symbol_id=(
                by_qualname.get(symbol.parent_qualname)
                if symbol.parent_qualname
                else None
            ),
        )
        for symbol in entry.symbols
    ]


def _imports(entry: StructuralFile) -> list[ExtractedImport]:
    outputs: list[ExtractedImport] = []
    for imported in entry.imports:
        status, resolved_file_id = _resolution(entry, imported)
        module = imported.module or None
        is_relative = imported.kind is ImportKind.RELATIVE

        if imported.wildcard:
            bindings: list[tuple[str | None, str | None]] = [("*", None)]
        elif imported.names:
            aliases = {real: alias for alias, real in imported.aliases.items()}
            bindings = [(name, aliases.get(name)) for name in imported.names]
        elif imported.aliases:
            # ``import package as local`` binds the module under the alias.
            bindings = [(None, alias) for alias in imported.aliases]
        else:
            bindings = [(None, None)]

        for name, alias in bindings:
            outputs.append(
                ExtractedImport(
                    import_id=extracted_import_id(
                        file_id=entry.file_id,
                        line=imported.line,
                        module=module,
                        name=name,
                        alias=alias,
                    ),
                    file_id=entry.file_id,
                    module=module,
                    name=name,
                    alias=alias,
                    is_relative=is_relative,
                    resolved_file_id=resolved_file_id,
                    resolution_status=status,
                )
            )
    return outputs


def _resolution(
    entry: StructuralFile, imported: ImportRef
) -> tuple[ImportResolutionStatus, str | None]:
    if imported.resolved_path:
        return (
            "resolved_internal",
            file_id(
                repo=entry.repo,
                path=imported.resolved_path,
                commit_sha=entry.commit_sha,
            ),
        )
    if imported.kind is ImportKind.RELATIVE:
        # An unresolved relative import is never guessed into a package.
        return "unresolved", None

    root = imported.module.split(".")[0]
    top_level = entry.path.split("/")[0].removesuffix(".py")
    if root and root == top_level:
        # Looks internal but was not in the selected set: do not call it a
        # third-party package.
        return "unresolved", None
    if root:
        return "resolved_external", None
    return "unresolved", None


def _config_keys(entry: StructuralFile) -> list[ExtractedConfigKey]:
    outputs: list[ExtractedConfigKey] = []
    for key in entry.config_keys:
        if key.line is None:
            continue
        outputs.append(
            ExtractedConfigKey(
                config_id=config_key_id(
                    repo=entry.repo, source=entry.path, key=key.key
                ),
                file_id=entry.file_id,
                key=key.key,
                value=key.value,
                value_type=key.value_type,
                line=key.line,
            )
        )
    return outputs


def _regions(entry: StructuralFile) -> list[ExtractedCodeRegion]:
    if not entry.content_hash:
        return []
    by_qualname: dict[str, str] = {}
    for symbol in entry.symbols:
        by_qualname.setdefault(symbol.qualname, symbol.symbol_id)

    regions: list[ExtractedCodeRegion] = []
    for region in entry.regions:
        if not region.kind.startswith("symbol:"):
            continue
        regions.append(
            ExtractedCodeRegion(
                region_id=region.region_id,
                file_id=entry.file_id,
                start_line=region.start_line,
                end_line=region.end_line,
                content_hash=entry.content_hash,
                excerpt=None,
                symbol_id=(
                    by_qualname.get(region.symbol_qualname)
                    if region.symbol_qualname
                    else None
                ),
                source="definition",
            )
        )
    return regions


__all__ = [
    "ExtractionOutputs",
    "build_batch_outputs",
    "build_extraction_outputs",
]
