import os
import json
import re
import sqlite3
import time
from flask import Flask, request
import requests

from openai import OpenAI

app = Flask(__name__)

# ====== LINE / OpenAI ======
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ====== note決済（合言葉） ======
NOTE_PAID_URL = os.environ.get("NOTE_PAID_URL", "").strip()          # note有料記事URL
NOTE_ACCESS_CODE = os.environ.get("NOTE_ACCESS_CODE", "").strip()    # 購入コード（有料記事に記載）
PAID_TTL_DAYS = int(os.environ.get("PAID_TTL_DAYS", "30"))            # 有料権限の有効日数
PAID_TTL_SECONDS = PAID_TTL_DAYS * 24 * 60 * 60

PAID_PRICE_TEXT = "980円"
PAID_LABEL = f"💎 特別鑑定（{PAID_PRICE_TEXT}）"

BUSY_MSG = (
    "いま、少し混み合っているみたい。\n"
    "30秒ほど置いて、もう一度。\n"
    "……月灯りの下で。"
)

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
def normalize_birthday(text: str):
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

def now_ts() -> int:
    return int(time.time())

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

    # note用：paid_until / note_code_used
    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        line_user_id TEXT PRIMARY KEY,
        paid INTEGER DEFAULT 0,
        paid_until INTEGER DEFAULT 0,
        note_code_used TEXT DEFAULT NULL
    )
    """)

    # 既存DBとの互換（カラム不足があっても落ちないように）
    existing_cols = set()
    c.execute("PRAGMA table_info(payments)")
    for row in c.fetchall():
        existing_cols.add(row[1])

    if "paid_until" not in existing_cols:
        try:
            c.execute("ALTER TABLE payments ADD COLUMN paid_until INTEGER DEFAULT 0")
        except Exception:
            pass

    if "note_code_used" not in existing_cols:
        try:
            c.execute("ALTER TABLE payments ADD COLUMN note_code_used TEXT DEFAULT NULL")
        except Exception:
            pass

    conn.commit()
    conn.close()

def get_user(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT line_user_id, concern, situation, intensity, stage FROM users WHERE line_user_id=?", (uid,))
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

def grant_paid(uid: str, code_used: str = None):
    exp = now_ts() + PAID_TTL_SECONDS
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    INSERT INTO payments (line_user_id, paid, paid_until, note_code_used)
    VALUES (?, 1, ?, ?)
    ON CONFLICT(line_user_id) DO UPDATE SET
      paid=1,
      paid_until=excluded.paid_until,
      note_code_used=COALESCE(excluded.note_code_used, payments.note_code_used)
    """, (uid, exp, code_used))
    conn.commit()
    conn.close()

def is_paid(uid: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT paid, paid_until FROM payments WHERE line_user_id=?", (uid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    paid, paid_until = int(row[0] or 0), int(row[1] or 0)
    return paid == 1 and paid_until > now_ts()

init_db()

# -----------------
# LINE送信
# -----------------
def _line_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    }

def reply_text(reply_token, text):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=_line_headers(),
        data=json.dumps({"replyToken": reply_token, "messages": [{"type": "text", "text": text}]})
    )

def reply_quick(reply_token, text, options):
    items = [{"type": "action", "action": {"type": "message", "label": o, "text": o}} for o in options]
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=_line_headers(),
        data=json.dumps({
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text, "quickReply": {"items": items}}]
        })
    )

def reply_quick_uri(reply_token, text, label, uri):
    items = [{"type": "action", "action": {"type": "uri", "label": label, "uri": uri}}]
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=_line_headers(),
        data=json.dumps({
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text, "quickReply": {"items": items}}]
        })
    )

# -----------------
# ツクヨミ：口調ルール（OpenAI指示）
# -----------------
TSUKUYOMI_STYLE_SYSTEM = (
    "次のルールで日本語出力。\n"
    "・一人称は使わない（『私は』などを避ける）\n"
    "・断言しすぎない。可能性の幅を残す\n"
    "・怖がらせない。静かで短文。\n"
    "・余白を残す\n"
    "・神秘性は保つが、嘘は書かない\n"
    "・『占い師』と名乗らない\n"
    "・世界観は『書院』『月灯り』『兆し』で統一\n"
    "・医療/法律/投資の助言はしない。結果や効果は保証しない\n"
)

# -----------------
# ツクヨミ：無料（簡易）鑑定
# -----------------
def generate_free_report(birthday, concern, situation, intensity) -> str:
    if not OPENAI_API_KEY or not client:
        return BUSY_MSG

    system = (
        TSUKUYOMI_STYLE_SYSTEM +
        "\n【無料簡易】\n"
        "・深層心理の決め打ち、具体的な日付の断定は避ける\n"
        "・出力は必ずこの3つだけ。\n"
        "【月の兆し】\n"
        "【今の流れ（表層）】\n"
        "【小さな助言】\n"
        "・全体300〜520文字\n"
    )
    user = (
        f"生年月日：{birthday}\n"
        f"ジャンル：{concern}\n"
        f"状況：{situation}\n"
        f"本気度：{intensity}\n"
        "上記に合わせて、無料簡易の文を作成。"
    )

    try:
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.85,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("OPENAI ERROR (FREE):", repr(e))
        return BUSY_MSG

# -----------------
# ツクヨミ：有料（特別）鑑定
# -----------------
def generate_paid_report(birthday, concern, situation, intensity) -> str:
    if not OPENAI_API_KEY or not client:
        return BUSY_MSG

    system = (
        TSUKUYOMI_STYLE_SYSTEM +
        "\n【特別】\n"
        "・無料より深く、具体性は上げる。ただし言い切りすぎない\n"
        "・実行できる提案に寄せる\n"
        "・出力は必ずこの順。\n"
        "【核心（いま起きていること）】\n"
        "【相手の深層（推測の範囲）】\n"
        "【30日以内の転機（幅を残す）】\n"
        "【取るべき行動（3つ）】\n"
        "【避けるべき行動（2つ）】\n"
        "【月読の言葉】\n"
        "・700〜1200文字\n"
    )
    user = (
        f"生年月日：{birthday}\n"
        f"ジャンル：{concern}\n"
        f"状況：{situation}\n"
        f"本気度：{intensity}\n"
        "上記に合わせて、特別鑑定の文を作成。"
    )

    try:
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.88,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("OPENAI ERROR (PAID):", repr(e))
        return BUSY_MSG

# -----------------
# note案内（ツクヨミ文体）
# -----------------
def note_guide_text() -> str:
    if not NOTE_PAID_URL:
        return (
            "いま、扉の鍵が見つからないみたい。\n"
            "少し時間を置いて。\n"
            "……また、書院で。"
        )
    return (
        f"💎 特別鑑定（{PAID_PRICE_TEXT}）\n"
        "扉は note に置いてある。\n"
        f"{NOTE_PAID_URL}\n\n"
        "購入後に表示される「購入コード」を送って。\n"
        "形式：購入コード XXXXX\n\n"
        "確認できたら、奥へ進める。"
    )

def try_accept_code(text: str):
    """
    '購入コード XXXX' / 'コード XXXX' を許容してコードを返す
    """
    t = text.strip()
    m = re.match(r"^(購入コード|コード)\s*[:：]?\s*(.+)$", t)
    if not m:
        return None
    return m.group(2).strip()

def paid_confirm_text() -> str:
    return (
        "……確認できた。\n"
        "扉が開く。\n\n"
        f"有効期限：{PAID_TTL_DAYS}日\n"
        "次に「特別鑑定」と送って。"
    )

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
        uid = event["source"]["userId"]
        text = (msg.get("text") or "").strip()

        row = get_user(uid)
        if not row:
            upsert_user(uid, stage="choose_concern")
            reply_quick(reply_token, "どの悩みを視る？\n……静かに。", CONCERNS)
            continue

        _, concern, situation, intensity, stage = row

        # いつでもリセット
        if text.lower() in ["reset", "リセット"]:
            upsert_user(uid, concern=None, situation=None, intensity=None, stage="choose_concern")
            reply_quick(reply_token, "最初から。\nどの悩み？", CONCERNS)
            continue

        # 購入コードはいつでも受け付ける（ステージ関係なし）
        code = try_accept_code(text)
        if code is not None:
            if not NOTE_ACCESS_CODE:
                reply_text(reply_token, "いま、合言葉が設定されていないみたい。\n運用側で確認を。")
                continue

            if code == NOTE_ACCESS_CODE:
                grant_paid(uid, code_used=code)
                reply_text(reply_token, paid_confirm_text())
            else:
                reply_text(reply_token, "その購入コードは見つからなかった。\nもう一度、確認を。\n\n" + note_guide_text())
            continue

        # note案内（コマンド）
        if text in ["note", "NOTE", "購入", "有料", "特別鑑定購入"]:
            reply_text(reply_token, note_guide_text())
            continue

        # === 有料導線：コマンド ===
        if text == "特別鑑定":
            if is_paid(uid):
                if not (concern and situation and intensity):
                    upsert_user(uid, stage="choose_concern")
                    reply_quick(reply_token, "まず、悩みを選んで。\n……月灯りの下で。", CONCERNS)
                else:
                    upsert_user(uid, stage="wait_birthday_paid")
                    reply_text(reply_token, "……奥へ。\n生年月日を送って。\n（例：1995/05/01）")
            else:
                reply_quick_uri(reply_token, note_guide_text(), PAID_LABEL, NOTE_PAID_URL or "https://note.com")
            continue

        # === 無料フロー（QuickReply） ===
        if stage == "choose_concern":
            if text in CONCERNS:
                upsert_user(uid, concern=text, situation=None, intensity=None, stage="choose_situation")
                reply_quick(reply_token, f"{text}。\nいまの状況は？", SITUATIONS[text])
            else:
                reply_quick(reply_token, "どの悩みを視る？", CONCERNS)
            continue

        if stage == "choose_situation":
            valid = SITUATIONS.get(concern or "", [])
            if text in valid:
                upsert_user(uid, situation=text, stage="choose_intensity")
                reply_quick(reply_token, "本気度はどれに近い？", INTENSITIES[concern])
            else:
                reply_quick(reply_token, "その中から選んで。", valid)
            continue

        if stage == "choose_intensity":
            valid = INTENSITIES.get(concern or "", [])
            if text in valid:
                upsert_user(uid, intensity=text, stage="wait_birthday_free")
                reply_text(reply_token, "生年月日を送って。\n（例：1995/05/01、1995-05-01、19950501、1995年5月1日）")
            else:
                reply_quick(reply_token, "その中から選んで。", valid)
            continue

        # === 無料：生年月日入力 → 無料鑑定 → note導線 ===
        if stage == "wait_birthday_free":
            bday = normalize_birthday(text)
            if not bday:
                reply_text(reply_token, "この形で。\n（例：1995/05/01）")
                continue

            report = generate_free_report(bday, concern, situation, intensity)
            if report == BUSY_MSG:
                reply_text(reply_token, report)
                continue

            upsell_text = (
                "\n\n――――――――――\n"
                "……ここから先は、まだ深い。\n"
                "表層だけを撫でた。\n\n"
                "特別鑑定では、次を丁寧に。\n"
                "・相手の深層（推測の範囲）\n"
                "・30日以内の転機（幅を残す）\n"
                "・行動の組み立て（3つ）\n\n"
                f"特別鑑定（{PAID_PRICE_TEXT}）\n"
                "note購入後の「購入コード」を送って。"
            )

            reply_text(reply_token, report + upsell_text + "\n\n" + note_guide_text())

            upsert_user(uid, stage="choose_concern")
            reply_quick(reply_token, "他の悩みも視る？", CONCERNS)
            continue

        # === 有料：生年月日入力 → 特別鑑定 ===
        if stage == "wait_birthday_paid":
            bday = normalize_birthday(text)
            if not bday:
                reply_text(reply_token, "この形で。\n（例：1995/05/01）")
                continue

            if not is_paid(uid):
                reply_text(reply_token, "まだ扉が閉じているみたい。\n\n" + note_guide_text())
                continue

            paid_report = generate_paid_report(bday, concern, situation, intensity)
            reply_text(reply_token, "💎 特別鑑定\n\n" + paid_report)

            upsert_user(uid, stage="choose_concern")
            reply_quick(reply_token, "また視る？", CONCERNS)
            continue

        # 想定外：初期化
        upsert_user(uid, stage="choose_concern")
        reply_quick(reply_token, "最初から。\nどの悩み？", CONCERNS)

    return "OK", 200
