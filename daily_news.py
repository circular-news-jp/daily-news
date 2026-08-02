import os
import re
import datetime
import time
import requests
from google import genai
from google.genai import types

import publish
import x_post

API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
RETRY_BASE_WAIT = int(os.environ.get("GEMINI_RETRY_BASE_WAIT", "30"))  # 秒

# 収益化: スポンサー枠(任意)。設定されていれば配信とアーカイブの末尾に差し込む。
SPONSOR_MESSAGE = os.environ.get("SPONSOR_MESSAGE", "").strip()

# アーカイブ公開(GitHub Pages)を行うか。CI では既定で有効。
ENABLE_ARCHIVE = os.environ.get("ENABLE_ARCHIVE", "1") not in ("0", "false", "False", "")

if not API_KEY or not DISCORD_WEBHOOK_URL:
    raise ValueError("GEMINI_API_KEY または DISCORD_WEBHOOK_URL が設定されていません。")

client = genai.Client(api_key=API_KEY)


def today_jst():
    """日本時間での「今日」。

    GitHub Actions のランナーは UTC なので date.today() を使うと、
    JST 5:00(=前日20:00 UTC)の実行が前日の日付になってしまう。
    朝刊の日付・記事ファイル名・X投稿キューはすべて JST に揃える。
    """
    return datetime.datetime.now(x_post.JST).date()


_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def dedupe_repeated_headings(text):
    """連続して繰り返された見出し行を1つにまとめる。

    Google検索グラウンディングを使うと応答が複数パートに分割され、
    パート境界で直前の見出し行が次パート先頭に再掲されることがある。
    parts をそのまま連結すると同じ見出しが2行続き、記事ページでも
    Discord配信でも見出しが二重に表示されてしまう。
    """
    result = []
    for line in text.split("\n"):
        if _HEADING_RE.match(line):
            # 直前の(空行を除く)行が同じ見出しなら捨てる
            prev = next((l for l in reversed(result) if l.strip()), None)
            if prev is not None and prev.strip() == line.strip():
                continue
        result.append(line)
    return "\n".join(result)


def get_gemini_news():
    """ニュースを取得する。

    戻り値は常にタプル (text, error):
      - 成功時: (本文文字列, None)
      - 失敗時: (None, エラー理由文字列)
    """
    today = today_jst()
    today_str = today.strftime("%Y年%m月%d日")
    week_ago_str = (today - datetime.timedelta(days=7)).strftime("%Y年%m月%d日")

    prompt = f"""
{week_ago_str}〜{today_str}の直近1週間に公開された、日本国内および国際的な「資源循環・サーキュラーエコノミー」関連の動向を、Google検索を用いて調査し、日次レポートにまとめてください。

# 探索領域と検索キーワード
各領域について、Google検索で複数キーワードを試して情報を収集してください。

1. 政策・法規制
   - 対象: 環境省・経産省などの資源循環/サーキュラーエコノミー関連の発表・改正・パブコメ
   - 検索例: 「資源循環」「サーキュラーエコノミー」「環境省 プレスリリース」「経産省 資源循環」「資源有効利用促進法」

2. 技術・研究動向
   - 対象: リサイクル技術、選別技術、リン回収、鉄スクラップ・非鉄金属の高度利用
   - 検索例: 「J-STAGE リン回収」「選別技術」「都市鉱山」「リサイクル 実証」

3. 産業動向
   - 対象: 国内外の企業によるリサイクル事業・投資・提携
   - 検索例: 「リサイクル 提携」「リサイクル 投資」「電炉 新設」

4. 海外規制・市場
   - 対象: EU(バッテリー規則・ESPR等)、中国(廃棄物輸入・スクラップ)、米国などの動き
   - 検索例: 「EU バッテリー規則」「ESPR」「中国 廃棄物 輸入」

5. 相場メモ
   - 対象: 鉄スクラップ・銅建値などの動向
   - 優先参照先: 「JX金属 電気銅建値」「東京製鉄 スクラップ 購入価格」「日刊市況通信」「MIRU」「環境新聞」のサイトを優先的に検索すること
   - 検索例: 「JX金属 建値 今週」「東京製鉄 スクラップ 価格 改定」「鉄スクラップ H2 相場」「銅建値 推移」
   - 特別ルール:
     - 数値（円/トンなど）は出典で確認できた場合のみ記載
     - 価格改定があった場合は、改定日・改定幅・改定後価格を明記
     - 数値が取れない場合は「上昇傾向」「横ばい」など定性記述
     - 該当情報なしの場合は「直近1週間で該当情報を確認できず」と明記

# 出力フォーマット
各領域について、以下の形式で記述してください:

### 1. 政策・法規制
- (情報1の要約)
- (情報2の要約)

### 2. 技術・研究動向
- ...

(以下同様、見出しは番号と短いカテゴリ名のみ。説明文は含めないこと)

各カテゴリ2〜4個の箇条書きで簡潔に。
"""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                ),
            )
            if response.candidates:
                candidate = response.candidates[0]
                output_text = ""
                for part in candidate.content.parts:
                    if part.text:
                        output_text += part.text
                # パート境界で再掲された見出しの重複を除去
                output_text = dedupe_repeated_headings(output_text)

                # grounding_metadata から参照URLを抽出
                sources = []
                grounding = getattr(candidate, "grounding_metadata", None)
                if grounding and getattr(grounding, "grounding_chunks", None):
                    for chunk in grounding.grounding_chunks:
                        web = getattr(chunk, "web", None)
                        if web and getattr(web, "uri", None):
                            title = getattr(web, "title", "") or web.uri
                            sources.append(f"- {title}\n  {web.uri}")

                if output_text.strip():
                    # ソースを末尾に追加
                    if sources:
                        output_text += "\n\n---\n## 参照ソース\n" + "\n".join(sources)
                    else:
                        output_text += "\n\n---\n⚠️ 参照ソースが取得できませんでした(内容の信頼性に注意)"

                    print(f"=== Gemini出力プレビュー(先頭500字) ===\n{output_text[:500]}\n=== ここまで ===")
                    print(f"=== 取得ソース数: {len(sources)} ===")
                    return output_text, None
            last_error = "空応答"
        except Exception as e:
            last_error = str(e)
            print(f"[試行 {attempt}/{MAX_RETRIES}] Geminiリクエストエラー: {e}")

        if attempt < MAX_RETRIES:
            wait = RETRY_BASE_WAIT * attempt  # 30s, 60s, 90s と線形バックオフ
            print(f"{wait}秒待機して再試行します...")
            time.sleep(wait)

    print(f"全{MAX_RETRIES}回の試行が失敗しました。最後のエラー: {last_error}")
    return None, last_error


def with_sponsor(text):
    """本文の末尾にスポンサー枠を差し込む(設定時のみ)。"""
    if not SPONSOR_MESSAGE:
        return text
    return f"{text}\n\n---\n## 📣 スポンサー\n{SPONSOR_MESSAGE}"


def send_discord(message):
    if not message:
        print("送信するメッセージが空です。")
        return

    header = f"📅 **{today_jst().strftime('%Y/%m/%d')} 資源循環朝刊**\n\n"
    full_text = header + message

    # Discord の1メッセージ上限は2000文字。安全マージンで1900に分割
    CHUNK = 1900
    chunks = [full_text[i:i + CHUNK] for i in range(0, len(full_text), CHUNK)]

    for idx, chunk in enumerate(chunks, start=1):
        suffix = f"\n\n— ({idx}/{len(chunks)})" if len(chunks) > 1 else ""
        payload = {"content": chunk + suffix}
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            response.raise_for_status()
            print(f"Discord送信成功 ({idx}/{len(chunks)})")
        except requests.exceptions.HTTPError as e:
            # 400系はレスポンス本文に原因が入っているので表示
            body = e.response.text if e.response is not None else "(no body)"
            print(f"Discord送信エラー ({idx}/{len(chunks)}): {e} / body={body}")
        except Exception as e:
            print(f"Discord送信エラー ({idx}/{len(chunks)}): {e}")

def send_discord_failure(reason):
    """Gemini が完全に失敗したときの障害通知"""
    today = today_jst().strftime("%Y/%m/%d")
    payload = {
        "content": (
            f"⚠️ **{today} 資源循環朝刊 配信失敗**\n"
            f"Gemini API から有効な応答を取得できませんでした。\n"
            f"理由: `{reason}`\n"
            f"明日の自動実行を待つか、Actions の `Run workflow` から手動で再実行してください。"
        )
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"障害通知の送信にも失敗: {e}")


if __name__ == "__main__":
    text, reason = get_gemini_news()
    if not text:
        # 失敗ケース
        send_discord_failure(reason or "不明なエラー")
    else:
        body = with_sponsor(text)
        send_discord(body)
        if ENABLE_ARCHIVE:
            entry_url = None
            try:
                entry_url = publish.publish_entry(today_jst(), body)
            except Exception as e:
                # 配信は成功しているので、アーカイブ失敗で全体を落とさない
                print(f"アーカイブ生成エラー(配信自体は成功): {e}")
            if entry_url:
                try:
                    # 記事へ誘導するXポスト。失敗しても配信・アーカイブは維持する
                    x_post.post_entry(today_jst(), body, entry_url)
                except Exception as e:
                    print(f"X投稿エラー(配信・アーカイブは成功): {e}")
