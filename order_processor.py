"""
Live order creation logic (wraps Script 2 logic — financialStatus=PENDING).

Adds release-instock-orders.py-style:
- Payment terms detection from notes / PO / shipping text
- Default Net 30
- Net 30 / 45 / 60 / 90 / 120 template mapping
- Free freight marker detection
- Freight calculation as FREIGHT_RATE_PERCENT of subtotal
- UPS Ground / UPS / FedEx shipping title detection

Important:
- shippingLines are included directly in orderCreate.
- payment terms are attached after orderCreate via paymentTermsCreate(referenceId=order_id).
"""

import os
import re
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Tuple, List, Dict

from shopify_core import (
    shopify_graphql, parse_price, to_gid, _to_yyyy_mm_dd,
    is_assortment_parent, expand_assortment_children, find_variant_id_and_price,
    normalize_country, to_mailing_address, order_po_exists_in_shopify,
    create_or_find_customer, ensure_company, ensure_company_location,
    get_or_create_matching_contact, grant_ordering_permission, _fix_countries,
    norm_po, SHOP_CURRENCY
)


# =========================
# CONFIG
# =========================

FREIGHT_RATE_PERCENT = Decimal(os.getenv("FREIGHT_RATE_PERCENT", "12").strip())
DEFAULT_FREIGHT_TITLE = os.getenv("DEFAULT_FREIGHT_TITLE", "UPS Ground").strip() or "UPS Ground"

DEFAULT_PAYMENT_TERMS_TEMPLATE_ID = os.getenv(
    "DEFAULT_PAYMENT_TERMS_TEMPLATE_ID",
    "gid://shopify/PaymentTermsTemplate/4",
).strip()

PAYMENT_TEMPLATE_MAP: Dict[int, str] = {
    30: os.getenv("PAYMENT_TERMS_TEMPLATE_ID_NET30", "").strip() or DEFAULT_PAYMENT_TERMS_TEMPLATE_ID,
    45: os.getenv("PAYMENT_TERMS_TEMPLATE_ID_NET45", "").strip(),
    60: os.getenv("PAYMENT_TERMS_TEMPLATE_ID_NET60", "").strip(),
    90: os.getenv("PAYMENT_TERMS_TEMPLATE_ID_NET90", "").strip(),
    120: os.getenv("PAYMENT_TERMS_TEMPLATE_ID_NET120", "").strip(),
}


# =========================
# MONEY / BASIC HELPERS
# =========================

def _shop_currency() -> str:
    return os.getenv("SHOP_CURRENCY", SHOP_CURRENCY or "USD")


def _price_set(amount):
    currency = _shop_currency()
    return {
        "shopMoney": {
            "amount": float(round(amount, 2)),
            "currencyCode": currency,
        }
    }


def _money_str(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _parse_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _decimal_from_price(value) -> Optional[Decimal]:
    parsed = parse_price(value)
    if parsed is None:
        return None
    return _parse_decimal(parsed)


# =========================
# NOTE / TEXT DETECTION
# =========================

def _build_order_note_blob(order: dict) -> str:
    """
    Mirrors release-instock-orders.py build_note_blob(), adapted for imported/live orders.

    Uses all likely places where terms/freight notes may appear.
    """
    parts = [
        order.get("specialInstructions") or "",
        order.get("shippingMethod") or "",
        order.get("poNumber") or "",
    ]
    return "\n".join(str(p) for p in parts if p).strip()


def _normalize_terms_text(text: str) -> str:
    upper = (text or "").upper()
    upper = upper.replace("TERMS:", " ")
    upper = upper.replace("TERMS", " ")
    return re.sub(r"[^A-Z0-9]+", "", upper)


def _detect_net_terms_days(text: str) -> Optional[int]:
    """
    Detects Net 30/45/60/90/120 from flexible user-entered notes.

    Examples:
    - NET30
    - Net 30
    - N30
    - N-30
    - Terms: Net 60
    """
    if not text:
        return None

    normalized = _normalize_terms_text(text)

    checks = [
        (120, ["NET120", "N120"]),
        (90, ["NET90", "N90"]),
        (60, ["NET60", "N60"]),
        (45, ["NET45", "N45"]),
        (30, ["NET30", "N30"]),
    ]

    for days, tokens in checks:
        for token in tokens:
            if token in normalized:
                return days

    haystack = text.upper()
    patterns = {
        120: [r"\bNET[\s\-_\/:]*120\b", r"\bN[\s\-_\/:]*120\b"],
        90: [r"\bNET[\s\-_\/:]*90\b", r"\bN[\s\-_\/:]*90\b"],
        60: [r"\bNET[\s\-_\/:]*60\b", r"\bN[\s\-_\/:]*60\b"],
        45: [r"\bNET[\s\-_\/:]*45\b", r"\bN[\s\-_\/:]*45\b"],
        30: [r"\bNET[\s\-_\/:]*30\b", r"\bN[\s\-_\/:]*30\b"],
    }

    for days, regexes in patterns.items():
        for pattern in regexes:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                return days

    return None


def _valid_free_freight_marker_present(text: str) -> bool:
    """
    Mirrors release-instock-orders.py free freight markers.

    Treats account-shipping instructions as free freight because freight should not
    be added to the order when the customer provides their shipping account.
    """
    if not text:
        return False

    patterns = [
        r"\bFF\b",
        r"\bFFA\b",
        r"\bF\s*/\s*F\b",
        r"\bFREE\s+FREIGHT\b",
        r"\bFREIGHT\s+FREE\b",
        r"\bFEDEXA\b",
        r"\bSHIP\s+(?:FED\s*EX|FEDEX|UPS|DHL|USPS)\s+\w+(?:\s+\w+)?\s+ACCOUNT\s+\d+",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _detect_freight_title(text: str) -> str:
    """
    Mirrors release-instock-orders.py carrier title logic.

    Priority:
    - UPS Ground exact marker => UPS Ground
    - UPS marker => UPS
    - FedEx / Fed Ex marker => FedEx
    - fallback => DEFAULT_FREIGHT_TITLE
    """
    if not text:
        return DEFAULT_FREIGHT_TITLE

    if re.search(r"\bUPS\s+GROUND\b", text, flags=re.IGNORECASE):
        return "UPS Ground"
    if re.search(r"\b(?:SHIP\s+)?UPS\b", text, flags=re.IGNORECASE):
        return "UPS"
    if re.search(r"\b(?:SHIP\s+)?FED\s*EX\b", text, flags=re.IGNORECASE):
        return "FedEx"
    if re.search(r"\b(?:SHIP\s+)?FEDEX\b", text, flags=re.IGNORECASE):
        return "FedEx"

    return DEFAULT_FREIGHT_TITLE


# =========================
# SHIPPING / FREIGHT
# =========================

def _build_shipping_lines(order: dict, subtotal: Optional[Decimal]) -> Tuple[List[dict], str, str, str]:
    """
    Returns:
      (shipping_lines, freight_action, freight_title, freight_price)

    freight_action:
      - free-freight
      - charge-freight
      - no-subtotal-error

    For free freight/account-shipping markers:
      returns no shippingLines so Shopify does not add freight.

    Otherwise:
      adds one shipping line calculated as FREIGHT_RATE_PERCENT of subtotal.
    """
    blob = _build_order_note_blob(order)

    if _valid_free_freight_marker_present(blob):
        return [], "free-freight", "", ""

    if subtotal is None:
        return [], "no-subtotal-error", "", ""

    freight_title = _detect_freight_title(blob)
    freight_amount = (subtotal * FREIGHT_RATE_PERCENT / Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    freight_price = _money_str(freight_amount)

    shipping_lines = [
        {
            "title": freight_title,
            "priceSet": {
                "shopMoney": {
                    "amount": float(freight_amount),
                    "currencyCode": _shop_currency(),
                }
            },
        }
    ]

    return shipping_lines, "charge-freight", freight_title, freight_price


# =========================
# PAYMENT TERMS
# =========================

def _build_issued_at(now_dt: datetime) -> str:
    return now_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_due_at(days_from_today: int, today) -> str:
    due_date = today + timedelta(days=days_from_today)
    due_at = datetime.combine(due_date, dt_time(0, 0, 0), tzinfo=timezone.utc)
    return due_at.isoformat().replace("+00:00", "Z")


def _build_payment_terms_attributes(
    order: dict,
    now_dt: Optional[datetime] = None,
) -> Tuple[dict, str, int]:
    """
    Returns:
      (paymentTermsAttributes, detected_label, detected_days)

    Defaults to Net 30 when nothing is detected.
    """
    now_dt = now_dt or datetime.now(timezone.utc)
    blob = _build_order_note_blob(order)

    detected_days = _detect_net_terms_days(blob)

    if detected_days:
        detected_label = f"Net {detected_days}"
    else:
        detected_days = 30
        detected_label = "Net 30 (defaulted)"

    template_id = (PAYMENT_TEMPLATE_MAP.get(detected_days) or "").strip()

    if not template_id:
        detected_days = 30
        detected_label = "Net 30 (defaulted)"
        template_id = DEFAULT_PAYMENT_TERMS_TEMPLATE_ID

    attrs = {
        "paymentTermsTemplateId": template_id,
    }

    # Same fixed/event-safe approach from release-instock-orders.py:
    # Net 120 uses dueAt. Other terms use issuedAt with fallback below.
    if detected_days == 120:
        attrs["paymentSchedules"] = [
            {
                "dueAt": _build_due_at(120, now_dt.date()),
            }
        ]
    else:
        attrs["paymentSchedules"] = [
            {
                "issuedAt": _build_issued_at(now_dt),
            }
        ]

    return attrs, detected_label, detected_days


def _is_issue_date_fixed_terms_error(exc: Exception) -> bool:
    return "issue date cannot be set with event or fixed payment terms" in str(exc).lower()


def _payment_terms_create(order_id: str, payment_terms_attributes: dict) -> dict:
    """
    Attach payment terms to a newly created live order.

    Requires Shopify scope:
      write_payment_terms
    """
    m = """
    mutation($referenceId: ID!, $paymentTermsAttributes: PaymentTermsCreateInput!) {
      paymentTermsCreate(
        referenceId: $referenceId,
        paymentTermsAttributes: $paymentTermsAttributes
      ) {
        paymentTerms {
          id
          dueInDays
          translatedName
          paymentTermsName
        }
        userErrors {
          field
          message
        }
      }
    }
    """

    out = shopify_graphql(
        m,
        {
            "referenceId": order_id,
            "paymentTermsAttributes": payment_terms_attributes,
        },
    )

    payload = (out.get("data", {}) or {}).get("paymentTermsCreate", {}) or {}
    errs = payload.get("userErrors", []) or []

    if errs:
        raise RuntimeError(f"paymentTermsCreate failed for {order_id}: {errs}")

    return payload.get("paymentTerms") or {}


def _attach_payment_terms_to_order(order_id: str, order: dict, subtotal: Optional[Decimal]) -> dict:
    """
    For $0.00 orders, skip payment terms, mirroring the free-order handling
    from release-instock-orders.py.

    For non-free orders, attach detected/defaulted payment terms.
    """
    if subtotal is not None and subtotal == Decimal("0.00"):
        return {}

    attrs, detected_label, detected_days = _build_payment_terms_attributes(order)

    payloads = []

    # First attempt: full payload with schedule.
    payloads.append((attrs, f"{detected_label} with schedule"))

    # Fallback: template only, matching release-instock-orders.py fallback behavior.
    template_only = {
        "paymentTermsTemplateId": attrs["paymentTermsTemplateId"],
    }
    payloads.append((template_only, f"{detected_label} template-only fallback"))

    last_exc = None

    for payload, description in payloads:
        try:
            return _payment_terms_create(order_id, payload)
        except Exception as exc:
            last_exc = exc

            # In the draft script this specific error falls through to fallback.
            if _is_issue_date_fixed_terms_error(exc):
                continue

            # If the first payload failed for another reason, still allow
            # template-only fallback once before raising.
            if payload is not template_only:
                continue

            raise

    if last_exc:
        raise last_exc

    return {}


# =========================
# ORDER CREATE
# =========================

def _try_order_create(order_input, options, note):
    m = """
    mutation($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
      orderCreate(order: $order, options: $options) {
        order {
          id
          name
          poNumber
          displayFinancialStatus
          paymentTerms {
            id
            dueInDays
            translatedName
            paymentTermsName
          }
          shippingLines(first: 5) {
            nodes {
              title
              discountedPriceSet {
                shopMoney {
                  amount
                  currencyCode
                }
              }
            }
          }
        }
        userErrors {
          field
          message
        }
      }
    }
    """

    out = shopify_graphql(m, {"order": order_input, "options": options or {}})
    payload = (out.get("data", {}) or {}).get("orderCreate", {}) or {}
    errs = payload.get("userErrors", []) or []
    created = payload.get("order") or {}
    order_id = created.get("id")
    dfs = created.get("displayFinancialStatus")

    return order_id, dfs, errs, out


def create_live_order(order, customer_id, company_id, company_contact_id, company_location_id):
    note_parts = []

    if order.get("poNumber"):
        note_parts.append(f"PO: {order['poNumber']}")

    if order.get("specialInstructions"):
        note_parts.append(str(order["specialInstructions"]))

    if order.get("shippingMethod"):
        note_parts.append(f"Shipping: {order['shippingMethod']}")

    line_items = []
    subtotal_for_freight = Decimal("0.00")
    subtotal_known = True

    for item in (order.get("details") or []):
        sku = (item.get("itemNumber") or "").strip()
        qty = int(item.get("quantity") or 0)
        parsed_price = parse_price(item.get("unitPrice"))

        if qty <= 0:
            continue

        if is_assortment_parent(sku):
            for child_sku, child_qty, fallback_price in expand_assortment_children(sku, qty):
                child_qty = int(child_qty)
                variant_id, shop_price = find_variant_id_and_price(child_sku)
                parsed_child_price = parse_price(fallback_price)

                if variant_id:
                    li = {
                        "variantId": variant_id,
                        "quantity": child_qty,
                    }

                    effective = parsed_child_price if parsed_child_price is not None else shop_price

                    if effective is not None:
                        subtotal_for_freight += Decimal(str(effective)) * Decimal(child_qty)

                        if shop_price is None or abs(effective - shop_price) > 1e-9:
                            li["priceSet"] = _price_set(float(effective))
                    else:
                        subtotal_known = False

                    line_items.append(li)

                else:
                    effective = parsed_child_price if parsed_child_price is not None else 0.01
                    subtotal_for_freight += Decimal(str(effective)) * Decimal(child_qty)

                    line_items.append({
                        "title": child_sku,
                        "sku": child_sku,
                        "quantity": child_qty,
                        "priceSet": _price_set(float(effective)),
                    })

            continue

        variant_id, shop_price = find_variant_id_and_price(sku)

        if variant_id:
            li = {
                "variantId": variant_id,
                "quantity": qty,
            }

            effective = parsed_price if parsed_price is not None else shop_price

            if effective is not None:
                subtotal_for_freight += Decimal(str(effective)) * Decimal(qty)

                if shop_price is None or abs(effective - shop_price) > 1e-9:
                    li["priceSet"] = _price_set(float(effective))
            else:
                subtotal_known = False

            line_items.append(li)

        else:
            effective = parsed_price if parsed_price is not None else 0.01
            subtotal_for_freight += Decimal(str(effective)) * Decimal(qty)

            line_items.append({
                "title": item.get("name") or (sku or "Item"),
                "sku": sku or None,
                "quantity": qty,
                "priceSet": _price_set(float(effective)),
            })

    if not line_items:
        raise RuntimeError("No valid line items found for order")

    ship_date_val = _to_yyyy_mm_dd(order.get("shipDate"))
    bill_to_email_val = (order.get("billToEmail") or "").strip() or None
    po_num_val = (order.get("poNumber") or "").strip() or None

    metafields = []

    if ship_date_val:
        metafields.append({
            "namespace": "b2b",
            "key": "ship_date",
            "value": ship_date_val,
        })

    if bill_to_email_val:
        metafields.append({
            "namespace": "b2b",
            "key": "bill_to_email",
            "value": bill_to_email_val,
        })

    if po_num_val:
        metafields.append({
            "namespace": "b2b",
            "key": "po_number",
            "value": po_num_val,
        })

    order_input = {
        "lineItems": line_items,
        "note": " | ".join([p for p in note_parts if p]),
        "tags": ["excel-import"],
        "billingAddress": to_mailing_address(order, "billing"),
        "shippingAddress": to_mailing_address(order, "shipping"),
        "poNumber": order.get("poNumber"),
        "email": order.get("shipToEmail") or order.get("billToEmail") or None,
        "financialStatus": "PENDING",
    }

    subtotal_for_terms_and_freight = subtotal_for_freight if subtotal_known else None

    shipping_lines, freight_action, freight_title, freight_price = _build_shipping_lines(
        order,
        subtotal_for_terms_and_freight,
    )

    if freight_action == "no-subtotal-error":
        raise RuntimeError("Could not determine order subtotal for freight calculation")

    if shipping_lines:
        order_input["shippingLines"] = shipping_lines

    if metafields:
        order_input["metafields"] = metafields

    if customer_id:
        order_input["customer"] = {
            "toAssociate": {
                "id": to_gid("Customer", customer_id),
            }
        }

    if company_location_id:
        order_input["companyLocationId"] = company_location_id

    options = {
        "sendReceipt": False,
        "sendFulfillmentReceipt": False,
    }

    # Attempt 1: full payload
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "full")
    if order_id:
        _attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs

    # Attempt 2: remove companyLocationId fallback
    saved_loc = order_input.pop("companyLocationId", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-companyLocationId")
    if order_id:
        _attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs

    if saved_loc:
        order_input["companyLocationId"] = saved_loc

    # Attempt 3: remove metafields fallback
    saved_mf = order_input.pop("metafields", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-metafields")
    if order_id:
        _attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs

    if saved_mf:
        order_input["metafields"] = saved_mf

    # Attempt 4: remove shippingLines fallback, but only if shipping was present.
    # This avoids freight blocking the entire order. If this path succeeds,
    # the order should be reviewed because freight was not applied.
    saved_shipping_lines = order_input.pop("shippingLines", None)
    if saved_shipping_lines:
        order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-shippingLines")
        if order_id:
            _attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
            return order_id, dfs

        order_input["shippingLines"] = saved_shipping_lines

    raise RuntimeError(f"orderCreate failed. Last errors: {errs}")


# =========================
# BATCH PROCESSING
# =========================

def process_live_orders(orders, progress_callback=None, cancel_event=None):
    """
    Process a list of orders as live orders (financialStatus=PENDING).

    cancel_event: threading.Event — if set, processing stops before the next order.

    Returns list of result dicts.
    """
    results = []
    seen_pos = set()

    for order in orders:
        # Cancellation check — stops before starting the next order
        if cancel_event and cancel_event.is_set():
            if progress_callback:
                progress_callback("—", "cancelled", "Job cancelled — remaining orders skipped")
            break

        po_number = order.get("poNumber")
        po_norm = norm_po(po_number)
        billToName = order.get("billToName")
        shipToName = order.get("shipToName") or billToName or "Default"

        _fix_countries(order)

        if po_norm in seen_pos:
            results.append({
                "po": po_number,
                "status": "skipped",
                "reason": "Duplicate in file",
                "id": None,
            })
            continue

        if progress_callback:
            progress_callback(po_number, "processing", f"Processing PO {po_number}...")

        if po_number and order_po_exists_in_shopify(po_number):
            seen_pos.add(po_norm)
            results.append({
                "po": po_number,
                "status": "skipped",
                "reason": "Already exists in Shopify",
                "id": None,
            })

            if progress_callback:
                progress_callback(po_number, "skipped", "Already exists in Shopify")

            continue

        try:
            customer_id, created = create_or_find_customer(order, po_number)
        except Exception as e:
            results.append({
                "po": po_number,
                "status": "error",
                "reason": str(e),
                "id": None,
            })

            if progress_callback:
                progress_callback(po_number, "error", str(e))

            seen_pos.add(po_norm)
            continue

        company_id = ensure_company(billToName) if billToName else None
        company_location_id = None
        company_contact_id = None

        if company_id:
            company_location_id = ensure_company_location(company_id, shipToName, order)

            if customer_id and company_location_id:
                company_contact_id = get_or_create_matching_contact(company_id, customer_id)

                if company_contact_id:
                    grant_ordering_permission(
                        company_contact_id,
                        company_location_id,
                        company_id,
                    )

        try:
            order_id, dfs = create_live_order(
                order,
                customer_id,
                company_id,
                company_contact_id,
                company_location_id,
            )

            results.append({
                "po": po_number,
                "status": "created",
                "reason": f"Order created ({dfs or 'PENDING'})",
                "id": order_id,
                "company": billToName,
                "line_count": len(order.get("details", [])),
                "financial_status": dfs,
            })

            if progress_callback:
                progress_callback(po_number, "created", f"Order created: {order_id}")

        except Exception as e:
            results.append({
                "po": po_number,
                "status": "error",
                "reason": str(e),
                "id": None,
            })

            if progress_callback:
                progress_callback(po_number, "error", str(e))

        seen_pos.add(po_norm)
        time.sleep(0.15)

    return results
