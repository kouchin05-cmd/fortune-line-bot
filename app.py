import os
from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

@app.route("/callback", methods=['POST'])
def callback():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
