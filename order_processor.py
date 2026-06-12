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
- DRY_RUN=false creates a live Shopify order.
- DRY_RUN=true creates a Shopify draft order instead, with payment terms and freight applied.
- Live order payment terms are attached after orderCreate via paymentTermsCreate(referenceId=order_id).
- If live order payment terms fail after order creation, the order is still treated as created.
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

DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

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


# =========================
# NOTE / TEXT DETECTION
# =========================

def _build_order_note_blob(order: dict) -> str:
    """
    Uses likely places where terms/freight notes may appear.
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


def _draft_shipping_line_from_shipping_lines(shipping_lines: List[dict]) -> Optional[dict]:
    """
    Convert live orderCreate shippingLines format to draftOrderCreate shippingLine format.

    Uses priceWithCurrency because that matches the draft update style already used
    in release-instock-orders.py.
    """
    if not shipping_lines:
        return None

    first = shipping_lines[0] or {}
    title = first.get("title") or DEFAULT_FREIGHT_TITLE
    price_set = first.get("priceSet") or {}
    shop_money = price_set.get("shopMoney") or {}
    amount = shop_money.get("amount")
    currency_code = shop_money.get("currencyCode") or _shop_currency()

    if amount is None:
        return None

    return {
        "title": title,
        "priceWithCurrency": {
            "amount": str(amount),
            "currencyCode": currency_code,
        },
    }


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

    # Net 120 uses the Fixed template plus dueAt 120 days out.
    # Normal Net terms use issuedAt.
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
    For $0.00 orders, skip payment terms.

    For non-free orders, attach detected/defaulted payment terms.
    """
    if subtotal is not None and subtotal == Decimal("0.00"):
        return {}

    attrs, detected_label, detected_days = _build_payment_terms_attributes(order)

    payloads = []

    # First attempt: full payload with schedule.
    payloads.append((attrs, f"{detected_label} with schedule"))

    # Fallback: template only.
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

            if _is_issue_date_fixed_terms_error(exc):
                continue

            if payload is not template_only:
                continue

            raise

    if last_exc:
        raise last_exc

    return {}


def _safe_attach_payment_terms_to_order(order_id: str, order: dict, subtotal: Optional[Decimal]) -> None:
    """
    Payment terms are applied after live order creation.

    If this fails, do not make the entire order upload look failed, because the
    Shopify order already exists.
    """
    try:
        _attach_payment_terms_to_order(order_id, order, subtotal)
    except Exception as terms_exc:
        print(f"WARNING: Order {order_id} created, but payment terms failed: {terms_exc}")


# =========================
# ORDER CREATE / DRAFT CREATE
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


def _draft_line_items_from_order_line_items(order_line_items: List[dict]) -> List[dict]:
    """
    Convert OrderCreateOrderLineItemInput-style line items to DraftOrderLineItemInput.

    Variant-backed lines keep variantId + quantity.
    Custom lines use title / sku / originalUnitPrice.
    """
    draft_items = []

    for li in order_line_items:
        qty = int(li.get("quantity") or 0)
        if qty <= 0:
            continue

        draft_li = {
            "quantity": qty,
        }

        price_set = li.get("priceSet") or {}
        shop_money = price_set.get("shopMoney") or {}
        amount = shop_money.get("amount")

        if li.get("variantId"):
            draft_li["variantId"] = li["variantId"]

            # Preserve overridden price, if there was one.
            if amount is not None:
                draft_li["originalUnitPrice"] = float(amount)

        else:
            draft_li["title"] = li.get("title") or li.get("sku") or "Item"

            if li.get("sku"):
                draft_li["sku"] = li.get("sku")

            draft_li["originalUnitPrice"] = float(amount if amount is not None else 0.01)

        draft_items.append(draft_li)

    return draft_items


def _try_draft_order_create(
    *,
    order: dict,
    order_input: dict,
    line_items: List[dict],
    shipping_lines: List[dict],
    subtotal: Optional[Decimal],
    customer_id,
    company_location_id,
) -> Tuple[Optional[str], Optional[str], List[dict], dict]:
    """
    DRY_RUN=true mode:
    Create a Shopify draft order instead of a live order.

    This lets us inspect payment terms + shipping/freight safely before creating real orders.
    """
    draft_line_items = _draft_line_items_from_order_line_items(line_items)
    shipping_line = _draft_shipping_line_from_shipping_lines(shipping_lines)

    draft_input = {
        "lineItems": draft_line_items,
        "note": order_input.get("note") or "",
        "tags": ["excel-import", "dry-run-draft"],
        "billingAddress": order_input.get("billingAddress"),
        "shippingAddress": order_input.get("shippingAddress"),
        "poNumber": order_input.get("poNumber"),
        "email": order_input.get("email"),
        "visibleToCustomer": False,
        "useCustomerDefaultAddress": False,
    }

    if shipping_line:
        draft_input["shippingLine"] = shipping_line

    if order_input.get("metafields"):
        draft_input["metafields"] = order_input["metafields"]

    if subtotal is None or subtotal != Decimal("0.00"):
        payment_attrs, detected_label, detected_days = _build_payment_terms_attributes(order)
        draft_input["paymentTerms"] = payment_attrs
    else:
        detected_label = "Skipped - $0.00 order"

    # For draft orders, try B2B company location first.
    # If Shopify rejects this shape, create_live_order retries as customer-only.
    if company_location_id:
        draft_input["purchasingEntity"] = {
            "purchasingCompany": {
                "companyLocationId": company_location_id,
            }
        }
    elif customer_id:
        draft_input["purchasingEntity"] = {
            "customerId": to_gid("Customer", customer_id),
        }

    m = """
    mutation($input: DraftOrderInput!) {
      draftOrderCreate(input: $input) {
        draftOrder {
          id
          name
          status
          poNumber
          paymentTerms {
            id
            dueInDays
            translatedName
            paymentTermsName
          }
          shippingLine {
            id
            title
            custom
            discountedPriceSet {
              shopMoney {
                amount
                currencyCode
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

    out = shopify_graphql(m, {"input": draft_input})
    payload = (out.get("data", {}) or {}).get("draftOrderCreate", {}) or {}
    errs = payload.get("userErrors", []) or []
    draft = payload.get("draftOrder") or {}

    if errs:
        print("====== DRY RUN DRAFT CREATE FAILED ======")
        print(f"PO: {order.get('poNumber')}")
        print(f"Errors: {errs}")
        print(f"Draft input: {draft_input}")
        print("========================================")
        return None, None, errs, out

    draft_id = draft.get("id")
    draft_name = draft.get("name")

    print("====== DRY RUN DRAFT CREATED ======")
    print(f"PO: {order.get('poNumber')}")
    print(f"Draft: {draft_name} / {draft_id}")
    print(f"Detected payment terms: {detected_label}")
    print(f"Shipping line: {shipping_line}")
    print("===================================")

    return draft_id, draft_name, [], out


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

    # DRY_RUN=true creates a draft order instead of a live order.
    if DRY_RUN:
        draft_id, draft_name, draft_errs, _ = _try_draft_order_create(
            order=order,
            order_input=order_input,
            line_items=line_items,
            shipping_lines=shipping_lines,
            subtotal=subtotal_for_terms_and_freight,
            customer_id=customer_id,
            company_location_id=company_location_id,
        )

        if draft_id:
            return draft_id, f"DRY_RUN_DRAFT {draft_name or ''}".strip()

        # Fallback: if B2B purchasingEntity caused a draft error, retry as customer only.
        if company_location_id and customer_id:
            draft_id, draft_name, draft_errs, _ = _try_draft_order_create(
                order=order,
                order_input=order_input,
                line_items=line_items,
                shipping_lines=shipping_lines,
                subtotal=subtotal_for_terms_and_freight,
                customer_id=customer_id,
                company_location_id=None,
            )

            if draft_id:
                return draft_id, f"DRY_RUN_DRAFT {draft_name or ''}".strip()

        raise RuntimeError(f"DRY_RUN draftOrderCreate failed. Errors: {draft_errs}")

    # Attempt 1: full payload
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "full")
    if order_id:
        _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs

    # Attempt 2: remove companyLocationId fallback
    saved_loc = order_input.pop("companyLocationId", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-companyLocationId")
    if order_id:
        _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs

    if saved_loc:
        order_input["companyLocationId"] = saved_loc

    # Attempt 3: remove metafields fallback
    saved_mf = order_input.pop("metafields", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-metafields")
    if order_id:
        _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
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
            _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
            return order_id, dfs

        order_input["shippingLines"] = saved_shipping_lines

    raise RuntimeError(f"orderCreate failed. Last errors: {errs}")


# =========================
# BATCH PROCESSING
# =========================

def process_live_orders(orders, progress_callback=None, cancel_event=None):
    """
    Process a list of orders as live orders.

    DRY_RUN=false:
      Creates live Shopify orders with financialStatus=PENDING.

    DRY_RUN=true:
      Creates Shopify draft orders with freight and payment terms applied.

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
            action_word = "Creating draft for" if DRY_RUN else "Processing"
            progress_callback(po_number, "processing", f"{action_word} PO {po_number}...")

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

            created_label = "Draft created" if DRY_RUN else "Order created"

            results.append({
                "po": po_number,
                "status": "created",
                "reason": f"{created_label} ({dfs or 'PENDING'})",
                "id": order_id,
                "company": billToName,
                "line_count": len(order.get("details", [])),
                "financial_status": dfs,
            })

            if progress_callback:
                progress_callback(po_number, "created", f"{created_label}: {order_id}")

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
