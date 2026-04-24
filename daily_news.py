import os
import datetime
import requests
import time
from google import genai
from google.genai import types

# GitHubのSecretsから値を受け取る設定
API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# APIキーがない場合にエラーを出す設定
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

    # モデル名を安定版の 'gemini-2.0-flash' に変更
    # 検索機能を使う場合は、この書き方が推奨されます
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        
        # 確実にテキスト部分だけを抽出
        if response.candidates:
            output_text = ""
            for part in response.candidates[0].content.parts:
                if part.text:
                    output_text += part.text
            return output_text
        return None

    except Exception as e:
        print(f"Geminiリクエストエラー: {e}")
        return None

def send_discord(message):
    if not message:
        print("送信するメッセージが空です。")
        return 
        
    payload = {"content": f"📅 **{datetime.date.today().strftime('%Y/%m/%d')} 資源循環・技術士朝刊**\n\n{message}"}
    
    # タイムアウト設定とエラーチェックを追加
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status() 
        print("Discordへの送信に成功しました。")
    except Exception as e:
        print(f"Discord送信エラー: {e}")

if __name__ == "__main__":
    news = get_gemini_news()
    send_discord(news)
