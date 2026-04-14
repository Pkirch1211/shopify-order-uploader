# Shopify B2B Order Uploader

A web app that lets your team upload an Excel "Flat File" and create draft or live orders in your Shopify B2B store.

## Features

- Drag-and-drop Excel upload (`.xlsx`)
- Validates file structure before doing anything
- Shows a full preview of POs and line items before submitting
- Choice: **Draft Orders** or **Live Orders** (PENDING)
- Real-time progress log during processing
- Duplicate PO detection (checks existing drafts + orders)
- B2B company / location / contact wiring
- Assortment parent SKU expansion
- Price matching with discount fallback (draft mode)
- Results table + CSV download after processing

---

## Deployment to Railway (Recommended)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "initial commit"
# create a repo on github.com, then:
git remote add origin https://github.com/YOUR_ORG/shopify-order-uploader.git
git push -u origin main
```

### 2. Deploy on Railway

1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
2. Select your repo
3. Railway auto-detects Python + the `Procfile`
4. Go to **Variables** and add:

| Variable | Value |
|---|---|
| `SHOPIFY_TOKEN` | `shpat_xxxxx` |
| `SHOPIFY_STORE` | `your-store.myshopify.com` |
| `SHOP_CURRENCY` | `USD` |
| `FLASK_SECRET` | any long random string |

5. Deploy — Railway gives you a URL like `https://your-app.up.railway.app`
6. Share that URL with your team

### 3. (Optional) Password protect

To restrict access, add this env var:

```
APP_PASSWORD=yourteampassword
```

And add basic auth middleware to `app.py` (see note at bottom).

---

## Local Development

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in .env
cp .env.example .env
# edit .env with your Shopify credentials

# 4. Run
python app.py
# Open http://localhost:5000
```

---

## File Structure

```
shopify-order-uploader/
├── app.py                  # Flask web server + API routes
├── shopify_core.py         # Shared Shopify logic (de-dupe, B2B, loader)
├── draft_processor.py      # Draft order creation (Script 1 logic)
├── order_processor.py      # Live order creation (Script 2 logic)
├── templates/
│   └── index.html          # Single-page UI
├── requirements.txt
├── Procfile                # For Railway/Heroku
├── .env.example
└── .gitignore
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SHOPIFY_TOKEN` | ✅ | Shopify Admin API access token (`shpat_...`) |
| `SHOPIFY_STORE` | ✅ | Store domain e.g. `mystore.myshopify.com` |
| `SHOP_CURRENCY` | Optional | Default: `USD` |
| `FLASK_SECRET` | Optional | Session secret key (auto-generated if missing) |
| `PORT` | Optional | Default: `5000` |

---

## Assortment Map

The assortment expansion map is in `shopify_core.py` under `ASSORTMENT_MAP`. Edit that dict to add/remove/change parent SKUs and their child expansions. No UI editor — change it in code and redeploy.

---

## Adding Password Protection (Optional)

In `app.py`, add before the routes:

```python
from functools import wraps
from flask import request, Response

APP_PASSWORD = os.getenv("APP_PASSWORD")

def require_password(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.password != APP_PASSWORD:
            return Response('Auth required', 401, {'WWW-Authenticate': 'Basic realm="Order Uploader"'})
        return f(*args, **kwargs)
    return decorated

# Then add @require_password to each route
```

---

## Notes

- Processing runs in a background thread per upload — safe for 4 concurrent users
- Job state is in-memory (resets on server restart) — fine for this use case
- Uploaded files are stored temporarily in `uploads/` and not auto-cleaned (add a cron if needed)
- The variant cache is cleared on each new job to avoid stale data
