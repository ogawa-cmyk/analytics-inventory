"""GA4 Inventory configuration."""
from pathlib import Path

VERSION = "1.2.0"
GITHUB_REPO = "ogawa-cmyk/analytics-inventory"

ROOT = Path(__file__).parent
TOKENS_DIR = ROOT / "tokens"
DATA_DIR = ROOT / "data"
DETAILS_DIR = DATA_DIR / "details"
GTM_DETAILS_DIR = DATA_DIR / "gtm_details"
SC_DETAILS_DIR = DATA_DIR / "sc_details"
INVENTORY_PATH = DATA_DIR / "inventory.json"
INDEXER_LOCK_PATH = DATA_DIR / "indexer.lock"
CLIENT_SECRET_PATH = ROOT / "client_secret.json"

for p in (TOKENS_DIR, DATA_DIR, DETAILS_DIR, GTM_DETAILS_DIR, SC_DETAILS_DIR):
    p.mkdir(parents=True, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/analytics.manage.users.readonly",
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

SERVER_PORT = 8788

# デモモード: True にすると書き込み系操作（データ再収集・通知設定の保存/送信）を無効化する。
# demo_server.py が起動時に True へパッチする。
DEMO_MODE = False

DATA_FRESHNESS_DAYS = 7
ECOMMERCE_LOOKBACK_DAYS = 30
EVENTS_LOOKBACK_DAYS = 30

ECOMMERCE_EVENTS = [
    "purchase",
    "begin_checkout",
    "add_to_cart",
    "view_item",
    "add_payment_info",
    "add_shipping_info",
    "refund",
]
