#!/usr/bin/env python3
"""Validate, quote, and place one guarded four-paper Prodigi live order.

The live API key is read from .env and used only in memory as X-API-Key. The
recipient is read from the gitignored work/prodigi-recipient.json file. Neither
credential nor recipient values are printed. All API traffic is restricted to
https://api.prodigi.com/v4.0, and all records are written without headers or
credentials under the gitignored work/prodigi-live-four-paper directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
RECIPIENT_PATH = PROJECT_DIR / "work/prodigi-recipient.json"
PRIVATE_WORK_DIR = PROJECT_DIR / "work/prodigi-live-four-paper"

API_SCHEME = "https"
API_HOST = "api.prodigi.com"
API_PREFIX = "/v4.0"
API_BASE = f"{API_SCHEME}://{API_HOST}{API_PREFIX}"

PAGES_BASE = "https://kylemcdonald.github.io/shufflesnap-print/"
MERCHANT_REFERENCE = "billion-12x12-four-paper-test-live"
MAX_PRETAX_QUOTE_USD = Decimal("60.00")
ALLOWED_QUOTE_ISSUES = {"destinationCountryCode.UsSalesTaxWarning"}

PAPERS = (
    (
        "GLOBAL-HPR-12X12",
        "Hahnemuhle Photo Rag",
        "outputs/prodigi/billion_12x12_global-hpr-12x12_300ppi.png",
    ),
    (
        "GLOBAL-FAP-12X12",
        "Enhanced Matte Art",
        "outputs/prodigi/billion_12x12_global-fap-12x12_300ppi.png",
    ),
    (
        "ART-FAP-BAP-12X12",
        "Budget Art Paper",
        "outputs/prodigi/billion_12x12_art-fap-bap-12x12_300ppi.png",
    ),
    (
        "ART-FAP-SAP-12X12",
        "Smooth Art Paper",
        "outputs/prodigi/billion_12x12_art-fap-sap-12x12_300ppi.png",
    ),
)


class LiveApiViolation(RuntimeError):
    """Raised when a request tries to leave the exact live API origin."""


@dataclass
class ApiFailure(RuntimeError):
    method: str
    endpoint: str
    status: int | None
    detail: Any

    def __str__(self) -> str:
        status = f"HTTP {self.status}" if self.status is not None else "network error"
        if isinstance(self.detail, (dict, list)):
            detail = json.dumps(self.detail, sort_keys=True, separators=(",", ":"))
        else:
            detail = str(self.detail)
        return f"{self.method} {self.endpoint}: {status}: {detail[:2000]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_assignments() -> dict[str, str]:
    if not ENV_PATH.is_file():
        raise RuntimeError("Credential file is missing")
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
    return assignments


def load_live_api_key() -> str:
    """Read exactly one live-labelled key without exposing its name or value."""
    assignments = read_assignments()
    preferred = (
        "PRODIGI_LIVE_API_KEY",
        "LIVE_PRODIGI_API_KEY",
        "PRODIGI_API_KEY_LIVE",
        "PRODIGI_LIVE_KEY",
    )
    matches = [assignments[name] for name in preferred if assignments.get(name)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        unique = set(matches)
        if len(unique) == 1:
            return matches[0]
        raise RuntimeError("Multiple conflicting live-labelled API keys were found")
    plausible = [
        value
        for name, value in assignments.items()
        if "LIVE" in name.upper()
        and "SANDBOX" not in name.upper()
        and "PRODIGI" in name.upper()
        and "KEY" in name.upper()
    ]
    if len(plausible) == 1:
        return plausible[0]
    if len(plausible) > 1:
        raise RuntimeError("Could not identify exactly one live-labelled Prodigi API key")
    non_sandbox_prodigi = [
        value
        for name, value in assignments.items()
        if "SANDBOX" not in name.upper()
        and "PRODIGI" in name.upper()
        and "KEY" in name.upper()
    ]
    if len(non_sandbox_prodigi) != 1:
        raise RuntimeError("Could not identify exactly one non-sandbox Prodigi API key")
    return non_sandbox_prodigi[0]


def validate_live_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != API_SCHEME or parsed.hostname != API_HOST:
        raise LiveApiViolation("Blocked request outside the Prodigi live API")
    if parsed.port not in (None, 443):
        raise LiveApiViolation("Blocked unexpected Prodigi live API port")
    if not (parsed.path == API_PREFIX or parsed.path.startswith(f"{API_PREFIX}/")):
        raise LiveApiViolation(f"Blocked request outside {API_PREFIX}")


class LiveOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        validate_live_url(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def decode_response(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")[:2000]


def redact(value: Any, private_values: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: redact(child, private_values) for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, private_values) for child in value]
    if isinstance(value, str):
        result = value
        for private in sorted((item for item in private_values if item), key=len, reverse=True):
            result = result.replace(private, "[REDACTED]")
        return result
    return value


class ProdigiLiveClient:
    def __init__(self, api_key: str, private_values: set[str]) -> None:
        self._api_key = api_key
        self._private_values = private_values | {api_key}
        self._opener = urllib.request.build_opener(LiveOnlyRedirectHandler())

    def request(self, method: str, endpoint: str,
                payload: dict[str, Any] | None = None) -> Any:
        if not endpoint.startswith("/") or "?" in endpoint:
            raise ValueError("Endpoint must be an absolute path without a query")
        url = f"{API_BASE}{endpoint}"
        validate_live_url(url)
        body = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self._api_key,
            "User-Agent": "shufflesnap-print-prodigi-live-order/1.0",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=90) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            detail = redact(decode_response(exc.read()), self._private_values)
            raise ApiFailure(method, endpoint, exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            detail = redact(f"{type(exc).__name__}: {exc}", self._private_values)
            raise ApiFailure(method, endpoint, None, detail) from None
        if not 200 <= status < 300:
            detail = redact(decode_response(raw), self._private_values)
            raise ApiFailure(method, endpoint, status, detail)
        return decode_response(raw)


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


def load_recipient() -> dict[str, Any]:
    if not RECIPIENT_PATH.is_file():
        raise RuntimeError("Private recipient record is missing")
    recipient = load_json(RECIPIENT_PATH)
    if not isinstance(recipient, dict) or not isinstance(recipient.get("address"), dict):
        raise RuntimeError("Private recipient record must contain recipient and address objects")
    if set(recipient) - {"name", "email", "phoneNumber", "address"}:
        raise RuntimeError("Private recipient record contains unsupported recipient fields")
    address = recipient["address"]
    required = ("line1", "townOrCity", "stateOrCounty", "postalOrZipCode", "countryCode")
    allowed_address = {*required, "line2"}
    if set(address) - allowed_address:
        raise RuntimeError("Private recipient record contains unsupported address fields")
    values = [recipient.get("name"), *(address.get(name) for name in required)]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise RuntimeError("Private recipient record has missing or invalid required fields")
    if address["countryCode"].strip().upper() != "US":
        raise RuntimeError("Private recipient country is not US")
    if not re.fullmatch(r"[A-Za-z]{2}", address["stateOrCounty"].strip()):
        raise RuntimeError("Private recipient state must be a two-letter US code")
    if not re.fullmatch(r"\d{5}(?:-\d{4})?", address["postalOrZipCode"].strip()):
        raise RuntimeError("Private recipient ZIP code format is invalid")
    return recipient


def assert_sanitized(value: Any, api_key: str) -> None:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if api_key and api_key in serialized:
        raise RuntimeError("Refusing to save a record containing the API key")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = str(key).lower().replace("-", "").replace("_", "")
                if normalized in {"apikey", "xapikey", "authorization"}:
                    raise RuntimeError("Refusing to save a credential-like field")
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)


def save_private_json(path: Path, value: Any, api_key: str) -> None:
    assert_sanitized(value, api_key)
    PRIVATE_WORK_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_WORK_DIR.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def product_from_response(response: Any, requested_sku: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError(f"{requested_sku} returned a non-object product response")
    product = response.get("product") if isinstance(response.get("product"), dict) else response
    if str(product.get("sku", "")).upper() != requested_sku.upper():
        raise RuntimeError(f"Product response did not match requested SKU {requested_sku}")
    return product


def summarize_product(product: dict[str, Any], sku: str, paper_name: str) -> dict[str, Any]:
    dimensions = product.get("productDimensions")
    try:
        exact_inches = (
            isinstance(dimensions, dict)
            and Decimal(str(dimensions.get("width"))) == Decimal("12")
            and Decimal(str(dimensions.get("height"))) == Decimal("12")
            and str(dimensions.get("units", "")).lower() in {"in", "inch", "inches"}
        )
        nominal_metric = (
            isinstance(dimensions, dict)
            and Decimal(str(dimensions.get("width"))) == Decimal("30")
            and Decimal(str(dimensions.get("height"))) == Decimal("30")
            and str(dimensions.get("units", "")).lower() in {"cm", "centimeter", "centimeters"}
        )
    except InvalidOperation:
        exact_inches = False
        nominal_metric = False
    if not (exact_inches or nominal_metric) or not sku.upper().endswith("-12X12"):
        raise RuntimeError(f"{sku} is not Prodigi's 12X12 size")
    variants = [item for item in product.get("variants", []) if isinstance(item, dict)]
    us_variants = [item for item in variants if "US" in item.get("shipsTo", [])]
    if not us_variants:
        raise RuntimeError(f"{sku} does not have a US-shippable variant")
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
    if not resolutions:
        raise RuntimeError(f"{sku} has no recommended default print-area resolution")
    return {
        "sku": sku,
        "paperName": paper_name,
        "description": product.get("description"),
        "productDimensions": dimensions,
        "exactly12x12Inches": exact_inches,
        "nominal30x30Centimeters": nominal_metric,
        "recommendedDefaultPrintAreaResolutions": resolutions,
        "shipsToUS": True,
        "attributes": {},
    }


def validate_products(client: ProdigiLiveClient, api_key: str) -> list[dict[str, Any]]:
    products = []
    for sku, paper_name, _ in PAPERS:
        encoded = urllib.parse.quote(sku, safe="")
        product = product_from_response(client.request("GET", f"/products/{encoded}"), sku)
        products.append(summarize_product(product, sku, paper_name))
    save_private_json(
        PRIVATE_WORK_DIR / "product-validation.json",
        {"apiBase": API_BASE, "validatedAt": utc_now(), "products": products},
        api_key,
    )
    print("LIVE PRODUCTS VALIDATED " + ", ".join(product["sku"] for product in products))
    return products


class PagesOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> urllib.request.Request | None:
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.scheme != "https" or parsed.hostname != "kylemcdonald.github.io":
            raise RuntimeError("Public asset redirected outside the expected GitHub Pages host")
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def validate_public_assets() -> None:
    opener = urllib.request.build_opener(PagesOnlyRedirectHandler())
    hashes: set[bytes] = set()
    for sku, _, relative_path in PAPERS:
        local_path = PROJECT_DIR / relative_path
        if not local_path.is_file():
            raise RuntimeError(f"Local public asset is missing for {sku}")
        url = urllib.parse.urljoin(PAGES_BASE, relative_path)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "kylemcdonald.github.io":
            raise RuntimeError(f"Unexpected public asset URL for {sku}")
        request = urllib.request.Request(
            url, headers={"User-Agent": "shufflesnap-prodigi-live-asset-check/1.0"}
        )
        with opener.open(request, timeout=90) as response:
            remote = response.read()
            content_type = response.headers.get("Content-Type", "")
            status = response.status
        local = local_path.read_bytes()
        if status != 200 or not content_type.lower().startswith("image/png"):
            raise RuntimeError(f"Public asset is not a reachable PNG for {sku}")
        remote_hash = hashlib.sha256(remote).digest()
        if remote_hash != hashlib.sha256(local).digest():
            raise RuntimeError(f"Public asset does not match the local proof for {sku}")
        hashes.add(remote_hash)
    if len(hashes) != len(PAPERS):
        raise RuntimeError("The four public proof assets are not all distinct")
    print("PUBLIC ASSETS VALIDATED four distinct byte-identical HTTPS PNG files")


def load_validated_products() -> list[dict[str, Any]]:
    path = PRIVATE_WORK_DIR / "product-validation.json"
    if not path.is_file():
        raise RuntimeError("Fresh live product validation is required before quoting")
    record = load_json(path)
    products = record.get("products") if isinstance(record, dict) else None
    expected = [sku for sku, _, _ in PAPERS]
    actual = [item.get("sku") for item in products or [] if isinstance(item, dict)]
    if record.get("apiBase") != API_BASE or actual != expected:
        raise RuntimeError("Saved product validation does not match this live order")
    return products


def quote_payload(products: list[dict[str, Any]]) -> dict[str, Any]:
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


def quote_issues(response: dict[str, Any]) -> list[dict[str, Any]]:
    issues = response.get("issues") or []
    quotes = response.get("quotes")
    if isinstance(quotes, list) and len(quotes) == 1:
        issues = [*issues, *(quotes[0].get("issues") or [])]
    return [item for item in issues if isinstance(item, dict)]


def validate_quote(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("Live quote returned a non-object response")
    if str(response.get("outcome", "")).lower() not in {"created", "createdwithissues"}:
        raise RuntimeError(f"Live quote outcome was not successful: {response.get('outcome')!r}")
    quotes = response.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise RuntimeError("Expected exactly one live Budget quote")
    quote = quotes[0]
    if str(quote.get("shipmentMethod", "")).lower() != "budget":
        raise RuntimeError("Live quote did not return Budget shipping")
    summary = quote.get("costSummary")
    total = summary.get("totalCost") if isinstance(summary, dict) else None
    if not isinstance(total, dict) or total.get("currency") != "USD":
        raise RuntimeError("Live quote did not return a USD total")
    try:
        total_amount = Decimal(str(total["amount"]))
    except (InvalidOperation, KeyError):
        raise RuntimeError("Live quote returned an invalid total") from None
    if total_amount > MAX_PRETAX_QUOTE_USD:
        raise RuntimeError(
            f"Live pre-tax quote {total_amount:.2f} USD exceeds the "
            f"{MAX_PRETAX_QUOTE_USD:.2f} USD safety limit"
        )
    unexpected = [
        issue for issue in quote_issues(response)
        if issue.get("errorCode") not in ALLOWED_QUOTE_ISSUES
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
    codes = [issue.get("errorCode") for issue in quote_issues(response)]
    if codes:
        print("LIVE QUOTE ISSUE CODES " + ", ".join(str(code) for code in codes))


def create_quote(client: ProdigiLiveClient, api_key: str) -> dict[str, Any]:
    payload = quote_payload(load_validated_products())
    response = client.request("POST", "/quotes", payload)
    validate_quote(response)
    save_private_json(
        PRIVATE_WORK_DIR / "quote.json",
        {"apiBase": API_BASE, "quotedAt": utc_now(), "request": payload, "response": response},
        api_key,
    )
    print_quote(response)
    return response


def validate_saved_quote() -> None:
    path = PRIVATE_WORK_DIR / "quote.json"
    if not path.is_file():
        raise RuntimeError("A saved live quote is required before order creation")
    record = load_json(path)
    if record.get("apiBase") != API_BASE:
        raise RuntimeError("Saved quote is not from the Prodigi live API")
    expected_request = quote_payload(load_validated_products())
    if record.get("request") != expected_request:
        raise RuntimeError("Saved live quote request does not match this order")
    validate_quote(record.get("response"))


def make_order_payload(products: list[dict[str, Any]], recipient: dict[str, Any],
                       idempotency_key: str) -> dict[str, Any]:
    asset_paths = {sku: path for sku, _, path in PAPERS}
    return {
        "idempotencyKey": idempotency_key,
        "merchantReference": MERCHANT_REFERENCE,
        "shippingMethod": "Budget",
        "recipient": recipient,
        "items": [
            {
                "merchantReference": f"live-{product['sku'].lower()}",
                "sku": product["sku"],
                "copies": 1,
                "sizing": "fillPrintArea",
                "attributes": {},
                "assets": [
                    {
                        "printArea": "default",
                        "url": urllib.parse.urljoin(PAGES_BASE, asset_paths[product["sku"]]),
                    }
                ],
            }
            for product in products
        ],
        "metadata": {
            "purpose": "Physical 12-inch paper comparison using SKU-specific proof assets",
            "artworkId": "billion_31623_seed0_test_interp75",
            "environment": "Prodigi live",
            "assetHosting": "Public HTTPS via GitHub Pages",
            "assetsPerSku": True,
        },
    }


def load_or_create_payload(products: list[dict[str, Any]], recipient: dict[str, Any],
                           api_key: str) -> dict[str, Any]:
    path = PRIVATE_WORK_DIR / "order-payload.json"
    if path.is_file():
        payload = load_json(path)
        try:
            uuid.UUID(str(payload.get("idempotencyKey")))
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Existing live order payload has an invalid idempotency key") from None
        if payload != make_order_payload(products, recipient, payload["idempotencyKey"]):
            raise RuntimeError("Existing live order payload differs from the requested order")
        return payload
    payload = make_order_payload(products, recipient, str(uuid.uuid4()))
    save_private_json(path, payload, api_key)
    save_private_json(
        PRIVATE_WORK_DIR / "order-audit.json",
        {
            "apiBase": API_BASE,
            "createdAt": utc_now(),
            "idempotencyKey": payload["idempotencyKey"],
            "merchantReference": MERCHANT_REFERENCE,
            "shippingMethod": "Budget",
            "skus": [product["sku"] for product in products],
            "copiesPerSku": 1,
        },
        api_key,
    )
    return payload


def order_summary(response: dict[str, Any]) -> dict[str, Any]:
    order = response.get("order") if isinstance(response.get("order"), dict) else {}
    status = order.get("status") if isinstance(order.get("status"), dict) else {}
    items = order.get("items") if isinstance(order.get("items"), list) else []
    return {
        "outcome": response.get("outcome"),
        "orderId": order.get("id"),
        "status": status,
        "items": [
            {"id": item.get("id"), "sku": item.get("sku"), "status": item.get("status")}
            for item in items if isinstance(item, dict)
        ],
        "charges": order.get("charges") or [],
        "shipments": order.get("shipments") or [],
    }


def create_and_retrieve_order(client: ProdigiLiveClient, api_key: str) -> None:
    validate_saved_quote()
    recipient = load_recipient()
    products = load_validated_products()
    validate_public_assets()
    payload = load_or_create_payload(products, recipient, api_key)
    create_path = PRIVATE_WORK_DIR / "order-response.json"
    retrieved_path = PRIVATE_WORK_DIR / "order-retrieved.json"
    if create_path.exists() or retrieved_path.exists():
        raise RuntimeError("Live order response already exists; refusing a duplicate POST")

    created = client.request("POST", "/orders", payload)
    if not isinstance(created, dict):
        raise RuntimeError("Live order creation returned a non-object response")
    outcome = str(created.get("outcome", "")).lower()
    if outcome not in {"created", "createdwithissues", "onhold", "alreadyexists"}:
        raise RuntimeError(f"Live order creation outcome was not successful: {created.get('outcome')!r}")
    save_private_json(
        create_path,
        {"apiBase": API_BASE, "createdAt": utc_now(), "response": created},
        api_key,
    )
    order = created.get("order") if isinstance(created.get("order"), dict) else {}
    order_id = order.get("id")
    if not order_id:
        raise RuntimeError("Successful live order response did not include order.id")
    retrieved = client.request("GET", f"/orders/{urllib.parse.quote(str(order_id), safe='')}")
    if not isinstance(retrieved, dict):
        raise RuntimeError("Live order retrieval returned a non-object response")
    save_private_json(
        retrieved_path,
        {"apiBase": API_BASE, "retrievedAt": utc_now(), "response": retrieved},
        api_key,
    )
    created_summary = order_summary(created)
    retrieved_summary = order_summary(retrieved)
    print(f"LIVE ORDER CREATE OUTCOME {created_summary['outcome']}")
    print(f"LIVE ORDER RETRIEVE OUTCOME {retrieved_summary['outcome']}")
    print(f"LIVE ORDER ID {retrieved_summary['orderId']}")
    print("LIVE ORDER STATUS " + json.dumps(retrieved_summary["status"], separators=(",", ":")))
    print("LIVE ORDER ITEMS " + json.dumps(retrieved_summary["items"], separators=(",", ":")))
    print("LIVE ORDER CHARGES " + json.dumps(retrieved_summary["charges"], separators=(",", ":")))
    print("LIVE ORDER SHIPMENTS " + json.dumps(retrieved_summary["shipments"], separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("preflight", "quote", "order"))
    args = parser.parse_args()
    api_key = ""
    recipient: dict[str, Any] = {}
    try:
        recipient = load_recipient()
        api_key = load_live_api_key()
        client = ProdigiLiveClient(api_key, private_strings(recipient))
        if args.stage == "preflight":
            validate_public_assets()
            validate_products(client, api_key)
            print("PRIVATE RECIPIENT VALIDATED complete US shipping address")
        elif args.stage == "quote":
            create_quote(client, api_key)
        else:
            create_and_retrieve_order(client, api_key)
        return 0
    except (ApiFailure, LiveApiViolation, RuntimeError, ValueError, OSError) as exc:
        safe = redact(str(exc), private_strings(recipient) | ({api_key} if api_key else set()))
        print(f"ERROR {safe}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
