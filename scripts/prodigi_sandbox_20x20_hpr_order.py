#!/usr/bin/env python3
"""Backward-compatible HPR entry point for the generic 20-inch workflow."""

from prodigi_sandbox_20x20_order import main


if __name__ == "__main__":
    raise SystemExit(main(default_paper="hpr"))
