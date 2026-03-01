from flask import Flask, request
import requests
import os
import json

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")

@app.route("/")
def home():
    return "Bot is running!", 200

@app.route("/callback", methods=["POST"])
def callback():
    body = request.json

    print("==== CALLBACK START ====")
    print("TOKEN:", CHANNEL_ACCESS_TOKEN)
    print("BODY:", body)

    for event in body.get("events", []):
        if event["type"] == "message" and event["message"]["type"] == "text":
            reply_token = event["replyToken"]
            user_message = event["message"]["text"]

            send_reply(reply_token, f"あなたはこう言いました🌙\n{user_message}")

    return "OK", 200


def send_reply(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    response = requests.post(url, headers=headers, data=json.dumps(data))

    print("==== LINE RESPONSE ====")
    print("STATUS:", response.status_code)
    print("TEXT:", response.text)
