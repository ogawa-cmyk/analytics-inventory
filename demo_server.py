"""Analytics Inventory デモサーバー (ポート 8790)

実データを一切使わず、学習・セミナー用のダミーデータで動作します。
  起動: python demo_server.py
  URL:  http://127.0.0.1:8790
"""
from pathlib import Path
import json
import sys

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

# ─── Step 1: config パッチ（他モジュールより先に実施） ─────────────
import config
_DEMO = _HERE / "demo_data"
config.DATA_DIR         = _DEMO
config.DETAILS_DIR      = _DEMO / "details"
config.GTM_DETAILS_DIR  = _DEMO / "gtm_details"
config.SC_DETAILS_DIR   = _DEMO / "sc_details"
config.INVENTORY_PATH   = _DEMO / "inventory.json"
config.INDEXER_LOCK_PATH= _DEMO / "indexer.lock"
config.SERVER_PORT      = 8790

# ─── Step 2: デモデータ定義 ────────────────────────────────────────
COLLECTED = "2026-06-29T10:00:00+09:00"

# ── GA4 プロパティ一覧（inventoryに埋め込む summary） ──
PROPS = [
    # P1: ECサイト本番 → A (30+20+10+15+10+15=100)
    {"auth_email":"tanaka@demo.example.com","account_id":"9001","account_display_name":"株式会社デモ小売",
     "property_id":"300000001","property_name":"properties/300000001","display_name":"ECサイト本番",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2022-01-15T09:00:00+00:00","stream_count":1,"measurement_ids":["G-EC12345"],
     "key_event_count":5,"key_event_names":["purchase","add_to_cart","begin_checkout","view_item","generate_lead"],
     "custom_dimension_count":8,"custom_metric_count":2,"my_roles":["roles/analytics.admin"],
     "is_tracked":True,"sessions_7d":45230,"events_7d":321500,"is_ecommerce":True,
     "ecommerce_events_found":["purchase","add_to_cart","begin_checkout"],"data_api_ok":True,
     "data_api_error":None,"collected_at":COLLECTED},
    # P2: コーポレートサイト → B (30+20+0+0+10+12=72)
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000002","property_name":"properties/300000002","display_name":"コーポレートサイト",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2021-06-01T09:00:00+00:00","stream_count":1,"measurement_ids":["G-CORP222"],
     "key_event_count":3,"key_event_names":["contact_form","download_brochure","video_play"],
     "custom_dimension_count":0,"custom_metric_count":0,"my_roles":["roles/analytics.editor"],
     "is_tracked":True,"sessions_7d":3520,"events_7d":28400,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
    # P3: メディアブログ → B (30+20+0+0+10+8=68)
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000003","property_name":"properties/300000003","display_name":"メディアブログ",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2020-03-10T09:00:00+00:00","stream_count":1,"measurement_ids":["G-BLOG333"],
     "key_event_count":3,"key_event_names":["scroll_50","newsletter_signup","share_article"],
     "custom_dimension_count":0,"custom_metric_count":0,"my_roles":["roles/analytics.editor"],
     "is_tracked":True,"sessions_7d":920,"events_7d":7800,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
    # P4: 採用サイト → B (30+12+0+15+10+8=75)
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000004","property_name":"properties/300000004","display_name":"採用サイト",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2023-04-01T09:00:00+00:00","stream_count":1,"measurement_ids":["G-RECR444"],
     "key_event_count":2,"key_event_names":["entry_form","document_download"],
     "custom_dimension_count":3,"custom_metric_count":0,"my_roles":["roles/analytics.viewer"],
     "is_tracked":True,"sessions_7d":210,"events_7d":1420,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
    # P5: LPページ専用 → C (30+12+0+0+10+4=56)
    {"auth_email":"tanaka@demo.example.com","account_id":"9001","account_display_name":"株式会社デモ小売",
     "property_id":"300000005","property_name":"properties/300000005","display_name":"LPページ専用",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2024-02-20T09:00:00+00:00","stream_count":1,"measurement_ids":["G-LP55555"],
     "key_event_count":1,"key_event_names":["cv_form"],
     "custom_dimension_count":0,"custom_metric_count":0,"my_roles":["roles/analytics.editor"],
     "is_tracked":True,"sessions_7d":85,"events_7d":520,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
    # P6: 開発・テスト用 → D (0+12+0+15+10+0=37)  ← is_tracked=False
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000006","property_name":"properties/300000006","display_name":"開発・テスト用",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2023-09-01T09:00:00+00:00","stream_count":1,"measurement_ids":["G-DEV6666"],
     "key_event_count":2,"key_event_names":["test_event","debug_hit"],
     "custom_dimension_count":3,"custom_metric_count":0,"my_roles":["roles/analytics.admin"],
     "is_tracked":False,"sessions_7d":0,"events_7d":0,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
    # P7: 旧サイト移行中 → F (0+12+0+0+0+0=12)  ← is_tracked=False, stream_count=0
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000007","property_name":"properties/300000007","display_name":"旧サイト移行中",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2019-11-01T09:00:00+00:00","stream_count":0,"measurement_ids":[],
     "key_event_count":1,"key_event_names":["old_goal"],
     "custom_dimension_count":0,"custom_metric_count":0,"my_roles":["roles/analytics.editor"],
     "is_tracked":False,"sessions_7d":0,"events_7d":0,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
    # P8: ECサブドメイン（モバイル） → B (30+12+10+0+10+12=74)
    {"auth_email":"tanaka@demo.example.com","account_id":"9001","account_display_name":"株式会社デモ小売",
     "property_id":"300000008","property_name":"properties/300000008","display_name":"ECサブドメイン（モバイル）",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2022-08-01T09:00:00+00:00","stream_count":1,"measurement_ids":["G-ECSUB88"],
     "key_event_count":2,"key_event_names":["purchase","add_to_cart"],
     "custom_dimension_count":0,"custom_metric_count":0,"my_roles":["roles/analytics.admin"],
     "is_tracked":True,"sessions_7d":2580,"events_7d":18900,"is_ecommerce":True,
     "ecommerce_events_found":["purchase","add_to_cart"],"data_api_ok":True,
     "data_api_error":None,"collected_at":COLLECTED},
    # P9: APIエラー発生中 → D (0+12+0+15+10+0=37)  ← data_api_ok=False
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000009","property_name":"properties/300000009","display_name":"APIエラー発生中",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2023-12-01T09:00:00+00:00","stream_count":1,"measurement_ids":["G-APIERR9"],
     "key_event_count":2,"key_event_names":["contact","download"],
     "custom_dimension_count":5,"custom_metric_count":0,"my_roles":["roles/analytics.viewer"],
     "is_tracked":None,"sessions_7d":0,"events_7d":0,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":False,
     "data_api_error":"QuotaExceeded: 1日のAPIコール上限に達しました","collected_at":COLLECTED},
    # P10: 新規開設サイト → F (0+0+0+0+0+0=0)
    {"auth_email":"suzuki@demo.example.com","account_id":"9002","account_display_name":"デモエージェンシー",
     "property_id":"300000010","property_name":"properties/300000010","display_name":"新規開設サイト",
     "time_zone":"Asia/Tokyo","currency_code":"JPY","property_type":"PROPERTY_TYPE_ORDINARY",
     "create_time":"2026-05-01T09:00:00+00:00","stream_count":0,"measurement_ids":[],
     "key_event_count":0,"key_event_names":[],
     "custom_dimension_count":0,"custom_metric_count":0,"my_roles":["roles/analytics.admin"],
     "is_tracked":False,"sessions_7d":0,"events_7d":0,"is_ecommerce":False,
     "ecommerce_events_found":[],"data_api_ok":True,"data_api_error":None,"collected_at":COLLECTED},
]

# ── GTM コンテナ一覧 ──
CONTS = [
    # C1: EC本番GTM → A (live付き)
    {"auth_email":"tanaka@demo.example.com","account_id":"DM-ACCOUNT-01","account_name":"株式会社デモ小売 GTM",
     "container_id":"GTM-DEMO01","public_id":"GTM-DEMO01","name":"ECサイト本番",
     "usage_context":["WEB"],"tag_count":12,"trigger_count":8,"variable_count":10,
     "version_id":"5","ga4_measurement_ids":["G-EC12345"],"domain_name":["example-ec.co.jp"],
     "collected_at":COLLECTED},
    # C2: コーポレートGTM → A (live付き)
    {"auth_email":"suzuki@demo.example.com","account_id":"DM-ACCOUNT-02","account_name":"デモエージェンシー GTM",
     "container_id":"GTM-DEMO02","public_id":"GTM-DEMO02","name":"コーポレートサイト",
     "usage_context":["WEB"],"tag_count":8,"trigger_count":5,"variable_count":6,
     "version_id":"3","ga4_measurement_ids":["G-CORP222"],"domain_name":["example-corp.co.jp"],
     "collected_at":COLLECTED},
    # C3: ブログGTM → A (live無し、MIDあり: 85点)
    {"auth_email":"suzuki@demo.example.com","account_id":"DM-ACCOUNT-02","account_name":"デモエージェンシー GTM",
     "container_id":"GTM-DEMO03","public_id":"GTM-DEMO03","name":"メディアブログ",
     "usage_context":["WEB"],"tag_count":7,"trigger_count":4,"variable_count":5,
     "version_id":"2","ga4_measurement_ids":["G-BLOG333"],"domain_name":["blog.example-corp.co.jp"],
     "collected_at":COLLECTED},
    # C4: UA系タグ残存 → B (live付き: ua=6, paused=4/12=33%)
    {"auth_email":"tanaka@demo.example.com","account_id":"DM-ACCOUNT-01","account_name":"株式会社デモ小売 GTM",
     "container_id":"GTM-DEMO04","public_id":"GTM-DEMO04","name":"UA系タグ残存コンテナ",
     "usage_context":["WEB"],"tag_count":12,"trigger_count":6,"variable_count":8,
     "version_id":"7","ga4_measurement_ids":["G-EC12345"],"domain_name":["example-ec.co.jp"],
     "collected_at":COLLECTED},
    # C5: GA4未連携（旧運用） → C (live無し, version無し, MID無し, tags=5)
    {"auth_email":"suzuki@demo.example.com","account_id":"DM-ACCOUNT-02","account_name":"デモエージェンシー GTM",
     "container_id":"GTM-DEMO05","public_id":"GTM-DEMO05","name":"旧運用コンテナ",
     "usage_context":["WEB"],"tag_count":5,"trigger_count":3,"variable_count":4,
     "version_id":None,"ga4_measurement_ids":[],"domain_name":["legacy.example.co.jp"],
     "collected_at":COLLECTED},
    # C6: 空コンテナ → D (live無し, version無し, MID無し, tags=0)
    {"auth_email":"suzuki@demo.example.com","account_id":"DM-ACCOUNT-02","account_name":"デモエージェンシー GTM",
     "container_id":"GTM-DEMO06","public_id":"GTM-DEMO06","name":"空コンテナ（未設定）",
     "usage_context":[],"tag_count":0,"trigger_count":0,"variable_count":0,
     "version_id":None,"ga4_measurement_ids":[],"domain_name":[],
     "collected_at":COLLECTED},
    # C7: 新規設定途中 → C (live無し, version無し, MIDあり, tags=0, triggers=2)
    {"auth_email":"tanaka@demo.example.com","account_id":"DM-ACCOUNT-01","account_name":"株式会社デモ小売 GTM",
     "container_id":"GTM-DEMO07","public_id":"GTM-DEMO07","name":"新規LP用（設定中）",
     "usage_context":["WEB"],"tag_count":0,"trigger_count":2,"variable_count":0,
     "version_id":None,"ga4_measurement_ids":["G-SITE777"],"domain_name":["lp.example.co.jp"],
     "collected_at":COLLECTED},
    # C8: 多タグ・整理不足 → B (live付き: ua=6, paused=6/18=33%, no config)
    {"auth_email":"suzuki@demo.example.com","account_id":"DM-ACCOUNT-02","account_name":"デモエージェンシー GTM",
     "container_id":"GTM-DEMO08","public_id":"GTM-DEMO08","name":"多タグ統合コンテナ",
     "usage_context":["WEB"],"tag_count":18,"trigger_count":12,"variable_count":15,
     "version_id":"4","ga4_measurement_ids":["G-CORP222","G-BLOG333"],"domain_name":["example-corp.co.jp","blog.example-corp.co.jp"],
     "collected_at":COLLECTED},
    # C9: テスト環境 → A (live付き: small, all clean)
    {"auth_email":"tanaka@demo.example.com","account_id":"DM-ACCOUNT-01","account_name":"株式会社デモ小売 GTM",
     "container_id":"GTM-DEMO09","public_id":"GTM-DEMO09","name":"ステージング検証環境",
     "usage_context":["WEB"],"tag_count":3,"trigger_count":2,"variable_count":3,
     "version_id":"1","ga4_measurement_ids":["G-TEST999"],"domain_name":["stg.example-ec.co.jp"],
     "collected_at":COLLECTED},
    # C10: 廃止予定 → D (live無し, version=1, MID無し, tags=0)
    {"auth_email":"tanaka@demo.example.com","account_id":"DM-ACCOUNT-01","account_name":"株式会社デモ小売 GTM",
     "container_id":"GTM-DEMO10","public_id":"GTM-DEMO10","name":"廃止予定コンテナ",
     "usage_context":[],"tag_count":0,"trigger_count":0,"variable_count":0,
     "version_id":"1","ga4_measurement_ids":[],"domain_name":[],
     "collected_at":COLLECTED},
]

# ── SC サイト一覧 ──
# site_hash は hashlib.sha1(url.encode()).hexdigest()[:16] で計算済み
SC_SITES = [
    # SC1: EC本番 → A (sitemap=2,clicks=3200,imps=42000,CTR=7.6%,pos=5.2,q=35,p=48,ga4=yes)
    {"auth_email":"tanaka@demo.example.com","site_url":"https://example-ec.co.jp/",
     "site_hash":"cfddd32a0a6a3b42","site_type":"URL_PREFIX","domain":"example-ec.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":3200,"impressions_28d":42000,"ctr_28d":0.0762,"position_28d":5.2,
     "sitemap_count":2,"sitemap_errors":0,"top_query_count":35,"top_page_count":48,
     "collected_at":COLLECTED},
    # SC2: コーポレート → B (sitemap=1,clicks=320,imps=4000,CTR=0.8%,pos=25,q=20,p=15,ga4=yes)
    {"auth_email":"suzuki@demo.example.com","site_url":"https://example-corp.co.jp/",
     "site_hash":"4979b293dd72d160","site_type":"URL_PREFIX","domain":"example-corp.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":320,"impressions_28d":4000,"ctr_28d":0.0080,"position_28d":25.3,
     "sitemap_count":1,"sitemap_errors":0,"top_query_count":20,"top_page_count":15,
     "collected_at":COLLECTED},
    # SC3: ブログ → B (sitemap=1,clicks=220,imps=32000,CTR=0.7%,pos=15.8,q=15,p=12,ga4=no)
    {"auth_email":"suzuki@demo.example.com","site_url":"https://blog.example-corp.co.jp/",
     "site_hash":"3ce5de2b6aebad5e","site_type":"URL_PREFIX","domain":"blog.example-corp.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":220,"impressions_28d":32000,"ctr_28d":0.0069,"position_28d":15.8,
     "sitemap_count":1,"sitemap_errors":0,"top_query_count":15,"top_page_count":12,
     "collected_at":COLLECTED},
    # SC4: 採用サイト → B (sitemap=1,clicks=95,imps=8000,CTR=1.2%,pos=18,q=8,p=6,ga4=yes)
    {"auth_email":"suzuki@demo.example.com","site_url":"https://recruit.example-corp.co.jp/",
     "site_hash":"452a8c6fcad8b1a4","site_type":"URL_PREFIX","domain":"recruit.example-corp.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":95,"impressions_28d":8000,"ctr_28d":0.0119,"position_28d":18.1,
     "sitemap_count":1,"sitemap_errors":0,"top_query_count":8,"top_page_count":6,
     "collected_at":COLLECTED},
    # SC5: LP → B (sitemap=1,clicks=25,imps=600,CTR=4.2%,pos=22,q=6,p=5,ga4=yes)
    {"auth_email":"tanaka@demo.example.com","site_url":"https://lp.example.co.jp/",
     "site_hash":"f1de99ccf1c16f57","site_type":"URL_PREFIX","domain":"lp.example.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":25,"impressions_28d":600,"ctr_28d":0.0417,"position_28d":22.4,
     "sitemap_count":1,"sitemap_errors":0,"top_query_count":6,"top_page_count":5,
     "collected_at":COLLECTED},
    # SC6: 新規開設（データなし） → F (sitemap=0,clicks=0,imps=0,ga4=yes via P10)
    {"auth_email":"suzuki@demo.example.com","site_url":"sc-domain:example-new.co.jp",
     "site_hash":"4946190357cf8d1a","site_type":"DOMAIN","domain":"example-new.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":0,"impressions_28d":0,"ctr_28d":0.0,"position_28d":None,
     "sitemap_count":0,"sitemap_errors":0,"top_query_count":0,"top_page_count":0,
     "collected_at":COLLECTED},
    # SC7: ECモバイルサブ → A (sitemap=1,clicks=580,imps=12000,CTR=4.8%,pos=8.5,q=18,p=16,ga4=yes)
    {"auth_email":"tanaka@demo.example.com","site_url":"https://mobile.example-ec.co.jp/",
     "site_hash":"787055513180cc5a","site_type":"URL_PREFIX","domain":"mobile.example-ec.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":580,"impressions_28d":12000,"ctr_28d":0.0483,"position_28d":8.5,
     "sitemap_count":1,"sitemap_errors":0,"top_query_count":18,"top_page_count":16,
     "collected_at":COLLECTED},
    # SC8: 旧ECサイト → C (sitemap=0,clicks=180,imps=3500,CTR=5.1%,pos=9.5,q=12,p=8,ga4=no)
    {"auth_email":"tanaka@demo.example.com","site_url":"https://old-ec.co.jp/",
     "site_hash":"d887c814df6ba729","site_type":"URL_PREFIX","domain":"old-ec.co.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":180,"impressions_28d":3500,"ctr_28d":0.0514,"position_28d":9.5,
     "sitemap_count":0,"sitemap_errors":0,"top_query_count":12,"top_page_count":8,
     "collected_at":COLLECTED},
    # SC9: パートナーサイト → B (sitemap=1,clicks=580,imps=12000,CTR=4.8%,pos=11.2,q=18,p=16,ga4=no)
    {"auth_email":"suzuki@demo.example.com","site_url":"https://partner.example.jp/",
     "site_hash":"d219d3c70b43dbe1","site_type":"URL_PREFIX","domain":"partner.example.jp",
     "permission_level":"siteFullUser","perf_ok":True,
     "clicks_28d":580,"impressions_28d":12000,"ctr_28d":0.0483,"position_28d":11.2,
     "sitemap_count":1,"sitemap_errors":0,"top_query_count":18,"top_page_count":16,
     "collected_at":COLLECTED},
    # SC10: サービスサイト → F (sitemap=0,clicks=0,imps=280,CTR=0,pos=35.2,ga4=no)
    {"auth_email":"suzuki@demo.example.com","site_url":"https://service.example.jp/",
     "site_hash":"13860a478f0bd3e9","site_type":"URL_PREFIX","domain":"service.example.jp",
     "permission_level":"siteOwner","perf_ok":True,
     "clicks_28d":0,"impressions_28d":280,"ctr_28d":0.0,"position_28d":35.2,
     "sitemap_count":0,"sitemap_errors":0,"top_query_count":0,"top_page_count":0,
     "collected_at":COLLECTED},
]

INVENTORY = {
    "generated_at": COLLECTED,
    "accounts_scanned": ["tanaka@demo.example.com", "suzuki@demo.example.com"],
    "properties": PROPS,
    "gtm_containers": CONTS,
    "sc_sites": SC_SITES,
    "errors": [],
}

# ── GTM ライブデータ（gtm_details/*.json）──
# C1: EC本番 ← ga4config×1, ua=0, paused=0/12
def _mk_tags(ga4_config_n, gaawe_n, ua_n, html_n, paused_n, total):
    tags = []
    tid = 1
    for i in range(ga4_config_n):
        tags.append({"tagId": str(tid), "name": f"GA4設定タグ", "type": "gaawc",
                     "parameter": [{"type":"template","key":"trackingId","value":"G-EC12345"}]})
        tid += 1
    for i in range(gaawe_n):
        tags.append({"tagId": str(tid), "name": f"GA4イベント{i+1}", "type": "gaawe"}); tid += 1
    for i in range(ua_n):
        tags.append({"tagId": str(tid), "name": f"UAタグ{i+1}", "type": "ua"}); tid += 1
    for i in range(html_n):
        tags.append({"tagId": str(tid), "name": f"カスタムHTML{i+1}", "type": "html"}); tid += 1
    # Fill remaining tags
    remaining = total - len(tags)
    for i in range(remaining):
        tags.append({"tagId": str(tid), "name": f"その他タグ{i+1}", "type": "img"}); tid += 1
    # Mark paused
    for i in range(paused_n):
        if i < len(tags):
            tags[-(i+1)]["paused"] = True
    return tags

def _mk_triggers(n):
    return [{"triggerId": str(i+1), "name": f"トリガー{i+1}", "type": "pageview"} for i in range(n)]

def _mk_vars(n):
    return [{"variableId": str(i+1), "name": f"変数{i+1}", "type": "v"} for i in range(n)]

GTM_LIVE = {
    "GTM-DEMO01": {  # EC本番: A→ ga4config=1, ua=0, paused=0/12
        "path": "accounts/DM01/containers/DEMO01/versions/5",
        "accountId": "DM01", "containerId": "DEMO01", "containerVersionId": "5",
        "name": "ECサイト本番 v5",
        "tag": _mk_tags(1, 5, 0, 1, 0, 12),
        "trigger": _mk_triggers(8),
        "variable": _mk_vars(10),
    },
    "GTM-DEMO02": {  # コーポレート: A → ga4config=1, ua=0, paused=0/8
        "path": "accounts/DM02/containers/DEMO02/versions/3",
        "accountId": "DM02", "containerId": "DEMO02", "containerVersionId": "3",
        "name": "コーポレートサイト v3",
        "tag": _mk_tags(1, 3, 0, 1, 0, 8),
        "trigger": _mk_triggers(5),
        "variable": _mk_vars(6),
    },
    "GTM-DEMO04": {  # UA残存: B → ga4config=0, ua=6, paused=4/12
        "path": "accounts/DM01/containers/DEMO04/versions/7",
        "accountId": "DM01", "containerId": "DEMO04", "containerVersionId": "7",
        "name": "UA系タグ残存 v7",
        "tag": _mk_tags(0, 2, 6, 0, 4, 12),
        "trigger": _mk_triggers(6),
        "variable": _mk_vars(8),
    },
    "GTM-DEMO08": {  # 多タグ整理不足: B → ga4config=0, ua=6, paused=6/18
        "path": "accounts/DM02/containers/DEMO08/versions/4",
        "accountId": "DM02", "containerId": "DEMO08", "containerVersionId": "4",
        "name": "多タグ統合コンテナ v4",
        "tag": _mk_tags(0, 4, 6, 2, 6, 18),
        "trigger": _mk_triggers(12),
        "variable": _mk_vars(15),
    },
    "GTM-DEMO09": {  # テスト環境: A → ga4config=1, ua=0, paused=0/3
        "path": "accounts/DM01/containers/DEMO09/versions/1",
        "accountId": "DM01", "containerId": "DEMO09", "containerVersionId": "1",
        "name": "ステージング検証 v1",
        "tag": _mk_tags(1, 1, 0, 0, 0, 3),
        "trigger": _mk_triggers(2),
        "variable": _mk_vars(3),
    },
}

# ── GA4 プロパティ詳細（details/*.json）── ※ streams.default_uri がSC紐付けに使われる
def _mk_property_detail(p, stream_uri, stream_mid=None):
    pid = p["property_id"]
    streams = []
    if stream_uri:
        streams.append({
            "name": f"properties/{pid}/dataStreams/1{pid[-4:]}",
            "type": "WEB_DATA_STREAM",
            "display_name": p["display_name"] + " ウェブストリーム",
            "create_time": p["create_time"],
            "default_uri": stream_uri,
            "measurement_id": stream_mid or (p["measurement_ids"][0] if p["measurement_ids"] else ""),
        })
    ke_names = p.get("key_event_names") or []
    return {
        "summary": p,
        "streams": streams,
        "key_events": [{"name": f"properties/{pid}/keyEvents/{n}", "event_name": n,
                         "counting_method": "ONCE_PER_EVENT"} for n in ke_names],
        "custom_dimensions": [{"parameterName": f"dim_{i}", "display_name": f"カスタムディメンション{i+1}",
                                "scope": "EVENT"} for i in range(p.get("custom_dimension_count") or 0)],
        "custom_metrics": [],
        "access_bindings": [{"principal": p["auth_email"], "roles": p["my_roles"]}],
        "events": [],
    }

# ストリームのURIをプロパティごとに設定（SC↔GA4ドメイン紐付けに使用）
_STREAM_URIS = {
    "300000001": "https://example-ec.co.jp",
    "300000002": "https://example-corp.co.jp",
    "300000003": "https://blog.example-corp.co.jp",
    "300000004": "https://recruit.example-corp.co.jp",
    "300000005": "https://lp.example.co.jp",
    "300000006": "https://dev-staging.example.co.jp",  # SCサイトなし
    "300000007": None,                                  # ストリームなし
    "300000008": "https://mobile.example-ec.co.jp",
    "300000009": "https://api-internal.example.jp",    # SCサイトなし
    "300000010": "https://example-new.co.jp",
}

PROPERTY_DETAILS = {
    p["property_id"]: _mk_property_detail(p, _STREAM_URIS.get(p["property_id"]))
    for p in PROPS
}

# ── SC サイト詳細（sc_details/*.json）──
def _mk_sc_queries(n, site_domain):
    kws = ["○○通販","○○ 購入","○○ 価格","○○ 比較","○○ レビュー","○○ 口コミ",
           "○○ 安い","○○ 送料","○○ 返品","○○ セール","○○ クーポン","○○ 会員",
           "○○ 公式","○○ 最安値","○○ おすすめ","○○ 評判","○○ 品質","○○ 人気",
           "○○ 定期便","○○ ポイント","○○ 限定","○○ 新商品","○○ 在庫","○○ 注文方法"]
    kws = [k.replace("○○", site_domain.split(".")[0]) for k in kws]
    return [{"query": kws[i % len(kws)], "clicks": max(1, 100-i*3),
             "impressions": max(5, 1000-i*25), "ctr": 0.05-(i*0.002), "position": 3+(i*0.8)}
            for i in range(min(n, len(kws)))]

def _mk_sc_pages(n, site_url):
    pages = ["/", "/products/", "/products/item-a/", "/products/item-b/", "/cart/",
             "/about/", "/contact/", "/faq/", "/news/", "/campaign/",
             "/member/", "/guide/", "/brand/", "/store/", "/sale/",
             "/blog/", "/review/", "/size-guide/", "/shipping/", "/privacy/"]
    return [{"page": site_url.rstrip("/") + pages[i % len(pages)],
             "clicks": max(1, 200-i*8), "impressions": max(5, 2000-i*70),
             "ctr": 0.06-(i*0.002), "position": 2+(i*0.6)}
            for i in range(min(n, len(pages)))]

def _mk_sitemap(site_url, count, errors=0):
    sitemaps = []
    for i in range(count):
        sitemaps.append({
            "path": site_url.rstrip("/") + ("/sitemap.xml" if i == 0 else f"/sitemap{i+1}.xml"),
            "type": "sitemap", "warnings": 0, "errors": errors if i == 0 else 0,
            "is_pending": False, "is_sitemaps_index": i == 0 and count > 1,
        })
    return sitemaps

SC_DETAILS = {}
for _sc in SC_SITES:
    _hash = _sc["site_hash"]
    _n_q = _sc["top_query_count"]
    _n_p = _sc["top_page_count"]
    _sm = _mk_sitemap(_sc["site_url"], _sc["sitemap_count"], _sc["sitemap_errors"])
    SC_DETAILS[_hash] = {
        "summary": _sc,
        "queries": _mk_sc_queries(_n_q, _sc["domain"]),
        "pages": _mk_sc_pages(_n_p, _sc["site_url"]),
        "devices": [
            {"device": "MOBILE",  "clicks": int(_sc["clicks_28d"]*0.60), "impressions": int(_sc["impressions_28d"]*0.55),
             "ctr": (_sc["ctr_28d"] or 0)*1.05, "position": (_sc["position_28d"] or 0)*1.02},
            {"device": "DESKTOP", "clicks": int(_sc["clicks_28d"]*0.38), "impressions": int(_sc["impressions_28d"]*0.42),
             "ctr": (_sc["ctr_28d"] or 0)*0.92, "position": (_sc["position_28d"] or 0)*0.96},
            {"device": "TABLET",  "clicks": int(_sc["clicks_28d"]*0.02), "impressions": int(_sc["impressions_28d"]*0.03),
             "ctr": (_sc["ctr_28d"] or 0)*0.70, "position": (_sc["position_28d"] or 0)*1.10},
        ],
        "countries": [
            {"country": "Japan",         "clicks": int(_sc["clicks_28d"]*0.92), "impressions": int(_sc["impressions_28d"]*0.90),
             "ctr": (_sc["ctr_28d"] or 0)*0.98},
            {"country": "United States", "clicks": int(_sc["clicks_28d"]*0.04), "impressions": int(_sc["impressions_28d"]*0.05),
             "ctr": (_sc["ctr_28d"] or 0)*0.75},
            {"country": "Other",         "clicks": int(_sc["clicks_28d"]*0.04), "impressions": int(_sc["impressions_28d"]*0.05),
             "ctr": (_sc["ctr_28d"] or 0)*0.70},
        ],
        "sitemaps": _sm,
    }

# ─── Step 3: データ書き込み ────────────────────────────────────────
def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _ensure_demo_data():
    if (_DEMO / "inventory.json").exists():
        return
    print("[demo] デモデータを生成中...")
    # Create required subdirectories
    for d in ["details", "gtm_details", "sc_details", "snapshots", "ai_runs", "ai_gtm_runs"]:
        (_DEMO / d).mkdir(parents=True, exist_ok=True)
    # Write inventory
    _write(_DEMO / "inventory.json", INVENTORY)
    # Write annotations (empty)
    _write(_DEMO / "annotations.json", {"properties": {}, "containers": {}})
    # Write property details
    for pid, detail in PROPERTY_DETAILS.items():
        _write(_DEMO / "details" / f"{pid}.json", detail)
    # Write GTM live details
    for cid, live in GTM_LIVE.items():
        _write(_DEMO / "gtm_details" / f"{cid}.json", live)
    # Write SC details
    for site_hash, sc_detail in SC_DETAILS.items():
        _write(_DEMO / "sc_details" / f"{site_hash}.json", sc_detail)
    print(f"[demo] 完了: {_DEMO}")

_ensure_demo_data()

# ─── Step 4: サーバーインポートと起動 ────────────────────────────────
import server  # noqa: E402  (config already patched above)

# Gunicorn / Cloud Run 用: `gunicorn demo_server:app`
app = server.app

if __name__ == "__main__":
    print("=" * 55)
    print("  Analytics Inventory デモサーバー")
    print("  URL: http://127.0.0.1:8790")
    print("  データ: セミナー用ダミーデータ (実データなし)")
    print("=" * 55)
    app.run(host="127.0.0.1", port=8790, debug=False)
