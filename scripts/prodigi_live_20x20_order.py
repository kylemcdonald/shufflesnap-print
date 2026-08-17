#!/usr/bin/env python3
"""Promote a hash-matched 20-inch sandbox proof to one guarded live order.

The script reads the live key and private recipient only into memory, restricts
API traffic to Prodigi's live v4 origin, and requires a matching local sandbox
order record. A fresh UUID is persisted before the live order request so an
interrupted retry cannot create a duplicate order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import prodigi_live_four_paper_order as live
from prodigi_20x20_config import Paper20x20, get_paper, paper_keys


PROJECT_DIR = Path(__file__).resolve().parents[1]
PAGES_BASE = "https://kylemcdonald.github.io/megalap-print/"
ALLOWED_QUOTE_ISSUES = {"destinationCountryCode.UsSalesTaxWarning"}
MAX_QUOTE_TOTAL_USD = Decimal("40.00")
MAX_QUOTE_AGE = timedelta(hours=2)


@dataclass(frozen=True)
class RunContext:
    paper: Paper20x20
    asset_path: str
    asset_url: str
    asset_sha256: str
    work_dir: Path
    sandbox_dir: Path
    merchant_reference: str


def build_context(paper: Paper20x20) -> RunContext:
    asset_path = f"outputs/prodigi/{paper.filename}"
    local_path = PROJECT_DIR / asset_path
    if not local_path.is_file():
        raise RuntimeError(
            f"Local asset is missing for {paper.sku}; run the preparation script first"
        )
    asset_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
    run_tag = f"{paper.key}-{asset_sha256[:12]}"
    return RunContext(
        paper=paper,
        asset_path=asset_path,
        asset_url=urllib.parse.urljoin(PAGES_BASE, asset_path),
        asset_sha256=asset_sha256,
        work_dir=PROJECT_DIR / f"work/prodigi-live-20x20-{run_tag}",
        sandbox_dir=PROJECT_DIR / f"work/prodigi-sandbox-20x20-{run_tag}",
        merchant_reference=f"billion-20x20-{paper.key}-{asset_sha256[:8]}-live",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_private_json(path: Path, value: Any, api_key: str) -> None:
    live.assert_sanitized(value, api_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def validate_public_asset(context: RunContext) -> None:
    parsed = urllib.parse.urlsplit(context.asset_url)
    if parsed.scheme != "https" or parsed.hostname != "kylemcdonald.github.io":
        raise RuntimeError("Blocked unexpected public asset origin")
    opener = urllib.request.build_opener(live.PagesOnlyRedirectHandler())
    request = urllib.request.Request(
        context.asset_url,
        headers={"User-Agent": "megalap-prodigi-live-20x20-asset-check/1.0"},
    )
    with opener.open(request, timeout=90) as response:
        remote = response.read()
        content_type = response.headers.get("Content-Type", "")
        status = response.status
        final_url = response.geturl()
    if status != 200 or not content_type.lower().startswith("image/png"):
        raise RuntimeError("Public print asset is not a reachable PNG")
    final = urllib.parse.urlsplit(final_url)
    if final.scheme != "https" or final.hostname != "kylemcdonald.github.io":
        raise RuntimeError("Public asset redirected outside the expected HTTPS host")
    local = (PROJECT_DIR / context.asset_path).read_bytes()
    local_hash = hashlib.sha256(local).hexdigest()
    if local_hash != context.asset_sha256:
        raise RuntimeError("Local print asset changed after context creation")
    if hashlib.sha256(remote).hexdigest() != local_hash:
        raise RuntimeError("Public print asset bytes do not match the local PNG")
    print(f"LIVE ASSET VERIFIED SHA-256 {local_hash}")


def sandbox_order_id(response_record: Any) -> str:
    response = response_record.get("response") if isinstance(response_record, dict) else None
    order = response.get("order") if isinstance(response, dict) else None
    order_id = order.get("id") if isinstance(order, dict) else None
    if not isinstance(order_id, str) or not order_id.startswith("ord_"):
        raise RuntimeError("Sandbox response record does not contain a valid order ID")
    return order_id


def validate_sandbox_baseline(
    context: RunContext, recipient: dict[str, Any]
) -> str:
    payload_path = context.sandbox_dir / "order-payload.json"
    response_path = context.sandbox_dir / "order-response.json"
    if not payload_path.is_file() or not response_path.is_file():
        raise RuntimeError(
            "Matching sandbox order records are missing for this paper and asset hash"
        )
    payload = load_json(payload_path)
    if not isinstance(payload, dict):
        raise RuntimeError("Sandbox order payload record is not an object")
    if payload.get("recipient") != recipient:
        raise RuntimeError("Private recipient differs from the matching sandbox order")
    if payload.get("shippingMethod") != "Budget":
        raise RuntimeError("Sandbox baseline did not use Budget shipping")
    if payload.get("branding") not in (None, {}):
        raise RuntimeError("Sandbox baseline contains branding inserts")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise RuntimeError("Sandbox baseline must contain exactly one item")
    item = items[0]
    expected = {
        "sku": context.paper.sku,
        "copies": 1,
        "sizing": "fillPrintArea",
        "attributes": {},
        "assets": [{"printArea": "default", "url": context.asset_url}],
    }
    actual = {key: item.get(key) for key in expected}
    if actual != expected:
        raise RuntimeError("Sandbox baseline item differs from the requested live item")
    baseline_id = sandbox_order_id(load_json(response_path))
    print(f"SANDBOX BASELINE VERIFIED {baseline_id}")
    return baseline_id


def product_from_response(response: Any, paper: Paper20x20) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("Live product lookup returned a non-object response")
    if str(response.get("outcome", "")).lower() != "ok":
        raise RuntimeError(
            f"Live product lookup outcome was not Ok: {response.get('outcome')!r}"
        )
    product = response.get("product") if isinstance(response.get("product"), dict) else response
    if str(product.get("sku", "")).upper() != paper.sku:
        raise RuntimeError(f"Live product response did not match {paper.sku}")
    return product


def validate_product(
    client: live.ProdigiLiveClient,
    api_key: str,
    context: RunContext,
    baseline_order_id: str,
) -> dict[str, Any]:
    product = product_from_response(
        client.request(
            "GET", f"/products/{urllib.parse.quote(context.paper.sku, safe='')}"
        ),
        context.paper,
    )
    dimensions = product.get("productDimensions")
    try:
        exact_size = (
            isinstance(dimensions, dict)
            and Decimal(str(dimensions.get("width"))) == Decimal("20")
            and Decimal(str(dimensions.get("height"))) == Decimal("20")
            and str(dimensions.get("units", "")).lower() in {"in", "inch", "inches"}
        )
    except InvalidOperation:
        exact_size = False
    if not exact_size:
        raise RuntimeError(
            f"Live product {context.paper.sku} is not exactly 20x20 inches"
        )
    variants = [item for item in product.get("variants", []) if isinstance(item, dict)]
    us_variants = [item for item in variants if "US" in item.get("shipsTo", [])]
    if not us_variants:
        raise RuntimeError(f"Live product {context.paper.sku} does not ship to the US")
    resolutions: list[dict[str, int]] = []
    for variant in us_variants:
        area = variant.get("printAreaSizes", {}).get("default", {})
        if {"horizontalResolution", "verticalResolution"}.issubset(area):
            resolution = {
                "horizontalResolution": int(area["horizontalResolution"]),
                "verticalResolution": int(area["verticalResolution"]),
            }
            if resolution not in resolutions:
                resolutions.append(resolution)
    expected_resolution = {"horizontalResolution": 6000, "verticalResolution": 6000}
    if expected_resolution not in resolutions:
        raise RuntimeError(
            f"Live product {context.paper.sku} does not recommend 6000x6000 pixels"
        )
    summary = {
        "sku": product.get("sku"),
        "description": product.get("description"),
        "paper": context.paper.paper,
        "productDimensions": dimensions,
        "catalogAttributes": product.get("attributes", {}),
        "recommendedDefaultPrintAreaResolutions": resolutions,
        "shipsToUS": True,
        "requestedOrderAttributes": {},
    }
    save_private_json(
        context.work_dir / "product-validation.json",
        {
            "apiBase": live.API_BASE,
            "assetSha256": context.asset_sha256,
            "sandboxBaselineOrderId": baseline_order_id,
            "validatedAt": live.utc_now(),
            "product": summary,
        },
        api_key,
    )
    print(
        f"LIVE PRODUCT VALID {summary['sku']} | {summary['description']} | "
        "20x20 in | default 6000x6000 px | ships to US: true"
    )
    return summary


def load_validation(context: RunContext) -> dict[str, Any]:
    path = context.work_dir / "product-validation.json"
    if not path.is_file():
        raise RuntimeError("Run the live preflight before requesting a quote")
    record = load_json(path)
    product = record.get("product") if isinstance(record.get("product"), dict) else {}
    if (
        record.get("apiBase") != live.API_BASE
        or record.get("assetSha256") != context.asset_sha256
        or str(product.get("sku", "")).upper() != context.paper.sku
        or not product.get("shipsToUS")
    ):
        raise RuntimeError("Saved live product validation does not match this order")
    baseline_id = record.get("sandboxBaselineOrderId")
    if not isinstance(baseline_id, str) or not baseline_id.startswith("ord_"):
        raise RuntimeError("Saved validation lacks a sandbox baseline order ID")
    return record


def quote_payload(context: RunContext) -> dict[str, Any]:
    return {
        "shippingMethod": "Budget",
        "destinationCountryCode": "US",
        "currencyCode": "USD",
        "items": [
            {
                "sku": context.paper.sku,
                "copies": 1,
                "attributes": {},
                "assets": [{"printArea": "default"}],
            }
        ],
    }


def quote_issues(response: dict[str, Any]) -> list[dict[str, Any]]:
    issues = [item for item in response.get("issues", []) if isinstance(item, dict)]
    quotes = response.get("quotes")
    if isinstance(quotes, list):
        for quote in quotes:
            if isinstance(quote, dict):
                issues.extend(
                    item for item in quote.get("issues", []) if isinstance(item, dict)
                )
    return issues


def validate_quote(response: Any, context: RunContext) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("Live quote returned a non-object response")
    if str(response.get("outcome", "")).lower() not in {
        "created",
        "createdwithissues",
    }:
        raise RuntimeError(f"Live quote outcome was not successful: {response.get('outcome')!r}")
    quotes = response.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise RuntimeError("Expected exactly one live Budget quote")
    quote = quotes[0]
    if str(quote.get("shipmentMethod", "")).lower() != "budget":
        raise RuntimeError("Live quote did not return Budget shipping")
    items = quote.get("items")
    if not isinstance(items, list) or len(items) != 1:
        raise RuntimeError("Live quote did not return exactly one item")
    item = items[0]
    if (
        str(item.get("sku", "")).upper() != context.paper.sku
        or int(item.get("copies", 0)) != 1
        or item.get("attributes") != {}
        or item.get("assets") != [{"printArea": "default"}]
    ):
        raise RuntimeError("Live quote item does not match the requested item")
    summary = quote.get("costSummary")
    total = summary.get("totalCost") if isinstance(summary, dict) else None
    if not isinstance(total, dict) or total.get("currency") != "USD":
        raise RuntimeError("Live quote did not return a USD total")
    try:
        total_amount = Decimal(str(total["amount"]))
    except (InvalidOperation, KeyError):
        raise RuntimeError("Live quote returned an invalid total") from None
    if total_amount > MAX_QUOTE_TOTAL_USD:
        raise RuntimeError(
            f"Live quote {total_amount:.2f} USD exceeds the "
            f"{MAX_QUOTE_TOTAL_USD:.2f} USD safety limit"
        )
    unexpected = [
        issue
        for issue in quote_issues(response)
        if str(issue.get("errorCode", "")) not in ALLOWED_QUOTE_ISSUES
    ]
    if unexpected:
        codes = [str(issue.get("errorCode") or "unspecified") for issue in unexpected]
        raise RuntimeError("Live quote returned unexpected issue codes: " + ", ".join(codes))
    return quote


def money(cost: Any) -> str:
    if not isinstance(cost, dict) or "amount" not in cost:
        return "not returned"
    return f"{cost['amount']} {cost.get('currency', '')}".strip()


def print_quote(response: dict[str, Any]) -> None:
    quote = response["quotes"][0]
    summary = quote.get("costSummary", {})
    print(f"LIVE QUOTE OUTCOME {response.get('outcome')}")
    print(f"LIVE QUOTE ITEMS {money(summary.get('items'))}")
    print(f"LIVE QUOTE SHIPPING {money(summary.get('shipping'))}")
    print(f"LIVE QUOTE TAX {money(summary.get('totalTax'))}")
    print(f"LIVE QUOTE TOTAL {money(summary.get('totalCost'))}")
    codes = [str(issue.get("errorCode")) for issue in quote_issues(response)]
    if codes:
        print("LIVE QUOTE ISSUE CODES " + ", ".join(codes))


def create_quote(
    client: live.ProdigiLiveClient, api_key: str, context: RunContext
) -> dict[str, Any]:
    load_validation(context)
    payload = quote_payload(context)
    response = client.request("POST", "/quotes", payload)
    validate_quote(response, context)
    save_private_json(
        context.work_dir / "quote.json",
        {
            "apiBase": live.API_BASE,
            "assetSha256": context.asset_sha256,
            "quotedAt": live.utc_now(),
            "request": payload,
            "response": response,
        },
        api_key,
    )
    print_quote(response)
    return response


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError("Saved live quote timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError("Saved live quote timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise RuntimeError("Saved live quote timestamp lacks a timezone")
    return parsed.astimezone(timezone.utc)


def validate_saved_quote(context: RunContext) -> dict[str, Any]:
    path = context.work_dir / "quote.json"
    if not path.is_file():
        raise RuntimeError("A saved live quote is required before order creation")
    record = load_json(path)
    if (
        record.get("apiBase") != live.API_BASE
        or record.get("assetSha256") != context.asset_sha256
        or record.get("request") != quote_payload(context)
    ):
        raise RuntimeError("Saved live quote does not match this order")
    age = datetime.now(timezone.utc) - parse_utc(record.get("quotedAt"))
    if age < timedelta(0) or age > MAX_QUOTE_AGE:
        raise RuntimeError("Saved live quote is stale; request a new quote")
    validate_quote(record.get("response"), context)
    return record


def expected_order_payload(
    recipient: dict[str, Any],
    idempotency_key: str,
    context: RunContext,
    baseline_order_id: str,
) -> dict[str, Any]:
    return {
        "idempotencyKey": idempotency_key,
        "merchantReference": context.merchant_reference,
        "shippingMethod": "Budget",
        "recipient": recipient,
        "items": [
            {
                "merchantReference": (
                    f"billion-20x20-{context.paper.key}-{context.asset_sha256[:8]}"
                ),
                "sku": context.paper.sku,
                "copies": 1,
                "sizing": "fillPrintArea",
                "attributes": {},
                "assets": [{"printArea": "default", "url": context.asset_url}],
            }
        ],
        "metadata": {
            "purpose": f"Physical 20-inch {context.paper.paper} artwork print",
            "sourceSandboxOrderId": baseline_order_id,
            "paper": context.paper.paper,
            "sku": context.paper.sku,
            "artworkId": "billion_31623_seed0_interp75_variant12_1px_dithered",
            "canvas": "6000x6000_srgb_300ppi_white_2in_margin",
            "colorDither": "per-point independent RGB uniform [-0.5,+0.5), seed 0",
            "assetSha256": context.asset_sha256,
            "environment": "Prodigi live",
            "assetHosting": "Public HTTPS via GitHub Pages",
        },
    }


def load_or_create_payload(
    recipient: dict[str, Any], context: RunContext, api_key: str
) -> dict[str, Any]:
    validation = load_validation(context)
    baseline_id = validation["sandboxBaselineOrderId"]
    path = context.work_dir / "order-payload.json"
    if path.is_file():
        payload = load_json(path)
        try:
            uuid.UUID(str(payload.get("idempotencyKey")))
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Existing live payload has an invalid idempotency key") from None
        expected = expected_order_payload(
            recipient, str(payload["idempotencyKey"]), context, baseline_id
        )
        if payload != expected:
            raise RuntimeError("Existing persistent live payload differs from this order")
        return payload
    payload = expected_order_payload(
        recipient, str(uuid.uuid4()), context, baseline_id
    )
    save_private_json(path, payload, api_key)
    save_private_json(
        context.work_dir / "order-audit.json",
        {
            "apiBase": live.API_BASE,
            "createdAt": live.utc_now(),
            "assetSha256": context.asset_sha256,
            "idempotencyKey": payload["idempotencyKey"],
            "merchantReference": context.merchant_reference,
            "sourceSandboxOrderId": baseline_id,
            "shippingMethod": "Budget",
            "sku": context.paper.sku,
            "copies": 1,
        },
        api_key,
    )
    return payload


def order_summary(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"outcome": None, "orderId": None, "status": {}, "items": [], "charges": [], "shipments": []}
    order = response.get("order") if isinstance(response.get("order"), dict) else {}
    items = order.get("items") if isinstance(order.get("items"), list) else []
    return {
        "outcome": response.get("outcome"),
        "orderId": order.get("id"),
        "status": order.get("status") if isinstance(order.get("status"), dict) else {},
        "items": [
            {"id": item.get("id"), "sku": item.get("sku"), "status": item.get("status")}
            for item in items
            if isinstance(item, dict)
        ],
        "charges": order.get("charges") or [],
        "shipments": order.get("shipments") or [],
    }


def print_order(response: Any, label: str) -> None:
    summary = order_summary(response)
    print(f"LIVE ORDER {label} OUTCOME {summary['outcome']}")
    print(f"LIVE ORDER ID {summary['orderId']}")
    print("LIVE ORDER STATUS " + json.dumps(summary["status"], separators=(",", ":")))
    print("LIVE ORDER ITEMS " + json.dumps(summary["items"], separators=(",", ":")))
    print("LIVE ORDER CHARGES " + json.dumps(summary["charges"], separators=(",", ":")))
    print("LIVE ORDER SHIPMENTS " + json.dumps(summary["shipments"], separators=(",", ":")))


def retrieve_order(
    client: live.ProdigiLiveClient, api_key: str, context: RunContext
) -> Any:
    create_path = context.work_dir / "order-response.json"
    retrieve_path = context.work_dir / "order-retrieved.json"
    if not create_path.is_file():
        raise RuntimeError("No saved live order response is available to retrieve")
    if retrieve_path.is_file():
        record = load_json(retrieve_path)
        print("LIVE ORDER RETRIEVAL already saved; no API request made")
        print_order(record.get("response"), "RETRIEVE")
        return record.get("response")
    created_record = load_json(create_path)
    created_summary = order_summary(created_record.get("response"))
    order_id = created_summary["orderId"]
    if not isinstance(order_id, str) or not order_id.startswith("ord_"):
        raise RuntimeError("Saved live order response lacks a valid order ID")
    retrieved = client.request(
        "GET", f"/orders/{urllib.parse.quote(order_id, safe='')}"
    )
    if not isinstance(retrieved, dict):
        raise RuntimeError("Live order retrieval returned a non-object response")
    save_private_json(
        retrieve_path,
        {"apiBase": live.API_BASE, "retrievedAt": live.utc_now(), "response": retrieved},
        api_key,
    )
    print_order(retrieved, "RETRIEVE")
    return retrieved


def create_and_retrieve_order(
    client: live.ProdigiLiveClient,
    api_key: str,
    recipient: dict[str, Any],
    context: RunContext,
) -> None:
    validate_saved_quote(context)
    baseline_id = validate_sandbox_baseline(context, recipient)
    if baseline_id != load_validation(context)["sandboxBaselineOrderId"]:
        raise RuntimeError("Sandbox baseline order changed after live preflight")
    validate_public_asset(context)
    payload = load_or_create_payload(recipient, context, api_key)
    create_path = context.work_dir / "order-response.json"
    if create_path.exists():
        raise RuntimeError("Live order response already exists; refusing another POST")
    created = client.request("POST", "/orders", payload)
    if not isinstance(created, dict):
        raise RuntimeError("Live order creation returned a non-object response")
    if str(created.get("outcome", "")).lower() not in {
        "created",
        "createdwithissues",
        "onhold",
        "alreadyexists",
    }:
        raise RuntimeError(
            f"Live order outcome was not successful: {created.get('outcome')!r}"
        )
    save_private_json(
        create_path,
        {"apiBase": live.API_BASE, "createdAt": live.utc_now(), "response": created},
        api_key,
    )
    print_order(created, "CREATE")
    retrieve_order(client, api_key, context)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("preflight", "quote", "order", "retrieve"))
    parser.add_argument(
        "--paper",
        choices=paper_keys(),
        default="fap",
        help="Configured 20-inch paper variant (default: fap)",
    )
    args = parser.parse_args()
    api_key = ""
    recipient: dict[str, Any] = {}
    try:
        context = build_context(get_paper(args.paper))
        print(
            f"LIVE RUN {context.paper.sku} asset {context.asset_sha256[:12]} "
            f"records {context.work_dir}"
        )
        recipient = live.load_recipient()
        api_key = live.load_live_api_key()
        client = live.ProdigiLiveClient(api_key, live.private_strings(recipient))
        if args.stage == "preflight":
            validate_public_asset(context)
            baseline_id = validate_sandbox_baseline(context, recipient)
            validate_product(client, api_key, context, baseline_id)
            print("PRIVATE RECIPIENT VERIFIED identical to sandbox baseline")
        elif args.stage == "quote":
            create_quote(client, api_key, context)
        elif args.stage == "order":
            create_and_retrieve_order(client, api_key, recipient, context)
        else:
            retrieve_order(client, api_key, context)
        return 0
    except (live.ApiFailure, live.LiveApiViolation, RuntimeError, ValueError, OSError) as exc:
        private = live.private_strings(recipient) | ({api_key} if api_key else set())
        safe = live.redact(str(exc), private)
        print(f"ERROR {safe}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
