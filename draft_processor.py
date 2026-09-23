"""
Draft order creation logic (wraps Script 1 logic).
"""
from shopify_core import (
    shopify_graphql, parse_price, to_gid, _to_yyyy_mm_dd,
    is_assortment_parent, expand_assortment_children, find_variant_id_and_price,
    normalize_country, to_mailing_address, draft_po_exists_in_shopify,
    create_or_find_customer, ensure_company, ensure_company_location,
    get_or_create_matching_contact, grant_ordering_permission, _fix_countries,
    norm_po
)
import time


# ---------------------------------------------------------------------------
# Template extras: order tags + metafields
#
#   order_tags      -> comma-separated, e.g. "trade-show, fall-26"
#   mt_repgroup_id  -> b2b.mt_repgroup_id
#   mt_record_id    -> b2b.mt_record_id
#
# Tags are unioned across all rows of a PO. For metafields, the first
# non-empty value across the PO's rows wins.
# ---------------------------------------------------------------------------

TAGS_COLUMN = "order_tags"
COLUMN_METAFIELDS = {
    # column header: (namespace, key, type)
    "mt_repgroup_id": ("b2b", "mt_repgroup_id", "single_line_text_field"),
    "mt_record_id":   ("b2b", "mt_record_id",   "single_line_text_field"),
}
DEFAULT_MF_TYPE = "single_line_text_field"
BASE_TAGS = ["excel-import"]


def _clean_cell(val):
    if val is None:
        return None
    # Excel/pandas turn numeric IDs into floats (12345 -> 12345.0)
    if isinstance(val, float):
        if val != val:  # NaN
            return None
        if val.is_integer():
            return str(int(val))
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _normalize_tags(raw):
    if not raw:
        return []
    parts = raw.split(",") if isinstance(raw, str) else list(raw)
    out, seen = [], set()
    for t in parts:
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def extract_order_extras(rows):
    """
    Call from the CSV parser with all raw row dicts belonging to one PO.
    Returns {"tags": [...], "customMetafields": [...]} to merge into the order dict.
    """
    tags = []
    mf_values = {}

    for row in rows or []:
        for header, val in (row or {}).items():
            if header is None:
                continue
            h = str(header).strip().lower()
            v = _clean_cell(val)
            if v is None:
                continue

            if h == TAGS_COLUMN:
                tags.extend(v.split(","))
            elif h in COLUMN_METAFIELDS and h not in mf_values:
                mf_values[h] = v

    mfs = []
    for col, v in mf_values.items():
        ns, key, mf_type = COLUMN_METAFIELDS[col]
        mfs.append({"namespace": ns, "key": key, "value": v, "type": mf_type})

    return {"tags": _normalize_tags(tags), "customMetafields": mfs}


def _build_custom_metafields(raw, reserved_keys):
    out = []
    for mf in raw or []:
        ns = (mf.get("namespace") or "").strip()
        key = (mf.get("key") or "").strip()
        val = _clean_cell(mf.get("value"))
        if not ns or not key or val is None:
            continue
        if (ns, key) in reserved_keys:
            continue  # core b2b fields always come from the standard columns
        out.append({
            "namespace": ns,
            "key": key,
            "value": val,
            "type": (mf.get("type") or DEFAULT_MF_TYPE).strip(),
        })
    return out


# ---------------------------------------------------------------------------
# Draft creation
# ---------------------------------------------------------------------------

def _try_draft_create(input_obj, note):
    m = """
    mutation($input: DraftOrderInput!) {
      draftOrderCreate(input: $input) {
        draftOrder { id name poNumber }
        userErrors { field message }
      }
    }"""
    out = shopify_graphql(m, {"input": input_obj})
    payload = (out.get("data", {}) or {}).get("draftOrderCreate", {}) or {}
    errs = payload.get("userErrors", []) or []
    draft = payload.get("draftOrder") or {}
    draft_id = draft.get("id")
    return draft_id, errs, out


def _force_min_price_on_custom_items(input_obj, min_price=0.01):
    for li in (input_obj.get("lineItems") or []):
        if "variantId" not in li:
            p = parse_price(li.get("originalUnitPrice"))
            if p is None or p <= 0:
                li["originalUnitPrice"] = float(min_price)


def create_draft_order(order, customer_id, company_id, company_contact_id, company_location_id, warnings=None):
    note_parts = []
    if order.get("poNumber"):
        note_parts.append(f"PO: {order['poNumber']}")
    if order.get("specialInstructions"):
        note_parts.append(str(order["specialInstructions"]))
    if order.get("shippingMethod"):
        note_parts.append(f"Shipping: {order['shippingMethod']}")

    line_items = []
    for item in (order.get("details") or []):
        sku = (item.get("itemNumber") or "").strip()
        qty = int(item.get("quantity") or 0)
        parsed_price = parse_price(item.get("unitPrice"))

        if is_assortment_parent(sku):
            for child_sku, child_qty, fallback_price in expand_assortment_children(sku, qty):
                variant_id, shop_price = find_variant_id_and_price(child_sku)
                parsed_child_price = parse_price(fallback_price)
                if variant_id:
                    li = {"variantId": variant_id, "quantity": int(child_qty)}
                    if shop_price is not None and parsed_child_price is not None:
                        if parsed_child_price < shop_price:
                            li["originalUnitPrice"] = float(shop_price)
                            diff = round(shop_price - parsed_child_price, 2)
                            if diff > 0:
                                li["appliedDiscount"] = {"value": float(diff), "valueType": "FIXED_AMOUNT", "title": "Excel price match"}
                        elif parsed_child_price > shop_price:
                            li["originalUnitPrice"] = float(parsed_child_price)
                        else:
                            li["originalUnitPrice"] = float(shop_price)
                    elif shop_price is not None:
                        li["originalUnitPrice"] = float(shop_price)
                    elif parsed_child_price is not None:
                        li["originalUnitPrice"] = float(parsed_child_price)
                else:
                    li = {
                        "title": child_sku, "sku": child_sku, "quantity": int(child_qty),
                        "originalUnitPrice": float(parsed_child_price) if parsed_child_price is not None else 0.01,
                    }
                line_items.append(li)
            continue

        variant_id, shop_price = find_variant_id_and_price(sku)
        if variant_id:
            li = {"variantId": variant_id, "quantity": qty}
            if shop_price is not None and parsed_price is not None:
                if parsed_price < shop_price:
                    li["originalUnitPrice"] = float(shop_price)
                    diff = round(shop_price - parsed_price, 2)
                    if diff > 0:
                        li["appliedDiscount"] = {"value": float(diff), "valueType": "FIXED_AMOUNT", "title": "Excel price match"}
                elif parsed_price > shop_price:
                    li["originalUnitPrice"] = float(parsed_price)
                else:
                    li["originalUnitPrice"] = float(shop_price)
            elif shop_price is not None:
                li["originalUnitPrice"] = float(shop_price)
            elif parsed_price is not None:
                li["originalUnitPrice"] = float(parsed_price)
        else:
            li = {
                "title": item.get("name") or (sku or "Item"),
                "sku": sku or None, "quantity": qty,
                "originalUnitPrice": float(parsed_price) if parsed_price is not None else 0.01,
            }
        line_items.append(li)

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

    core_keys = {(m["namespace"], m["key"]) for m in metafields}
    core_keys |= {("b2b", "ship_date"), ("b2b", "bill_to_email"), ("b2b", "po_number")}
    custom_mf = _build_custom_metafields(order.get("customMetafields"), core_keys)
    all_mf = metafields + custom_mf

    tags = _normalize_tags(BASE_TAGS + _normalize_tags(order.get("tags")))

    input_obj = {
        "lineItems": line_items,
        "note": " | ".join([p for p in note_parts if p]),
        "tags": tags,
        "billingAddress": to_mailing_address(order, "billing"),
        "shippingAddress": to_mailing_address(order, "shipping"),
        "poNumber": order.get("poNumber"),
        "email": order.get("shipToEmail") or order.get("billToEmail") or None,
    }
    if all_mf:
        input_obj["metafields"] = all_mf

    if company_id and company_contact_id and company_location_id:
        input_obj["purchasingEntity"] = {
            "purchasingCompany": {
                "companyId": company_id,
                "companyContactId": company_contact_id,
                "companyLocationId": company_location_id,
            }
        }
    elif customer_id:
        input_obj["purchasingEntity"] = {"customerId": to_gid("Customer", customer_id)}

    dropped = []

    def _ok(did, first_errs=None):
        if warnings is not None and dropped:
            msg = "Dropped on retry: " + ", ".join(dropped)
            if first_errs:
                msg += " | first error: " + "; ".join(e.get("message", "") for e in first_errs)
            warnings.append(msg)
        return did

    draft_id, errs, _ = _try_draft_create(input_obj, "full")
    if draft_id:
        return draft_id
    first_errs = errs

    # A bad custom metafield (wrong type, bad namespace) shouldn't cost us the
    # company link or the core b2b metafields — shed custom ones first.
    if custom_mf:
        if metafields:
            input_obj["metafields"] = metafields
        else:
            input_obj.pop("metafields", None)
        dropped.append("custom metafields")
        draft_id, errs, _ = _try_draft_create(input_obj, "no-custom-metafields")
        if draft_id:
            return _ok(draft_id, first_errs)

    pe = input_obj.pop("purchasingEntity", None)
    if pe:
        draft_id, errs, _ = _try_draft_create(input_obj, "no-purchasingEntity")
        if draft_id:
            dropped.append("purchasing entity")
            return _ok(draft_id, first_errs)
        input_obj["purchasingEntity"] = pe

    saved_mf = input_obj.pop("metafields", None)
    draft_id, errs, _ = _try_draft_create(input_obj, "no-metafields")
    if draft_id:
        if saved_mf:
            dropped.append("all metafields")
        return _ok(draft_id, first_errs)
    if saved_mf:
        input_obj["metafields"] = saved_mf

    _force_min_price_on_custom_items(input_obj, 0.01)
    draft_id, errs, _ = _try_draft_create(input_obj, "force-min-custom-price")
    if draft_id:
        dropped.append("custom item prices forced to $0.01")
        return _ok(draft_id, first_errs)

    raise RuntimeError(f"draftOrderCreate failed. Last errors: {errs}")


def process_draft_orders(orders, progress_callback=None, cancel_event=None):
    """
    Process a list of orders as drafts.
    progress_callback(po_number, status, message) called for each PO.
    cancel_event: threading.Event — if set, processing stops before the next order.
    Returns list of result dicts.
    """
    results = []
    seen_pos = set()

    for i, order in enumerate(orders):
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
            results.append({"po": po_number, "status": "skipped", "reason": "Duplicate in file", "id": None})
            continue

        if progress_callback:
            progress_callback(po_number, "processing", f"Processing PO {po_number}...")

        # Check if already exists
        if po_number and draft_po_exists_in_shopify(po_number):
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
                    grant_ordering_permission(company_contact_id, company_location_id, company_id)

        try:
            warnings = []
            draft_id = create_draft_order(
                order, customer_id, company_id, company_contact_id, company_location_id,
                warnings=warnings,
            )
            reason = "Draft created"
            if warnings:
                reason += " — " + " | ".join(warnings)
            results.append({
                "po": po_number, "status": "created", "reason": reason,
                "id": draft_id, "company": billToName,
                "line_count": len(order.get("details", [])),
                "warnings": warnings,
            })
            if progress_callback:
                msg = f"Draft created: {draft_id}"
                if warnings:
                    msg += " — " + " | ".join(warnings)
                progress_callback(po_number, "created", msg)
        except Exception as e:
            results.append({"po": po_number, "status": "error", "reason": str(e), "id": None})
            if progress_callback:
                progress_callback(po_number, "error", str(e))

        seen_pos.add(po_norm)
        time.sleep(0.15)

    return results
