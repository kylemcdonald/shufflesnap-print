#!/usr/bin/env python3
"""Shared configuration for repeatable 20-inch Prodigi paper workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Paper20x20:
    key: str
    sku: str
    paper: str
    filename: str


PAPERS: dict[str, Paper20x20] = {
    "hpr": Paper20x20(
        key="hpr",
        sku="GLOBAL-HPR-20X20",
        paper="Hahnemühle Photo Rag",
        filename="billion_20x20_global-hpr-20x20_300ppi.png",
    ),
    "fap": Paper20x20(
        key="fap",
        sku="GLOBAL-FAP-20X20",
        paper="Enhanced Matte Art",
        filename="billion_20x20_global-fap-20x20_300ppi.png",
    ),
}


def paper_keys() -> tuple[str, ...]:
    return tuple(PAPERS)


def get_paper(key: str) -> Paper20x20:
    try:
        return PAPERS[key]
    except KeyError:
        choices = ", ".join(paper_keys())
        raise ValueError(f"Unknown paper {key!r}; choose one of: {choices}") from None
