"""
GA4-Inventory 操作マニュアル PDF生成スクリプト
Usage: python scripts/build_manual_pdf.py
出力: static/manual.pdf
"""
import os
import sys
import io
import time
import pathlib
# Windows で utf-8 出力を強制
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright
from fpdf import FPDF

BASE_URL = "http://127.0.0.1:8790"
OUT_DIR  = pathlib.Path(__file__).parent.parent / "static"
IMG_DIR  = OUT_DIR / "manual_shots"
OUT_PDF  = OUT_DIR / "manual.pdf"
IMG_DIR.mkdir(parents=True, exist_ok=True)

# ---- スクリーンショット定義 ----
SHOTS = [
    ("01_home",           "/",                          1920, 900),
    ("02_home_alerts",    "/",                          1920, 900),   # アラート部分
    ("03_ga4_list",       "/properties",                1920, 900),
    ("04_ga4_detail",     "/property/300000001",        1920, 2000),
    ("05_gtm_list",       "/gtm",                       1920, 900),
    ("06_gtm_detail",     "/gtm/GTM-DEMO01/tag",        1920, 1200),
    ("07_sc_list",        "/search-console",            1920, 900),
    ("08_sc_detail",      "/sc/cfddd32a0a6a3b42",       1920, 1400),
    ("09_usage",          "/usage",                     1920, 900),
]

def take_screenshots():
    print("[INFO] スクリーンショット取得中...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--force-device-scale-factor=1.25"]
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1.25,
        )
        page = ctx.new_page()

        for name, path, width, height in SHOTS:
            url = BASE_URL + path
            print(f"  -> {name}: {url}")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(url, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(800)

            # ホームのアラート部分は折りたたみを開く
            if name == "02_home_alerts":
                try:
                    page.locator("details").first.evaluate("el => el.open = true")
                    page.wait_for_timeout(300)
                except Exception:
                    pass

            # GA4詳細はページ上部のみキャプチャ
            clip = None
            if name == "04_ga4_detail":
                clip = {"x": 0, "y": 0, "width": width, "height": 1200}

            out = str(IMG_DIR / f"{name}.png")
            page.screenshot(path=out, full_page=(clip is None), clip=clip)
            print(f"     saved: {out}")

        browser.close()
    print("✅ スクリーンショット完了\n")


# ---- PDF ビルド ----
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
]

class ManualPDF(FPDF):
    FONT_NAME = "JPN"

    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=18)
        self._add_japanese_font()
        self.set_font(self.FONT_NAME, size=10)

    def _add_japanese_font(self):
        for path in FONT_CANDIDATES:
            if os.path.exists(path):
                self.add_font(self.FONT_NAME, fname=path, uni=True)
                print(f"  フォント: {path}")
                return
        # フォールバック: ASCII のみ
        self.add_font(self.FONT_NAME, fname=None)
        print("  警告: 日本語フォントが見つかりません。ASCII のみ表示されます。")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font(self.FONT_NAME, size=8)
        self.set_text_color(150)
        self.cell(0, 5, "GA4・GTM・SC 計測管理ツール 操作マニュアル", align="L")
        self.ln(1)
        self.set_draw_color(200)
        self.set_line_width(0.3)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)
        self.set_text_color(0)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-14)
        self.set_font(self.FONT_NAME, size=8)
        self.set_text_color(150)
        self.cell(0, 5, f"- {self.page_no() - 1} -", align="C")
        self.set_text_color(0)

    def cover_page(self):
        self.add_page()
        self.set_fill_color(18, 73, 148)
        self.rect(0, 0, 210, 60, style="F")
        self.set_y(18)
        self.set_font(self.FONT_NAME, size=22)
        self.set_text_color(255)
        self.cell(0, 10, "GA4・GTM・SC", align="C")
        self.ln(10)
        self.set_font(self.FONT_NAME, size=16)
        self.cell(0, 8, "計測管理ツール 操作マニュアル", align="C")
        self.set_text_color(0)

        self.set_y(70)
        self.set_font(self.FONT_NAME, size=11)
        lines = [
            "本マニュアルでは、複数クライアントのGA4・GTM・Search Console を",
            "一元管理するツールの基本操作から活用方法までを解説します。",
        ]
        for l in lines:
            self.cell(0, 7, l, align="C")
            self.ln()

        self.set_y(100)
        self._toc()

        self.set_y(-30)
        self.set_font(self.FONT_NAME, size=9)
        self.set_text_color(120)
        self.cell(0, 5, "© HAPPY ANALYTICS  /  Ogawa Taku", align="C")
        self.set_text_color(0)

    def _toc(self):
        self.set_fill_color(245, 248, 255)
        x, y = 20, self.get_y()
        self.set_xy(x, y)
        self.set_font(self.FONT_NAME, size=12)
        self.cell(170, 8, "目次", border=0)
        self.ln(10)
        items = [
            ("1", "ツール概要とホーム画面"),
            ("2", "GA4 プロパティ管理"),
            ("3", "GTM コンテナ管理"),
            ("4", "Search Console 管理"),
            ("5", "ヘルススコアの見方"),
            ("6", "アラートと対応フロー"),
            ("7", "AI 分析の使い方"),
            ("8", "クライアント管理（タグ・お気に入り・メモ）"),
            ("9", "キーボードショートカット"),
        ]
        self.set_font(self.FONT_NAME, size=10)
        for num, title in items:
            self.set_x(x + 4)
            self.cell(8, 7, num + ".", border=0)
            self.cell(150, 7, title, border=0)
            self.ln()

    def chapter_title(self, num, title):
        self.add_page()
        self.set_fill_color(18, 73, 148)
        self.rect(10, self.get_y() - 2, 190, 12, style="F")
        self.set_font(self.FONT_NAME, size=13)
        self.set_text_color(255)
        self.cell(0, 8, f"第{num}章  {title}", align="L")
        self.set_text_color(0)
        self.ln(14)

    def section_title(self, title):
        self.set_font(self.FONT_NAME, size=11)
        self.set_fill_color(230, 240, 255)
        self.cell(0, 7, f"  {title}", fill=True)
        self.ln(9)

    def body_text(self, text, indent=0):
        self.set_font(self.FONT_NAME, size=9)
        self.set_x(10 + indent)
        self.multi_cell(190 - indent, 5.5, text)
        self.ln(2)

    def bullet(self, items, indent=4):
        self.set_font(self.FONT_NAME, size=9)
        for item in items:
            self.set_x(10 + indent)
            self.cell(5, 5.5, "・")
            self.multi_cell(185 - indent, 5.5, item)

    def insert_shot(self, name, caption="", max_w=180):
        path = str(IMG_DIR / f"{name}.png")
        if not os.path.exists(path):
            self.body_text(f"[スクリーンショット: {name}]")
            return
        try:
            from PIL import Image
            im = Image.open(path)
            iw, ih = im.size
        except Exception:
            # PIL なしでも OK: FPDF は内部で解析する
            iw, ih = 1920, 1080  # デフォルト比率

        ratio = ih / iw
        w = min(max_w, 190 - self.l_margin)
        h = w * ratio

        # 残り高さに収まらなければ改ページ
        if self.get_y() + h + 8 > (self.h - self.b_margin):
            self.add_page()

        x = (210 - w) / 2
        self.image(path, x=x, y=self.get_y(), w=w)
        self.ln(h + 2)
        if caption:
            self.set_font(self.FONT_NAME, size=8)
            self.set_text_color(100)
            self.cell(0, 4, caption, align="C")
            self.set_text_color(0)
            self.ln(6)


def build_pdf(pdf: ManualPDF):
    # ---- 表紙 ----
    pdf.cover_page()

    # ================================================================
    # 第1章 ツール概要とホーム画面
    # ================================================================
    pdf.chapter_title("1", "ツール概要とホーム画面")

    pdf.section_title("ツールの目的")
    pdf.body_text(
        "複数クライアントの GA4 プロパティ・GTM コンテナ・Search Console サイトを横断管理し、"
        "計測実装の問題を自動検出、AI 分析で具体的な改善案を出すためのツールです。"
    )
    pdf.bullet([
        "計測停止・キーイベント未設定・カスタムディメンション過多などを自動検出",
        "UA系タグ残存・paused率過多などの GTM 評価",
        "SC 流入0・sitemap 未登録・低 CTR の検出",
        "ヘルススコアによる定量評価（GA4/GTM/SC 共通の100点満点）",
        "AI による業種別の改善提案・委任プロンプト生成",
    ])
    pdf.ln(4)

    pdf.section_title("ホーム画面")
    pdf.body_text(
        "ホームを開くと、その日に対応すべきことが一覧で確認できます。"
        "上部に4列のヘルスサマリー、下に要対応アラートタイル、"
        "クイックアクションボタンが並びます。"
    )
    pdf.insert_shot("01_home", "ホーム画面 — ヘルスサマリーと要対応タイル")

    pdf.section_title("折りたたみセクション（ホーム下部）")
    pdf.body_text("ホーム下部の4セクションはデフォルト閉じ。クリックで展開できます。")
    pdf.bullet([
        "📈 最近の変化 — 前回スナップショット比の変化TOP5",
        "🚨 要対応 TOP10 — GA4/GTM/SC をタブ切替で表示",
        "🤖 AI分析の活用状況 — 自動診断・カバレッジ・最頻出課題TOP5",
        "⭐ お気に入り・タグ・MID重複 — 3カード横並び",
    ])

    # ================================================================
    # 第2章 GA4 プロパティ管理
    # ================================================================
    pdf.chapter_title("2", "GA4 プロパティ管理")

    pdf.section_title("プロパティ一覧")
    pdf.body_text(
        "メニュー「GA4プロパティ」またはショートカット g → p でアクセス。"
        "ヘルスグレード（A〜F）・アラート有無・タグ・お気に入りでフィルタ可能。"
        "チェックボックスで複数選択 → 「🤖 一括AI分析」でまとめて分析できます。"
    )
    pdf.insert_shot("03_ga4_list", "GA4 プロパティ一覧 — グレード別フィルタ・一括操作")

    pdf.section_title("プロパティ詳細")
    pdf.body_text(
        "プロパティ名をクリックすると詳細ページが開きます。"
        "右サイドにスティッキー目次が表示され、"
        "基本情報・スコア内訳・差分履歴・AI分析などのセクションへ即移動できます。"
    )
    pdf.insert_shot("04_ga4_detail", "プロパティ詳細 — スコア内訳・AI分析ボタン")

    pdf.section_title("AI 分析の実行（GA4）")
    pdf.body_text(
        "詳細ページの「🤖 AIで分析・アクションプラン生成」ボタンを押し、"
        "モデル（Sonnet 4.6 / Opus 4.8）を選択して「▶ 分析実行」をクリック。"
        "30〜90秒で所見・課題リスト・優先度付きアクションプランが表示されます。"
        "各アクションの「🤖 委任可」を展開すると Claude/ChatGPT 向けプロンプトが出ます。"
    )

    # ================================================================
    # 第3章 GTM コンテナ管理
    # ================================================================
    pdf.chapter_title("3", "GTM コンテナ管理")

    pdf.section_title("GTM コンテナ一覧")
    pdf.body_text(
        "メニュー「GTM」またはショートカット g → g でアクセス。"
        "GA4 との紐付け・UA残存件数・paused 率などがひと目で分かります。"
    )
    pdf.insert_shot("05_gtm_list", "GTM コンテナ一覧 — ヘルスグレード・UA残存件数")

    pdf.section_title("GTM コンテナ詳細")
    pdf.body_text(
        "コンテナ名をクリックすると詳細ページへ。"
        "「タグ」「トリガー」「変数」タブで中身を確認できます。"
        "UA系タグ・GA4 Config タグの有無も自動チェックされます。"
    )
    pdf.insert_shot("06_gtm_detail", "GTM コンテナ詳細 — タグ一覧タブ")

    # ================================================================
    # 第4章 Search Console 管理
    # ================================================================
    pdf.chapter_title("4", "Search Console 管理")

    pdf.section_title("SC サイト一覧")
    pdf.body_text(
        "メニュー「Search Console」またはショートカット g → s でアクセス。"
        "クリック数・表示回数・CTR・平均掲載順位・sitemap 状態がひと目で確認できます。"
        "同ドメインの GA4 プロパティが紐付けられている場合はバッジが表示されます。"
    )
    pdf.insert_shot("07_sc_list", "Search Console サイト一覧 — KPI・ヘルスグレード")

    pdf.section_title("SC サイト詳細")
    pdf.body_text(
        "サイト名をクリックすると詳細ページが開きます。"
        "検索クエリ TOP20・ページ別パフォーマンス・デバイス・国別分布・sitemap 状態を確認できます。"
    )
    pdf.insert_shot("08_sc_detail", "SC サイト詳細 — クエリ・ページ・Sitemap タブ")

    # ================================================================
    # 第5章 ヘルススコアの見方
    # ================================================================
    pdf.chapter_title("5", "ヘルススコアの見方")

    pdf.section_title("100点満点のスコアリング")
    pdf.body_text(
        "GA4・GTM・SC それぞれを100点満点で評価します。"
        "A（80点以上）〜F（20点未満）の6段階グレードで表示されます。"
    )
    pdf.body_text("【GA4 主要スコア項目】")
    pdf.bullet([
        "計測中（sessions > 0）: 30点",
        "キーイベント設定（3件以上: 20点 / 1〜2件: 12点）",
        "カスタムディメンション（1〜30件: 15点）",
        "データストリーム設定: 10点",
        "eコマース計測（purchase 発生）: 10点",
    ])
    pdf.ln(3)
    pdf.body_text("【GTM 主要スコア項目】")
    pdf.bullet([
        "バージョン公開済: 15点",
        "タグ数 1件以上: 15点",
        "GA4 Measurement ID あり: 15点",
        "UA タグなし（残存するとマイナス）: 15点",
        "paused率 5%以下: 10点",
    ])
    pdf.ln(3)
    pdf.body_text("【SC 主要スコア項目】")
    pdf.bullet([
        "Sitemap 登録済: 15点",
        "クリック数（多いほど高得点）: 最大15点",
        "表示回数（多いほど高得点）: 最大15点",
        "CTR（1%以上で加点）: 最大10点",
        "GA4 プロパティとの連携: 10点",
    ])

    # ================================================================
    # 第6章 アラートと対応フロー
    # ================================================================
    pdf.chapter_title("6", "アラートと対応フロー")

    pdf.section_title("アラートの種類（13種類）")
    pdf.body_text(
        "ホームの「今日の要対応」タイルをクリックするとモーダルが開き、"
        "「対象 X件」タブと「📋 対策方法」タブを切り替えて確認できます。"
    )
    pdf.body_text("【GA4 アラート（5種）】")
    pdf.bullet([
        "untracked — 計測停止（sessions = 0）",
        "no_streams — データストリーム未設定",
        "api_err — Data API エラー",
        "no_ke — キーイベント未設定",
        "cd_overflow — カスタムディメンション過多（50件超）",
    ])
    pdf.ln(2)
    pdf.body_text("【GTM アラート（3種）】")
    pdf.bullet([
        "no_tags — タグなし",
        "ua_left — UA系タグ残存",
        "no_ga4 — GA4 Config タグなし",
    ])
    pdf.ln(2)
    pdf.body_text("【SC アラート（3種）】")
    pdf.bullet([
        "sc_no_clicks — 直近28日クリック0",
        "sc_no_sitemap — Sitemap 未登録",
        "sc_low_ctr — CTR 極低（0.5%未満）",
    ])
    pdf.ln(2)
    pdf.body_text("【クロスアラート（2種）】")
    pdf.bullet([
        "duplicate_mids — GA4 Measurement ID 重複",
        "mismatch — GA4↔GTM 不整合",
    ])

    # ================================================================
    # 第7章 AI 分析の使い方
    # ================================================================
    pdf.chapter_title("7", "AI 分析の使い方")

    pdf.section_title("① 個別分析")
    pdf.body_text(
        "GA4 プロパティ詳細またはGTMコンテナ詳細の「🤖 AIで分析」ボタンから実行。"
        "所見 → 課題 → アクション → 委任プロンプトの順に結果が表示されます。"
        "Sonnet 4.6（約12円）または Opus 4.8（約65円）を選択できます。"
    )

    pdf.section_title("② 一括AI分析")
    pdf.body_text(
        "プロパティ一覧でチェックボックスを選択 →「🤖 一括AI分析」。"
        "10件なら約120円・約5分で全件のサマリー＋課題＋アクションが揃います。"
        "「⚠ アラート分を選択」ボタンで問題のある全件を一括選択できます。"
    )

    pdf.section_title("③ 自動診断（3日ごと）")
    pdf.body_text(
        "3日ごとのデータ更新後に自動で動き、新規アラート発生プロパティを最大15件分析。"
        "結果はホームの「🤖 AI分析の活用状況」セクションに表示されます。"
        "ヘッダの「分析▾」ドロップダウン → 「自動診断を実行」で手動起動も可。"
    )

    pdf.section_title("④ AI委任プロンプトの使い方")
    pdf.body_text(
        "個別分析の各アクション項目を展開すると、1,000〜2,000字の「委任プロンプト」が表示されます。"
        "「Claude」または「ChatGPT」ボタンで外部 AI に飛んでそのまま貼り付けて依頼できます。"
    )

    # ================================================================
    # 第8章 クライアント管理
    # ================================================================
    pdf.chapter_title("8", "クライアント管理（タグ・お気に入り・メモ）")

    pdf.section_title("お気に入り（★）")
    pdf.body_text(
        "プロパティ一覧・GTM一覧・各詳細ページで★をクリックして登録。"
        "ホームの「⭐ お気に入り」セクションに常時表示されます。"
        "頻繁にチェックする5〜10件を登録しておくと朝チェックが効率化されます。"
    )

    pdf.section_title("タグ付け")
    pdf.body_text(
        "各詳細ページの「🏷 タグ・メモ」セクションでカンマ区切りで複数タグを設定できます。"
        "一覧画面の「タグ」フィルタで絞り込み可能。"
        "例: 「契約中, EC, A社, 月次レポート対象」のように2〜3軸で運用すると管理しやすくなります。"
    )

    pdf.section_title("メモ")
    pdf.body_text(
        "クライアント担当者・連絡履歴・特記事項などを記録できます。"
        "詳細ページの「🏷 タグ・メモ」セクションから保存してください。"
    )

    pdf.body_text(
        "【運用例】「契約中」「トライアル」「休眠」「業界:EC」「業界:メディア」のように"
        "ステータスと業界を2軸で管理すると、後で一括AI分析やレポート作成の際に"
        "素早く絞り込めます。"
    )

    # ================================================================
    # 第9章 キーボードショートカット
    # ================================================================
    pdf.chapter_title("9", "キーボードショートカット")

    pdf.section_title("画面遷移ショートカット")
    shortcuts = [
        ("g → h", "ホームへ移動"),
        ("g → p", "GA4 プロパティ一覧へ移動"),
        ("g → g", "GTM コンテナ一覧へ移動"),
        ("g → s", "Search Console 一覧へ移動"),
        ("g → a", "一括AI分析ページへ移動"),
        ("g → u", "使い方ガイドへ移動"),
        ("?",      "ヘルプを開く"),
        ("/",      "検索ボックスにフォーカス"),
    ]
    pdf.set_font(pdf.FONT_NAME, size=9)
    col_w = [30, 120]
    pdf.set_fill_color(230, 240, 255)
    pdf.cell(col_w[0], 7, "キー", border=1, fill=True)
    pdf.cell(col_w[1], 7, "動作", border=1, fill=True)
    pdf.ln()
    for key, action in shortcuts:
        pdf.cell(col_w[0], 6, key, border=1)
        pdf.cell(col_w[1], 6, action, border=1)
        pdf.ln()
    pdf.ln(6)

    pdf.section_title("その他の操作ヒント")
    pdf.bullet([
        "ダーク/ライトモード切替: ヘッダ右上の 🌓 ボタン（Cookie で保存）",
        "データ更新中: ヘッダのステータスクリックで詳細進捗パネルを表示",
        "CSV エクスポート: 一覧画面の「⬇ CSV」ボタン（フィルタ結果も対応）",
        "アラートモーダル: 対象タブ（件数一覧）と対策方法タブ（手順）をタブ切替",
        "ページ内検索: ブラウザの Ctrl+F で日本語検索可",
    ])

    # 使い方ガイドページのスクリーンショット
    pdf.add_page()
    pdf.section_title("参考: 使い方ガイドページ（/usage）")
    pdf.body_text(
        "ツール内の「使い方ガイド」ページ（ヘッダ「使い方」またはショートカット g → u）でも"
        "同様の内容を随時確認できます。"
    )
    pdf.insert_shot("09_usage", "ツール内 使い方ガイドページ")


def main():
    print("=== GA4-Inventory 操作マニュアル PDF 生成 ===\n")

    take_screenshots()

    print("📄 PDF 生成中...")
    pdf = ManualPDF()
    build_pdf(pdf)
    pdf.output(str(OUT_PDF))
    print(f"✅ PDF 保存: {OUT_PDF}\n")
    print(f"  サイズ: {OUT_PDF.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
