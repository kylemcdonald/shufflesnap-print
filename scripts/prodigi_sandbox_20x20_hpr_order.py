#!/usr/bin/env python3
"""Validate, quote, and create one Pages-backed 20-inch HPR sandbox order.

All Prodigi requests use the sandbox-only client. Credentials stay in memory;
the recipient and sanitized API records remain under the gitignored work tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import prodigi_sandbox_test as base


PROJECT_DIR = Path(__file__).resolve().parents[1]
PRIVATE_WORK_DIR = PROJECT_DIR / "work/prodigi-sandbox-20x20-hpr"
RECIPIENT_PATH = PROJECT_DIR / "work/prodigi-recipient.json"
PAGES_BASE = "https://kylemcdonald.github.io/megalap-print/"
ASSET_PATH = "outputs/prodigi/billion_20x20_global-hpr-20x20_300ppi.png"
ASSET_URL = urllib.parse.urljoin(PAGES_BASE, ASSET_PATH)

SKU = "GLOBAL-HPR-20X20"
PAPER_NAME = "Hahnemühle Photo Rag"
MERCHANT_REFERENCE = "billion-20x20-hpr-sandbox"
ALLOWED_QUOTE_ISSUES = {"destinationCountryCode.UsSalesTaxWarning"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def private_strings(node: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(node, dict):
        for child in node.values():
            values.update(private_strings(child))
    elif isinstance(node, list):
        for child in node:
            values.update(private_strings(child))
    elif isinstance(node, str) and node:
        values.add(node)
    return values


def redact(node: Any, private_values: set[str]) -> Any:
    if isinstance(node, dict):
        return {key: redact(value, private_values) for key, value in node.items()}
    if isinstance(node, list):
        return [redact(value, private_values) for value in node]
    if isinstance(node, str):
        value = node
        for private in sorted(private_values, key=len, reverse=True):
            if private:
                value = value.replace(private, "[REDACTED]")
        return value
    return node


def save_private_json(path: Path, value: Any, api_key: str) -> None:
    base.assert_sanitized(value, api_key)
    PRIVATE_WORK_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_WORK_DIR.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def load_recipient() -> dict[str, Any]:
    if not RECIPIENT_PATH.is_file():
        raise RuntimeError("Private recipient record is missing")
    recipient = load_json(RECIPIENT_PATH)
    if not isinstance(recipient, dict) or not isinstance(recipient.get("address"), dict):
        raise RuntimeError("Private recipient record must contain a name and address object")
    if set(recipient) - {"name", "email", "phoneNumber", "address"}:
        raise RuntimeError("Private recipient record contains unsupported recipient fields")
    address = recipient["address"]
    required = ("line1", "townOrCity", "stateOrCounty", "postalOrZipCode", "countryCode")
    if set(address) - {*required, "line2"}:
        raise RuntimeError("Private recipient record contains unsupported address fields")
    values = [recipient.get("name"), *(address.get(field) for field in required)]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise RuntimeError("Private recipient record has missing or invalid required fields")
    if address["countryCode"].strip().upper() != "US":
        raise RuntimeError("Private recipient country is not US")
    if not re.fullmatch(r"[A-Za-z]{2}", address["stateOrCounty"].strip()):
        raise RuntimeError("Private recipient state must be a two-letter US code")
    if not re.fullmatch(r"\d{5}(?:-\d{4})?", address["postalOrZipCode"].strip()):
        raise RuntimeError("Private recipient ZIP code format is invalid")
    return recipient


def product_from_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("Product lookup returned a non-object response")
    if str(response.get("outcome", "")).lower() != "ok":
        raise RuntimeError(f"Product lookup outcome was not Ok: {response.get('outcome')!r}")
    product = response.get("product") if isinstance(response.get("product"), dict) else response
    if str(product.get("sku", "")).upper() != SKU:
        raise RuntimeError(f"Product response did not match requested SKU {SKU}")
    return product


def validate_product(client: base.ProdigiSandboxClient, api_key: str) -> dict[str, Any]:
    response = client.request("GET", f"/products/{urllib.parse.quote(SKU, safe='')}")
    product = product_from_response(response)
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
        raise RuntimeError(f"{SKU} is not exactly 20×20 inches: {dimensions!r}")

    variants = [item for item in product.get("variants", []) if isinstance(item, dict)]
    if not variants:
        raise RuntimeError(f"{SKU} returned no variants")
    ships_to_us = any("US" in item.get("shipsTo", []) for item in variants)
    if not ships_to_us:
        raise RuntimeError(f"{SKU} does not ship to the US")
    resolutions: list[dict[str, int]] = []
    for variant in variants:
        default = variant.get("printAreaSizes", {}).get("default", {})
        if {"horizontalResolution", "verticalResolution"}.issubset(default):
            resolution = {
                "horizontalResolution": int(default["horizontalResolution"]),
                "verticalResolution": int(default["verticalResolution"]),
            }
            if resolution not in resolutions:
                resolutions.append(resolution)
    expected_resolution = {"horizontalResolution": 6000, "verticalResolution": 6000}
    if expected_resolution not in resolutions:
        raise RuntimeError(f"{SKU} does not recommend the expected 6000×6000 default area")

    summary = {
        "sku": product.get("sku"),
        "description": product.get("description"),
        "paper": PAPER_NAME,
        "productDimensions": dimensions,
        "catalogAttributes": product.get("attributes", {}),
        "recommendedDefaultPrintAreaResolutions": resolutions,
        "shipsToUS": ships_to_us,
        "requestedOrderAttributes": {},
    }
    save_private_json(
        PRIVATE_WORK_DIR / "product-validation.json",
        {
            "apiBase": base.API_BASE,
            "validatedAt": base.utc_now(),
            "product": summary,
        },
        api_key,
    )
    print(
        f"VALID {summary['sku']} | {summary['description']} | "
        "20×20 in | default 6000×6000 px | ships to US: true"
    )
    return summary


def load_validated_product() -> dict[str, Any]:
    path = PRIVATE_WORK_DIR / "product-validation.json"
    if not path.is_file():
        raise RuntimeError("Run product validation before requesting a quote")
    record = load_json(path)
    product = record.get("product") if isinstance(record.get("product"), dict) else {}
    if (
        record.get("apiBase") != base.API_BASE
        or str(product.get("sku", "")).upper() != SKU
        or not product.get("shipsToUS")
    ):
        raise RuntimeError("Saved product validation is missing or invalid")
    return product


def quote_payload() -> dict[str, Any]:
    return {
        "shippingMethod": "Budget",
        "destinationCountryCode": "US",
        "currencyCode": "USD",
        "items": [
            {
                "sku": SKU,
                "copies": 1,
                "attributes": {},
                "assets": [{"printArea": "default"}],
            }
        ],
    }


def response_issues(response: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    candidates = response.get("issues")
    if isinstance(candidates, list):
        collected.extend(item for item in candidates if isinstance(item, dict))
    quotes = response.get("quotes")
    if isinstance(quotes, list):
        for quote in quotes:
            if isinstance(quote, dict) and isinstance(quote.get("issues"), list):
                collected.extend(item for item in quote["issues"] if isinstance(item, dict))
    return collected


def validate_quote_issues(response: dict[str, Any]) -> None:
    issues = response_issues(response)
    unexpected = [
        item for item in issues if str(item.get("errorCode", "")) not in ALLOWED_QUOTE_ISSUES
    ]
    if unexpected:
        raise RuntimeError(
            "Quote returned unexpected issues: "
            + json.dumps(unexpected, sort_keys=True, separators=(",", ":"))
        )


def create_quote(client: base.ProdigiSandboxClient, api_key: str) -> dict[str, Any]:
    load_validated_product()
    payload = quote_payload()
    response = client.request("POST", "/quotes", payload)
    if not isinstance(response, dict):
        raise RuntimeError("Quote returned a non-object response")
    if str(response.get("outcome", "")).lower() not in {"created", "createdwithissues"}:
        raise RuntimeError(f"Quote outcome was not successful: {response.get('outcome')!r}")
    quotes = response.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise RuntimeError("Expected exactly one Budget quote")
    if str(quotes[0].get("shipmentMethod", "")).lower() != "budget":
        raise RuntimeError(f"Expected Budget; got {quotes[0].get('shipmentMethod')!r}")
    validate_quote_issues(response)
    save_private_json(
        PRIVATE_WORK_DIR / "quote.json",
        {
            "apiBase": base.API_BASE,
            "quotedAt": base.utc_now(),
            "request": payload,
            "response": response,
        },
        api_key,
    )
    base.print_quote_costs(response)
    issues = response_issues(response)
    if issues:
        print("QUOTE ISSUES " + json.dumps(issues, sort_keys=True, separators=(",", ":")))
    return response


def quote_is_successful() -> bool:
    path = PRIVATE_WORK_DIR / "quote.json"
    if not path.is_file():
        return False
    record = load_json(path)
    return (
        record.get("apiBase") == base.API_BASE
        and str(record.get("response", {}).get("outcome", "")).lower()
        in {"created", "createdwithissues"}
    )


def validate_public_asset() -> str:
    local_path = PROJECT_DIR / ASSET_PATH
    if not local_path.is_file():
        raise RuntimeError("Local 20-inch print asset is missing")
    parsed = urllib.parse.urlsplit(ASSET_URL)
    if parsed.scheme != "https" or parsed.hostname != "kylemcdonald.github.io":
        raise RuntimeError("Blocked unexpected public asset origin")
    request = urllib.request.Request(
        ASSET_URL,
        headers={"User-Agent": "megalap-prodigi-asset-check/1.0"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        remote = response.read()
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
        status = response.status
    if status != 200 or not content_type.lower().startswith("image/png"):
        raise RuntimeError("Public print asset is not a reachable PNG")
    if urllib.parse.urlsplit(final_url).scheme != "https":
        raise RuntimeError("Public print asset redirected away from HTTPS")
    local = local_path.read_bytes()
    local_hash = hashlib.sha256(local).hexdigest()
    if hashlib.sha256(remote).hexdigest() != local_hash:
        raise RuntimeError("Public print asset bytes do not match the local PNG")
    print(f"ASSET VERIFIED public HTTPS PNG matches local SHA-256 {local_hash}")
    return local_hash


def expected_order_payload(recipient: dict[str, Any], idempotency_key: str,
                           asset_hash: str) -> dict[str, Any]:
    return {
        "idempotencyKey": idempotency_key,
        "merchantReference": MERCHANT_REFERENCE,
        "shippingMethod": "Budget",
        "recipient": recipient,
        "items": [
            {
                "merchantReference": "billion-20x20-hpr",
                "sku": SKU,
                "copies": 1,
                "sizing": "fillPrintArea",
                "attributes": {},
                "assets": [{"printArea": "default", "url": ASSET_URL}],
            }
        ],
        "metadata": {
            "purpose": "Validates the 20-inch Hahnemühle Photo Rag workflow",
            "artworkId": "billion_31623_seed0_interp75_variant12_1px",
            "canvas": "6000x6000_srgb_300ppi_white_2in_margin",
            "assetSha256": asset_hash,
            "environment": "Prodigi sandbox",
            "assetHosting": "Public HTTPS via GitHub Pages",
        },
    }


def load_or_create_payload(recipient: dict[str, Any], asset_hash: str,
                           api_key: str) -> dict[str, Any]:
    path = PRIVATE_WORK_DIR / "order-payload.json"
    if path.is_file():
        payload = load_json(path)
        try:
            uuid.UUID(str(payload.get("idempotencyKey")))
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Existing order payload has an invalid idempotency key") from None
        expected = expected_order_payload(recipient, str(payload["idempotencyKey"]), asset_hash)
        if payload != expected:
            raise RuntimeError("Existing persistent order payload differs from this order")
        return payload
    payload = expected_order_payload(recipient, str(uuid.uuid4()), asset_hash)
    save_private_json(path, payload, api_key)
    return payload


def create_and_retrieve_order(client: base.ProdigiSandboxClient,
                              api_key: str) -> dict[str, Any]:
    if not quote_is_successful():
        raise RuntimeError("A successful saved sandbox quote is required")
    load_validated_product()
    recipient = load_recipient()
    asset_hash = validate_public_asset()
    payload = load_or_create_payload(recipient, asset_hash, api_key)
    create_path = PRIVATE_WORK_DIR / "order-response.json"
    retrieve_path = PRIVATE_WORK_DIR / "order-retrieved.json"
    if create_path.exists() or retrieve_path.exists():
        raise RuntimeError("Order response already exists; refusing another POST")

    created = client.request("POST", "/orders", payload)
    if not isinstance(created, dict):
        raise RuntimeError("Order creation returned a non-object response")
    if str(created.get("outcome", "")).lower() not in {
        "created", "onhold", "createdwithissues", "alreadyexists"
    }:
        raise RuntimeError(f"Order outcome was not successful: {created.get('outcome')!r}")
    save_private_json(
        create_path,
        {"apiBase": base.API_BASE, "createdAt": base.utc_now(), "response": created},
        api_key,
    )
    order = created.get("order") if isinstance(created.get("order"), dict) else {}
    order_id = order.get("id")
    if not order_id:
        raise RuntimeError("Successful order response did not contain order.id")

    retrieved = client.request("GET", f"/orders/{urllib.parse.quote(str(order_id), safe='')}")
    if not isinstance(retrieved, dict):
        raise RuntimeError("Order retrieval returned a non-object response")
    save_private_json(
        retrieve_path,
        {"apiBase": base.API_BASE, "retrievedAt": base.utc_now(), "response": retrieved},
        api_key,
    )
    base.print_order_summary(created, retrieved)
    return retrieved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("address", "product", "quote", "order"))
    args = parser.parse_args()
    api_key = ""
    recipient: dict[str, Any] = {}
    try:
        if args.stage == "address":
            load_recipient()
            print("RECIPIENT VALIDATED required fields present; destination US")
            return 0
        api_key = base.load_api_key()
        client = base.ProdigiSandboxClient(api_key)
        if args.stage == "product":
            validate_product(client, api_key)
        elif args.stage == "quote":
            create_quote(client, api_key)
        else:
            recipient = load_recipient()
            create_and_retrieve_order(client, api_key)
        return 0
    except base.ApiFailure as exc:
        private = private_strings(recipient) | ({api_key} if api_key else set())
        detail = redact(exc.detail, private)
        status = f"HTTP {exc.status}" if exc.status is not None else "network error"
        rendered = json.dumps(detail, sort_keys=True, separators=(",", ":")) \
            if isinstance(detail, (dict, list)) else str(detail)
        print(f"ERROR {exc.method} {exc.endpoint}: {status}: {rendered[:2000]}", file=sys.stderr)
        return 1
    except (base.SandboxViolation, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
