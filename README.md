# MegaLAP print proofs

Deterministic 12-inch print-test assets for comparing four Prodigi paper stocks
through the Prodigi sandbox and live APIs.

The source artwork is reduced and cropped with Pillow/Lanczos. The generated
assets are 3600 × 3600 RGB images at 300 ppi with an embedded standard sRGB ICC
profile. Each order asset includes a visible paper name and Prodigi SKU while
leaving the artwork and diagnostic regions unchanged.

Public asset links are published through GitHub Pages. Local credentials and
order records are excluded by `.gitignore` and must never be committed.

## Generate

```bash
python3 scripts/generate_billion_12x12_test_sheet.py
python3 scripts/generate_prodigi_paper_variants.py
```

## Prodigi workflows

The sandbox scripts validate products, quote, and exercise order creation
without charging or fulfillment. `prodigi_live_four_paper_order.py` is a
separate live-only workflow with strict host checks, a pre-tax quote limit, a
persistent idempotency key, and private records under
`work/prodigi-live-four-paper/`.

Run the live stages separately and inspect the quote before authorizing the
order stage:

```bash
python3 scripts/prodigi_live_four_paper_order.py preflight
python3 scripts/prodigi_live_four_paper_order.py quote
python3 scripts/prodigi_live_four_paper_order.py order
```

The live order stage charges the configured account and starts physical
fulfillment. Never run it as an automated test.
