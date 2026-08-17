# MegaLAP print assets

Deterministic print assets for MegaLAP artwork, including 12-inch paper-stock
proofs and 20-inch Hahnemühle Photo Rag and Enhanced Matte Art prints.

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
python3 scripts/prepare_billion_20x20_print.py --paper hpr
python3 scripts/prepare_billion_20x20_print.py --paper fap
```

The 20-inch preparation script validates the selected 4800 × 4800 RGB sRGB
render, preserves its pixels unchanged, and centers it at `(600, 600)` on a
6000 × 6000 pure-white canvas. At 300 ppi this produces a 16-inch artwork with
a 2-inch margin on every side.

Configured 20-inch variants live in `scripts/prodigi_20x20_config.py`:

| Key | Prodigi SKU | Paper | Public asset |
| --- | --- | --- | --- |
| `hpr` | `GLOBAL-HPR-20X20` | Hahnemühle Photo Rag | `billion_20x20_global-hpr-20x20_300ppi.png` |
| `fap` | `GLOBAL-FAP-20X20` | Enhanced Matte Art, 200gsm | `billion_20x20_global-fap-20x20_300ppi.png` |

## Prodigi workflows

The sandbox scripts validate products, quote, and exercise order creation
without charging or fulfillment. `prodigi_live_four_paper_order.py` is a
separate live-only workflow with strict host checks, a pre-tax quote limit, a
persistent idempotency key, and private records under
`work/prodigi-live-four-paper/`.

The reusable 20-inch sandbox workflow supports configured paper variants and
stores each run under an asset-hash-specific private directory. A new render
therefore receives a new persistent idempotency key without disturbing prior
order records:

```bash
python3 scripts/prepare_billion_20x20_print.py --paper fap
# Commit and publish the generated asset through GitHub Pages.
python3 scripts/prodigi_sandbox_20x20_order.py asset --paper fap
python3 scripts/prodigi_sandbox_20x20_order.py run --paper fap
```

The `asset` stage requires the public HTTPS PNG to match the local bytes. The
`run` stage validates the private recipient and product, requests a Budget
quote, persists the order payload and UUID, creates one sandbox order, and
retrieves it once. It refuses a second order POST for the same paper-and-asset
record. Use `--paper hpr` for Hahnemühle Photo Rag or `--paper fap` for Enhanced
Matte Art. The older `prodigi_sandbox_20x20_hpr_order.py` command remains as an
HPR-compatible wrapper.

Run the live stages separately and inspect the quote before authorizing the
order stage:

```bash
python3 scripts/prodigi_live_four_paper_order.py preflight
python3 scripts/prodigi_live_four_paper_order.py quote
python3 scripts/prodigi_live_four_paper_order.py order
```

The live order stage charges the configured account and starts physical
fulfillment. Never run it as an automated test.

## Branded packaging inserts

Research checked on 2026-08-16 against Prodigi's
[v4 API reference](https://www.prodigi.com/print-api/docs/reference/),
[packaging-insert specifications](https://www.prodigi.com/branded-packaging-inserts/),
and the current sandbox API.

### What the order API supports

The v4 order object has an optional top-level `branding` object. Each selected
insert is a public print-ready asset URL:

```json
{
  "branding": {
    "postcard": {
      "url": "https://example.com/inserts/postcard.pdf"
    },
    "sticker_exterior_round": {
      "url": "https://example.com/inserts/sticker-round.pdf"
    }
  }
}
```

Supported keys are `postcard`, `flyer`, `packing_slip_bw`,
`packing_slip_color`, `sticker_exterior_round`,
`sticker_exterior_rectangle`, `sticker_interior_round`, and
`sticker_interior_rectangle`. Inserts apply to the order rather than to an
individual print item. Prodigi currently limits an order to one of each insert
type, even if the order contains multiple items.

Branding sets and their default assignment to Print API orders are created and
managed in the Prodigi dashboard, not through an API endpoint. A create-order
payload can provide per-order `branding` URLs to override those defaults.

The current MegaLAP order script intentionally does **not** send `branding`.
Add it only after the quote-schema issue below is resolved and print-ready
insert assets have been published and hash-verified.

### Stickers and postcards

| Insert | Finished size and placement | Artwork notes | Standard / Pro price |
| --- | --- | --- | --- |
| Exterior round sticker | 65mm diameter; tube end-cap | Supply at 68mm diameter | $1.25 / $0.40 |
| Exterior rectangular sticker | 102 × 76mm (4 × 3in); cardboard box | Supply at 107 × 77mm | $1.25 / $0.40 |
| Interior round sticker | 25mm diameter; tissue seal or product rear | Supply at 28mm diameter | $1.25 / $0.40 |
| Interior rectangular sticker | 102 × 76mm (4 × 3in); product rear | Supply at 107 × 77mm | $1.25 / $0.40 |
| Postcard | A6, 105 × 148mm; inside package | Single-sided, approximately 4mm white border, 260gsm ultra smooth | $2.50 / $0.65 |

Prodigi's
[sticker templates and guide](https://www.prodigi.com/download/artwork-preparation/branded-insert-stickers-guide-and-templates.zip)
specify a 3mm safe area inside the trim, 300dpi, CMYK, single-sided,
press-ready PDF output. The rectangular bleed is asymmetric: 2.5mm on the
long edges and 0.5mm on the short edges. Prodigi's general insert FAQ accepts
PDF, PNG, or JPG, but the sticker-specific guide should take precedence for
sticker production files. Prices above are the displayed USD prices and can
change; verify them in the account dashboard before a live order.

Prodigi's product-availability table currently marks both Enhanced Matte Art
and Hahnemühle Photo Rag as supporting the **full range** of branded inserts.
Actual inclusion still depends on the fulfilment facility selected during
allocation. If an insert becomes unavailable, Prodigi says it is omitted and
not charged. When an order splits across multiple Prodigi facilities, inserts
are included in each shipment but charged once per order.

### Unresolved quote-schema mismatch

Prodigi's
[order-method support article](https://support.prodigi.com/hc/en-us/articles/19847266436764-How-do-I-access-branded-insert-settings-for-different-order-methods)
says the quote endpoint can price requested inserts, but the public v4 quote
schema does not document an insert property.
Sandbox probes on 2026-08-16 produced these exact non-secret failures:

- `branding.postcard`: HTTP 400, `ModelBindingFailed`, `UnknownField`
- `branding.sticker_exterior_round`: HTTP 400, `ModelBindingFailed`, `UnknownField`
- top-level `inserts`: HTTP 400, `ModelBindingFailed`, `UnknownField`

The ordinary no-insert quote succeeded in the same probe. No insert order was
created. Sanitized probe records are stored locally under
`work/prodigi-sandbox-insert-research/` and remain gitignored.

Before enabling inserts for a live order, ask Prodigi support for the current
v4 **quote request** field and confirm that it uses the same per-order semantics
as the documented create-order `branding` object. Then add optional branding
configuration to `prodigi_sandbox_20x20_order.py`, require the quote to show the
insert cost, and complete a sandbox order with correctly sized public assets.
