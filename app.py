import os
import re
import time
import sqlite3
import json
import tempfile
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, flash
import anthropic
import genanki
from pitch_audio import build_pitch_field, build_pitch_graphs_field, fetch_word_audio

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
                pitch_accents    TEXT DEFAULT '',
                audio_file       TEXT DEFAULT '',
                audio_data       BLOB,
                exported         INTEGER DEFAULT 0,
                created_at       TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # Migrate existing DB: add new columns if missing
        existing = {r[1] for r in conn.execute("PRAGMA table_info(words)")}
        for col, defn in [
            ("pitch_accents", "TEXT DEFAULT ''"),
            ("pitch_graphs",  "TEXT DEFAULT ''"),
            ("audio_file",    "TEXT DEFAULT ''"),
            ("audio_data",    "BLOB"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE words ADD COLUMN {col} {defn}")


init_db()


def _fix_furigana(text: str) -> str:
    """Add a space before each kanji[reading] group that follows kana/punctuation.

    Anki's furigana filter uses spaces to determine where a kanji group starts.
    Without a space, preceding kana is mistakenly included in the kanji group.
    e.g. "は天然[てんねん]" → "は 天然[てんねん]"
    """
    # Insert a space between a kana/punctuation character and the start of a kanji group
    # that is immediately followed by [reading], but only when not already spaced.
    return re.sub(
        r'(?<=[^\s一-鿿])(?=[一-鿿]+\[)',
        ' ',
        text,
    )


def generate_with_claude(word: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    prompt = f"""คุณเป็นผู้เชี่ยวชาญภาษาญี่ปุ่น กรุณา generate ข้อมูลสำหรับคำศัพท์ต่อไปนี้แล้วตอบเป็น JSON เท่านั้น

คำศัพท์: {word}

JSON fields ที่ต้องการ:
- word_reading: การอ่านของคำศัพท์นี้เป็นภาษาฮิรางานะ (เฉพาะตัวคำ ไม่ใช่ประโยค เช่น べんきょう)
- thai_meaning: ความหมายภาษาไทย กระชับ 1-3 ความหมาย
- english_meaning: ความหมายภาษาอังกฤษ กระชับ 1-3 ความหมาย
- sentence: ประโยคตัวอย่างภาษาญี่ปุ่น ระดับ N3-N4 เป็นธรรมชาติ
- sentence_furigana: ประโยคเดิม แต่ใส่ furigana แบบ Anki เฉพาะคันจิ กฎสำคัญ:
  1. ใส่ [reading] ต่อท้าย kanji group เช่น 勉強[べんきょう]
  2. ต้องมี space ก่อน kanji group ที่ตามหลัง hiragana/katakana/เครื่องหมาย
  3. ตัวอย่างที่ถูก: 日本[にほん]は 天然[てんねん] 資源[しげん]に 乏[とぼ]しい 国[くに]です。
  4. ตัวอย่างที่ผิด: 日本[にほん]は天然[てんねん]資源[しげん]に乏[とぼ]しい国[くに]です。
  5. hiragana/katakana ล้วนไม่ต้องใส่ bracket
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
    result = json.loads(text)
    if "sentence_furigana" in result:
        result["sentence_furigana"] = _fix_furigana(result["sentence_furigana"])
    return result


_QFMT = """\
<div class="bubble">

<div id="HASH">
    <span id="freq">
        {{Lesson}}
    </span>
    <span id="freq">
        Freq\t{{frequency}}
    </span>
</div>

<br><br>
<span class='japanese'>{{Expression}}</span>

<hr><br><br><br>
<div class="context">{{Sentence}}</div>

<br><br><br>
</div>"""

_AFMT = """\
<div class="bubble">

<div id="HASH">
    <span id="freq">
        {{Lesson}}
    </span>
    <span id="freq">
        Freq\t{{frequency}}
    </span>
</div>

<div class=left>
    <div id=freq>
        {{Card Number}}
    </div>
</div>

<div id='word'>{{pitch-accents}}</div>

<div id="kanjiHover">
    <span class='japanese'>{{Expression}}</span>

<label class="pitchToggle">
  <input type="checkbox">
  <div class="pitch">
    {{pitch-accent-positions}}
    {{pitch-accent-graphs}}
  </div>
</label>

<hr>

<span class='english'>{{hint:❃}}</span>
<div style='color:#e74c3c; font-size:22px; margin:6px 0;'>🇹🇭 {{Thai_Meaning}}</div>

<div><br></div>

<div class="context">{{furigana:Sentence-furigana-plain}}</div>

<span class='english'>{{hint:❃❃}}</span>
<div style='color:#27ae60; font-size:18px; margin:6px 0;'>🇹🇭 {{Thai_Sentence}}</div>

</div>

{{Word Audio}}
{{Sentence Audio}}

<br><br>

</div>

<script>
  var elem = document.querySelector(".soundLink, .replaybutton");
  if (elem) { elem.click(); }
</script>

<script src ="_kanjiHover.js"></script>"""

_CSS = """\
@font-face {
  font-family: "NotoSerifJPLocal";
  src: url("_NotoSerifJP.ttf");
}
@font-face {
  font-family: TakaoPMincho;
  src: url("_TakaoPMincho.ttf");
}
@font-face {
  font-family: OpenSansLight;
  src: url("_OpenSans-Light.ttf");
}

.card {
  font-family: TakaoPMincho;
  font-size: 40px;
  text-align: center;
  color: black;
  margin: 20px auto;
  padding: 0 20px;
  max-width: 800px;
  background:
    linear-gradient(rgba(181,172,159,0.45),rgba(181,172,159,0.45)),
    url("_wallpaper_manga_wall_black_1.jpg");
  background-position: center;
  background-attachment: fixed;
}

.bubble {
  border: double blue;
  border-radius: 10px;
  padding: 10px;
  background-color: white;
  box-shadow: -20px 20px 0 rgba(0,0,0,0.4);
}

hr { margin-bottom: 0; }
rt { font-size: 25px; }
#pitchGraph { font-size: 2rem; }
#word span { border-color: #fd5c5c !important; padding-top: 10px; }
a { color: inherit !important; text-decoration: none; }
b, strong { font-weight: normal; background-color: #A9D8FF; padding: 0 3px; margin: 0 3px; border-radius: 0; }
u { text-decoration-line: underline; text-decoration-color: #e63946; text-decoration-thickness: 3px; text-underline-offset: 8px; }

#freq { display: inline-block; margin: 8px 8px -10px; margin-top: 0.3rem; border: 1px solid grey; border-radius: 5px; padding: 4px; font-size: 0.9rem; }
#HASH { display: flex; justify-content: space-between; }
.left { text-align: left; }

ul, ol { list-style: none; margin: 0; padding: 0; display: inline-block; }
li { text-align: center; }
#word li:not(:first-child) { display: none; }
#pitchGraph li:not(:first-child) { display: none; }
ol > li:not(:first-child) { display: none; }

.pitch { font-size: 0.7em; cursor: pointer; transition: max-height 0.15s ease-out; }
.pitchToggle { cursor: pointer; display: inline-block; pointer-events: none; }
.pitchToggle input { display: none; }
.pitch ol li:not(:first-child) { display: none; }
.pitchToggle input:checked + .pitch ol li { display: list-item; }
.pitchToggle:has(ol li:nth-child(2)) { pointer-events: auto; }

.japanese { font-size: 80px; }
.japanese-highlight { font-size: 50px; color: salmon; }
.reading { font-size: 35px; }
.english { font-size: 30px; font-family: OpenSansLight; color: #b000b0; }
.context { font-size: 40px; }

.soundLink { background: url("_sound-speaker-black.svg") no-repeat center; background-size: contain; width: 44px; height: 44px; display: inline-block; cursor: pointer; }
.playImage { display: none; }

.nightMode.card { color: #e0e0e0 !important; background: linear-gradient(rgba(10,10,18,0.50),rgba(10,10,18,0.50)), url("_wallpaper_manga_wall_black_1.jpg"); background-position: center; }
.nightMode .bubble { background-color: #1E1E2E; }
.nightMode b, .nightMode strong { background-color: #0F5ED2; }
.nightMode .soundLink { background-image: url("_sound-speaker-white.svg"); }

.android .card, .ios .card { background-color: #ffffff !important; background-image: none !important; }
.android .bubble, .ios .bubble { background: none !important; border: none !important; box-shadow: none !important; padding: 5px; }
.android .nightMode.card, .ios .nightMode.card { background-color: #1E1E2E !important; }
.android .japanese, .ios .japanese { font-size: 60px; }
.android .context, .ios .context { font-size: 35px; }
.android rt, .ios rt { font-size: 0.55em; }
.android .english, .ios .english { font-size: 24px; }
.android u, .ios u { text-underline-offset: 3px; }"""


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
            "name": "English Translate",
            "qfmt": _QFMT,
            "afmt": _AFMT,
        }],
        css=_CSS,
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
        result = generate_with_claude(word)

        # Use word_reading (hiragana of the word itself) for pitch + audio lookup
        word_reading = result.get("word_reading", "").strip()

        pitch_html = build_pitch_field(word, word_reading)
        pitch_graphs_html = build_pitch_graphs_field(word, word_reading)
        result["pitch_accents"] = pitch_html
        result["pitch_graphs"] = pitch_graphs_html
        result["has_pitch"] = bool(pitch_html)

        audio_result = fetch_word_audio(word, word_reading)
        if audio_result:
            import base64
            filename, audio_bytes = audio_result
            result["audio_file"] = filename
            result["audio_b64"] = base64.b64encode(audio_bytes).decode()
            result["has_audio"] = True
        else:
            result["has_audio"] = False

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/save", methods=["POST"])
def save_word():
    d = request.get_json() or {}
    if not d.get("expression", "").strip():
        return jsonify({"error": "ไม่มีคำศัพท์"}), 400

    audio_data = None
    if d.get("audio_b64"):
        import base64
        audio_data = base64.b64decode(d["audio_b64"])

    with get_db() as conn:
        conn.execute("""
            INSERT INTO words
              (expression, thai_meaning, english_meaning, sentence, sentence_furi,
               thai_sentence, english_sentence, pitch_accents, pitch_graphs, audio_file, audio_data)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d.get("expression", ""),
            d.get("thai_meaning", ""),
            d.get("english_meaning", ""),
            d.get("sentence", ""),
            d.get("sentence_furigana", ""),
            d.get("thai_sentence", ""),
            d.get("english_sentence", ""),
            d.get("pitch_accents", ""),
            d.get("pitch_graphs", ""),
            d.get("audio_file", ""),
            audio_data,
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

    model      = make_anki_model()
    deck       = genanki.Deck(ANKI_DECK_ID, "Nihongo Vocab")
    tmp_files  = []   # temp audio files to clean up after

    for w in words:
        # Write audio with exact original filename so [sound:xxx.mp3] matches
        audio_field = ""
        if w["audio_data"] and w["audio_file"]:
            audio_path = os.path.join(tempfile.gettempdir(), w["audio_file"])
            with open(audio_path, "wb") as f:
                f.write(bytes(w["audio_data"]))
            tmp_files.append(audio_path)
            audio_field = f"[sound:{w['audio_file']}]"

        deck.add_note(genanki.Note(
            model=model,
            fields=[
                w["sentence"] or "",           # 0  Sentence
                w["sentence_furi"] or "",       # 1  Sentence-furigana-plain
                w["english_sentence"] or "",    # 2  ❃❃
                w["expression"] or "",          # 3  Expression
                w["pitch_accents"] or "",       # 4  pitch-accents
                w["english_meaning"] or "",     # 5  ❃
                audio_field,                   # 6  Word Audio
                "",                             # 7  Sentence Audio
                "", "", "",                     # 8-10 Lesson/freq/CardNo
                w["pitch_graphs"] or "", "",    # 11-12 pitch graphs/positions
                w["thai_meaning"] or "",        # 13 Thai_Meaning
                w["thai_sentence"] or "",       # 14 Thai_Sentence
            ],
        ))

    out = f"/tmp/nihongo_{int(time.time())}.apkg"
    pkg = genanki.Package(deck)
    pkg.media_files = tmp_files
    pkg.write_to_file(out)

    for tmp_path in tmp_files:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    with get_db() as conn:
        conn.execute("UPDATE words SET exported=1 WHERE exported=0")
    return send_file(out, as_attachment=True, download_name="nihongo.apkg")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
