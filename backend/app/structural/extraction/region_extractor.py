"""CodeRegion extraction.

Regions are the coarse structural units that review comments and commits
can be anchored to without inventing precision:

* one region per symbol (its full declaration span)
* one region per config key (its declaration line)

Regions never claim a hunk-to-symbol mapping; that requires diff analysis
and belongs to a later, separately validated step.
"""

from __future__ import annotations

from app.structural.extraction.ids import region_id
from app.structural.extraction.models import CodeRegion, ConfigKey, Symbol


def regions_for_symbols(
    symbols: list[Symbol], *, repo: str, path: str, commit_sha: str
) -> list[CodeRegion]:
    return [
        CodeRegion(
            region_id=region_id(
                repo=repo,
                path=path,
                commit_sha=commit_sha,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
            ),
            path=path,
            commit_sha=commit_sha,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            kind=f"symbol:{symbol.kind.value}",
            symbol_qualname=symbol.qualname,
        )
        for symbol in symbols
    ]


def regions_for_config_keys(
    keys: list[ConfigKey], *, repo: str, path: str, commit_sha: str
) -> list[CodeRegion]:
    regions: list[CodeRegion] = []
    for key in keys:
        if key.line is None:
            continue
        regions.append(
            CodeRegion(
                region_id=region_id(
                    repo=repo,
                    path=path,
                    commit_sha=commit_sha,
                    start_line=key.line,
                    end_line=key.line,
                ),
                path=path,
                commit_sha=commit_sha,
                start_line=key.line,
                end_line=key.line,
                kind="config_key",
                config_key=key.key,
            )
        )
    return regions
