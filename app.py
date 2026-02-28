from flask import Flaskimport os
import sqlite3
import requests
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------
# DB初期化
# -----------------
def init_db():
    conn = sqlite3.connect("fortune.db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        line_id TEXT PRIMARY KEY,
        birthday TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# -----------------
# 占い生成
# -----------------
def generate_fortune(birthday):
    prompt = f"""
    {birthday}生まれの女性向けに、
    恋愛特化の占いを300文字で作成。
    最後に自然にプロ鑑定誘導を入れる。
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content

# -----------------
# LINE返信
# -----------------
def reply_to_line(reply_token, message):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": message}]
    }
    requests.post(url, headers=headers, json=data)

# -----------------
# Webhook
# -----------------
@app.route("/callback", methods=["POST"])
def callback():
    data = request.json
    event = data["events"][0]
    reply_token = event["replyToken"]
    user_id = event["source"]["userId"]
    text = event["message"]["text"]

    conn = sqlite3.connect("fortune.db")
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE line_id=?", (user_id,))
    user = c.fetchone()

    if not user:
        c.execute("INSERT INTO users VALUES (?, ?)", (user_id, text))
        conn.commit()

        fortune = generate_fortune(text)

        affiliate_link = "あなたのアフィリエイトリンク"

        reply_to_line(reply_token, fortune + f"\n\n🔮本気で知りたい方はこちら👇\n{affiliate_link}")
    else:
        reply_to_line(reply_token, "もう一度生年月日を入力してください🌙")

    conn.close()
    return "OK"

@app.route("/")
def home():
    return "Fortune App Running"

app = Flask(__name__)

@app.route("/")
def home():
    return "Fortune App Running"

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
