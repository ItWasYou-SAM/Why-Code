"""Component 3: structural enrichment.

Tree-sitter extraction of symbols, imports, config keys, code regions and
docs for the files Component 1 selected. No full-repo parsing, no call
graph, no type inference, no LLM.
"""

from app.structural.config import StructuralConfig
from app.structural.extraction.file_extractor import FileExtractor, extract_file
from app.structural.extraction.models import (
    CodeRegion,
    ConfigKey,
    ImportRef,
    Language,
    StructuralBatch,
    StructuralFile,
    Symbol,
    SymbolKind,
)
from app.structural.extraction.outputs import (
    ExtractionOutputs,
    build_extraction_outputs,
)
from app.structural.graph.graph_records import build_structural_graph
from app.structural.pipeline import StructuralPipeline

__all__ = [
    "CodeRegion",
    "ConfigKey",
    "ExtractionOutputs",
    "FileExtractor",
    "ImportRef",
    "Language",
    "StructuralBatch",
    "StructuralConfig",
    "StructuralFile",
    "StructuralPipeline",
    "Symbol",
    "SymbolKind",
    "build_extraction_outputs",
    "build_structural_graph",
    "extract_file",
]
