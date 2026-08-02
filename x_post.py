"""生成した日次記事を X(旧Twitter)に自動投稿するモジュール。

投稿は2種類:
  1. 朝の全体ポスト … 生成直後に、要点3件+記事URLで記事全体へ誘導する
  2. カテゴリ別ポスト … 「相場メモ」「技術・研究動向」など領域ごとに、
     設定した時刻へ分散して投稿する

2は朝の生成時に投稿予定キュー(state/x_queue.json)を書き出しておき、
各時刻に起動するワークフローが「期限が来ていて未投稿の先頭1件」だけを投稿する。
実行が失敗・遅延しても次の時刻に持ち越され、二重投稿もしない。

認証は X API v2 のユーザーコンテキスト(OAuth 1.0a)を使う。

必要な環境変数(4つすべて揃っているときだけ投稿する):
  X_API_KEY / X_API_SECRET             … Developer Portal の Consumer Keys
  X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET … 同 Authentication Tokens(Read and write 権限)

任意:
  X_HASHTAGS   … 末尾に付けるハッシュタグ(スペース区切り)
  X_POST_SLOTS … カテゴリ別ポストの時刻(JST, HH:MM をカンマ区切り)
"""

import os
import re
import sys
import json
import datetime

import requests
from requests_oauthlib import OAuth1

JST = datetime.timezone(datetime.timedelta(hours=9))

API_URL = "https://api.twitter.com/2/tweets"

X_API_KEY = os.environ.get("X_API_KEY", "").strip()
X_API_SECRET = os.environ.get("X_API_SECRET", "").strip()
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "").strip()
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "").strip()

# CI では未設定のリポジトリ変数が空文字として渡るため、空の場合も既定値にフォールバックする
DEFAULT_HASHTAGS = "#資源循環 #サーキュラーエコノミー #リサイクル"
HASHTAGS = os.environ.get("X_HASHTAGS", "").strip() or DEFAULT_HASHTAGS

# カテゴリ別ポストの投稿時刻(JST)。"HH:MM=キーワード" でカテゴリを指名でき、
# キーワードなしのスロットには、指名されなかったカテゴリを本文の順で埋める。
# ここを変えたら .github/workflows/x_post.yml の cron も合わせること。
DEFAULT_SLOTS = "08:00=政策,12:00=相場,15:00=産業,18:00=技術,21:00=海外"


def _parse_slots(spec):
    slots = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        time_part, _, keyword = chunk.partition("=")
        slots.append((time_part.strip(), keyword.strip()))
    return slots


POST_SLOTS = _parse_slots(os.environ.get("X_POST_SLOTS", "").strip() or DEFAULT_SLOTS)
QUEUE_PATH = os.environ.get("X_QUEUE_PATH", os.path.join("state", "x_queue.json"))

# カテゴリ名(部分一致)ごとの絵文字。見出し文言が多少ぶれても拾えるようにする。
CATEGORY_EMOJI = (
    ("相場", "📈"),
    ("政策", "🏛️"),
    ("法規", "🏛️"),
    ("技術", "🔬"),
    ("研究", "🔬"),
    ("産業", "🏭"),
    ("海外", "🌍"),
)

# X の投稿上限は「加重文字数」280。日本語などの全角は1文字=2としてカウントされ、
# URL は実際の長さによらず t.co 短縮後の 23 文字として扱われる。
MAX_WEIGHTED = 280
URL_WEIGHT = 23
_URL_RE = re.compile(r"https?://\S+")

# 加重1でカウントされるコードポイント範囲(twitter-text の既定設定)
_LIGHT_RANGES = (
    (0x0000, 0x10FF),
    (0x2000, 0x200D),
    (0x2010, 0x201F),
    (0x2032, 0x2037),
)


def _char_weight(ch):
    cp = ord(ch)
    for lo, hi in _LIGHT_RANGES:
        if lo <= cp <= hi:
            return 1
    return 2


def weighted_len(text):
    """X の加重文字数を返す(URLは23文字換算)。"""
    total = 0
    pos = 0
    for m in _URL_RE.finditer(text):
        total += sum(_char_weight(c) for c in text[pos:m.start()])
        total += URL_WEIGHT
        pos = m.end()
    total += sum(_char_weight(c) for c in text[pos:])
    return total


def _truncate_weighted(text, limit):
    """加重文字数が limit 以内に収まるよう末尾を切り詰める(切った場合は…を付ける)。"""
    if weighted_len(text) <= limit:
        return text
    out = []
    total = 0
    for ch in text:
        w = _char_weight(ch)
        if total + w > limit - 2:  # … の分(全角=2)を残す
            break
        out.append(ch)
        total += w
    return "".join(out).rstrip() + "…"


def _fill_lines(candidates, budget, max_lines=3):
    """加重文字数 budget に収まるだけ行を詰める。

    途中で切れた文章は読みにくいので、丸ごと入る項目だけを採用する。
    1件も入らないときに限り、先頭の項目を入る範囲まで切り詰める。
    """
    lines = []
    for cand in candidates:
        if len(lines) >= max_lines or budget <= 0:
            break
        cost = weighted_len(cand) + 1  # 改行分
        if cost <= budget:
            lines.append(cand)
            budget -= cost
        elif not lines:
            lines.append(_truncate_weighted(cand, budget - 1))
            budget = 0
    return lines


def _strip_markdown(line):
    line = _URL_RE.sub("", line)
    line = re.sub(r"[*`_\[\]]", "", line)
    return re.sub(r"\s+", " ", line).strip()


def extract_categories(body_md):
    """本文Markdownを [(カテゴリ名, [箇条書き, ...]), ...] に分解する(本文の並び順)。

    「参照ソース」「スポンサー」以降は対象外。
    """
    text = body_md
    for marker in ("\n---\n## 参照ソース", "\n---\n## 📣 スポンサー", "\n---\n⚠️"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]

    categories = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            name = _strip_markdown(line.lstrip("#").strip())
            # 「1. 政策・法規制」→「政策・法規制」
            name = re.sub(r"^\d+[.、．]\s*", "", name)
            categories.append((name, []))
            continue
        if line.startswith(("-", "・", "*")):
            item = _strip_markdown(line.lstrip("-・*").strip())
            if not item:
                continue
            if not categories:
                categories.append(("", []))
            categories[-1][1].append(item)
    return [(name, items) for name, items in categories if items]


def extract_highlights(body_md, per_category=1):
    """カテゴリごとに先頭 per_category 件を取った (カテゴリ名, 箇条書き) のリスト。"""
    return [
        (name, item)
        for name, items in extract_categories(body_md)
        for item in items[:per_category]
    ]


def _category_emoji(name):
    for key, emoji in CATEGORY_EMOJI:
        if key in name:
            return emoji
    return "📰"


def build_reply(url, label="▼全文はこちら"):
    """記事URLをぶら下げるリプライ本文。

    URL入りポストは表示が伸びにくいため、本文にはURLを入れず
    リプライとしてぶら下げる。
    """
    return f"{label}\n{url}"


def build_post(date, body_md):
    """記事の要点を伝えるメインの投稿文を組み立てる(URLはリプライに回す)。"""
    header = f"📅 {date.strftime('%Y/%m/%d')} 資源循環朝刊"
    footer = HASHTAGS

    budget = MAX_WEIGHTED - weighted_len(f"{header}\n\n\n\n{footer}")
    candidates = [
        f"・{category}: {item}" if category else f"・{item}"
        for category, item in extract_highlights(body_md)
    ]
    body = "\n".join(_fill_lines(candidates, budget))
    return f"{header}\n\n{body}\n\n{footer}" if body else f"{header}\n\n{footer}"


def build_category_post(date, category, items):
    """1カテゴリ分の投稿文を組み立てる(URLはリプライに回す)。入る範囲で箇条書きを詰める。"""
    header = f"{_category_emoji(category)} {category}({date.strftime('%m/%d')})"
    footer = HASHTAGS

    budget = MAX_WEIGHTED - weighted_len(f"{header}\n\n\n\n{footer}")
    lines = _fill_lines([f"・{item}" for item in items], budget)

    if not lines:
        return None
    return f"{header}\n\n" + "\n".join(lines) + f"\n\n{footer}"


def assign_slots(categories, slots=None):
    """スロットにカテゴリを割り当てて [(時刻, カテゴリ名, 箇条書き)] を返す。

    キーワード付きスロットには名前が部分一致するカテゴリを優先で割り当て、
    残りのスロットは未割り当てのカテゴリで本文の順に埋める。
    スロット数を超えたカテゴリは投稿対象外(その日は見送り)。
    """
    slots = POST_SLOTS if slots is None else slots
    remaining = list(categories)
    assigned = {}

    for time_part, keyword in slots:
        if not keyword:
            continue
        match = next((c for c in remaining if keyword in c[0]), None)
        if match:
            assigned[time_part] = match
            remaining.remove(match)

    result = []
    for time_part, _ in slots:
        if time_part in assigned:
            result.append((time_part, *assigned[time_part]))
        elif remaining:
            result.append((time_part, *remaining.pop(0)))
    result.sort(key=lambda r: r[0])
    return result


def build_queue(date, body_md, url):
    """カテゴリ別ポストの投稿予定(キュー)を組み立てる。"""
    items = []
    for slot, category, bullets in assign_slots(extract_categories(body_md)):
        text = build_category_post(date, category, bullets)
        if text:
            items.append(
                {
                    "slot": slot,
                    "category": category,
                    "text": text,
                    "reply": build_reply(url, "▼詳細は本日の朝刊で"),
                    "posted": False,
                }
            )
    return {"date": date.strftime("%Y-%m-%d"), "url": url, "items": items}


def save_queue(queue, path=QUEUE_PATH):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
    print(f"X投稿キューを書き出しました: {path}(全{len(queue['items'])}件)")


def load_queue(path=QUEUE_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"X投稿キューの読み込みに失敗: {e}")
        return None


def is_configured():
    return all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET])


def _auth():
    return OAuth1(
        X_API_KEY,
        client_secret=X_API_SECRET,
        resource_owner_key=X_ACCESS_TOKEN,
        resource_owner_secret=X_ACCESS_TOKEN_SECRET,
    )


class XApiError(RuntimeError):
    """X API がエラーを返したときの例外(原因の切り分け用に本文を保持する)。"""

    HINTS = {
        401: "キーの値が違う可能性があります(前後の空白や改行が混ざっていないか確認)。",
        402: "X側のクレジット残高が不足しています。Developer Portal で残高を確認してください。",
        403: "アクセストークンが read 権限のままか、同じ本文の重複投稿の可能性があります。",
        429: "レート上限に達しています。投稿間隔かスロット数を見直してください。",
    }

    def __init__(self, status, body):
        self.status = status
        self.body = body
        hint = self.HINTS.get(status, "")
        super().__init__(f"X API {status}: {body}" + (f"\nヒント: {hint}" if hint else ""))

    @property
    def is_duplicate(self):
        return self.status == 403 and "duplicate" in self.body.lower()

    @property
    def is_out_of_credit(self):
        """クレジット残高切れ。設定ミスではないのでジョブは落とさず見送る。"""
        return self.status == 402


def _post(text, reply_to=None):
    payload = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    res = requests.post(API_URL, json=payload, auth=_auth(), timeout=15)
    if res.status_code >= 400:
        raise XApiError(res.status_code, res.text)
    return res.json()["data"]["id"]


def _post_with_reply(text, reply_text):
    """本文を投稿し、記事URLのリプライをぶら下げる。

    リプライだけ失敗しても本文は世に出ているので、例外は投げずに記録する。
    """
    tweet_id = _post(text)
    if reply_text:
        try:
            _post(reply_text, reply_to=tweet_id)
        except Exception as e:
            print(f"URLリプライの投稿に失敗(本文は投稿済み): {e}")
    return tweet_id


def post_entry(date, body_md, url):
    """朝の全体ポストを投稿し、カテゴリ別ポストのキューを書き出す。

    キューは認証が未設定でも書き出す(あとから Secrets を入れれば当日分から動く)。
    """
    save_queue(build_queue(date, body_md, url))

    if not is_configured():
        print("X の認証情報が未設定のため投稿をスキップします。")
        return False
    if not url:
        print("記事URLが取得できないため X 投稿をスキップします。")
        return False

    try:
        tweet_id = _post_with_reply(build_post(date, body_md), build_reply(url))
    except XApiError as e:
        if e.is_out_of_credit:
            print("⚠️ Xのクレジット残高が不足しているため、朝の全体ポストを見送りました。")
            return False
        raise
    print(f"X投稿成功: https://x.com/i/status/{tweet_id}")
    return True


def post_due(now=None, path=QUEUE_PATH):
    """キューのうち、時刻が来ていて未投稿の先頭1件を投稿する。

    1回の起動で1件だけ投稿し、取りこぼしは次のスロットに持ち越す。
    投稿したら True を返す(呼び出し側はキューの変更をコミットする)。
    """
    now = now or datetime.datetime.now(JST)
    queue = load_queue(path)
    if not queue:
        print("X投稿キューがありません。スキップします。")
        return False
    if queue.get("date") != now.strftime("%Y-%m-%d"):
        print(f"キューが本日分ではありません(date={queue.get('date')})。スキップします。")
        return False

    hhmm = now.strftime("%H:%M")
    due = [i for i in queue["items"] if not i.get("posted") and i["slot"] <= hhmm]
    if not due:
        print(f"現在時刻 {hhmm}(JST)に投稿すべき項目はありません。")
        return False
    if not is_configured():
        print("X の認証情報が未設定のため投稿をスキップします。")
        return False

    item = due[0]
    try:
        tweet_id = _post_with_reply(item["text"], item.get("reply"))
    except XApiError as e:
        if e.is_out_of_credit:
            # 残高が復活すれば投稿されるよう、未投稿のまま残して見送る
            print(f"⚠️ Xのクレジット残高が不足しているため投稿できません[{item['slot']} {item['category']}]")
            print(f"   {e.body}")
            return False
        if not e.is_duplicate:
            raise
        # 同じ本文が既に投稿されている(前回の実行が記録前に落ちた等)。
        # 投稿済みとして記録し、次のスロットへ進める。
        print(f"同一内容が投稿済みのため、投稿済みとして記録します[{item['slot']} {item['category']}]")
        item["posted"] = True
        item["posted_at"] = now.isoformat(timespec="seconds")
        item["note"] = "duplicate"
        save_queue(queue, path)
        return False

    item["posted"] = True
    item["posted_at"] = now.isoformat(timespec="seconds")
    item["tweet_id"] = tweet_id
    save_queue(queue, path)
    print(f"X投稿成功[{item['slot']} {item['category']}]: https://x.com/i/status/{tweet_id}")
    return True


SAMPLE_MD = """### 1. 政策・法規制
- 環境省は、2026年7月21日付で「令和7年度補正予算 地域資源の徹底活用に向けた資源循環加速化事業（モデル事業）」の公募採択結果を公表した。
- 経産省が再生材利用率の目標設定に向けた検討会を開催。

### 2. 技術・研究動向
- 使用済みリチウムイオン電池からのリチウム回収技術の実証が国内で開始。

### 3. 産業動向
- 大手商社が欧州のプラスチックリサイクル企業へ出資すると発表。

### 4. 海外規制・市場
- EUのESPRに基づく個別製品規則の第一弾が公表された。

### 5. 相場メモ
- 東京製鉄がH2の購入価格を1トンあたり500円引き上げた(8/1出荷分から)。
- 電気銅建値は横ばい圏で推移。
"""


if __name__ == "__main__":
    if "--post-due" in sys.argv:
        # 実投稿。GitHub Actions のスロット実行から呼ばれる
        post_due()
    else:
        # 動作確認用: 投稿はせず、組み立てた本文と加重文字数を表示する
        url = "https://circular-news-jp.github.io/daily-news/entries/2026-08-02.html"
        today = datetime.datetime.now(JST).date()

        def show(label, text, reply):
            print(f"\n===== {label} =====")
            print(text)
            print(f"[加重文字数: {weighted_len(text)}]")
            print("--- ぶら下げるリプライ ---")
            print(reply)

        show("朝の全体ポスト", build_post(today, SAMPLE_MD), build_reply(url))
        for item in build_queue(today, SAMPLE_MD, url)["items"]:
            show(f"{item['slot']} {item['category']}", item["text"], item["reply"])
