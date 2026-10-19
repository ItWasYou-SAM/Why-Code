"""Structural component configuration and file-selection policy.

Selection is intentionally narrow: only files that Component 1 already
marked as changed in a high-signal PR, plus explicitly pinned files and
(resolvable) direct imports. No repository walking.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.structural.extraction.models import Language

LANGUAGE_BY_SUFFIX: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".toml": Language.TOML,
    ".json": Language.JSON,
    ".env": Language.ENV,
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
    ".rst": Language.MARKDOWN,
}

GENERATED_MARKERS = (
    ".pb.go",
    "_pb2.py",
    ".generated.",
    "generated/",
    "dist/",
    "build/",
)

LOCK_FILES = {
    "poetry.lock",
    "Pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
}


class StructuralConfig(BaseModel):
    repo: str
    commit_sha: str | None = None
    include_tests: bool = False
    include_ci: bool = False
    include_docs: bool = True
    include_imports: bool = True
    pinned_paths: list[str] = Field(default_factory=list)
    max_file_bytes: int = 1_000_000
    artifacts_dir: Path = Path("artifacts")
    content_dir: Path = Path("artifacts/structural/content")


def language_for_path(path: str) -> Language | None:
    lowered = path.lower()
    for suffix, language in LANGUAGE_BY_SUFFIX.items():
        if lowered.endswith(suffix):
            return language
    return None


def exclusion_reason(path: str, config: StructuralConfig) -> str | None:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name in {lock.lower() for lock in LOCK_FILES}:
        return "lock_file"
    if any(marker in lowered for marker in GENERATED_MARKERS):
        return "generated_file"
    if not config.include_tests and (
        "/tests/" in f"/{lowered}"
        or lowered.startswith("tests/")
        or "/test_" in f"/{lowered}"
        or name.startswith("test_")
        or name.endswith("_test.py")
    ):
        return "test_file"
    if not config.include_ci and (
        lowered.startswith(".github/")
        or lowered.startswith(".gitlab-ci")
        or name in {".travis.yml", "azure-pipelines.yml"}
    ):
        return "ci_configuration"
    if not config.include_docs and language_for_path(lowered) is Language.MARKDOWN:
        return "documentation"
    if language_for_path(lowered) is None:
        return "unsupported_language"
    return None
