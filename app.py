from flask import Flask, request, abort

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

@app.route("/callback", methods=['POST'])
def callback():
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
