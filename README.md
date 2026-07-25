# 資源循環朝刊 Bot

資源循環・サーキュラーエコノミー分野のニュースを Gemini(Google検索付き)で毎朝自動収集し、
**Discord配信**・**Webアーカイブ公開(GitHub Pages)**・**RSS配信**を行う Bot です。

毎日 UTC 20:00(日本時間 翌朝5:00)に GitHub Actions で自動実行されます。

## 構成

| ファイル | 役割 |
|---|---|
| `daily_news.py` | ニュース収集(Gemini)+ Discord配信 |
| `publish.py` | 日次記事のHTML化・アーカイブ一覧・RSS生成(`docs/` に出力) |
| `.github/workflows/daily_run.yml` | 定時実行と `docs/` のコミット |

## 必須シークレット(Settings → Secrets and variables → Actions → Secrets)

| 名前 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Gemini API キー |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL |

## 収益化まわりの設定(任意 / Variables に設定)

広告収益型・低労力モデルの土台です。すべて **Variables**(Secrets ではなく Variables タブ)に設定します。

| 名前 | 内容 | 例 |
|---|---|---|
| `SUBSCRIBE_URL` | メール購読フォームのURL。設定すると各ページに購読ボタンが出る | `https://xxxx.beehiiv.com/subscribe` |
| `SPONSOR_MESSAGE` | スポンサー枠のテキスト。設定すると配信・記事末尾に差し込む | `本日のスポンサー: 〇〇株式会社 …` |
| `SITE_BASE_URL` | 公開サイトのベースURL(RSSの絶対リンク用) | `https://<user>.github.io/daily-news/` |

## 一度きりのセットアップ手順

### 1. GitHub Pages を有効化
Settings → Pages → **Source: Deploy from a branch** → Branch: `main` / フォルダ `/docs` を選択。
初回実行後、`https://<user>.github.io/daily-news/` でアーカイブが公開されます。

### 2. メール配信(Beehiiv 等)を RSS で接続
1. Beehiiv / Substack / Mailchimp などで publication を作成。
2. **RSS→メール自動配信**の設定で、フィードURL `https://<user>.github.io/daily-news/feed.xml` を登録。
   - 以後、新しい記事が出るたびに自動でメール配信されます(コード変更不要)。
3. その publication の登録フォームURLを `SUBSCRIBE_URL` に設定。

### 3. スポンサーが決まったら
`SPONSOR_MESSAGE`(Variables)を書き換えるだけ。次回実行から配信・記事に反映されます。

## 収益化モデルの考え方

- **無料配信で業界特化の濃い読者リストを育て、スポンサー枠で回収**する広告収益型。
- サブスク課金は行わない(AI生成の参考情報を有償にすると信頼性・著作権の壁が立つため)。
- ESP はコードに直接埋め込まず、**RSS を共通のつなぎ役**にしてベンダーロックインを避ける。
- 全ページに免責文を常時表示し、誤情報リスクに備える。
