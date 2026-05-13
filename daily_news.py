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
    あなたは技術士（資源工学）の専門コンサルタントです。
    {today}の情報を調査し、以下の項目で「資源循環・技術士朝刊」を作成してください。
    1. 【資源相場】鉄スクラップ（H2）価格、銅建値の最新動向
    2. 【政策・法規制】環境省・経産省の資源循環、サーキュラーエコノミー関連の最新発表
    3. 【技術動向】J-STAGE等の論文やニュース（リン回収、鉄粉利用、選別技術など）
    4. 【海外動向】欧州バッテリー規制、中国スクラップ動向など
    技術士として、実務への影響を交えた専門的な考察を添えて、1分で読める量にまとめてください。
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
        return
    payload = {
        "content": f"📅 **{datetime.date.today().strftime('%Y/%m/%d')} 資源循環・技術士朝刊**\n\n{message}"
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        print("Discordへの送信に成功しました。")
    except Exception as e:
        print(f"Discord送信エラー: {e}")


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
