import os
import time
import sqlite3
import json
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, flash
import anthropic
import genanki

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nihongo-dev-secret")

_db_path = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "words.db"))

# tamago.apkg note type ID — keeps template/styling when imported
ANKI_MODEL_ID = 1778316791120
ANKI_DECK_ID  = 2000000000001


def get_db():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db_dir = os.path.dirname(_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS words (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                expression       TEXT NOT NULL,
                thai_meaning     TEXT,
                english_meaning  TEXT,
                sentence         TEXT,
                sentence_furi    TEXT,
                thai_sentence    TEXT,
                english_sentence TEXT,
                exported         INTEGER DEFAULT 0,
                created_at       TEXT DEFAULT (datetime('now','localtime'))
            )
        """)


def generate_with_claude(word: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    prompt = f"""คุณเป็นผู้เชี่ยวชาญภาษาญี่ปุ่น กรุณา generate ข้อมูลสำหรับคำศัพท์ต่อไปนี้แล้วตอบเป็น JSON เท่านั้น

คำศัพท์: {word}

JSON fields ที่ต้องการ:
- thai_meaning: ความหมายภาษาไทย กระชับ 1-3 ความหมาย
- english_meaning: ความหมายภาษาอังกฤษ กระชับ 1-3 ความหมาย
- sentence: ประโยคตัวอย่างภาษาญี่ปุ่น ระดับ N3-N4 เป็นธรรมชาติ
- sentence_furigana: ประโยคเดิม แต่ใส่ furigana แบบ Anki เฉพาะคันจิ เช่น 勉強[べんきょう]する (hiragana/katakana ล้วนไม่ต้องใส่ bracket)
- thai_sentence: คำแปลประโยคภาษาไทย
- english_sentence: คำแปลประโยคภาษาอังกฤษ

ตอบเฉพาะ JSON เท่านั้น"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def make_anki_model():
    return genanki.Model(
        ANKI_MODEL_ID,
        "Manga Wallpaper Japanese Note+",
        fields=[
            {"name": "Sentence"},
            {"name": "Sentence-furigana-plain"},
            {"name": "❃❃"},
            {"name": "Expression"},
            {"name": "pitch-accents"},
            {"name": "❃"},
            {"name": "Word Audio"},
            {"name": "Sentence Audio"},
            {"name": "Lesson"},
            {"name": "frequency"},
            {"name": "Card Number"},
            {"name": "pitch-accent-graphs"},
            {"name": "pitch-accent-positions"},
            {"name": "Thai_Meaning"},
            {"name": "Thai_Sentence"},
        ],
        templates=[{
            "name": "Recognition",
            "qfmt": "{{Expression}}",
            "afmt": """{{FrontSide}}<hr id="answer">
<div style="font-size:1.1em">{{Thai_Meaning}}</div>
<div style="margin-top:8px">{{furigana:Sentence-furigana-plain}}</div>
<div style="color:#27ae60">{{Thai_Sentence}}</div>""",
        }],
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    with get_db() as conn:
        pending  = conn.execute("SELECT * FROM words WHERE exported=0 ORDER BY created_at DESC").fetchall()
        exported = conn.execute("SELECT * FROM words WHERE exported=1 ORDER BY created_at DESC LIMIT 20").fetchall()
    return render_template("index.html", pending=pending, exported=exported)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    word = (request.get_json() or {}).get("word", "").strip()
    if not word:
        return jsonify({"error": "กรุณากรอกคำศัพท์"}), 400
    try:
        return jsonify(generate_with_claude(word))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/save", methods=["POST"])
def save_word():
    d = request.get_json() or {}
    if not d.get("expression", "").strip():
        return jsonify({"error": "ไม่มีคำศัพท์"}), 400
    with get_db() as conn:
        conn.execute("""
            INSERT INTO words
              (expression, thai_meaning, english_meaning, sentence, sentence_furi, thai_sentence, english_sentence)
            VALUES (?,?,?,?,?,?,?)
        """, (
            d.get("expression", ""),
            d.get("thai_meaning", ""),
            d.get("english_meaning", ""),
            d.get("sentence", ""),
            d.get("sentence_furigana", ""),
            d.get("thai_sentence", ""),
            d.get("english_sentence", ""),
        ))
    return jsonify({"ok": True})


@app.route("/delete/<int:word_id>", methods=["POST"])
def delete_word(word_id):
    with get_db() as conn:
        conn.execute("DELETE FROM words WHERE id=?", (word_id,))
    return redirect(url_for("index"))


@app.route("/export")
def export():
    with get_db() as conn:
        words = conn.execute("SELECT * FROM words WHERE exported=0").fetchall()
    if not words:
        flash("ไม่มีคำศัพท์ใหม่ที่รอ export", "error")
        return redirect(url_for("index"))

    model = make_anki_model()
    deck  = genanki.Deck(ANKI_DECK_ID, "Nihongo Vocab")
    for w in words:
        deck.add_note(genanki.Note(
            model=model,
            fields=[
                w["sentence"] or "",           # 0  Sentence
                w["sentence_furi"] or "",       # 1  Sentence-furigana-plain
                w["english_sentence"] or "",    # 2  ❃❃
                w["expression"] or "",          # 3  Expression
                "",                             # 4  pitch-accents
                w["english_meaning"] or "",     # 5  ❃
                "", "", "", "", "",             # 6-10 Audio/Lesson/freq/CardNo
                "", "",                         # 11-12 pitch graphs/positions
                w["thai_meaning"] or "",        # 13 Thai_Meaning
                w["thai_sentence"] or "",       # 14 Thai_Sentence
            ],
        ))

    out = f"/tmp/nihongo_{int(time.time())}.apkg"
    genanki.Package(deck).write_to_file(out)
    with get_db() as conn:
        conn.execute("UPDATE words SET exported=1 WHERE exported=0")
    return send_file(out, as_attachment=True, download_name="nihongo.apkg")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
