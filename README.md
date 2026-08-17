# MegaLAP print assets

Deterministic print assets for MegaLAP artwork, including 12-inch paper-stock
proofs and the 20-inch Hahnemühle Photo Rag print.

The 12-inch proof artwork is reduced and cropped with Pillow/Lanczos. Those
assets are 3600 × 3600 RGB images with visible paper/SKU labels. The 20-inch
asset preserves its 4800 × 4800 artwork pixels unchanged inside a 6000 × 6000
canvas. Every print asset is 300 ppi with an embedded standard sRGB ICC profile.

Public asset links are published through GitHub Pages. Local credentials and
order records are excluded by `.gitignore` and must never be committed.

## Generate

```bash
python3 scripts/generate_billion_12x12_test_sheet.py
python3 scripts/generate_prodigi_paper_variants.py
python3 scripts/prepare_billion_20x20_print.py
```

The 20-inch preparation script validates the selected 4800 × 4800 RGB sRGB
render, preserves its pixels unchanged, and centers it at `(600, 600)` on a
6000 × 6000 pure-white canvas. At 300 ppi this produces a 16-inch artwork with
a 2-inch margin on every side.

## Prodigi workflows

The sandbox scripts validate products, quote, and exercise order creation
without charging or fulfillment. `prodigi_live_four_paper_order.py` is a
separate live-only workflow with strict host checks, a pre-tax quote limit, a
persistent idempotency key, and private records under
`work/prodigi-live-four-paper/`.

The single-print 20-inch Photo Rag sandbox workflow is staged separately:

```bash
python3 scripts/prodigi_sandbox_20x20_hpr_order.py address
python3 scripts/prodigi_sandbox_20x20_hpr_order.py product
python3 scripts/prodigi_sandbox_20x20_hpr_order.py quote
python3 scripts/prodigi_sandbox_20x20_hpr_order.py order
```

Run the live stages separately and inspect the quote before authorizing the
order stage:

```bash
python3 scripts/prodigi_live_four_paper_order.py preflight
python3 scripts/prodigi_live_four_paper_order.py quote
python3 scripts/prodigi_live_four_paper_order.py order
```

The live order stage charges the configured account and starts physical
fulfillment. Never run it as an automated test.
