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

client = OpenAI(api_key=OPENAI_API_KEY)

DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

# -----------------
# DB（ユーザー状態）
# -----------------
DB_PATH = "fortune.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        line_user_id TEXT PRIMARY KEY,
        concern TEXT,
        stage TEXT DEFAULT 'choose_concern'
    )
    """)
    conn.commit()
    conn.close()

def get_user(line_user_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT line_user_id, concern, stage FROM users WHERE line_user_id=?", (line_user_id,))
    row = c.fetchone()
    conn.close()
    return row  # (id, concern, stage) or None

def upsert_user(line_user_id: str, concern: str = None, stage: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    INSERT INTO users (line_user_id, concern, stage)
    VALUES (?, ?, ?)
    ON CONFLICT(line_user_id) DO UPDATE SET
      concern = COALESCE(excluded.concern, users.concern),
      stage   = COALESCE(excluded.stage, users.stage)
    """, (line_user_id, concern, stage))
    conn.commit()
    conn.close()

init_db()

# -----------------
# LINE送信
# -----------------
def reply_text(reply_token: str, text: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    r = requests.post(url, headers=headers, data=json.dumps(payload))
    # デバッグしたい時だけ見る（普段は不要）
    print("LINE REPLY:", r.status_code, r.text)

def reply_quickreply_choose_concern(reply_token: str):
    """
    悩み選択をQuick Replyで出す
    """
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

    quick_items = [
        {"type": "action", "action": {"type": "message", "label": "恋愛", "text": "恋愛"}},
        {"type": "action", "action": {"type": "message", "label": "復縁", "text": "復縁"}},
        {"type": "action", "action": {"type": "message", "label": "不倫", "text": "不倫"}},
        {"type": "action", "action": {"type": "message", "label": "結婚", "text": "結婚"}},
        {"type": "action", "action": {"type": "message", "label": "金運", "text": "金運"}},
    ]

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": "どの悩みを占う？🌙（ボタンで選んでね）",
                "quickReply": {"items": quick_items},
            }
        ],
    }

    r = requests.post(url, headers=headers, data=json.dumps(payload))
    print("LINE QUICKREPLY:", r.status_code, r.text)

# -----------------
# OpenAI 占い生成
# -----------------
def generate_fortune(birthday: str, concern: str) -> str:
    if not OPENAI_API_KEY:
        return "設定エラー：OPENAI_API_KEYが未設定です。"

    system = (
        "あなたは恋愛・人生相談に強い占い師です。"
        "ユーザーに寄り添い、優しく、安心できる日本語で占います。"
        "断定しすぎず、行動につながる助言を入れます。"
        "文字数は300〜500文字。"
        "最後に必ず『今日の開運アクション：〇〇』を1つ入れてください。"
    )

    user = f"生年月日：{birthday}\n相談ジャンル：{concern}\nこの相談ジャンルに合わせた占いを作ってください。"

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.8,
        )
        text = resp.choices[0].message.content.strip()
        return f"🔮{concern}占い（{birthday}）\n\n{text}"
    except Exception as e:
        print("OPENAI ERROR:", repr(e))
        return "ごめんね、占いの生成でエラーが出たみたい💦 もう一度送ってみてね。"

# -----------------
# Routes
# -----------------
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
        line_user_id = event["source"]["userId"]
        text = msg.get("text", "").strip()

        # ユーザー状態を取得（なければ作る）
        row = get_user(line_user_id)
        if not row:
            upsert_user(line_user_id, stage="choose_concern")
            reply_quickreply_choose_concern(reply_token)
            continue

        _, concern, stage = row

        # いつでもリセットできる隠しコマンド（便利）
        if text in ["リセット", "reset"]:
            upsert_user(line_user_id, concern=None, stage="choose_concern")
            reply_quickreply_choose_concern(reply_token)
            continue

        # 1) 悩み選択ステージ
        if stage == "choose_concern":
            if text in ["恋愛", "復縁", "不倫", "結婚", "金運"]:
                upsert_user(line_user_id, concern=text, stage="wait_birthday")
                reply_text(reply_token, f"OK🌙『{text}』で占うね。\n生年月日をこの形式で送ってね：1995/05/01")
            else:
                reply_quickreply_choose_concern(reply_token)
            continue

        # 2) 生年月日待ちステージ
        if stage == "wait_birthday":
            if DATE_RE.match(text):
                result = generate_fortune(birthday=text, concern=concern or "恋愛")
                reply_text(reply_token, result)
                # 連続利用できるように、次も悩み選択に戻す（好みで変更OK）
                upsert_user(line_user_id, stage="choose_concern")
            else:
                reply_text(reply_token, "生年月日をこの形式で送ってね🌙\n例：1995/05/01\n（やり直すなら「リセット」）")
            continue

        # 想定外：初期化
        upsert_user(line_user_id, stage="choose_concern")
        reply_quickreply_choose_concern(reply_token)

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
