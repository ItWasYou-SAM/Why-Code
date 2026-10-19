"""Second sample module, imported by sample_client via a relative import."""

from __future__ import annotations


class Transport:
    def handle(self) -> None:
        return None


def close_all(transports: list[Transport]) -> None:
    for transport in transports:
        transport.handle()
