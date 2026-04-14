import os
import re
import math
import time
import requests
import pandas as pd
from decimal import Decimal, InvalidOperation
from datetime import datetime, UTC
from urllib.parse import urlparse, parse_qs

# -------------------- Config --------------------
SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN", "")
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")
SHOP_CURRENCY = os.getenv("SHOP_CURRENCY", "USD")
HTTP_TIMEOUT = 25
LOG_TIMINGS = False

REST_PAGE_LIMIT = 250
MAX_PAGES_PER_STATUS = 100
REST_PAGE_SLEEP = 0.03
REST_DEDUPE_ENABLED = True

SHOPIFY_BASE = ""
shopify_rest_headers = {}

def init_shopify():
    global SHOPIFY_BASE, shopify_rest_headers, SHOPIFY_TOKEN, SHOPIFY_STORE
    SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN", "")
    SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "")
    if not SHOPIFY_TOKEN or not SHOPIFY_STORE:
        raise ValueError("Missing SHOPIFY_TOKEN or SHOPIFY_STORE in environment")
    SHOPIFY_BASE = (
        f"https://{SHOPIFY_STORE.strip('/')}"
        if not SHOPIFY_STORE.startswith("http")
        else SHOPIFY_STORE.rstrip("/")
    )
    shopify_rest_headers = {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json"
    }

# -------------------- Utilities --------------------
_norm_hash_space = re.compile(r"[#\s]")

def norm_po(po):
    return _norm_hash_space.sub("", str(po or "").strip()).upper()

def parse_price(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", s.replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None

def to_gid(kind, numeric_id):
    return f"gid://shopify/{kind}/{numeric_id}"

def _to_yyyy_mm_dd(val):
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date().isoformat()
        except Exception:
            pass
    return s[:10]

def shopify_graphql(query, variables=None):
    url = f"{SHOPIFY_BASE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    r = requests.post(
        url,
        headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()

# -------------------- Assortment Map --------------------
ASSORTMENT_MAP = {
    "LL-00-0014": [
        ("LL-20-3227", 6, 3.50), ("LL-20-3228", 6, 3.50), ("LL-20-3229", 6, 3.50),
        ("LL-20-3230", 6, 3.50), ("LL-20-3231", 6, 3.50), ("LL-20-3232", 6, 3.50),
        ("LL-99-0014", 1, 0.00),
    ],
    "LL-00-0015": [
        ("LL-20-3250", 6, 3.50), ("LL-20-3251", 6, 3.50), ("LL-20-3252", 6, 3.50),
        ("LL-20-3253", 6, 3.50), ("LL-20-3254", 6, 3.50), ("LL-20-3255", 6, 3.50),
        ("LL-99-0015", 1, 0.00),
    ],
    "LL-DISP-FA-SP26": [
        ("LL-16-3149", 8, 6.50), ("LL-16-3151", 4, 6.50), ("LL-16-3223", 4, 6.50),
        ("LL-16-3150", 4, 6.50), ("LL-16-3203", 4, 6.50), ("LL-16-3222", 4, 6.50),
        ("LL-16-3243", 4, 6.50), ("LL-16-3206", 4, 6.50),
    ],
    "LL-DISP-FA-FA26": [
        ("LL-16-3248", 4, 6.50), ("LL-16-3157", 8, 6.50),
        ("LL-16-3247", 4, 6.50), ("LL-16-3158", 8, 6.50),
    ],
    "LL-DISP-FA-WN26": [
        ("LL-16-3245", 8, 6.50), ("LL-16-3162", 8, 6.50),
        ("LL-16-3246", 4, 6.50), ("LL-16-3244", 4, 6.50),
    ],
    "LL-00-0016": [
        ("165005", 8, 6.50), ("165008", 8, 6.50), ("LL-16-3149", 8, 6.50),
        ("LL-16-3148", 4, 6.50), ("LL-16-3150", 4, 6.50), ("LL-16-3153", 4, 6.50),
        ("LL-99-3087", 1, 0.00),
    ],
    "LL-00-0018": [
        ("165005", 8, 6.50), ("165006", 4, 6.50), ("165007", 8, 6.50),
        ("165008", 8, 6.50), ("165009", 8, 6.50), ("165012", 8, 6.50),
        ("165014", 4, 6.50), ("LL-16-3148", 8, 6.50), ("LL-16-3149", 8, 6.50),
        ("LL-16-3150", 8, 6.50), ("LL-16-3151", 4, 6.50), ("LL-16-3152", 4, 6.50),
        ("LL-16-3153", 8, 6.50), ("LL-00-0004", 1, 0.00),
    ],
    "LL-00-0017": [
        ("11-2501", 6, 6.50), ("11-2502", 6, 6.50), ("LL-12-3066-CP12", 4, 6.50),
        ("LL-12-3102", 4, 10.00), ("11-2507", 4, 6.50), ("LL-12-3121", 4, 6.50),
        ("11-2508-V2", 6, 10.00), ("LL-12-3123", 4, 6.50), ("LL-12-3109", 4, 6.50),
        ("LL-12-3018", 4, 6.50), ("99-9504", 1, 0.00),
    ],
    "LL-DISP-WR2": [
        ("11-2501", 6, 6.50), ("11-2504", 6, 6.50), ("LL-12-3121", 8, 6.50),
        ("LL-12-3123", 8, 6.50), ("LL-12-3124", 4, 6.50), ("99-9504", 1, 0.00),
    ],
}

def is_assortment_parent(sku):
    return (sku or "") in ASSORTMENT_MAP

def expand_assortment_children(parent_sku, parent_qty):
    base = ASSORTMENT_MAP.get(parent_sku, [])
    q = int(parent_qty or 0)
    return [(child, per * q, fallback) for (child, per, fallback) in base]

# -------------------- Catalog --------------------
_variant_cache = {}

def find_variant_id_and_price(sku):
    if not sku:
        return None, None
    if sku in _variant_cache:
        return _variant_cache[sku]
    q = """
    query($q: String!) {
      productVariants(first: 1, query: $q) {
        nodes { id sku price }
      }
    }"""
    try:
        d = shopify_graphql(q, {"q": f"sku:{sku}"})
        nodes = (((d.get("data", {}) or {}).get("productVariants", {}) or {}).get("nodes", []) or [])
        if nodes:
            vid = (nodes[0] or {}).get("id")
            shop_price = parse_price((nodes[0] or {}).get("price"))
            result = (vid, shop_price)
            _variant_cache[sku] = result
            return result
    except Exception:
        pass
    return None, None

def clear_variant_cache():
    _variant_cache.clear()

# -------------------- Country/State --------------------
US_STATE_ABBR = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO",
    "connecticut":"CT","delaware":"DE","district of columbia":"DC","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS",
    "kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA",
    "michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT",
    "nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ","new mexico":"NM",
    "new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
    "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
    "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
    "virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY",
}

def normalize_country(v):
    if not v:
        return None
    v = v.strip()
    if len(v) == 2:
        return v.upper()
    if v.lower().startswith("united states") or v.upper() in {"US", "USA"}:
        return "US"
    return v

def normalize_zone(country_code, state):
    if not state:
        return None
    cc = (country_code or "").upper()
    s = str(state).strip()
    if len(s) == 2:
        return s.upper()
    if cc == "US":
        return US_STATE_ABBR.get(s.lower()) or s
    return s

def _fix_countries(order):
    original_ship = order.get("shipToCountry")
    if original_ship and original_ship != "US":
        order["shipToCountry"] = "US"
    bt = (order.get("billToCountry") or "").strip()
    if bt:
        bt_norm = normalize_country(bt)
        if (bt_norm or "").upper() in {"US"}:
            if bt != "US":
                order["billToCountry"] = "US"
        else:
            if len(bt) == 2 and bt != bt.upper():
                order["billToCountry"] = bt.upper()

# -------------------- B2B --------------------
_company_id_cache = {}
_location_id_cache = {}
_role_id_cache = {}

def find_company_by_name(name):
    if not name:
        return None
    if name in _company_id_cache:
        return _company_id_cache[name]
    q = """query($q: String!) { companies(first: 10, query: $q) { edges { node { id name } } } }"""
    data = shopify_graphql(q, {"q": name})
    edges = (data.get("data", {}) or {}).get("companies", {}).get("edges", [])
    for e in edges:
        node = e.get("node", {})
        if node.get("name", "").strip().lower() == name.strip().lower():
            cid = node.get("id")
            if cid:
                _company_id_cache[name] = cid
                return cid
    return None

def create_company(name):
    m = """mutation($input: CompanyCreateInput!) { companyCreate(input: $input) { company { id name } userErrors { field message } } }"""
    data = shopify_graphql(m, {"input": {"company": {"name": name}}})
    errs = (data.get("data", {}) or {}).get("companyCreate", {}).get("userErrors", [])
    if errs:
        raise RuntimeError(f"companyCreate error: {errs}")
    return (data.get("data", {}) or {}).get("companyCreate", {}).get("company", {}).get("id")

def ensure_company(name):
    if not name:
        return None
    cid = _company_id_cache.get(name)
    if cid:
        return cid
    cid = find_company_by_name(name) or create_company(name)
    if cid:
        _company_id_cache[name] = cid
    return cid

def to_company_address_input(order, kind):
    if kind == "billing":
        return {
            "address1": order.get("billToAddress1"),
            "address2": order.get("billToAddress2"),
            "city": order.get("billToCity"),
            "zip": order.get("billToZip"),
            "countryCode": normalize_country(order.get("billToCountry")) or "US",
            "zoneCode": normalize_zone(normalize_country(order.get("billToCountry")) or "US", order.get("billToState")),
            "recipient": f"{order.get('buyerFirstName') or ''} {order.get('buyerLastName') or ''}".strip() or None,
        }
    else:
        return {
            "address1": order.get("shipToAddress1"),
            "address2": order.get("shipToAddress2"),
            "city": order.get("shipToCity"),
            "zip": order.get("shipToZip"),
            "countryCode": normalize_country(order.get("shipToCountry")) or "US",
            "zoneCode": normalize_zone(normalize_country(order.get("shipToCountry")) or "US", order.get("shipToState")),
            "phone": order.get("shipToPhone"),
            "recipient": f"{order.get('buyerFirstName') or ''} {order.get('buyerLastName') or ''}".strip() or None,
        }

def ensure_company_location(company_id, name, order):
    key = (company_id, name or "Default")
    if key in _location_id_cache:
        return _location_id_cache[key]
    q = """
    query($id: ID!) {
      company(id: $id) {
        name
        locations(first: 50) {
          nodes { id name shippingAddress { address1 } billingAddress { address1 } }
        }
      }
    }"""
    data = shopify_graphql(q, {"id": company_id})
    company_node = (data.get("data", {}) or {}).get("company", {}) or {}
    company_name = company_node.get("name") or ""
    nodes = (company_node.get("locations", {}) or {}).get("nodes") or []
    desired_name = name or "Default"

    for n in nodes:
        if (n.get("name") or "").strip() == desired_name.strip():
            lid = n.get("id")
            _location_id_cache[key] = lid
            try:
                ship_has = bool((n.get("shippingAddress") or {}).get("address1"))
                bill_has = bool((n.get("billingAddress") or {}).get("address1"))
                m = """mutation($locationId: ID!, $address: CompanyAddressInput!, $types: [CompanyAddressType!]!) {
                    companyLocationAssignAddress(locationId: $locationId, address: $address, addressTypes: $types) {
                      userErrors { field message } } }"""
                if not ship_has:
                    shopify_graphql(m, {"locationId": lid, "address": to_company_address_input(order,"shipping"), "types": ["SHIPPING"]})
                if not bill_has and order.get("billToAddress1"):
                    shopify_graphql(m, {"locationId": lid, "address": to_company_address_input(order,"billing"), "types": ["BILLING"]})
            except Exception:
                pass
            return lid

    if len(nodes) == 1:
        n = nodes[0]
        lid = n.get("id")
        current_name = (n.get("name") or "").strip()
        ship_has = bool((n.get("shippingAddress") or {}).get("address1"))
        bill_has = bool((n.get("billingAddress") or {}).get("address1"))
        is_generic_name = current_name.lower() in {company_name.strip().lower(), "default"}
        if is_generic_name and not ship_has and not bill_has:
            try:
                m = """mutation($locationId: ID!, $address: CompanyAddressInput!, $types: [CompanyAddressType!]!) {
                    companyLocationAssignAddress(locationId: $locationId, address: $address, addressTypes: $types) {
                      userErrors { field message } } }"""
                shopify_graphql(m, {"locationId": lid, "address": to_company_address_input(order,"shipping"), "types": ["SHIPPING"]})
                if order.get("billToAddress1"):
                    shopify_graphql(m, {"locationId": lid, "address": to_company_address_input(order,"billing"), "types": ["BILLING"]})
            except Exception:
                pass
            try:
                mu = """mutation($id: ID!, $input: CompanyLocationUpdateInput!) {
                  companyLocationUpdate(id: $id, input: $input) {
                    companyLocation { id name } userErrors { field message } } }"""
                shopify_graphql(mu, {"id": lid, "input": {"name": desired_name}})
            except Exception:
                pass
            _location_id_cache[key] = lid
            return lid

    m = """mutation($companyId: ID!, $input: CompanyLocationInput!) {
      companyLocationCreate(companyId: $companyId, input: $input) {
        companyLocation { id name } userErrors { field message } } }"""
    loc_input = {
        "name": desired_name,
        "shippingAddress": to_company_address_input(order, "shipping"),
        "billingSameAsShipping": not bool(order.get("billToAddress1")),
    }
    if order.get("billToAddress1"):
        loc_input["billingAddress"] = to_company_address_input(order, "billing")
    data2 = shopify_graphql(m, {"companyId": company_id, "input": loc_input})
    errs = (((data2.get("data", {}) or {}).get("companyLocationCreate", {}) or {}).get("userErrors", []) or [])
    if errs:
        return None
    lid = (((data2.get("data", {}) or {}).get("companyLocationCreate", {}) or {}).get("companyLocation", {}) or {}).get("id")
    if lid:
        _location_id_cache[key] = lid
    return lid

def iterate_company_contacts(company_id):
    q = """
    query($id: ID!, $after: String) {
      company(id: $id) {
        contacts(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes { id customer { id } }
        }
      }
    }"""
    after = None
    while True:
        d = shopify_graphql(q, {"id": company_id, "after": after})
        contacts = (((d.get("data", {}) or {}).get("company", {}) or {}).get("contacts", {}) or {})
        for n in (contacts.get("nodes") or []):
            yield (n.get("id"), (n.get("customer") or {}).get("id"))
        pi = contacts.get("pageInfo") or {}
        if pi.get("hasNextPage"):
            after = pi.get("endCursor")
        else:
            break

def get_or_create_matching_contact(company_id, customer_id_numeric):
    target_gid = to_gid("Customer", customer_id_numeric)
    for cid, cust_gid in iterate_company_contacts(company_id):
        if cust_gid == target_gid:
            return cid
    m = """
    mutation($companyId: ID!, $customerId: ID!) {
      companyAssignCustomerAsContact(companyId: $companyId, customerId: $customerId) {
        companyContact { id } userErrors { field message }
      }
    }"""
    out = shopify_graphql(m, {"companyId": company_id, "customerId": target_gid})
    payload = (out.get("data", {}) or {}).get("companyAssignCustomerAsContact", {}) or {}
    contact = (payload.get("companyContact") or {})
    if contact.get("id"):
        return contact["id"]
    errs = payload.get("userErrors", []) or []
    if any("already associated" in (e.get("message", "").lower()) for e in errs):
        for cid, cust_gid in iterate_company_contacts(company_id):
            if cust_gid == target_gid:
                return cid
    return None

def get_company_role_id(company_id, role_name="Ordering only"):
    key = (company_id, role_name)
    if key in _role_id_cache:
        return _role_id_cache[key]
    q = """query($id: ID!) { company(id: $id) { contactRoles(first: 20) { nodes { id name } } } }"""
    d = shopify_graphql(q, {"id": company_id})
    nodes = ((((d.get("data", {}) or {}).get("company", {}) or {}).get("contactRoles", {}) or {}).get("nodes", []) or [])
    for n in nodes:
        if (n.get("name") or "").strip().lower() == role_name.strip().lower():
            _role_id_cache[key] = n.get("id")
            return n.get("id")
    return None

def grant_ordering_permission(company_contact_id, company_location_id, company_id):
    role_id = get_company_role_id(company_id, "Ordering only")
    if not (role_id and company_contact_id and company_location_id):
        return
    m = """
    mutation($companyContactId: ID!, $companyContactRoleId: ID!, $companyLocationId: ID!) {
      companyContactAssignRole(
        companyContactId: $companyContactId,
        companyContactRoleId: $companyContactRoleId,
        companyLocationId: $companyLocationId
      ) {
        companyContactRoleAssignment { id } userErrors { field message }
      }
    }"""
    out = shopify_graphql(m, {
        "companyContactId": company_contact_id,
        "companyContactRoleId": role_id,
        "companyLocationId": company_location_id,
    })
    errs = (((out.get("data", {}) or {}).get("companyContactAssignRole", {}) or {}).get("userErrors", []) or [])
    if errs and not any("already" in (e.get("message", "").lower()) for e in errs):
        pass  # non-blocking

# -------------------- Excel Loader --------------------
def _normalize_sku(val):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    s = str(val).strip()
    if re.fullmatch(r"\d+\.0", s):
        return s[:-2]
    return s

ALIASES = {
    "poNumber": ["poNumber"],
    "company": ["companyName"],
    "billToName": ["billToName"],
    "billToAddress1": ["billToAddress1"],
    "billToAddress2": [],
    "billToCity": ["billToCity"],
    "billToState": ["billToState"],
    "billToZip": ["billToZip"],
    "billToCountry": ["billToCountry"],
    "billToPhone": [],
    "billToEmail": ["billToEmail"],
    "shipToName": ["shipToName"],
    "shipToAddress1": ["shipToAddress1"],
    "shipToAddress2": [],
    "shipToCity": ["shipToCity"],
    "shipToState": ["shipToState"],
    "shipToZip": ["shipToZip"],
    "shipToCountry": ["shipToCountry"],
    "shipToPhone": [],
    "shipToEmail": ["shipToEmail"],
    "buyerFirstName": ["buyerFirstName"],
    "buyerLastName": ["buyerLastName"],
    "shippingMethod": [],
    "specialInstructions": ["specialInstructions"],
    "shipDate": ["shipDate"],
    "itemNumber": ["Item #"],
    "name": ["Description"],
    "quantity": ["Quantity"],
    "unitPrice": ["Unit Cost"],
}

REQUIRED_COLUMNS = ["poNumber", "Item #", "Quantity"]

def _alias_get(row, aliases, default=None):
    for a in aliases:
        if a and a in row and pd.notna(row[a]) and str(row[a]).strip() != "":
            return row[a]
    return default

def validate_excel(path):
    """Returns (is_valid, errors, warnings, column_list)"""
    errors = []
    warnings = []
    try:
        df = pd.read_excel(str(path), sheet_name="Flat File", dtype=str)
    except Exception as e:
        return False, [f"Could not read 'Flat File' sheet: {e}"], [], []

    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)

    missing_required = []
    for req in REQUIRED_COLUMNS:
        if req not in cols:
            missing_required.append(req)
    if missing_required:
        errors.append(f"Missing required columns: {', '.join(missing_required)}")

    optional_checks = {
        "billToAddress1": "Billing address",
        "shipToAddress1": "Shipping address",
        "Unit Cost": "Unit Cost (prices)",
        "Description": "Item descriptions",
    }
    for col, label in optional_checks.items():
        if col not in cols:
            warnings.append(f"{label} column '{col}' not found — will use defaults")

    if not errors:
        df_clean = df[df["poNumber"].notna() & (df["poNumber"].str.strip() != "")]
        if len(df_clean) == 0:
            errors.append("No rows with valid PO numbers found")

    return len(errors) == 0, errors, warnings, cols

def load_orders_from_excel(path, sheet_name="Flat File"):
    df = pd.read_excel(str(path), sheet_name=sheet_name, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    grouped = {}
    for _, row in df.iterrows():
        vrow = row.to_dict()
        po = _alias_get(vrow, ALIASES["poNumber"])
        if not po:
            continue
        if po not in grouped:
            grouped[po] = {
                "poNumber": str(po),
                "buyerFirstName": _alias_get(vrow, ALIASES["buyerFirstName"]),
                "buyerLastName": _alias_get(vrow, ALIASES["buyerLastName"]),
                "shipToEmail": _alias_get(vrow, ALIASES["shipToEmail"]),
                "billToEmail": _alias_get(vrow, ALIASES["billToEmail"]),
                "billToName": _alias_get(vrow, ALIASES["billToName"]) or _alias_get(vrow, ALIASES["company"]),
                "shipToName": _alias_get(vrow, ALIASES["shipToName"]) or _alias_get(vrow, ALIASES["company"]) or "Default",
                "shipToAddress1": _alias_get(vrow, ALIASES["shipToAddress1"]),
                "shipToAddress2": _alias_get(vrow, ALIASES["shipToAddress2"]),
                "shipToCity": _alias_get(vrow, ALIASES["shipToCity"]),
                "shipToState": _alias_get(vrow, ALIASES["shipToState"]),
                "shipToZip": _alias_get(vrow, ALIASES["shipToZip"]),
                "shipToCountry": _alias_get(vrow, ALIASES["shipToCountry"]) or "US",
                "shipToPhone": _alias_get(vrow, ALIASES["shipToPhone"]),
                "billToAddress1": _alias_get(vrow, ALIASES["billToAddress1"]),
                "billToAddress2": _alias_get(vrow, ALIASES["billToAddress2"]),
                "billToCity": _alias_get(vrow, ALIASES["billToCity"]),
                "billToState": _alias_get(vrow, ALIASES["billToState"]),
                "billToZip": _alias_get(vrow, ALIASES["billToZip"]),
                "billToCountry": _alias_get(vrow, ALIASES["billToCountry"]) or "US",
                "billToPhone": _alias_get(vrow, ALIASES["billToPhone"]),
                "shippingMethod": _alias_get(vrow, ALIASES["shippingMethod"]),
                "specialInstructions": _alias_get(vrow, ALIASES["specialInstructions"]),
                "shipDate": _alias_get(vrow, ALIASES["shipDate"]),
                "details": [],
            }

        raw_sku = _alias_get(vrow, ALIASES["itemNumber"])
        sku = _normalize_sku(raw_sku)
        name = _alias_get(vrow, ALIASES["name"])
        raw_qty = _alias_get(vrow, ALIASES["quantity"])
        try:
            qty = int(float(raw_qty)) if raw_qty is not None and str(raw_qty).strip() != "" else 0
        except Exception:
            qty = 0
        price = _alias_get(vrow, ALIASES["unitPrice"])

        if qty <= 0:
            continue
        if not (sku or (name and str(name).strip())):
            continue

        grouped[po]["details"].append({
            "itemNumber": sku,
            "name": str(name).strip() if name is not None else None,
            "quantity": int(qty),
            "unitPrice": price,
        })

    orders = [o for o in grouped.values() if o.get("details")]
    return orders

# -------------------- Address helpers --------------------
def to_mailing_address(order, kind):
    if kind == "billing":
        return {
            "firstName": order.get("buyerFirstName"),
            "lastName": order.get("buyerLastName"),
            "company": order.get("billToName"),
            "address1": order.get("billToAddress1"),
            "address2": order.get("billToAddress2"),
            "city": order.get("billToCity"),
            "province": order.get("billToState"),
            "zip": order.get("billToZip"),
            "country": normalize_country(order.get("billToCountry")) or "US",
            "phone": order.get("billToPhone"),
        }
    else:
        return {
            "firstName": order.get("buyerFirstName"),
            "lastName": order.get("buyerLastName"),
            "company": order.get("shipToName"),
            "address1": order.get("shipToAddress1"),
            "address2": order.get("shipToAddress2"),
            "city": order.get("shipToCity"),
            "province": order.get("shipToState"),
            "zip": order.get("shipToZip"),
            "country": normalize_country(order.get("shipToCountry")) or "US",
            "phone": order.get("shipToPhone"),
        }

# -------------------- Customer helpers --------------------
def _lc(s):
    return (s or "").strip().lower()

def find_customer_by_email(email):
    url = f"{SHOPIFY_BASE}/admin/api/{SHOPIFY_API_VERSION}/customers/search.json"
    r = requests.get(url, headers=shopify_rest_headers, params={"query": f"email:{email}"}, timeout=HTTP_TIMEOUT)
    if r.ok:
        for c in r.json().get("customers", []):
            if _lc(c.get("email")) == _lc(email):
                return c
    return None

def find_customer_by_name_company(first, last, company):
    terms = []
    if first:
        terms.append(f"first_name:{first}")
    if last:
        terms.append(f"last_name:{last}")
    if company:
        terms.append(f"company:'{company}'")
    if not terms:
        return None
    q = " ".join(terms)
    url = f"{SHOPIFY_BASE}/admin/api/{SHOPIFY_API_VERSION}/customers/search.json"
    r = requests.get(url, headers=shopify_rest_headers, params={"query": q}, timeout=HTTP_TIMEOUT)
    if r.ok:
        for c in r.json().get("customers", []):
            if _lc(c.get("first_name")) == _lc(first) and _lc(c.get("last_name")) == _lc(last) and _lc(c.get("company")) == _lc(company):
                return c
    return None

def create_or_find_customer(order, po_number):
    buyer_email = order.get("shipToEmail") or order.get("billToEmail") or None
    first_name = order.get("buyerFirstName")
    last_name = order.get("buyerLastName")
    billToName = order.get("billToName")

    customer = find_customer_by_email(buyer_email) if buyer_email else None
    if not customer:
        customer = find_customer_by_name_company(first_name, last_name, billToName)

    if customer:
        return customer["id"], False

    def _clean_addr(d):
        return {k: v for k, v in d.items() if v not in (None, "", [])}

    addresses = []
    bill_addr = _clean_addr({
        "address1": order.get("billToAddress1"), "address2": order.get("billToAddress2"),
        "city": order.get("billToCity"), "province": order.get("billToState"),
        "zip": order.get("billToZip"), "country": normalize_country(order.get("billToCountry")) or "US",
        "phone": order.get("billToPhone"), "company": billToName, "default": True,
    })
    if bill_addr.get("address1"):
        addresses.append(bill_addr)

    ship_addr = _clean_addr({
        "address1": order.get("shipToAddress1"), "address2": order.get("shipToAddress2"),
        "city": order.get("shipToCity"), "province": order.get("shipToState"),
        "zip": order.get("shipToZip"), "country": normalize_country(order.get("shipToCountry")) or "US",
        "phone": order.get("shipToPhone"), "company": order.get("shipToName"),
    })
    if ship_addr.get("address1"):
        addresses.append(ship_addr)

    import re as _re
    customer_body = {"first_name": first_name, "last_name": last_name, "tags": "excel-import", "company": billToName}
    if buyer_email and _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(buyer_email)):
        customer_body["email"] = buyer_email
    if addresses:
        customer_body["addresses"] = addresses

    create_url = f"{SHOPIFY_BASE}/admin/api/{SHOPIFY_API_VERSION}/customers.json"
    cr = requests.post(create_url, headers=shopify_rest_headers, json={"customer": customer_body}, timeout=HTTP_TIMEOUT)

    if cr.status_code == 422:
        err_json = cr.json() if cr.content else {"raw": cr.text}
        import json as _json
        reason = _json.dumps(err_json).lower()
        retry_body = dict(customer_body)
        if "email" in reason:
            retry_body.pop("email", None)
        if "phone" in reason:
            retry_body.pop("phone", None)
        cr = requests.post(create_url, headers=shopify_rest_headers, json={"customer": retry_body}, timeout=HTTP_TIMEOUT)

    if cr.status_code == 422:
        raise RuntimeError(f"Cannot create customer for PO {po_number}: {cr.json()}")

    cr.raise_for_status()
    return cr.json()["customer"]["id"], True

# -------------------- De-dupe --------------------
def _draft_exists_graphql(po):
    try:
        for pattern in (f'po_number:"{po}"', f"po_number:{po}"):
            q = """query($q: String!) { draftOrders(first: 1, query: $q) { edges { node { id poNumber } } } }"""
            res = shopify_graphql(q, {"q": pattern})
            edges = (((res.get("data", {}) or {}).get("draftOrders", {}) or {}).get("edges", []) or [])
            for e in edges:
                node = e.get("node", {}) or {}
                if norm_po(node.get("poNumber")) == norm_po(po):
                    return True
    except Exception:
        pass
    return False

def _rest_draft_exists(target_po_norm):
    base = f"{SHOPIFY_BASE}/admin/api/{SHOPIFY_API_VERSION}/draft_orders.json"
    fields = "id,po_number,tags,created_at"
    for status in ("open", "invoice_sent"):
        params = {"status": status, "limit": REST_PAGE_LIMIT, "fields": fields}
        url = base
        pages = 0
        while True:
            pages += 1
            try:
                r = requests.get(url, headers=shopify_rest_headers, params=params, timeout=HTTP_TIMEOUT)
                r.raise_for_status()
            except Exception:
                break
            for d in (r.json().get("draft_orders") or []):
                if norm_po(d.get("po_number")) == target_po_norm:
                    return True
            link = r.headers.get("Link") or ""
            if 'rel="next"' not in link:
                break
            try:
                next_part = [p for p in link.split(",") if 'rel="next"' in p][0]
                next_url = next_part[next_part.find("<")+1:next_part.find(">")]
                pu = urlparse(next_url)
                url = f"{pu.scheme}://{pu.netloc}{pu.path}"
                q = parse_qs(pu.query)
                params = {"status": status, "limit": REST_PAGE_LIMIT, "page_info": q.get("page_info", [""])[0]}
            except Exception:
                break
            if pages >= MAX_PAGES_PER_STATUS:
                break
            time.sleep(REST_PAGE_SLEEP)
    return False

def draft_po_exists_in_shopify(po):
    target = norm_po(po)
    try:
        q = """query($q: String!) { orders(first: 1, query: $q) { edges { node { id poNumber } } } }"""
        res = shopify_graphql(q, {"q": f"po_number:{po} -status:cancelled"})
        edges = (((res.get("data", {}) or {}).get("orders", {}) or {}).get("edges", []) or [])
        for e in edges:
            n = e.get("node", {}) or {}
            if norm_po(n.get("poNumber")) == target:
                return True
    except Exception:
        pass
    if _draft_exists_graphql(po):
        return True
    if REST_DEDUPE_ENABLED:
        return _rest_draft_exists(target)
    return False

def _rest_order_exists(target_po_norm):
    base = f"{SHOPIFY_BASE}/admin/api/{SHOPIFY_API_VERSION}/orders.json"
    fields = "id,po_number,cancelled_at,created_at"
    params = {"status": "any", "limit": REST_PAGE_LIMIT, "fields": fields}
    url = base
    pages = 0
    while True:
        pages += 1
        try:
            r = requests.get(url, headers=shopify_rest_headers, params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json() or {}
        except Exception:
            return False
        for o in (data.get("orders", []) or []):
            if o.get("cancelled_at"):
                continue
            po_raw = o.get("po_number") or o.get("poNumber") or ""
            if norm_po(po_raw) == target_po_norm:
                return True
        link = r.headers.get("Link") or r.headers.get("link") or ""
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not m:
            break
        url = m.group(1)
        params = {}
        if pages >= MAX_PAGES_PER_STATUS:
            break
        time.sleep(REST_PAGE_SLEEP)
    return False

def order_po_exists_in_shopify(po):
    target = norm_po(po)
    try:
        q = """query($q: String!) { orders(first: 1, query: $q) { edges { node { id poNumber cancelledAt } } } }"""
        for pattern in (f'po_number:"{po}" -status:cancelled', f"po_number:{po} -status:cancelled"):
            res = shopify_graphql(q, {"q": pattern})
            edges = (((res.get("data", {}) or {}).get("orders", {}) or {}).get("edges", []) or [])
            for e in edges:
                n = e.get("node", {}) or {}
                if n.get("cancelledAt"):
                    continue
                if norm_po(n.get("poNumber")) == target:
                    return True
    except Exception:
        pass
    if REST_DEDUPE_ENABLED:
        return _rest_order_exists(target)
    return False
