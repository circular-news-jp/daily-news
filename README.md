# 資源循環朝刊 Bot

資源循環・サーキュラーエコノミー分野のニュースを Gemini(Google検索付き)で毎朝自動収集し、
**Discord配信**・**Webアーカイブ公開(GitHub Pages)**・**RSS配信**・**X(旧Twitter)自動投稿**を行う Bot です。

毎日 UTC 20:00(日本時間 翌朝5:00)に GitHub Actions で自動実行されます。

## 構成

| ファイル | 役割 |
|---|---|
| `daily_news.py` | ニュース収集(Gemini)+ Discord配信 |
| `publish.py` | 日次記事のHTML化・アーカイブ一覧・RSS生成(`docs/` に出力) |
| `x_post.py` | 記事へ誘導する X ポストの組み立て・投稿キュー・時刻到来分の投稿 |
| `state/x_queue.json` | その日のカテゴリ別ポストの予定と投稿済みフラグ(自動生成) |
| `.github/workflows/daily_run.yml` | 朝の生成・配信と `docs/` のコミット |
| `.github/workflows/x_post.yml` | 各時刻のカテゴリ別X投稿 |

## 必須シークレット(Settings → Secrets and variables → Actions → Secrets)

| 名前 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Gemini API キー |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL |

## X(旧Twitter)自動投稿の設定

`@circular-news-jp` から**1日6回**、記事へ誘導するポストを自動投稿します。

| 時刻(JST) | 内容 |
|---|---|
| 5:00 前後 | 全体ポスト(要点3件 + 記事URL)。記事生成の直後 |
| 8:00 | 政策・法規制 |
| 12:00 | 相場メモ |
| 15:00 | 産業動向 |
| 18:00 | 技術・研究動向 |
| 21:00 | 海外規制・市場 |

朝の生成時にカテゴリ別の投稿文を `state/x_queue.json` に書き出し、各時刻に起動する
`x_post.yml` が「時刻が来ていて未投稿の先頭1件」だけを投稿します。実行が遅延・失敗しても
次の時刻に持ち越され、同じ内容を二重投稿することはありません。

記事URLは本文に入れず、**リプライとしてぶら下げます**(URL入りポストは表示が伸びにくいため)。
したがって1回の投稿につき本文+リプライの2ポストを消費します。

**Secrets** に以下の4つを登録すると有効化され、1つでも欠けていれば投稿はスキップされます
(投稿に失敗しても Discord配信・アーカイブは通常どおり完了します)。

| 名前 | 取得場所(X Developer Portal) |
|---|---|
| `X_API_KEY` | Keys and tokens → Consumer Keys → API Key |
| `X_API_SECRET` | 同 → API Key Secret |
| `X_ACCESS_TOKEN` | 同 → Authentication Tokens → Access Token |
| `X_ACCESS_TOKEN_SECRET` | 同 → Access Token Secret |

任意の **Variables**:

| 名前 | 内容 | 既定値 |
|---|---|---|
| `X_HASHTAGS` | ポスト末尾のハッシュタグ | `#資源循環 #サーキュラーエコノミー #リサイクル` |
| `X_POST_SLOTS` | カテゴリ別ポストの時刻割り当て(JST) | `08:00=政策,12:00=相場,15:00=産業,18:00=技術,21:00=海外` |

`X_POST_SLOTS` は `時刻=カテゴリ名の一部` をカンマ区切りで書きます。キーワードは見出しへの
部分一致で、一致しない時刻には残ったカテゴリが本文の順に埋められます。時刻だけを書けば
(例: `08:00,12:00`)本文の並び順どおりに割り当てられます。

> **注意**: 時刻を変えるときは `.github/workflows/x_post.yml` の `cron`(UTC表記)も合わせて
> 変更してください。ワークフローが起動しない時刻を指定しても投稿されません。

### 取得手順

1. [developer.x.com](https://developer.x.com/) に `@circular-news-jp` でログインし、Free プランで App を作成。
2. **User authentication settings** で App permissions を **Read and write** に設定
   (Type of App は *Web App / Automated App or Bot*、Callback URI は自サイトURLで可)。
3. **Keys and tokens** で Access Token を **生成し直す**。
   権限変更の前に発行したトークンは read 権限のままなので、必ず権限設定のあとに再生成すること。
4. 上表の4つを Settings → Secrets and variables → Actions → **Secrets** に登録。

Free プランの書き込み上限は **500ポスト/月**です。1日6回 × (本文+URLリプライ) = 12ポスト/日、
約360ポスト/月なので収まりますが、余裕は大きくありません。スロットを増やす場合は
この上限に注意してください(手動での `Run workflow` も消費します)。

投稿文だけを確認したいときは、ローカルで `python x_post.py` を実行すると全6回分の本文・
リプライ・加重文字数(全角=2、URL=23でカウント)が表示されます。実際の投稿は行いません。

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
