#!/usr/bin/env python3
"""Run one distinct four-paper Pages-backed Prodigi sandbox test."""

from __future__ import annotations

from pathlib import Path

import prodigi_sandbox_pages_order as workflow


PROJECT_DIR = Path(__file__).resolve().parents[1]

workflow.PRIVATE_WORK_DIR = PROJECT_DIR / "work/prodigi-sandbox-four-paper"
workflow.MERCHANT_REFERENCE = "billion-12x12-four-paper-test-sandbox-pages"
workflow.ASSETS = {
    "GLOBAL-HPR-12X12": "outputs/prodigi/billion_12x12_global-hpr-12x12_300ppi.png",
    "GLOBAL-FAP-12X12": "outputs/prodigi/billion_12x12_global-fap-12x12_300ppi.png",
    "ART-FAP-BAP-12X12": "outputs/prodigi/billion_12x12_art-fap-bap-12x12_300ppi.png",
    "ART-FAP-SAP-12X12": "outputs/prodigi/billion_12x12_art-fap-sap-12x12_300ppi.png",
}


if __name__ == "__main__":
    raise SystemExit(workflow.main())
