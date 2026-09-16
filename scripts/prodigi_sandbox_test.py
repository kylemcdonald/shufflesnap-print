#!/usr/bin/env python3
"""Run the guarded Prodigi v4 sandbox checks for the 12-inch paper proof.

The API key is read from ../.env, used only as an in-memory X-API-Key header,
and never printed or written. Network requests and redirects are restricted to
https://api.sandbox.prodigi.com/v4.0.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / ".env"
WORK_DIR = PROJECT_DIR / "work/prodigi-sandbox"

API_SCHEME = "https"
API_HOST = "api.sandbox.prodigi.com"
API_PREFIX = "/v4.0"
API_BASE = f"{API_SCHEME}://{API_HOST}{API_PREFIX}"

CANDIDATES = (
    ("GLOBAL-HPR-12X12", "Hahnemühle Photo Rag"),
    ("GLOBAL-FAP-12X12", "Enhanced Matte Art"),
    ("ART-FAP-BAP-12X12", "Budget Art Paper"),
    ("ART-FAP-SAP-12X12", "Smooth Art Paper"),
)

SAMPLE_ASSET = (
    "https://pwintyimages.blob.core.windows.net/"
    "samples/stars/test-sample-grey.png"
)
MERCHANT_REFERENCE = "billion-12x12-paper-test-sandbox"


class SandboxViolation(RuntimeError):
    pass


@dataclass
class ApiFailure(RuntimeError):
    method: str
    endpoint: str
    status: int | None
    detail: Any

    def __str__(self) -> str:
        status = f"HTTP {self.status}" if self.status is not None else "network error"
        if isinstance(self.detail, (dict, list)):
            rendered = json.dumps(self.detail, sort_keys=True, separators=(",", ":"))
        else:
            rendered = str(self.detail)
        return f"{self.method} {self.endpoint}: {status}: {rendered[:2000]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_api_key() -> str:
    """Read only a sandbox-labelled Prodigi key without echoing any value."""
    if not ENV_PATH.is_file():
        raise RuntimeError(f"Credential file not found: {ENV_PATH}")
    assignments: dict[str, str] = {}
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and value:
            assignments[name] = value
    preferred = (
        "PRODIGI_SANDBOX_API_KEY",
        "SANDBOX_PRODIGI_API_KEY",
        "PRODIGI_SANDBOX_KEY",
        "PRODIGI_API_KEY_SANDBOX",
    )
    for name in preferred:
        if assignments.get(name):
            return assignments[name]
    plausible = [
        value
        for name, value in assignments.items()
        if "SANDBOX" in name.upper()
        and "LIVE" not in name.upper()
        and ("PRODIGI" in name.upper() or "API" in name.upper())
    ]
    if len(plausible) == 1:
        return plausible[0]

    def edit_distance(left: str, right: str) -> int:
        previous = list(range(len(right) + 1))
        for row, left_char in enumerate(left, 1):
            current = [row]
            for column, right_char in enumerate(right, 1):
                current.append(
                    min(
                        current[-1] + 1,
                        previous[column] + 1,
                        previous[column - 1] + (left_char != right_char),
                    )
                )
            previous = current
        return previous[-1]

    near_sandbox = []
    for name, value in assignments.items():
        parts = name.upper().replace("-", "_").split("_")
        if (
            "PRODIGI" in parts
            and "API" in parts
            and "KEY" in parts
            and "LIVE" not in parts
            and any(len(part) == 7 and edit_distance(part, "SANDBOX") == 1 for part in parts)
        ):
            near_sandbox.append(value)
    if len(near_sandbox) == 1:
        return near_sandbox[0]
    raise RuntimeError(
        "Could not identify exactly one sandbox-labelled Prodigi API key assignment in .env"
    )


def validate_sandbox_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != API_SCHEME or parsed.hostname != API_HOST:
        raise SandboxViolation(f"Blocked non-sandbox URL: {parsed.scheme}://{parsed.netloc}")
    if parsed.port not in (None, 443):
        raise SandboxViolation(f"Blocked unexpected sandbox port: {parsed.port}")
    if not (parsed.path == API_PREFIX or parsed.path.startswith(f"{API_PREFIX}/")):
        raise SandboxViolation(f"Blocked URL outside {API_PREFIX}: {parsed.path}")


class SandboxOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        validate_sandbox_url(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


class ProdigiSandboxClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._opener = urllib.request.build_opener(SandboxOnlyRedirectHandler())

    def request(self, method: str, endpoint: str,
                payload: dict[str, Any] | None = None) -> Any:
        if not endpoint.startswith("/") or "?" in endpoint:
            raise ValueError(f"Endpoint must be an absolute path without a query: {endpoint}")
        url = f"{API_BASE}{endpoint}"
        validate_sandbox_url(url)
        body = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self._api_key,
            "User-Agent": "shufflesnap-print-prodigi-sandbox-test/1.0",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=60) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = decode_response(raw)
            raise ApiFailure(method, endpoint, exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApiFailure(method, endpoint, None, f"{type(exc).__name__}: {exc}") from None
        if status < 200 or status >= 300:
            raise ApiFailure(method, endpoint, status, decode_response(raw))
        return decode_response(raw)


def decode_response(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")[:2000]


def assert_sanitized(value: Any, api_key: str) -> None:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if api_key and api_key in serialized:
        raise RuntimeError("Refusing to save a record containing the API key")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = str(key).lower().replace("-", "").replace("_", "")
                if normalized in {"apikey", "xapikey", "authorization"}:
                    raise RuntimeError(f"Refusing to save credential-like field: {key}")
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


def save_json(path: Path, value: Any, api_key: str) -> None:
    assert_sanitized(value, api_key)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dimensions_are_12_square(dimensions: Any) -> bool:
    if not isinstance(dimensions, dict):
        return False
    try:
        return (
            Decimal(str(dimensions.get("width"))) == Decimal("12")
            and Decimal(str(dimensions.get("height"))) == Decimal("12")
            and str(dimensions.get("units", "")).lower() in {"in", "inch", "inches"}
        )
    except InvalidOperation:
        return False


def selected_empty_attribute_variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    variants = product.get("variants")
    if not isinstance(variants, list):
        return []
    empty = [variant for variant in variants if isinstance(variant, dict) and not variant.get("attributes")]
    if empty:
        return empty
    if not product.get("attributes"):
        return [variant for variant in variants if isinstance(variant, dict)]
    return []


def summarize_product(product: dict[str, Any], requested_sku: str,
                      expected_material: str) -> dict[str, Any]:
    actual_sku = str(product.get("sku", ""))
    if actual_sku.upper() != requested_sku.upper():
        raise RuntimeError(f"Requested {requested_sku}, but API returned SKU {actual_sku!r}")
    dimensions = product.get("productDimensions")
    if not isinstance(dimensions, dict) or not {"width", "height", "units"}.issubset(dimensions):
        raise RuntimeError(f"{requested_sku} returned invalid product dimensions: {dimensions!r}")
    variants = [variant for variant in product.get("variants", []) if isinstance(variant, dict)]
    if not variants:
        raise RuntimeError(f"{requested_sku} returned no product variants")
    ships_to_us = any("US" in variant.get("shipsTo", []) for variant in variants)
    resolutions: list[dict[str, int]] = []
    for variant in variants:
        default_area = variant.get("printAreaSizes", {}).get("default", {})
        if {
            "horizontalResolution",
            "verticalResolution",
        }.issubset(default_area):
            resolution = {
                "horizontalResolution": int(default_area["horizontalResolution"]),
                "verticalResolution": int(default_area["verticalResolution"]),
            }
            if resolution not in resolutions:
                resolutions.append(resolution)
    if not resolutions:
        raise RuntimeError(f"{requested_sku} has no recommended default print-area resolution")
    return {
        "sku": actual_sku,
        "candidateMaterial": expected_material,
        "description": product.get("description"),
        "productDimensions": dimensions,
        "exactly12x12Inches": dimensions_are_12_square(dimensions),
        "catalogAttributes": product.get("attributes", {}),
        "recommendedDefaultPrintAreaResolutions": resolutions,
        "shipsToUS": ships_to_us,
        "requestedQuoteAttributes": {},
    }


def validate_products(client: ProdigiSandboxClient, api_key: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    failures: list[str] = []
    for sku, material in CANDIDATES:
        try:
            response = client.request("GET", f"/products/{urllib.parse.quote(sku, safe='')}")
            if not isinstance(response, dict):
                raise RuntimeError(f"Unexpected non-object response: {type(response).__name__}")
            if isinstance(response.get("product"), dict):
                if str(response.get("outcome", "")).lower() != "ok":
                    raise RuntimeError(f"Product lookup outcome was not Ok: {response.get('outcome')!r}")
                product = response["product"]
            else:
                product = response
            summary = summarize_product(product, sku, material)
            summaries.append(summary)
            resolution_text = ", ".join(
                f"{r['horizontalResolution']}×{r['verticalResolution']} px"
                for r in summary["recommendedDefaultPrintAreaResolutions"]
            )
            dims = summary["productDimensions"]
            print(
                f"VALID {summary['sku']} | {summary['description']} | "
                f"{dims['width']}×{dims['height']} {dims['units']} | "
                f"default {resolution_text} | ships to US: {summary['shipsToUS']}"
            )
        except ApiFailure as exc:
            if exc.status in (401, 403):
                raise
            failures.append(str(exc))
        except RuntimeError as exc:
            failures.append(f"{sku}: {exc}")

    record = {
        "apiBase": API_BASE,
        "validatedAt": utc_now(),
        "products": summaries,
        "failures": failures,
    }
    save_json(WORK_DIR / "product-validation.json", record, api_key)
    if failures:
        raise RuntimeError("SKU validation failed: " + " | ".join(failures))
    if len(summaries) != len(CANDIDATES):
        raise RuntimeError("SKU validation did not return all requested candidates")
    if not all(summary["shipsToUS"] for summary in summaries):
        unavailable = [summary["sku"] for summary in summaries if not summary["shipsToUS"]]
        raise RuntimeError(f"Validated SKU does not ship to US: {', '.join(unavailable)}")
    return summaries


def load_validated_products() -> list[dict[str, Any]]:
    path = WORK_DIR / "product-validation.json"
    if not path.is_file():
        raise RuntimeError("Run product validation before requesting a quote")
    record = load_json(path)
    if record.get("apiBase") != API_BASE or record.get("failures"):
        raise RuntimeError("Product validation record is missing, failed, or for another API base")
    products = record.get("products")
    expected = [sku.upper() for sku, _ in CANDIDATES]
    actual = [str(product.get("sku", "")).upper() for product in products or []]
    if actual != expected or not all(product.get("shipsToUS") for product in products):
        raise RuntimeError("Product validation record does not contain all requested US-shippable candidates")
    return products


def make_quote_payload(products: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "shippingMethod": "Budget",
        "destinationCountryCode": "US",
        "currencyCode": "USD",
        "items": [
            {
                "sku": product["sku"],
                "copies": 1,
                "attributes": {},
                "assets": [{"printArea": "default"}],
            }
            for product in products
        ],
    }


def request_quote(client: ProdigiSandboxClient, api_key: str) -> dict[str, Any]:
    products = load_validated_products()
    payload = make_quote_payload(products)
    response = client.request("POST", "/quotes", payload)
    if not isinstance(response, dict):
        raise RuntimeError(f"Quote returned non-object response: {type(response).__name__}")
    outcome = str(response.get("outcome", ""))
    if outcome.lower() not in {"created", "createdwithissues"}:
        raise RuntimeError(f"Quote outcome was not successful: {outcome!r}")
    quotes = response.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise RuntimeError(f"Expected one Budget quote; got {len(quotes) if isinstance(quotes, list) else 'none'}")
    quote = quotes[0]
    if str(quote.get("shipmentMethod", "")).lower() != "budget":
        raise RuntimeError(f"Expected Budget quote; got {quote.get('shipmentMethod')!r}")
    record = {
        "apiBase": API_BASE,
        "quotedAt": utc_now(),
        "request": payload,
        "response": response,
    }
    save_json(WORK_DIR / "quote.json", record, api_key)
    print_quote_costs(response)
    issues = response.get("issues") or quote.get("issues") or []
    if issues:
        print("QUOTE ISSUES " + json.dumps(issues, sort_keys=True, separators=(",", ":")))
    return response


def money_text(cost: Any) -> str:
    if not isinstance(cost, dict) or "amount" not in cost:
        return "not returned by API"
    currency = cost.get("currency", "")
    return f"{cost['amount']} {currency}".strip()


def print_quote_costs(response: dict[str, Any]) -> None:
    quote = response["quotes"][0]
    summary = quote.get("costSummary", {})
    items = summary.get("items")
    shipping = summary.get("shipping")
    tax = (
        summary.get("totalTax")
        or summary.get("tax")
        or summary.get("taxes")
        or summary.get("salesTax")
    )
    total = summary.get("totalCost") or summary.get("total") or summary.get("grandTotal")
    derived_total = None
    if total is None and isinstance(items, dict) and isinstance(shipping, dict):
        try:
            if items.get("currency") == shipping.get("currency"):
                amount = Decimal(str(items["amount"])) + Decimal(str(shipping["amount"]))
                if isinstance(tax, dict) and tax.get("currency") == items.get("currency"):
                    amount += Decimal(str(tax["amount"]))
                derived_total = f"{amount:.2f} {items.get('currency', '')}".strip()
        except (InvalidOperation, KeyError):
            pass
    print(f"QUOTE OUTCOME {response.get('outcome')}")
    print(f"QUOTE ITEMS {money_text(items)}")
    print(f"QUOTE SHIPPING {money_text(shipping)}")
    print(f"QUOTE TAX {money_text(tax)}")
    if total is not None:
        print(f"QUOTE TOTAL {money_text(total)}")
    elif derived_total is not None:
        suffix = " (derived from returned components; tax not returned)" if tax is None else " (derived)"
        print(f"QUOTE TOTAL {derived_total}{suffix}")
    else:
        print("QUOTE TOTAL not returned by API")


def quote_record_is_successful() -> bool:
    path = WORK_DIR / "quote.json"
    if not path.is_file():
        return False
    record = load_json(path)
    response = record.get("response", {})
    return (
        record.get("apiBase") == API_BASE
        and str(response.get("outcome", "")).lower() in {"created", "createdwithissues"}
    )


def expected_order_payload(products: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
    return {
        "idempotencyKey": idempotency_key,
        "merchantReference": MERCHANT_REFERENCE,
        "shippingMethod": "Budget",
        "recipient": {
            "name": "Kyle Sandbox Test",
            "address": {
                "line1": "14 Test Place",
                "townOrCity": "Somewhere",
                "stateOrCounty": "CA",
                "postalOrZipCode": "12345",
                "countryCode": "US",
            },
        },
        "items": [
            {
                "merchantReference": f"paper-test-{index + 1}-{product['sku'].lower()}",
                "sku": product["sku"],
                "copies": 1,
                "sizing": "fillPrintArea",
                "attributes": {},
                "assets": [
                    {
                        "printArea": "default",
                        "url": SAMPLE_ASSET,
                    }
                ],
            }
            for index, product in enumerate(products)
        ],
        "metadata": {
            "purpose": "Validates a 12-inch paper-comparison workflow",
            "artworkId": "billion_31623_seed0_test_interp75",
            "environment": "Prodigi sandbox",
            "asset": "Prodigi documented public sample image; regenerated proof sheet is not uploaded",
        },
    }


def load_or_create_order_payload(products: list[dict[str, Any]], api_key: str) -> dict[str, Any]:
    path = WORK_DIR / "order-payload.json"
    if path.is_file():
        payload = load_json(path)
        key = payload.get("idempotencyKey")
        try:
            uuid.UUID(str(key))
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Existing order payload has an invalid idempotencyKey") from None
        expected = expected_order_payload(products, str(key))
        if payload != expected:
            raise RuntimeError("Existing persistent order payload does not match this workflow")
        return payload
    payload = expected_order_payload(products, str(uuid.uuid4()))
    save_json(path, payload, api_key)
    return payload


def create_and_retrieve_order(client: ProdigiSandboxClient, api_key: str) -> dict[str, Any]:
    if not quote_record_is_successful():
        raise RuntimeError("A successful saved sandbox quote is required before order creation")
    products = load_validated_products()
    payload = load_or_create_order_payload(products, api_key)
    create_path = WORK_DIR / "order-response.json"
    retrieve_path = WORK_DIR / "order-retrieved.json"
    if create_path.exists() or retrieve_path.exists():
        raise RuntimeError(
            "Saved order response already exists; refusing to issue another create or retrieve request"
        )

    response = client.request("POST", "/orders", payload)
    if not isinstance(response, dict):
        raise RuntimeError(f"Order returned non-object response: {type(response).__name__}")
    outcome = str(response.get("outcome", ""))
    if outcome.lower() not in {"created", "onhold", "createdwithissues", "alreadyexists"}:
        raise RuntimeError(f"Order creation outcome was not successful: {outcome!r}")
    create_record = {
        "apiBase": API_BASE,
        "createdAt": utc_now(),
        "response": response,
    }
    save_json(create_path, create_record, api_key)
    order = response.get("order")
    order_id = order.get("id") if isinstance(order, dict) else None
    if not order_id:
        raise RuntimeError("Successful order response did not include order.id")

    retrieved = client.request("GET", f"/orders/{urllib.parse.quote(str(order_id), safe='')}")
    if not isinstance(retrieved, dict):
        raise RuntimeError(f"Order retrieval returned non-object response: {type(retrieved).__name__}")
    retrieve_record = {
        "apiBase": API_BASE,
        "retrievedAt": utc_now(),
        "response": retrieved,
    }
    save_json(retrieve_path, retrieve_record, api_key)
    print_order_summary(response, retrieved)
    return retrieved


def print_order_summary(created: dict[str, Any], retrieved: dict[str, Any]) -> None:
    order = retrieved.get("order") if isinstance(retrieved.get("order"), dict) else {}
    status = order.get("status") if isinstance(order.get("status"), dict) else {}
    items = order.get("items") if isinstance(order.get("items"), list) else []
    item_summary = [
        {"id": item.get("id"), "sku": item.get("sku"), "status": item.get("status")}
        for item in items
        if isinstance(item, dict)
    ]
    print(f"ORDER CREATE OUTCOME {created.get('outcome')}")
    print(f"ORDER RETRIEVE OUTCOME {retrieved.get('outcome')}")
    print(f"ORDER ID {order.get('id')}")
    print(f"ORDER STATUS {json.dumps(status, sort_keys=True, separators=(',', ':'))}")
    print(f"ORDER ITEMS {json.dumps(item_summary, sort_keys=True, separators=(',', ':'))}")
    print(f"ORDER CHARGES {json.dumps(order.get('charges', []), sort_keys=True, separators=(',', ':'))}")
    print(f"ORDER SHIPMENTS {json.dumps(order.get('shipments', []), sort_keys=True, separators=(',', ':'))}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("products", "quote", "order"))
    args = parser.parse_args()

    try:
        api_key = load_api_key()
        client = ProdigiSandboxClient(api_key)
        if args.stage == "products":
            validate_products(client, api_key)
        elif args.stage == "quote":
            request_quote(client, api_key)
        else:
            create_and_retrieve_order(client, api_key)
    except (ApiFailure, SandboxViolation, RuntimeError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
