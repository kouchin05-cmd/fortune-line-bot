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
# 深掘り定義（5ジャンル）
# -------------------------
CONCERNS = ["恋愛", "復縁", "不倫", "結婚", "金運"]

SITUATIONS = {
    "恋愛": ["① 片想い", "② 両想いか分からない", "③ 付き合っている"],
    "復縁": ["① 連絡は取れている", "② ブロックされている", "③ 自然消滅"],
    "不倫": ["① 相手が既婚", "② 自分が既婚", "③ お互い既婚"],
    "結婚": ["① 相手がいる", "② 出会いがない", "③ 婚活中"],
    "金運": ["① 収入が増えない", "② 借金がある", "③ 投資で迷っている"],
}

EMOTION_QUESTION = {
    "恋愛": "その人のことで最近いちばんつらかったことを、短くでいいので教えて🌙",
    "復縁": "まだ相手を忘れられない理由を、短くでいいので教えて🌙",
    "不倫": "この関係を「続けたい / 終わらせたい」どちらに気持ちが近い？理由も一言でOK🌙",
    "結婚": "結婚に対する不安を、短くでいいので教えて🌙",
    "金運": "お金のことでいちばん怖いことを、短くでいいので教えて🌙",
}

# -------------------------
# DB（状態管理）
# stage:
#   choose_concern
#   choose_situation
#   ask_emotion
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
        emotion TEXT,
        stage TEXT DEFAULT 'choose_concern'
    )
    """)

    # 既存DBが古い場合に列を追加（あっても無視）
    for col, coltype in [("situation", "TEXT"), ("emotion", "TEXT"), ("stage", "TEXT"), ("concern", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    conn.commit()
    conn.close()

def get_user(uid: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT line_user_id, concern, situation, emotion, stage FROM users WHERE line_user_id=?", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(uid: str, concern=None, situation=None, emotion=None, stage=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    INSERT INTO users (line_user_id, concern, situation, emotion, stage)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(line_user_id) DO UPDATE SET
      concern   = COALESCE(excluded.concern, users.concern),
      situation = COALESCE(excluded.situation, users.situation),
      emotion   = COALESCE(excluded.emotion, users.emotion),
      stage     = COALESCE(excluded.stage, users.stage)
    """, (uid, concern, situation, emotion, stage))
    conn.commit()
    conn.close()

init_db()

# -------------------------
# LINE API
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

def reply_quickreply_choose_concern(reply_token: str):
    url = "https://api.line.me/v2/bot/message/reply"

    items = []
    for c in CONCERNS:
        items.append({"type": "action", "action": {"type": "message", "label": c, "text": c}})

    payload = {
        "replyToken": reply_token,
        "messages": [{
            "type": "text",
            "text": "どの悩みを占う？🌙（ボタンで選んでね）",
            "quickReply": {"items": items}
        }]
    }
    r = requests.post(url, headers=_line_headers(), data=json.dumps(payload))
    print("LINE QUICK:", r.status_code, r.text)

# -------------------------
# OpenAI 占い生成
# -------------------------
def generate_fortune(birthday: str, concern: str, situation: str, emotion: str) -> str:
    if not client:
        return "設定エラー：OPENAI_API_KEYが未設定です。"

    # 心理設計：共感→具体助言→開運アクション→“選ばれた人だけ”導線
    system = (
        "あなたは恋愛・人生相談に強い占い師です。"
        "ユーザーの感情に強く共鳴し、安心させ、具体的な行動に落とし込みます。"
        "断定しすぎず、希望を残しつつも曖昧すぎない表現にします。"
        "日本語、300〜550文字程度。"
        "必ず最後に『今日の開運アクション：〇〇』を1つ入れてください。"
        "文章はやさしく、品があり、過度に煽らない。"
    )

    user = (
        f"生年月日：{birthday}\n"
        f"相談ジャンル：{concern}\n"
        f"状況：{situation}\n"
        f"悩みの核心（本人の言葉）：{emotion}\n\n"
        "上記を踏まえて占い結果を作ってください。"
        "特に『本人の言葉』に寄り添い、気持ちが軽くなる一文を最初に入れてください。"
        "そのうえで、今後1週間の流れと、今すぐできる行動を2つ提案してください。"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.85,
    )

    text = resp.choices[0].message.content.strip()
    return f"🔮{concern}占い（{birthday}）\n状況：{situation}\n\n{text}"

def add_soft_affiliate_hook(fortune_text: str) -> str:
    if not AFFILIATE_LINK:
        return fortune_text

    # 心理設計：限定感／自己選択／売り込み回避
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

        row = get_user(uid)
        if not row:
            upsert_user(uid, stage="choose_concern")
            reply_quickreply_choose_concern(reply_token)
            continue

        _, concern, situation, emotion, stage = row

        # いつでもリセット
        if text.lower() in ["reset", "リセット"]:
            upsert_user(uid, concern=None, situation=None, emotion=None, stage="choose_concern")
            reply_quickreply_choose_concern(reply_token)
            continue

        # 1) 悩み選択
        if stage == "choose_concern":
            if text in CONCERNS:
                upsert_user(uid, concern=text, situation=None, emotion=None, stage="choose_situation")
                options = "\n".join(SITUATIONS[text])
                reply_text(reply_token, f"OK🌙『{text}』ね。\nまず状況を選んでね。\n{options}")
            else:
                reply_quickreply_choose_concern(reply_token)
            continue

        # 2) 状況選択
        if stage == "choose_situation":
            if concern in SITUATIONS and text in SITUATIONS[concern]:
                # "① 片想い" のまま保存（短く整形したければここで加工）
                upsert_user(uid, situation=text, stage="ask_emotion")
                q = EMOTION_QUESTION.get(concern, "今の気持ちを短くでいいので教えて🌙")
                reply_text(reply_token, q)
            else:
                options = "\n".join(SITUATIONS.get(concern, []))
                reply_text(reply_token, f"その中から選んでね🌙\n{options}\n（やり直すなら「リセット」）")
            continue

        # 3) 感情入力（自由入力）
        if stage == "ask_emotion":
            # 文字数を軽く制限（長すぎると読みづらいので）
            em = text[:120]
            upsert_user(uid, emotion=em, stage="wait_birthday")
            reply_text(reply_token, "ありがとう🌙\n最後に、生年月日をこの形式で送ってね：1995/05/01")
            continue

        # 4) 生年月日待ち → 占い生成
        if stage == "wait_birthday":
            if DATE_RE.match(text):
                try:
                    fortune = generate_fortune(
                        birthday=text,
                        concern=concern or "恋愛",
                        situation=situation or "",
                        emotion=emotion or ""
                    )
                    fortune = add_soft_affiliate_hook(fortune)
                    reply_text(reply_token, fortune)
                except Exception as e:
                    # 429やその他はログに出して、ユーザーには優しく返す
                    msg = repr(e)
                    print("OPENAI ERROR:", msg)
                    if "429" in msg:
                        reply_text(reply_token, "今ちょっと混み合ってるみたい💦 30秒ほど待ってからもう一度送ってね🌙")
                    else:
                        reply_text(reply_token, "ごめんね、占いの生成でエラーが出たみたい💦 もう一度送ってみてね。")

                # 次の利用のために最初に戻す（リピート導線）
                upsert_user(uid, stage="choose_concern", situation=None, emotion=None)
            else:
                reply_text(reply_token, "生年月日をこの形式で送ってね🌙\n例：1995/05/01\n（やり直すなら「リセット」）")
            continue

        # 想定外は初期化
        upsert_user(uid, concern=None, situation=None, emotion=None, stage="choose_concern")
        reply_quickreply_choose_concern(reply_token)

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
