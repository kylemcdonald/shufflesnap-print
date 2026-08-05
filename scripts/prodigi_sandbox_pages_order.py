#!/usr/bin/env python3
"""Quote and create the private-address, GitHub-Pages Prodigi sandbox test.

No credential or address value is printed. The order records are written under
the gitignored work/ directory. All Prodigi traffic uses the sandbox client from
prodigi_sandbox_test.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import prodigi_sandbox_test as base


PROJECT_DIR = Path(__file__).resolve().parents[1]
PRIVATE_WORK_DIR = PROJECT_DIR / "work/prodigi-sandbox-pages"
PAGES_BASE = "https://kylemcdonald.github.io/megalap-print/"
MERCHANT_REFERENCE = "billion-12x12-paper-test-sandbox-pages"

ASSETS = {
    "GLOBAL-HPR-12X12": "outputs/prodigi/billion_12x12_global-hpr-12x12_300ppi.png",
    "GLOBAL-FAP-12X12": "outputs/prodigi/billion_12x12_global-fap-12x12_300ppi.png",
    "ART-FAP-BAP-12X12": "outputs/prodigi/billion_12x12_art-fap-bap-12x12_300ppi.png",
}


def env_assignments() -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw_line in base.ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().upper()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and value:
            assignments[name] = value
    return assignments


def select_field(assignments: dict[str, str], semantic_name: str,
                 aliases: tuple[str, ...], *, required: bool = True) -> str | None:
    exact = [assignments[alias] for alias in aliases if assignments.get(alias)]
    if not exact:
        suffix = [
            value
            for name, value in assignments.items()
            if any(name.endswith(f"_{alias}") for alias in aliases)
            and not any(token in name for token in ("API_KEY", "SANDBOX", "LIVE"))
        ]
        exact = suffix
    values = list(dict.fromkeys(exact))
    if len(values) == 1:
        return values[0]
    if not values and not required:
        return None
    state = "missing" if not values else "ambiguous"
    raise RuntimeError(f"Recipient field {semantic_name} is {state} in .env")


def normalize_country(value: str) -> str:
    normalized = value.strip().upper().replace(".", "")
    if normalized in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        return "US"
    return normalized


def load_recipient() -> dict[str, Any]:
    assignments = env_assignments()
    name = select_field(assignments, "name", ("RECIPIENT_NAME", "SHIPPING_NAME", "FULL_NAME", "NAME"))
    line1 = select_field(
        assignments,
        "address line 1",
        (
            "ADDRESS_LINE1", "ADDRESS_LINE_1", "ADDRESS1", "ADDRESS_1",
            "STREET_ADDRESS", "ADDRESS", "LINE1", "LINE_1", "NUMBER",
        ),
    )
    line2 = select_field(
        assignments,
        "address line 2",
        ("ADDRESS_LINE2", "ADDRESS_LINE_2", "ADDRESS2", "ADDRESS_2", "LINE2", "LINE_2"),
        required=False,
    )
    city = select_field(assignments, "city", ("TOWN_OR_CITY", "CITY", "TOWN"))
    state = select_field(assignments, "state", ("STATE_OR_COUNTY", "STATE", "REGION", "PROVINCE"))
    postal = select_field(
        assignments,
        "postal code",
        ("POSTAL_OR_ZIP_CODE", "POSTAL_CODE", "POSTCODE", "ZIP_CODE", "ZIP"),
    )
    country_value = select_field(assignments, "country", ("COUNTRY_CODE", "COUNTRY"))
    country = normalize_country(str(country_value))
    if country != "US":
        raise RuntimeError("The saved recipient country is not US; refusing to change the requested destination")
    address: dict[str, str] = {
        "line1": str(line1),
        "townOrCity": str(city),
        "stateOrCounty": str(state),
        "postalOrZipCode": str(postal),
        "countryCode": country,
    }
    if line2:
        address["line2"] = line2
    return {"name": str(name), "address": address}


def save_private_json(path: Path, value: Any, api_key: str) -> None:
    base.assert_sanitized(value, api_key)
    PRIVATE_WORK_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validated_products() -> list[dict[str, Any]]:
    products = base.load_validated_products()
    if [product["sku"] for product in products] != list(ASSETS):
        raise RuntimeError("Validated product order does not match the Pages asset mapping")
    return products


def validate_public_assets() -> None:
    for sku, relative_path in ASSETS.items():
        url = urllib.parse.urljoin(PAGES_BASE, relative_path)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "kylemcdonald.github.io":
            raise RuntimeError(f"Blocked unexpected asset host for {sku}")
        request = urllib.request.Request(url, headers={"User-Agent": "megalap-prodigi-asset-check/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            remote = response.read()
            content_type = response.headers.get("Content-Type", "")
            status = response.status
        local = (PROJECT_DIR / relative_path).read_bytes()
        if status != 200 or not content_type.lower().startswith("image/png"):
            raise RuntimeError(f"Public asset for {sku} is not a reachable PNG")
        if hashlib.sha256(remote).digest() != hashlib.sha256(local).digest():
            raise RuntimeError(f"Public asset bytes do not match local asset for {sku}")


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


def create_quote(client: base.ProdigiSandboxClient, api_key: str) -> dict[str, Any]:
    payload = quote_payload(validated_products())
    response = client.request("POST", "/quotes", payload)
    if not isinstance(response, dict):
        raise RuntimeError("Quote returned a non-object response")
    if str(response.get("outcome", "")).lower() not in {"created", "createdwithissues"}:
        raise RuntimeError(f"Quote outcome was not successful: {response.get('outcome')!r}")
    quotes = response.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise RuntimeError("Expected exactly one quote")
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
    issues = response.get("issues") or quotes[0].get("issues") or []
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


def make_order_payload(products: list[dict[str, Any]], recipient: dict[str, Any],
                       idempotency_key: str) -> dict[str, Any]:
    return {
        "idempotencyKey": idempotency_key,
        "merchantReference": MERCHANT_REFERENCE,
        "shippingMethod": "Budget",
        "recipient": recipient,
        "items": [
            {
                "merchantReference": f"pages-{product['sku'].lower()}",
                "sku": product["sku"],
                "copies": 1,
                "sizing": "fillPrintArea",
                "attributes": {},
                "assets": [
                    {
                        "printArea": "default",
                        "url": urllib.parse.urljoin(PAGES_BASE, ASSETS[product["sku"]]),
                    }
                ],
            }
            for product in products
        ],
        "metadata": {
            "purpose": "Validates a 12-inch paper comparison using SKU-specific proof assets",
            "artworkId": "billion_31623_seed0_test_interp75",
            "environment": "Prodigi sandbox",
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
            raise RuntimeError("Existing Pages order payload has an invalid idempotency key") from None
        expected = make_order_payload(products, recipient, payload["idempotencyKey"])
        if payload != expected:
            raise RuntimeError("Existing persistent Pages order payload differs from the requested order")
        return payload
    payload = make_order_payload(products, recipient, str(uuid.uuid4()))
    save_private_json(path, payload, api_key)
    return payload


def create_order(client: base.ProdigiSandboxClient, api_key: str) -> dict[str, Any]:
    if not quote_is_successful():
        raise RuntimeError("A successful fresh Pages quote is required")
    recipient = load_recipient()
    products = validated_products()
    validate_public_assets()
    payload = load_or_create_payload(products, recipient, api_key)
    create_path = PRIVATE_WORK_DIR / "order-response.json"
    retrieved_path = PRIVATE_WORK_DIR / "order-retrieved.json"
    if create_path.exists() or retrieved_path.exists():
        raise RuntimeError("Pages order records already exist; refusing another POST or retrieval")

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
        retrieved_path,
        {"apiBase": base.API_BASE, "retrievedAt": base.utc_now(), "response": retrieved},
        api_key,
    )
    base.print_order_summary(created, retrieved)
    return retrieved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("address", "quote", "order"))
    args = parser.parse_args()
    try:
        if args.stage == "address":
            recipient = load_recipient()
            required = {"line1", "townOrCity", "stateOrCounty", "postalOrZipCode", "countryCode"}
            if not recipient.get("name") or not required.issubset(recipient.get("address", {})):
                raise RuntimeError("Recipient validation failed")
            print("RECIPIENT VALIDATED required name and address fields present; destination US")
            return 0
        api_key = base.load_api_key()
        client = base.ProdigiSandboxClient(api_key)
        if args.stage == "quote":
            create_quote(client, api_key)
        else:
            create_order(client, api_key)
        return 0
    except (base.ApiFailure, base.SandboxViolation, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
