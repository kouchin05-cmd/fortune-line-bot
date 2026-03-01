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

AFFILIATE_LINKS = {
    "恋愛": os.environ.get("AFFILIATE_RENAI", ""),
    "復縁": os.environ.get("AFFILIATE_FUKUEN", ""),
    "不倫": os.environ.get("AFFILIATE_FURIN", ""),
    "結婚": os.environ.get("AFFILIATE_KEKKON", ""),
    "金運": os.environ.get("AFFILIATE_KINUN", ""),
}

client = OpenAI(api_key=OPENAI_API_KEY)

DB_PATH = "fortune.db"

CONCERNS = ["恋愛", "復縁", "不倫", "結婚", "金運"]

SITUATIONS = {
    "恋愛": ["片想い", "両想い不明", "交際中"],
    "復縁": ["連絡OK", "ブロック", "自然消滅"],
    "不倫": ["相手既婚", "自分既婚", "お互い既婚"],
    "結婚": ["相手あり", "出会いなし", "婚活中"],
    "金運": ["収入停滞", "借金不安", "投資迷い"],
}

INTENSITIES = {
    "恋愛": ["告白したい", "気持ち知りたい", "別れ迷い"],
    "復縁": ["まだ好き", "迷ってる", "諦めたい"],
    "不倫": ["続けたい", "終わらせたい", "決めきれない"],
    "結婚": ["早く決めたい", "不安が強い", "迷ってる"],
    "金運": ["今すぐ変えたい", "不安が大きい", "様子見"],
}

# -----------------
# 生年月日ゆる判定
# -----------------
def normalize_birthday(text):
    t = text.strip()
    patterns = [
        r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$",
        r"^(\d{4})(\d{2})(\d{2})$",
        r"^(\d{4})年(\d{1,2})月(\d{1,2})日$",
    ]
    for p in patterns:
        m = re.match(p, t)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return f"{y}/{mo:02d}/{d:02d}"
    return None

# -----------------
# DB
# -----------------
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
    conn.commit()
    conn.close()

def get_user(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE line_user_id=?", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(uid, concern=None, situation=None, intensity=None, stage=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    INSERT INTO users (line_user_id, concern, situation, intensity, stage)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(line_user_id) DO UPDATE SET
      concern=COALESCE(excluded.concern, users.concern),
      situation=COALESCE(excluded.situation, users.situation),
      intensity=COALESCE(excluded.intensity, users.intensity),
      stage=COALESCE(excluded.stage, users.stage)
    """, (uid, concern, situation, intensity, stage))
    conn.commit()
    conn.close()

init_db()

# -----------------
# LINE返信
# -----------------
def headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

def reply_text(reply_token, text):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=headers(),
        data=json.dumps({"replyToken": reply_token, "messages": [{"type": "text", "text": text}]})
    )

def reply_quick(reply_token, text, options):
    items = [{"type": "action", "action": {"type": "message", "label": o, "text": o}} for o in options]
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=headers(),
        data=json.dumps({
            "replyToken": reply_token,
            "messages": [{
                "type": "text",
                "text": text,
                "quickReply": {"items": items}
            }]
        })
    )

# -----------------
# 鑑定生成
# -----------------
def generate_report(birthday, concern, situation, intensity):
    system = """
あなたはプロの占い師です。
必ず以下の形式で簡易鑑定書を作成してください。

【総合】
【今の流れ】
【行動アドバイス】
【NG行動】
【開運アクション】
"""

    user = f"""
生年月日：{birthday}
ジャンル：{concern}
状況：{situation}
本気度：{intensity}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.8,
    )

    return response.choices[0].message.content.strip()

# -----------------
# Webhook
# -----------------
@app.route("/callback", methods=["POST"])
def callback():
    body = request.json

    for event in body.get("events", []):
        if event["type"] != "message":
            continue

        reply_token = event["replyToken"]
        uid = event["source"]["userId"]
        text = event["message"]["text"].strip()

        row = get_user(uid)

        if not row:
            upsert_user(uid, stage="choose_concern")
            reply_quick(reply_token, "どの悩みを占う？🌙", CONCERNS)
            continue

        _, concern, situation, intensity, stage = row

        if stage == "choose_concern":
            if text in CONCERNS:
                upsert_user(uid, concern=text, stage="choose_situation")
                reply_quick(reply_token, "今の状況は？", SITUATIONS[text])
            else:
                reply_quick(reply_token, "どの悩みを占う？🌙", CONCERNS)
            continue

        if stage == "choose_situation":
            if text in SITUATIONS.get(concern, []):
                upsert_user(uid, situation=text, stage="choose_intensity")
                reply_quick(reply_token, "本気度は？", INTENSITIES[concern])
            continue

        if stage == "choose_intensity":
            if text in INTENSITIES.get(concern, []):
                upsert_user(uid, intensity=text, stage="wait_birthday")
                reply_text(reply_token, "生年月日を送ってね🌙（例：1995/05/01）")
            continue

        if stage == "wait_birthday":
            bday = normalize_birthday(text)
            if bday:
                report = generate_report(bday, concern, situation, intensity)
                link = AFFILIATE_LINKS.get(concern, "")
                if link:
                    report += f"\n\n――――\n特別鑑定はこちら👇\n{link}"
                reply_text(reply_token, report)
                upsert_user(uid, stage="choose_concern")
                reply_quick(reply_token, "他の悩みも占う？🌙", CONCERNS)
            else:
                reply_text(reply_token, "生年月日を正しい形式で送ってね🌙")
            continue

    return "OK", 200

@app.route("/")
def home():
    return "Bot is running!"
