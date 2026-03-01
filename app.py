import os
import json
import re
from flask import Flask, request
import requests
from openai import OpenAI

app = Flask(__name__)

# Railway Variables
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

@app.route("/")
def home():
    return "Bot is running!", 200

@app.route("/callback", methods=["POST"])
def callback():
    body = request.json

    # デバッグ（必要なら後で消せる）
    print("==== CALLBACK START ====")
    print("TOKEN:", "SET" if CHANNEL_ACCESS_TOKEN else None)
    print("OPENAI:", "SET" if OPENAI_API_KEY else None)
    print("BODY:", body)

    for event in body.get("events", []):
        if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
            reply_token = event["replyToken"]
            user_message = event["message"]["text"].strip()

            # 1) 生年月日形式なら占い
            if DATE_RE.match(user_message):
                result = generate_fortune(birthday=user_message)
                send_reply(reply_token, result)
            else:
                # 2) 形式が違うなら案内
                guide = (
                    "生年月日をこの形式で送ってね🌙\n"
                    "例：1995/05/01"
                )
                send_reply(reply_token, guide)

    return "OK", 200


def generate_fortune(birthday: str) -> str:
    # OpenAIキーが未設定のときの保険
    if not OPENAI_API_KEY:
        return "設定エラー：OPENAI_API_KEYが未設定です。RailwayのVariablesを確認してね。"

    system = (
        "あなたは恋愛占いに強い占い師です。"
        "ユーザーの不安に寄り添い、優しく、安心できる文章で占います。"
        "断定しすぎず、行動につながる助言を入れます。"
        "日本語、300〜500文字。"
        "最後に『今日の開運アクション：〇〇』を1つ入れてください。"
    )

    user = f"生年月日：{birthday}\n恋愛運を占ってください。"

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
        return f"🔮恋愛占い（{birthday}）\n\n{text}"
    except Exception as e:
        print("OPENAI ERROR:", str(e))
        return "ごめんね、占いの生成でエラーが出たみたい💦 もう一度送ってみてね。"


def send_reply(reply_token: str, message: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": message}],
    }
    r = requests.post(url, headers=headers, data=json.dumps(data))
    print("==== LINE RESPONSE ====")
    print("STATUS:", r.status_code)
    print("TEXT:", r.text)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
