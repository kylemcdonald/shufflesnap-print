# MegaLAP print proofs

Deterministic 12-inch print-test assets for comparing three Prodigi paper stocks
in the Prodigi sandbox.

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

