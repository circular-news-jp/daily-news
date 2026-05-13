import os
import datetime
import time
import requests
from google import genai
from google.genai import types

API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
RETRY_BASE_WAIT = int(os.environ.get("GEMINI_RETRY_BASE_WAIT", "30"))  # 秒

if not API_KEY or not DISCORD_WEBHOOK_URL:
    raise ValueError("GEMINI_API_KEY または DISCORD_WEBHOOK_URL が設定されていません。")

client = genai.Client(api_key=API_KEY)


def get_gemini_news():
    today = datetime.date.today().strftime("%Y年%m月%d日")
   prompt = f"""
{today}時点で公開されている、日本国内および国際的な「資源循環・サーキュラーエコノミー」関連の最新動向を、Google検索を用いて調査し、簡潔な日次レポートにまとめてください。

# 出力ルール（厳守）
- 検索で確認できた一次情報のみを記載し、推測・想像で補完しないこと
- 各項目には可能な限り出典URLを併記すること
- 該当する情報が見つからない項目は「本日該当なし」と明記し、無理に埋めないこと
- 数値・日付・固有名詞は出典で確認できたものだけを記載すること
- 全体で1500文字以内

# 項目
1. 【政策・法規制】環境省・経産省・METIなどの資源循環/サーキュラーエコノミー関連の発表・改正・パブコメ
2. 【技術・研究動向】リサイクル技術、選別技術、リン回収、鉄スクラップ・非鉄金属の高度利用に関する論文・実証・プレスリリース
3. 【産業動向】国内外の企業によるリサイクル事業・投資・提携の動き
4. 【海外規制・市場】EU(バッテリー規則・ESPR等)、中国(廃棄物輸入・スクラップ)、米国などの動き
5. 【相場メモ】鉄スクラップ・銅建値などの定性的な動向（数値は出典が明確な場合のみ）

各項目は2〜4行の箇条書きで簡潔に。読了1分以内を目安。
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
                output_text = ""
                for part in response.candidates[0].content.parts:
                    if part.text:
                        output_text += part.text
                if output_text.strip():
                    return output_text
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


def send_discord(message):
    if not message:
        print("送信するメッセージが空です。")
        return

    header = f"📅 **{datetime.date.today().strftime('%Y/%m/%d')} 資源循環・技術士朝刊**\n\n"
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
    today = datetime.date.today().strftime("%Y/%m/%d")
    payload = {
        "content": (
            f"⚠️ **{today} 資源循環・技術士朝刊 配信失敗**\n"
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
    result = get_gemini_news()
    if isinstance(result, tuple):
        # 失敗ケース: (None, error_reason)
        _, reason = result
        send_discord_failure(reason)
    else:
        send_discord(result)
