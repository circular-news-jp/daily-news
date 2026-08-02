"""日次レポートを GitHub Pages 用の静的サイトとして公開するモジュール。

生成物(すべて docs/ 配下):
  - entries/YYYY-MM-DD.html … 日次記事(SEOの入口ページ)
  - index.html               … アーカイブ一覧 + 購読導線
  - feed.xml                 … RSSフィード(Beehiiv等がRSS→メール配信で取り込む)
  - entries.json             … 一覧/RSS再生成用のメタデータ

ベンダー(ESP)には依存しない。購読は SUBSCRIBE_URL で外部の登録フォームへ誘導する。
"""

import os
import re
import json
import html
import datetime
from email.utils import format_datetime

import markdown as md

# ------- 設定(環境変数で上書き可) -------
SITE_TITLE = os.environ.get("SITE_TITLE", "資源循環朝刊")
SITE_DESCRIPTION = os.environ.get(
    "SITE_DESCRIPTION",
    "資源循環・サーキュラーエコノミーの動向を毎朝お届けするニュースダイジェスト。",
)
# RSS のリンクを絶対URLにするためのベースURL(末尾スラッシュ付き)
# CI では未設定のリポジトリ変数が空文字として渡るため、空の場合も既定値にフォールバックする
DEFAULT_SITE_BASE_URL = "https://circular-news-jp.github.io/daily-news/"
SITE_BASE_URL = (
    os.environ.get("SITE_BASE_URL", "").strip() or DEFAULT_SITE_BASE_URL
).rstrip("/") + "/"
# 購読フォームのURL(Beehiiv等の登録ページ)。未設定なら購読ボタンは表示しない。
SUBSCRIBE_URL = os.environ.get("SUBSCRIBE_URL", "").strip()

DOCS_DIR = os.environ.get("DOCS_DIR", "docs")
ENTRIES_DIR = os.path.join(DOCS_DIR, "entries")
MANIFEST_PATH = os.path.join(DOCS_DIR, "entries.json")
MAX_RSS_ITEMS = 30

DISCLAIMER = (
    "本レポートは生成AI(Gemini)がGoogle検索を用いて自動作成した参考情報です。"
    "内容の正確性・完全性を保証するものではなく、投資・取引・経営判断の材料とする場合は"
    "必ず原典・一次情報をご確認ください。"
)

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", "Noto Sans JP", Meiryo, sans-serif;
  line-height: 1.75; color: #1a1a1a; background: #fafafa; }
.wrap { max-width: 720px; margin: 0 auto; padding: 24px 20px 64px; }
header.site { border-bottom: 1px solid #e5e5e5; padding-bottom: 16px; margin-bottom: 28px; }
header.site a { text-decoration: none; color: inherit; }
header.site h1 { font-size: 1.35rem; margin: 0 0 4px; }
header.site p { margin: 0; color: #666; font-size: .9rem; }
h1, h2, h3 { line-height: 1.4; }
article h2 { font-size: 1.05rem; margin-top: 1.6em; border-left: 4px solid #2e7d32; padding-left: .5em; }
article h3 { font-size: 1rem; margin-top: 1.4em; border-left: 4px solid #66bb6a; padding-left: .5em; }
article ul { padding-left: 1.2em; }
article a { color: #1565c0; word-break: break-all; }
.entry-date { color: #666; font-size: .85rem; margin: 0 0 8px; }
.subscribe { background: #e8f5e9; border: 1px solid #c8e6c9; border-radius: 10px; padding: 18px 20px; margin: 28px 0; text-align: center; }
.subscribe .btn { display: inline-block; background: #2e7d32; color: #fff; text-decoration: none; padding: 10px 22px; border-radius: 999px; font-weight: 600; margin-top: 6px; }
.sponsor { background: #fff8e1; border: 1px solid #ffe082; border-radius: 10px; padding: 12px 16px; }
.entry-list { list-style: none; padding: 0; }
.entry-list li { border-bottom: 1px solid #eee; padding: 14px 0; }
.entry-list a { text-decoration: none; color: #1565c0; font-weight: 600; }
.entry-list .snippet { color: #555; font-size: .9rem; margin-top: 4px; }
footer.site { margin-top: 48px; padding-top: 16px; border-top: 1px solid #e5e5e5; color: #888; font-size: .78rem; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e6e6; background: #121212; }
  header.site { border-color: #333; } header.site p, .entry-list .snippet { color: #aaa; }
  article a, .entry-list a { color: #64b5f6; }
  .subscribe { background: #1b2e1c; border-color: #2e4a30; }
  .sponsor { background: #2e2a12; border-color: #5c4d16; }
  .entry-list li { border-color: #2a2a2a; } footer.site { border-color: #333; }
}
"""


def _ensure_dirs():
    os.makedirs(ENTRIES_DIR, exist_ok=True)


def _load_manifest():
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_manifest(entries):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _make_snippet(body_md, limit=140):
    """本文Markdownから、参照ソース・スポンサー以降を除いた要約テキストを作る。"""
    text = body_md
    # 「参照ソース」や「スポンサー」以降は要約に含めない
    for marker in ("\n---\n## 参照ソース", "\n---\n## 📣 スポンサー", "\n---\n⚠️"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    # Markdown記号・URL・改行を落としてプレーン化
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[#>*`\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _subscribe_block():
    if not SUBSCRIBE_URL:
        return ""
    return (
        '<div class="subscribe">'
        "<div>📬 毎朝の資源循環ニュースをメールで受け取る</div>"
        f'<a class="btn" href="{html.escape(SUBSCRIBE_URL)}">無料で購読する</a>'
        "</div>"
    )


def _page(title, body_html, description):
    esc_title = html.escape(title)
    esc_desc = html.escape(description)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc_title}</title>
<meta name="description" content="{esc_desc}">
<link rel="alternate" type="application/rss+xml" title="{html.escape(SITE_TITLE)}" href="{SITE_BASE_URL}feed.xml">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="site">
<a href="{SITE_BASE_URL}"><h1>{html.escape(SITE_TITLE)}</h1></a>
<p>{html.escape(SITE_DESCRIPTION)}</p>
</header>
{body_html}
<footer class="site">
<p>{html.escape(DISCLAIMER)}</p>
<p><a href="{SITE_BASE_URL}feed.xml">RSSフィード</a></p>
</footer>
</div>
</body>
</html>
"""


def _render_entry_page(date, body_md, snippet):
    date_str = date.strftime("%Y/%m/%d")
    title = f"{date_str} {SITE_TITLE}"
    body_html = md.markdown(body_md, extensions=["extra", "nl2br", "sane_lists"])
    inner = (
        f'<article>'
        f'<h1>{html.escape(date_str)} のダイジェスト</h1>'
        f'<p class="entry-date">{html.escape(date_str)}</p>'
        f'{body_html}'
        f'</article>'
        f'{_subscribe_block()}'
        f'<p><a href="{SITE_BASE_URL}">← 一覧へ戻る</a></p>'
    )
    return _page(title, inner, snippet)


def _render_index(entries):
    items = []
    for e in entries:
        items.append(
            f'<li><a href="entries/{html.escape(e["file"])}">{html.escape(e["date"])} のダイジェスト</a>'
            f'<div class="snippet">{html.escape(e["snippet"])}</div></li>'
        )
    inner = (
        f'{_subscribe_block()}'
        f'<h2>アーカイブ</h2>'
        f'<ul class="entry-list">{"".join(items)}</ul>'
    )
    return _page(SITE_TITLE, inner, SITE_DESCRIPTION)


def _render_rss(entries):
    now = format_datetime(datetime.datetime.now(datetime.timezone.utc))
    items_xml = []
    for e in entries[:MAX_RSS_ITEMS]:
        link = f'{SITE_BASE_URL}entries/{e["file"]}'
        try:
            d = datetime.datetime.strptime(e["date"], "%Y/%m/%d").replace(
                tzinfo=datetime.timezone.utc
            )
            pub = format_datetime(d)
        except Exception:
            pub = now
        items_xml.append(
            "<item>"
            f"<title>{html.escape(e['date'])} {html.escape(SITE_TITLE)}</title>"
            f"<link>{html.escape(link)}</link>"
            f"<guid isPermaLink=\"true\">{html.escape(link)}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<description>{html.escape(e['snippet'])}</description>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{html.escape(SITE_TITLE)}</title>"
        f"<link>{html.escape(SITE_BASE_URL)}</link>"
        f"<description>{html.escape(SITE_DESCRIPTION)}</description>"
        "<language>ja</language>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        f"{''.join(items_xml)}"
        "</channel></rss>\n"
    )


def publish_entry(date, body_md):
    """1日分のレポートを公開する。記事HTMLを書き出し、一覧とRSSを再生成する。

    戻り値は公開された記事の絶対URL(SNS投稿などの誘導先に使う)。
    """
    _ensure_dirs()

    date_str = date.strftime("%Y/%m/%d")
    file_name = date.strftime("%Y-%m-%d") + ".html"
    snippet = _make_snippet(body_md)

    # 記事HTML
    with open(os.path.join(ENTRIES_DIR, file_name), "w", encoding="utf-8") as f:
        f.write(_render_entry_page(date, body_md, snippet))

    # マニフェスト更新(同日再実行は上書き)
    entries = [e for e in _load_manifest() if e.get("file") != file_name]
    entries.append({"date": date_str, "file": file_name, "snippet": snippet})
    entries.sort(key=lambda e: e["file"], reverse=True)
    _save_manifest(entries)

    # 一覧 + RSS
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(_render_index(entries))
    with open(os.path.join(DOCS_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(_render_rss(entries))

    print(f"アーカイブ公開: entries/{file_name}(全{len(entries)}件 / RSS更新)")
    return f"{SITE_BASE_URL}entries/{file_name}"
