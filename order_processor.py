"""
Live order creation logic (wraps Script 2 logic — financialStatus=PENDING).
"""
import os
from shopify_core import (
    shopify_graphql, parse_price, to_gid, _to_yyyy_mm_dd,
    is_assortment_parent, expand_assortment_children, find_variant_id_and_price,
    normalize_country, to_mailing_address, order_po_exists_in_shopify,
    create_or_find_customer, ensure_company, ensure_company_location,
    get_or_create_matching_contact, grant_ordering_permission, _fix_countries,
    norm_po, SHOP_CURRENCY
)
import time


def _price_set(amount):
    currency = os.getenv("SHOP_CURRENCY", SHOP_CURRENCY or "USD")
    return {"shopMoney": {"amount": float(round(amount, 2)), "currencyCode": currency}}


def _try_order_create(order_input, options, note):
    m = '''
    mutation($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
      orderCreate(order: $order, options: $options) {
        order { id name poNumber displayFinancialStatus }
        userErrors { field message }
      }
    }'''
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
                    effective = parsed_child_price if parsed_child_price is not None else shop_price
                    if effective is not None:
                        if shop_price is None or abs(effective - shop_price) > 1e-9:
                            li["priceSet"] = _price_set(float(effective))
                    line_items.append(li)
                else:
                    effective = parsed_child_price if parsed_child_price is not None else 0.01
                    line_items.append({
                        "title": child_sku, "sku": child_sku,
                        "quantity": int(child_qty), "priceSet": _price_set(float(effective)),
                    })
            continue

        variant_id, shop_price = find_variant_id_and_price(sku)
        if variant_id:
            li = {"variantId": variant_id, "quantity": qty}
            effective = parsed_price if parsed_price is not None else shop_price
            if effective is not None:
                if shop_price is None or abs(effective - shop_price) > 1e-9:
                    li["priceSet"] = _price_set(float(effective))
            line_items.append(li)
        else:
            effective = parsed_price if parsed_price is not None else 0.01
            line_items.append({
                "title": item.get("name") or (sku or "Item"),
                "sku": sku or None, "quantity": qty,
                "priceSet": _price_set(float(effective)),
            })

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
    if metafields:
        order_input["metafields"] = metafields

    if customer_id:
        order_input["customer"] = {"toAssociate": {"id": to_gid("Customer", customer_id)}}
    if company_location_id:
        order_input["companyLocationId"] = company_location_id

    options = {"sendReceipt": False, "sendFulfillmentReceipt": False}

    order_id, dfs, errs, _ = _try_order_create(order_input, options, "full")
    if order_id:
        return order_id, dfs

    saved_loc = order_input.pop("companyLocationId", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-companyLocationId")
    if order_id:
        return order_id, dfs
    if saved_loc:
        order_input["companyLocationId"] = saved_loc

    saved_mf = order_input.pop("metafields", None)
    order_id, dfs, errs, _ = _try_order_create(order_input, options, "no-metafields")
    if order_id:
        return order_id, dfs
    if saved_mf:
        order_input["metafields"] = saved_mf

    raise RuntimeError(f"orderCreate failed. Last errors: {errs}")


def process_live_orders(orders, progress_callback=None):
    """
    Process a list of orders as live orders (financialStatus=PENDING).
    Returns list of result dicts.
    """
    results = []
    seen_pos = set()

    for order in orders:
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
                    grant_ordering_permission(company_contact_id, company_location_id, company_id)

        try:
            order_id, dfs = create_live_order(order, customer_id, company_id, company_contact_id, company_location_id)
            results.append({
                "po": po_number, "status": "created", "reason": f"Order created ({dfs or 'PENDING'})",
                "id": order_id, "company": billToName,
                "line_count": len(order.get("details", [])),
                "financial_status": dfs,
            })
            if progress_callback:
                progress_callback(po_number, "created", f"Order created: {order_id}")
        except Exception as e:
            results.append({"po": po_number, "status": "error", "reason": str(e), "id": None})
            if progress_callback:
                progress_callback(po_number, "error", str(e))

        seen_pos.add(po_norm)
        time.sleep(0.15)

    return results
