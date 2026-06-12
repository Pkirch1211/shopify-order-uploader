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

DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

print("====== ORDER_PROCESSOR VERSION: POST_CREATE_DRAFT_UPDATE_V2_FREE_FREIGHT_ZERO_SHIP_LOADED ======")
print(f"====== DRY_RUN={DRY_RUN} ======")

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

DRAFT_RECHECK_QUERY = """
query RecheckDraft($id: ID!) {
  draftOrder(id: $id) {
    id
    name
    status
    note2
    poNumber
    currencyCode
    subtotalPriceSet {
      shopMoney {
        amount
        currencyCode
      }
    }
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
}
"""

DRAFT_UPDATE_MUTATION = """
mutation UpdateDraftOrder($id: ID!, $input: DraftOrderInput!) {
  draftOrderUpdate(id: $id, input: $input) {
    draftOrder {
      id
      name
      tags
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
      order {
        id
        name
        tags
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""


def _data(out: dict) -> dict:
    return (out.get("data") if isinstance(out, dict) and "data" in out else out) or {}


def _shop_currency() -> str:
    return os.getenv("SHOP_CURRENCY", SHOP_CURRENCY or "USD")


def _price_set(amount):
    return {
        "shopMoney": {
            "amount": float(round(amount, 2)),
            "currencyCode": _shop_currency(),
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


def _build_order_note_blob(order: dict) -> str:
    candidate_keys = [
        "specialInstructions",
        "shippingMethod",
        "poNumber",
        "notes",
        "note",
        "note2",
        "orderNotes",
        "orderNote",
        "poNotes",
        "poNote",
        "customerNotes",
        "customerNote",
        "internalNotes",
        "internalNote",
        "comments",
        "comment",
        "memo",
        "terms",
        "paymentTerms",
        "paymentTerm",
        "shipVia",
        "shipViaDescription",
        "shippingInstructions",
        "freightInstructions",
        "routing",
        "carrier",
    ]

    parts = []

    for key in candidate_keys:
        value = order.get(key)
        if value is not None and str(value).strip():
            parts.append(f"{key}: {value}")

    for key, value in order.items():
        key_l = str(key).lower()
        if any(token in key_l for token in ["note", "term", "ship", "freight", "carrier", "routing"]):
            if value is not None and str(value).strip():
                entry = f"{key}: {value}"
                if entry not in parts:
                    parts.append(entry)

    blob = "\n".join(parts).strip()

    print("====== NOTE BLOB USED FOR TERMS/FREIGHT DETECTION ======")
    print(blob or "(empty)")
    print("=======================================================")

    return blob


def _build_draft_note(order: dict) -> str:
    blob = _build_order_note_blob(order)
    return blob or f"PO: {order.get('poNumber') or ''}".strip()


def _build_draft_style_note_blob(draft: dict) -> str:
    parts = [
        draft.get("note2") or "",
        draft.get("poNumber") or "",
    ]
    blob = "\n".join(parts).strip()

    print("====== DRAFT NOTE BLOB USED FOR POST-CREATE UPDATE ======")
    print(blob or "(empty)")
    print("========================================================")

    return blob


def _normalize_terms_text(text: str) -> str:
    upper = (text or "").upper()
    upper = upper.replace("TERMS:", " ")
    upper = upper.replace("TERMS", " ")
    return re.sub(r"[^A-Z0-9]+", "", upper)


def _detect_net_terms_days(text: str) -> Optional[int]:
    if not text:
        print("====== TERMS DETECTION DEBUG ======")
        print("No text supplied for terms detection")
        print("===================================")
        return None

    haystack = text.upper()
    normalized = _normalize_terms_text(text)

    print("====== TERMS DETECTION DEBUG ======")
    print(f"Raw text: {text}")
    print(f"Normalized text: {normalized}")

    regex_checks = [
        (120, [r"\bNET[\s\-_\/:]*120\b", r"\bN[\s\-_\/:]*120\b"]),
        (90, [r"\bNET[\s\-_\/:]*90\b", r"\bN[\s\-_\/:]*90\b"]),
        (60, [r"\bNET[\s\-_\/:]*60\b", r"\bN[\s\-_\/:]*60\b"]),
        (45, [r"\bNET[\s\-_\/:]*45\b", r"\bN[\s\-_\/:]*45\b"]),
        (30, [r"\bNET[\s\-_\/:]*30\b", r"\bN[\s\-_\/:]*30\b"]),
    ]

    for days, patterns in regex_checks:
        for pattern in patterns:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                print(f"Detected Net {days} by regex: {pattern}")
                print("===================================")
                return days

    token_checks = [
        (120, ["NET120", "N120"]),
        (90, ["NET90", "N90"]),
        (60, ["NET60", "N60"]),
        (45, ["NET45", "N45"]),
        (30, ["NET30", "N30"]),
    ]

    for days, tokens in token_checks:
        for token in tokens:
            if token in normalized:
                print(f"Detected Net {days} by normalized token: {token}")
                print("===================================")
                return days

    print("No Net terms detected")
    print("===================================")
    return None


def _valid_free_freight_marker_present(text: str) -> bool:
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


def _build_shipping_lines(order: dict, subtotal: Optional[Decimal]) -> Tuple[List[dict], str, str, str]:
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

    return [
        {
            "title": freight_title,
            "priceSet": {
                "shopMoney": {
                    "amount": float(freight_amount),
                    "currencyCode": _shop_currency(),
                }
            },
        }
    ], "charge-freight", freight_title, freight_price


def _draft_subtotal_amount(draft: dict) -> Optional[Decimal]:
    subtotal_set = draft.get("subtotalPriceSet") or {}
    shop_money = subtotal_set.get("shopMoney") or {}
    return _parse_decimal(shop_money.get("amount"))


def _current_shipping_price(draft: dict) -> str:
    shipping_line = draft.get("shippingLine") or {}
    discounted = shipping_line.get("discountedPriceSet") or {}
    shop_money = discounted.get("shopMoney") or {}
    return (shop_money.get("amount") or "").strip()


def _shipping_line_matches(draft: dict, expected_title: str, expected_price: str) -> bool:
    shipping_line = draft.get("shippingLine") or {}
    if not shipping_line:
        return False

    current_title = (shipping_line.get("title") or "").strip()
    current_price = _parse_decimal(_current_shipping_price(draft))
    desired_price = _parse_decimal(expected_price)

    if current_title != expected_title:
        return False
    if current_price is None or desired_price is None:
        return False

    return current_price == desired_price


def _build_freight_quote_from_draft(draft: dict) -> Tuple[bool, str, str, str]:
    blob = _build_draft_style_note_blob(draft)

    if _valid_free_freight_marker_present(blob):
        freight_title = _detect_freight_title(blob)
        freight_price = "0.00"
        print("====== FREE FREIGHT DETECTED ======")
        print(f"Carrier title for free freight: {freight_title}")
        print(f"Free freight price: {freight_price}")
        print("===================================")
        return True, "free-freight", freight_title, freight_price

    subtotal = _draft_subtotal_amount(draft)
    if subtotal is None:
        return False, "Could not determine draft subtotal for freight calculation", "", ""

    freight_title = _detect_freight_title(blob)
    freight_amount = (subtotal * FREIGHT_RATE_PERCENT / Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    return True, "charge-freight", freight_title, _money_str(freight_amount)


def _build_issued_at(now_dt: datetime) -> str:
    return now_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_due_at(days_from_today: int, today) -> str:
    due_date = today + timedelta(days=days_from_today)
    due_at = datetime.combine(due_date, dt_time(0, 0, 0), tzinfo=timezone.utc)
    return due_at.isoformat().replace("+00:00", "Z")


def _payment_terms_name(payment_terms: Optional[dict]) -> str:
    if not payment_terms:
        return ""
    return (
        payment_terms.get("translatedName")
        or payment_terms.get("paymentTermsName")
        or ""
    )


def _payment_terms_match_detected(payment_terms: Optional[dict], detected_days: Optional[int]) -> bool:
    if not payment_terms or not detected_days:
        return False

    name = (_payment_terms_name(payment_terms) or "").strip().upper()
    due_in_days = payment_terms.get("dueInDays")

    if detected_days in (30, 45, 60, 90):
        if due_in_days == detected_days:
            return True
        if f"NET {detected_days}" in name or f"NET{detected_days}" in name:
            return True
        return False

    if detected_days == 120:
        if "NET 120" in name or "NET120" in name:
            return True
        if "FIXED" in name:
            return True
        if due_in_days == 120:
            return True
        return False

    return False


def _build_payment_terms_attributes_from_text(
    text: str,
    now_dt: Optional[datetime] = None,
) -> Tuple[dict, str, int]:
    now_dt = now_dt or datetime.now(timezone.utc)
    detected_days = _detect_net_terms_days(text)

    if detected_days:
        detected_label = f"Net {detected_days}"
    else:
        detected_days = 30
        detected_label = "Net 30 (defaulted)"

    template_id = (PAYMENT_TEMPLATE_MAP.get(detected_days) or "").strip()

    if not template_id:
        print(
            f"WARNING: detected Net {detected_days} but no template ID configured; "
            f"defaulting to Net 30 template {DEFAULT_PAYMENT_TERMS_TEMPLATE_ID}"
        )
        detected_days = 30
        detected_label = "Net 30 (defaulted)"
        template_id = DEFAULT_PAYMENT_TERMS_TEMPLATE_ID

    attrs = {
        "paymentTermsTemplateId": template_id,
    }

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

    print("====== PAYMENT TERMS DETECTION ======")
    print(f"Detected label: {detected_label}")
    print(f"Detected days: {detected_days}")
    print(f"Template ID: {template_id}")
    print(f"Payload: {attrs}")
    print("=====================================")

    return attrs, detected_label, detected_days


def _build_payment_terms_attributes(order: dict, now_dt: Optional[datetime] = None) -> Tuple[dict, str, int]:
    return _build_payment_terms_attributes_from_text(_build_order_note_blob(order), now_dt)


def _is_issue_date_fixed_terms_error(exc: Exception) -> bool:
    return "issue date cannot be set with event or fixed payment terms" in str(exc).lower()


def _payment_terms_create(order_id: str, payment_terms_attributes: dict) -> dict:
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

    payload = _data(out).get("paymentTermsCreate", {}) or {}
    errs = payload.get("userErrors", []) or []

    if errs:
        raise RuntimeError(f"paymentTermsCreate failed for {order_id}: {errs}")

    return payload.get("paymentTerms") or {}


def _attach_payment_terms_to_order(order_id: str, order: dict, subtotal: Optional[Decimal]) -> dict:
    if subtotal is not None and subtotal == Decimal("0.00"):
        return {}

    attrs, detected_label, detected_days = _build_payment_terms_attributes(order)

    payloads = [
        (attrs, f"{detected_label} with schedule"),
        ({"paymentTermsTemplateId": attrs["paymentTermsTemplateId"]}, f"{detected_label} template-only fallback"),
    ]

    last_exc = None

    for payload, description in payloads:
        try:
            print(f"Attempting payment terms update for live order: {description}")
            return _payment_terms_create(order_id, payload)
        except Exception as exc:
            last_exc = exc
            print(f"Payment terms attempt failed ({description}): {exc}")

            if _is_issue_date_fixed_terms_error(exc):
                continue

            if payload is not payloads[-1][0]:
                continue

            raise

    if last_exc:
        raise last_exc

    return {}


def _safe_attach_payment_terms_to_order(order_id: str, order: dict, subtotal: Optional[Decimal]) -> None:
    try:
        _attach_payment_terms_to_order(order_id, order, subtotal)
    except Exception as terms_exc:
        print(f"WARNING: Order {order_id} created, but payment terms failed: {terms_exc}")


def _draft_order_update(draft_id: str, input_payload: dict) -> dict:
    print("====== DRAFT ORDER UPDATE REQUEST ======")
    print(f"Draft ID: {draft_id}")
    print(f"Input: {input_payload}")
    print("========================================")

    out = shopify_graphql(
        DRAFT_UPDATE_MUTATION,
        {
            "id": draft_id,
            "input": input_payload,
        },
    )

    print("====== DRAFT ORDER UPDATE RAW RESPONSE ======")
    print(out)
    print("=============================================")

    payload = _data(out).get("draftOrderUpdate", {}) or {}
    errs = payload.get("userErrors", []) or []

    if errs:
        raise RuntimeError(f"draftOrderUpdate userErrors: {errs}")

    return payload.get("draftOrder") or {}


def _recheck_draft(draft_id: str) -> dict:
    out = shopify_graphql(DRAFT_RECHECK_QUERY, {"id": draft_id})
    draft = _data(out).get("draftOrder")

    if not draft:
        raise RuntimeError(f"Draft {draft_id} not found during recheck")

    return draft


def _try_update_payment_terms_payloads(
    draft: dict,
    payloads: List[Tuple[dict, str]],
) -> Tuple[bool, str]:
    last_exc: Optional[Exception] = None

    for payload, description in payloads:
        try:
            print(f"Draft {draft.get('name')} | attempting payment terms update: {description}")
            _draft_order_update(draft["id"], payload)
            return True, description
        except Exception as exc:
            last_exc = exc
            print(f"Draft {draft.get('name')} | payment terms update attempt failed ({description}): {exc}")

            if _is_issue_date_fixed_terms_error(exc):
                continue

            raise

    if last_exc:
        raise last_exc

    return False, "No payment terms payloads were attempted"


def _ensure_draft_shipping_logic(draft: dict) -> Tuple[bool, str, str, str, str]:
    ok, freight_action, freight_title, freight_price = _build_freight_quote_from_draft(draft)

    if not ok:
        return False, freight_action, freight_title, freight_price, freight_action

    if not freight_title:
        freight_title = DEFAULT_FREIGHT_TITLE

    if _shipping_line_matches(draft, freight_title, freight_price):
        return True, freight_action, freight_title, freight_price, (
            f"Existing shipping already matches {freight_title} at {freight_price}"
        )

    currency_code = (draft.get("currencyCode") or "").strip() or _shop_currency() or "USD"
    shipping_payload = {
        "shippingLine": {
            "title": freight_title,
            "priceWithCurrency": {
                "amount": freight_price,
                "currencyCode": currency_code,
            },
        }
    }

    print(
        f"Draft {draft.get('name')} | setting custom shipping to "
        f"{freight_title} at {freight_price} {currency_code} "
        f"because freight_action={freight_action}"
    )
    _draft_order_update(draft["id"], shipping_payload)

    if freight_action == "free-freight":
        return True, freight_action, freight_title, freight_price, (
            f"Set free freight shipping to {freight_title} at {freight_price} {currency_code}"
        )

    return True, freight_action, freight_title, freight_price, (
        f"Set shipping to {freight_title} at {freight_price} {currency_code}"
    )


def _ensure_draft_payment_terms(draft: dict, now_dt: datetime) -> Tuple[bool, str, str, Optional[int]]:
    existing = draft.get("paymentTerms")
    existing_name = _payment_terms_name(existing)

    blob = _build_draft_style_note_blob(draft)
    attrs, detected_label, detected_days = _build_payment_terms_attributes_from_text(blob, now_dt)
    template_id = attrs["paymentTermsTemplateId"]

    if detected_days == 120:
        due_at = attrs.get("paymentSchedules", [{}])[0].get("dueAt")
        payloads = [
            (
                {
                    "paymentTerms": {
                        "paymentTermsTemplateId": template_id,
                        "paymentSchedules": [
                            {
                                "dueAt": due_at,
                            }
                        ],
                    }
                },
                f"fixed/event-safe update to Net 120 using template {template_id} with dueAt {due_at}",
            ),
            (
                {
                    "paymentTerms": {
                        "paymentTermsTemplateId": template_id,
                    }
                },
                f"template-only fallback to Net 120 using template {template_id}",
            ),
        ]
    else:
        issued_at = attrs.get("paymentSchedules", [{}])[0].get("issuedAt")
        payloads = [
            (
                {
                    "paymentTerms": {
                        "paymentTermsTemplateId": template_id,
                        "paymentSchedules": [
                            {
                                "issuedAt": issued_at,
                            }
                        ],
                    }
                },
                f"standard update to Net {detected_days} using template {template_id} with issuedAt {issued_at}",
            ),
            (
                {
                    "paymentTerms": {
                        "paymentTermsTemplateId": template_id,
                    }
                },
                f"template-only fallback to Net {detected_days} using template {template_id}",
            ),
        ]

    print(
        f"Draft {draft.get('name')} | overriding existing payment terms "
        f"'{existing_name or 'NONE'}' to {detected_label} using template {template_id}"
    )

    ok, attempt_description = _try_update_payment_terms_payloads(draft, payloads)

    if not ok:
        return False, f"Failed to update payment terms to {detected_label}", detected_label, detected_days

    return True, f"Overrode payment terms to {detected_label} ({attempt_description})", detected_label, detected_days


def _post_create_update_draft_freight_and_terms(draft_id: str) -> Tuple[dict, str]:
    now_dt = datetime.now(timezone.utc)

    latest = _recheck_draft(draft_id)

    freight_ok, freight_action, freight_title, freight_price, freight_reason = _ensure_draft_shipping_logic(latest)
    print(f"{latest.get('name')} | freight-check={freight_ok} | {freight_reason}")

    latest = _recheck_draft(draft_id)
    current_freight_title = ((latest.get("shippingLine") or {}).get("title") or "").strip()
    current_freight_price = _current_shipping_price(latest)

    if freight_action in ("charge-freight", "free-freight") and not _shipping_line_matches(latest, freight_title, freight_price):
        raise RuntimeError(
            f"Expected shipping '{freight_title}' at {freight_price} but Shopify returned "
            f"'{current_freight_title or 'NONE'}' at {current_freight_price or 'NONE'}"
        )

    subtotal = _draft_subtotal_amount(latest)
    is_free_order = subtotal is not None and subtotal == Decimal("0.00")

    if is_free_order:
        terms_ok = True
        terms_reason = "Skipped - $0 free order"
        detected_terms = ""
        detected_days = None
    else:
        terms_ok, terms_reason, detected_terms, detected_days = _ensure_draft_payment_terms(latest, now_dt)

    print(f"{latest.get('name')} | payment-terms-check={terms_ok} | {terms_reason}")

    latest = _recheck_draft(draft_id)
    payment_terms_after = _payment_terms_name(latest.get("paymentTerms"))

    if detected_terms:
        if not payment_terms_after:
            raise RuntimeError(f"Detected {detected_terms} but payment terms are still blank after update")
        if not _payment_terms_match_detected(latest.get("paymentTerms"), detected_days):
            raise RuntimeError(
                f"Detected {detected_terms} but Shopify returned '{payment_terms_after}' after update"
            )

    summary = (
        f"freight={freight_action}"
        + (f" {freight_title} {freight_price}" if freight_title or freight_price else "")
        + f"; terms={payment_terms_after or terms_reason}"
    )

    print("====== DRY RUN DRAFT POST-CREATE UPDATE COMPLETE ======")
    print(f"Draft: {latest.get('name')} / {latest.get('id')}")
    print(f"Shipping: {((latest.get('shippingLine') or {}).get('title') or 'NONE')} @ {_current_shipping_price(latest) or 'NONE'}")
    print(f"Payment terms: {payment_terms_after or 'NONE'}")
    print("======================================================")

    return latest, summary


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
    payload = _data(out).get("orderCreate", {}) or {}
    errs = payload.get("userErrors", []) or []
    created = payload.get("order") or {}
    order_id = created.get("id")
    dfs = created.get("displayFinancialStatus")

    return order_id, dfs, errs, out


def _draft_line_items_from_order_line_items(order_line_items: List[dict]) -> List[dict]:
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
    customer_id,
    company_location_id,
) -> Tuple[Optional[str], Optional[str], List[dict], dict]:
    draft_line_items = _draft_line_items_from_order_line_items(line_items)

    draft_input = {
        "lineItems": draft_line_items,
        "note": _build_draft_note(order),
        "tags": ["excel-import", "dry-run-draft"],
        "billingAddress": order_input.get("billingAddress"),
        "shippingAddress": order_input.get("shippingAddress"),
        "poNumber": order_input.get("poNumber"),
        "email": order_input.get("email"),
        "visibleToCustomer": False,
        "useCustomerDefaultAddress": False,
    }

    if order_input.get("metafields"):
        draft_input["metafields"] = order_input["metafields"]

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

    print("====== DRY RUN DRAFT CREATE INPUT SUMMARY ======")
    print(f"PO: {order.get('poNumber')}")
    print(f"Line item count: {len(draft_line_items)}")
    print(f"Initial note: {draft_input.get('note')}")
    print(f"Purchasing entity: {draft_input.get('purchasingEntity')}")
    print("================================================")

    m = """
    mutation($input: DraftOrderInput!) {
      draftOrderCreate(input: $input) {
        draftOrder {
          id
          name
          status
          poNumber
        }
        userErrors {
          field
          message
        }
      }
    }
    """

    out = shopify_graphql(m, {"input": draft_input})
    payload = _data(out).get("draftOrderCreate", {}) or {}
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

    print("====== DRY RUN DRAFT CREATED; NOW POST-UPDATING ======")
    print(f"PO: {order.get('poNumber')}")
    print(f"Draft: {draft_name} / {draft_id}")
    print("=====================================================")

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
        metafields.append({"namespace": "b2b", "key": "ship_date", "value": ship_date_val})
    if bill_to_email_val:
        metafields.append({"namespace": "b2b", "key": "bill_to_email", "value": bill_to_email_val})
    if po_num_val:
        metafields.append({"namespace": "b2b", "key": "po_number", "value": po_num_val})

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

    print("====== FREIGHT DETECTION ======")
    print(f"PO: {order.get('poNumber')}")
    print(f"Freight action: {freight_action}")
    print(f"Freight title: {freight_title}")
    print(f"Freight price: {freight_price}")
    print(f"Subtotal used: {subtotal_for_terms_and_freight}")
    print("===============================")

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

    if DRY_RUN:
        draft_id, draft_name, draft_errs, _ = _try_draft_order_create(
            order=order,
            order_input=order_input,
            line_items=line_items,
            customer_id=customer_id,
            company_location_id=company_location_id,
        )

        if not draft_id and company_location_id and customer_id:
            draft_id, draft_name, draft_errs, _ = _try_draft_order_create(
                order=order,
                order_input=order_input,
                line_items=line_items,
                customer_id=customer_id,
                company_location_id=None,
            )

        if not draft_id:
            raise RuntimeError(f"DRY_RUN draftOrderCreate failed. Errors: {draft_errs}")

        latest, post_update_summary = _post_create_update_draft_freight_and_terms(draft_id)
        return draft_id, f"DRY_RUN_DRAFT {latest.get('name') or draft_name or ''} | {post_update_summary}".strip()

    order_id, dfs, errs, _ = _try_order_create(order_input, options, "full")
    if order_id:
        _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs

    saved_loc = order_input.pop("companyLocationId", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-companyLocationId")
    if order_id:
        _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs
    if saved_loc:
        order_input["companyLocationId"] = saved_loc

    saved_mf = order_input.pop("metafields", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-metafields")
    if order_id:
        _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
        return order_id, dfs
    if saved_mf:
        order_input["metafields"] = saved_mf

    saved_shipping_lines = order_input.pop("shippingLines", None)
    if saved_shipping_lines:
        order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-shippingLines")
        if order_id:
            _safe_attach_payment_terms_to_order(order_id, order, subtotal_for_terms_and_freight)
            return order_id, dfs

        order_input["shippingLines"] = saved_shipping_lines

    raise RuntimeError(f"orderCreate failed. Last errors: {errs}")


def process_live_orders(orders, progress_callback=None, cancel_event=None):
    results = []
    seen_pos = set()

    for order in orders:
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
            results.append({"po": po_number, "status": "skipped", "reason": "Duplicate in file", "id": None})
            continue

        if progress_callback:
            action_word = "Creating draft for" if DRY_RUN else "Processing"
            progress_callback(po_number, "processing", f"{action_word} PO {po_number}...")

        if po_number and order_po_exists_in_shopify(po_number):
            seen_pos.add(po_norm)
            results.append({"po": po_number, "status": "skipped", "reason": "Already exists in Shopify", "id": None})
            if progress_callback:
                progress_callback(po_number, "skipped", "Already exists in Shopify")
            continue

        try:
            customer_id, created = create_or_find_customer(order, po_number)
        except Exception as e:
            results.append({"po": po_number, "status": "error", "reason": str(e), "id": None})
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
            results.append({"po": po_number, "status": "error", "reason": str(e), "id": None})
            if progress_callback:
                progress_callback(po_number, "error", str(e))

        seen_pos.add(po_norm)
        time.sleep(0.15)

    return results
