"""Deterministic inclusion rules for evidence-pack artifacts.

The packer is deliberately conservative. A merged PR can contain hundreds of
files, comments and commits; most of them do not explain *why* a region
changed. These predicates encode the exclusions from the Phase 2 plan so the
same decision is made on every replay and is easy to audit.
"""

from __future__ import annotations

TEST_SEGMENTS = frozenset({"test", "tests", "testing", "__tests__"})
TEST_FILE_SUFFIXES = ("_test.py", "_test.rs", "_test.go", "_test.ts", "_test.tsx")
CI_SEGMENTS = frozenset({"ci", ".ci"})
CI_PATH_PREFIXES = (".github/workflows/", ".github/actions/")
CI_FILENAMES = frozenset({".travis.yml", "appveyor.yml", "jenkinsfile", "azure-pipelines.yml"})
DOC_SUFFIXES = (".md", ".rst", ".adoc", ".txt")


def _parts(path: str) -> list[str]:
    return [part for part in path.lower().replace("\\", "/").split("/") if part]


def is_test_path(path: str) -> bool:
    """True for test modules and anything under a test directory."""
    parts = _parts(path)
    if any(part in TEST_SEGMENTS for part in parts):
        return True
    basename = parts[-1] if parts else ""
    return (
        basename.startswith("test_")
        or basename.endswith(TEST_FILE_SUFFIXES)
        or basename == "conftest.py"
    )


def is_ci_path(path: str) -> bool:
    """True for workflow definitions and CI configuration, never code."""
    normalized = path.lower().replace("\\", "/")
    if normalized.startswith(CI_PATH_PREFIXES):
        return True
    parts = _parts(path)
    if any(part in CI_SEGMENTS for part in parts):
        return True
    basename = parts[-1] if parts else ""
    return basename in CI_FILENAMES


def is_readme_path(path: str) -> bool:
    parts = _parts(path)
    basename = parts[-1] if parts else ""
    return basename.startswith("readme")


def is_doc_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    return normalized.endswith(DOC_SUFFIXES) or "/docs/" in normalized


def is_config_path(path: str) -> bool:
    normalized = path.lower()
    return normalized.endswith(
        (".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".conf")
    )


__all__ = [
    "is_ci_path",
    "is_config_path",
    "is_doc_path",
    "is_readme_path",
    "is_test_path",
]
