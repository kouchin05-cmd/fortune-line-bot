import os
import json
import re
import sqlite3
from flask import Flask, request
import requests
from openai import OpenAI

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AFFILIATE_LINK = os.environ.get("AFFILIATE_LINK", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

DB_PATH = "fortune.db"

# -------------------------
# 5ジャンル
# -------------------------
CONCERNS = ["恋愛", "復縁", "不倫", "結婚", "金運"]

# 状況（ボタン）
SITUATIONS = {
    "恋愛": ["片想い", "両想い不明", "交際中"],
    "復縁": ["連絡OK", "ブロック", "自然消滅"],
    "不倫": ["相手既婚", "自分既婚", "お互い既婚"],
    "結婚": ["相手あり", "出会いなし", "婚活中"],
    "金運": ["収入停滞", "借金不安", "投資迷い"],
}

# 本気度（心理トリガー：ボタン）
INTENSITIES = {
    "恋愛": ["告白したい", "気持ち知りたい", "別れ迷い"],
    "復縁": ["まだ好き", "迷ってる", "諦めたい"],
    "不倫": ["続けたい", "終わらせたい", "決めきれない"],
    "結婚": ["早く決めたい", "不安が強い", "迷ってる"],
    "金運": ["今すぐ変えたい", "不安が大きい", "様子見"],
}

# -------------------------
# DB（状態管理）
# stage:
#   choose_concern
#   choose_situation
#   choose_intensity
#   wait_birthday
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        line_user_id TEXT PRIMARY KEY,
        concern TEXT,
        situation TEXT,
        intensity TEXT,
        stage TEXT DEFAULT 'choose_concern'
    )
    """)
    # 既存DBが古い場合に列追加（あっても無視）
    for col, coltype in [("situation","TEXT"), ("intensity","TEXT"), ("stage","TEXT"), ("concern","TEXT")]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")
        except Exception:
            pass
    conn.commit()
    conn.close()

def get_user(uid: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT line_user_id, concern, situation, intensity, stage FROM users WHERE line_user_id=?", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(uid: str, concern=None, situation=None, intensity=None, stage=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    INSERT INTO users (line_user_id, concern, situation, intensity, stage)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(line_user_id) DO UPDATE SET
      concern   = COALESCE(excluded.concern, users.concern),
      situation = COALESCE(excluded.situation, users.situation),
      intensity = COALESCE(excluded.intensity, users.intensity),
      stage     = COALESCE(excluded.stage, users.stage)
    """, (uid, concern, situation, intensity, stage))
    conn.commit()
    conn.close()

init_db()

# -------------------------
# LINE送信
# -------------------------
def _line_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

def reply_text(reply_token: str, text: str):
    url = "https://api.line.me/v2/bot/message/reply"
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    r = requests.post(url, headers=_line_headers(), data=json.dumps(payload))
    print("LINE REPLY:", r.status_code, r.text)

def reply_quick(reply_token: str, text: str, options: list[str]):
    """
    options: 最大13推奨（QuickReply制限）
    各ボタンは message action で、押すとその文字列を送る
    """
    url = "https://api.line.me/v2/bot/message/reply"
    items = [{"type":"action","action":{"type":"message","label":opt, "text":opt}} for opt in options]
    payload = {
        "replyToken": reply_token,
        "messages": [{
            "type": "text",
            "text": text,
            "quickReply": {"items": items}
        }]
    }
    r = requests.post(url, headers=_line_headers(), data=json.dumps(payload))
    print("LINE QUICK:", r.status_code, r.text)

def choose_concern(reply_token: str):
    reply_quick(reply_token, "どの悩みを占う？🌙", CONCERNS)

def choose_situation(reply_token: str, concern: str):
    opts = SITUATIONS.get(concern, [])
    reply_quick(reply_token, f"OK🌙『{concern}』ね。今の状況を選んでね。", opts)

def choose_intensity(reply_token: str, concern: str):
    opts = INTENSITIES.get(concern, [])
    reply_quick(reply_token, "本気度に近いものを選んでね🌙", opts)

# -------------------------
# OpenAI 占い生成
# -------------------------
def generate_fortune(birthday: str, concern: str, situation: str, intensity: str) -> str:
    if not client:
        return "設定エラー：OPENAI_API_KEYが未設定です。"

    system = (
        "あなたは恋愛・人生相談に強い占い師です。"
        "ユーザーの気持ちに寄り添い、安心させ、具体的な行動に落とし込みます。"
        "断定しすぎず、希望を残しつつも曖昧すぎない表現。"
        "日本語、350〜600文字。"
        "必ず最後に『今日の開運アクション：〇〇』を1つ入れてください。"
        "過度に煽らない。品のある口調。"
    )

    user = (
        f"生年月日：{birthday}\n"
        f"相談ジャンル：{concern}\n"
        f"状況：{situation}\n"
        f"本気度：{intensity}\n\n"
        "この条件に合わせて占いを作ってください。\n"
        "最初に共感の一文→次に1週間の流れ→今すぐできる行動を2つ→最後に開運アクション。"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.85,
    )
    text = resp.choices[0].message.content.strip()
    return f"🔮{concern}占い（{birthday}）\n状況：{situation}\n本気度：{intensity}\n\n{text}"

def add_soft_affiliate_hook(fortune_text: str) -> str:
    if not AFFILIATE_LINK:
        return fortune_text
    hook = (
        "\n\n――――\n"
        "ここまで読んで、まだ心がざわつくなら。\n"
        "本気で流れを変えたい人だけ、こちらを見てください🌙\n"
        f"{AFFILIATE_LINK}"
    )
    return fortune_text + hook

# -------------------------
# Routes
# -------------------------
@app.route("/")
def home():
    return "Bot is running!", 200

@app.route("/callback", methods=["POST"])
def callback():
    body = request.json

    for event in body.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        reply_token = event["replyToken"]
        uid = event["source"]["userId"]
        text = (msg.get("text") or "").strip()

        # 初回ユーザー
        row = get_user(uid)
        if not row:
            upsert_user(uid, stage="choose_concern")
            choose_concern(reply_token)
            continue

        _, concern, situation, intensity, stage = row

        # いつでもリセット
        if text.lower() in ["reset", "リセット"]:
            upsert_user(uid, concern=None, situation=None, intensity=None, stage="choose_concern")
            choose_concern(reply_token)
            continue

        # 1) 悩み選択
        if stage == "choose_concern":
            if text in CONCERNS:
                upsert_user(uid, concern=text, situation=None, intensity=None, stage="choose_situation")
                choose_situation(reply_token, text)
            else:
                choose_concern(reply_token)
            continue

        # 2) 状況選択
        if stage == "choose_situation":
            valid = SITUATIONS.get(concern or "", [])
            if text in valid:
                upsert_user(uid, situation=text, stage="choose_intensity")
                choose_intensity(reply_token, concern)
            else:
                choose_situation(reply_token, concern)
            continue

        # 3) 本気度選択
        if stage == "choose_intensity":
            valid = INTENSITIES.get(concern or "", [])
            if text in valid:
                upsert_user(uid, intensity=text, stage="wait_birthday")
                reply_text(reply_token, "ありがとう🌙 最後に生年月日をこの形式で送ってね：1995/05/01")
            else:
                choose_intensity(reply_token, concern)
            continue

        # 4) 生年月日
        if stage == "wait_birthday":
            if DATE_RE.match(text):
                try:
                    fortune = generate_fortune(
                        birthday=text,
                        concern=concern or "恋愛",
                        situation=situation or "",
                        intensity=intensity or ""
                    )
                    fortune = add_soft_affiliate_hook(fortune)
                    reply_text(reply_token, fortune)
                except Exception as e:
                    msg = repr(e)
                    print("OPENAI ERROR:", msg)
                    if "429" in msg:
                        reply_text(reply_token, "今ちょっと混み合ってるみたい💦 30秒ほど待ってからもう一度送ってね🌙")
                    else:
                        reply_text(reply_token, "ごめんね、占いの生成でエラーが出たみたい💦 もう一度送ってみてね。")

                # 次の利用へ戻す
                upsert_user(uid, stage="choose_concern", situation=None, intensity=None)
                choose_concern(reply_token)
            else:
                reply_text(reply_token, "生年月日をこの形式で送ってね🌙\n例：1995/05/01\n（やり直すなら「リセット」）")
            continue

        # 想定外は初期化
        upsert_user(uid, concern=None, situation=None, intensity=None, stage="choose_concern")
        choose_concern(reply_token)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
