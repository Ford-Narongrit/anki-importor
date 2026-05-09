"""
Pitch accent SVG generation (replicating AJT Japanese add-on)
and audio fetching from NHK 2016 MP3 index.
"""

import os
import json
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

_CACHE_DIR = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "words.db"))
CACHE_DIR = os.path.dirname(_CACHE_DIR) if os.path.dirname(_CACHE_DIR) else os.path.dirname(__file__)

KANJIUM_URL   = "https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt"
NHK_INDEX_URL = "https://raw.githubusercontent.com/Ajatt-Tools/nhk_2016_pronunciations_index_mp3/main/index.json"
NHK_AUDIO_BASE = "https://raw.githubusercontent.com/Ajatt-Tools/nhk_2016_pronunciations_index_mp3/main/media/"


# ── Mora / kana utils ─────────────────────────────────────────────────────────

_SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")


def split_moras(text: str) -> list[str]:
    moras, i = [], 0
    while i < len(text):
        if i + 1 < len(text) and text[i + 1] in _SMALL_KANA:
            moras.append(text[i : i + 2])
            i += 2
        else:
            moras.append(text[i])
            i += 1
    return moras


def hira_to_kata(text: str) -> str:
    return "".join(chr(ord(c) + 96) if "ぁ" <= c <= "ん" else c for c in text)


# ── Kanjium pitch data ────────────────────────────────────────────────────────

_pitch_db: dict[str, list[int]] = {}
_pitch_loaded = False


def _load_pitch_db() -> None:
    global _pitch_db, _pitch_loaded
    if _pitch_loaded:
        return

    cache = os.path.join(CACHE_DIR, "kanjium_accents.txt")
    if not os.path.exists(cache):
        try:
            with urllib.request.urlopen(KANJIUM_URL, timeout=30) as r:
                data = r.read().decode("utf-8")
            with open(cache, "w", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            _pitch_loaded = True
            return
    else:
        with open(cache, encoding="utf-8") as f:
            data = f.read()

    for line in data.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        word, reading, accent_str = parts[0], parts[1], parts[2]
        accents = [int(a) for a in accent_str.split(",") if a.strip().isdigit()]
        if accents:
            for key in {word, reading, hira_to_kata(reading)}:
                if key:
                    _pitch_db.setdefault(key, accents)

    _pitch_loaded = True


def get_pitch_numbers(word: str, reading: str = "") -> list[int]:
    _load_pitch_db()
    kata = hira_to_kata(reading)
    return _pitch_db.get(word) or _pitch_db.get(reading) or _pitch_db.get(kata) or []


# ── SVG pitch graph (replicating AJT Japanese) ───────────────────────────────

# Layout constants matching AJT defaults
_S   = 30    # top padding / size_unit
_XS  = 50    # x_step between moras
_GH  = 40    # vertical gap between high and low
_HP  = 20    # horizontal padding
_CR  = 4.0   # circle radius
_SW  = 2.5   # stroke width
_FS  = 14.0  # font size
_VH  = 110   # visible height in px

_HIGH_Y = _S
_LOW_Y  = _S + _GH
_TEXT_Y = _LOW_Y + _XS
_FONT   = "Noto Sans, Noto Sans CJK JP, IPAexGothic, Yu Gothic, Sans-Serif"


def _pitch_class(n: int, p: int) -> str:
    if p == 0:  return "heiban"
    if p == 1:  return "atamadaka"
    if p == n:  return "odaka"
    return "nakadaka"


def _is_high(i: int, p: int) -> bool:
    """Return True if mora at index i is HIGH for pitch number p."""
    if p == 0:  return i > 0          # heiban:    L H H H…
    if p == 1:  return i == 0         # atamadaka: H L L L…
    return 0 < i < p                  # nakadaka/odaka: L H…H L…


def generate_pitch_svg(reading: str, pitch: int) -> str:
    kata  = hira_to_kata(reading)
    moras = split_moras(kata)
    if not moras:
        return ""

    n   = len(moras)
    # Build sequence: word moras + 1 trailing mora (shows particle pitch)
    seq = [(m, _is_high(i, pitch)) for i, m in enumerate(moras)]
    seq.append(("", pitch == 0))  # trailing: high only for heiban

    svg_w = len(seq) * _XS + _HP * 2
    svg_h = _TEXT_Y + _S

    # Compute (x, y) for each mora
    pts, x = [], _S + _HP
    for (m, high) in seq:
        pts.append((x, _HIGH_Y if high else _LOW_Y, m, m == ""))
        x += _XS

    lines, circles, texts = [], [], []

    for i in range(len(pts) - 1):
        x1, y1, _, _ = pts[i]
        x2, y2, _, t2 = pts[i + 1]
        c = "gray" if t2 else "black"
        lines.append(
            f'<line stroke="{c}" stroke-width="{_SW}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )

    for (x, y, txt, trail) in pts:
        c = "gray" if trail else "black"
        circles.append(
            f'<circle fill="{c}" stroke="{c}" stroke-width="{_SW}" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="{_CR}"/>'
        )
        if not trail and txt:
            texts.append(
                f'<text fill="black" font-size="{_FS}px" text-anchor="middle" '
                f'x="{x:.1f}" y="{_TEXT_Y}">{txt}</text>'
            )

    pc = _pitch_class(n, pitch)
    inner = (
        f'<g class="lines">{"".join(lines)}</g>'
        f'<g class="circles">{"".join(circles)}</g>'
        f'<g class="text">{"".join(texts)}</g>'
    )
    return (
        f'<svg class="ajt__pitch_svg" style="font-family:{_FONT}" '
        f'viewBox="0 0 {svg_w} {svg_h}" height="{_VH}px" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<g class="{pc}">{inner}</g></svg>'
    )


def build_pitch_field(word: str, reading: str) -> str:
    """Generate HTML for the pitch-accents Anki field (ol > li > SVG)."""
    pitches = get_pitch_numbers(word, reading)
    if not pitches:
        return ""
    items = "".join(
        f"<li>{generate_pitch_svg(reading or word, p)}</li>"
        for p in pitches[:2]
        if generate_pitch_svg(reading or word, p)
    )
    return f"<ol>{items}</ol>" if items else ""


# ── NHK 2016 MP3 audio ───────────────────────────────────────────────────────

_nhk: dict[str, list[str]] = {}
_nhk_loaded = False


def _load_nhk_index() -> None:
    global _nhk, _nhk_loaded
    if _nhk_loaded:
        return

    cache = os.path.join(CACHE_DIR, "nhk_index.json")
    if not os.path.exists(cache):
        try:
            with urllib.request.urlopen(NHK_INDEX_URL, timeout=30) as r:
                data = r.read().decode("utf-8")
            with open(cache, "w", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            _nhk_loaded = True
            return
    else:
        with open(cache, encoding="utf-8") as f:
            data = f.read()

    _nhk = json.loads(data).get("headwords", {})
    _nhk_loaded = True


def fetch_word_audio(word: str, reading: str = "") -> "tuple[str, bytes] | None":
    """Return (filename, mp3_bytes) or None if not found."""
    _load_nhk_index()
    kata = hira_to_kata(reading)
    files = _nhk.get(word) or _nhk.get(reading) or _nhk.get(kata) or []
    if not files:
        return None
    filename = files[0]
    try:
        with urllib.request.urlopen(NHK_AUDIO_BASE + filename, timeout=10) as r:
            return filename, r.read()
    except Exception:
        return None
