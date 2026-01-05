import os
import uuid
import tempfile
import subprocess
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, abort
from faster_whisper import WhisperModel

import argostranslate.package
import argostranslate.translate

APP_DIR = Path(__file__).parent
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# أفضل موديل للعربي: large-v3 (أو medium إذا بدك أسرع)
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "large-v3")

app = Flask(__name__)

# CPU: int8 أفضل للسرعة. لو عندك GPU ممكن تغيّرها.
model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")


def ensure_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        raise RuntimeError("ffmpeg غير مثبت أو مش موجود في PATH.")


def extract_audio_wav(video_path: Path, wav_path: Path):
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def seconds_to_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = int(round((sec - int(sec)) * 1000))
    total = int(sec)
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def srt_timestamp(sec: float) -> str:
    # SRT: HH:MM:SS,mmm
    if sec < 0:
        sec = 0.0
    ms = int(round((sec - int(sec)) * 1000))
    total = int(sec)
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_vtt(items) -> str:
    lines = ["WEBVTT", ""]
    for it in items:
        start = seconds_to_timestamp(it["start"])
        end = seconds_to_timestamp(it["end"])
        text = (it["text"] or "").strip()
        if not text:
            continue
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def make_srt(items) -> str:
    out = []
    idx = 1
    for it in items:
        text = (it["text"] or "").strip()
        if not text:
            continue
        out.append(str(idx))
        out.append(f"{srt_timestamp(it['start'])} --> {srt_timestamp(it['end'])}")
        out.append(text)
        out.append("")  # blank line
        idx += 1
    return "\n".join(out)


# -------------------- Argos Translate (EN -> HE) --------------------
def ensure_argos_en_he():
    installed = argostranslate.translate.get_installed_languages()
    codes = {l.code for l in installed}

    if "en" in codes and "he" in codes:
        en = next((l for l in installed if l.code == "en"), None)
        he = next((l for l in installed if l.code == "he"), None)
        if en and he and en.get_translation(he):
            return

    argostranslate.package.update_package_index()
    pkgs = argostranslate.package.get_available_packages()

    target_pkg = next((p for p in pkgs if p.from_code == "en" and p.to_code == "he"), None)
    if not target_pkg:
        raise RuntimeError("مش لاقي باكج ترجمة en->he في Argos. شغّل مرة واحدة مع إنترنت.")

    pkg_path = target_pkg.download()
    argostranslate.package.install_from_path(pkg_path)


def translate_en_to_he(text: str) -> str:
    if not text.strip():
        return text

    installed = argostranslate.translate.get_installed_languages()
    en = next((l for l in installed if l.code == "en"), None)
    he = next((l for l in installed if l.code == "he"), None)

    if not en or not he:
        ensure_argos_en_he()
        installed = argostranslate.translate.get_installed_languages()
        en = next((l for l in installed if l.code == "en"), None)
        he = next((l for l in installed if l.code == "he"), None)

    translation = en.get_translation(he)
    if not translation:
        ensure_argos_en_he()
        installed = argostranslate.translate.get_installed_languages()
        en = next((l for l in installed if l.code == "en"), None)
        he = next((l for l in installed if l.code == "he"), None)
        translation = en.get_translation(he)

    return translation.translate(text)
# -------------------------------------------------------------------


def overlap(a_start, a_end, b_start, b_end) -> float:
    left = max(a_start, b_start)
    right = min(a_end, b_end)
    return max(0.0, right - left)


def best_src_text_for_window(src_segs, t0, t1) -> str:
    parts = []
    for s in src_segs:
        if overlap(t0, t1, s["start"], s["end"]) > 0:
            if s["text"]:
                parts.append(s["text"].strip())
    if not parts and src_segs:
        mid = (t0 + t1) / 2.0
        closest = min(src_segs, key=lambda x: abs(((x["start"] + x["end"]) / 2.0) - mid))
        return (closest["text"] or "").strip()
    return " ".join(parts).strip()


from typing import Optional

def find_video_by_id(vid_id: str) -> Optional[Path]:

    candidates = list(UPLOAD_DIR.glob(f"{vid_id}.*"))
    return candidates[0] if candidates else None


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/upload")
def upload():
    if "video" not in request.files:
        return jsonify({"ok": False, "error": "ما في ملف فيديو"}), 400

    f = request.files["video"]
    if not f.filename:
        return jsonify({"ok": False, "error": "اسم الملف فاضي"}), 400

    vid_id = str(uuid.uuid4())
    ext = Path(f.filename).suffix.lower() or ".mp4"
    save_path = UPLOAD_DIR / f"{vid_id}{ext}"
    f.save(save_path)

    return jsonify({"ok": True, "id": vid_id, "filename": save_path.name})


@app.post("/api/transcribe")
def transcribe():
    """
    body:
      - id: video id
      - output: "dual" | "he" | "en" | "src"
      - language: optional, default "ar" للتركيز على العربي
    """
    data = request.get_json(silent=True) or {}
    vid_id = data.get("id")
    output = (data.get("output") or "dual").lower()
    # افتراضي عربي لتحسين الدقة
    language = (data.get("language") or "ar").strip() or "ar"

    if not vid_id:
        return jsonify({"ok": False, "error": "id ناقص"}), 400

    video_path = find_video_by_id(vid_id)
    if not video_path:
        return jsonify({"ok": False, "error": "الفيديو مش موجود"}), 404

    try:
        ensure_ffmpeg()
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    if output in ("he", "dual"):
        try:
            ensure_argos_en_he()
        except Exception as e:
            return jsonify({"ok": False, "error": f"مشكلة Argos: {e}"}), 500

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wav_path = td / "audio.wav"

        try:
            extract_audio_wav(video_path, wav_path)
        except subprocess.CalledProcessError:
            return jsonify({"ok": False, "error": "فشل استخراج الصوت (ffmpeg)"}), 500

        # أهم خيار: تثبيت اللغة للعربي + VAD
        options = {"beam_size": 5, "vad_filter": True, "language": language}

        # 1) الأصلي (عربي) لو dual أو src
        src_segs = []
        if output in ("src", "dual"):
            src_segments, _ = model.transcribe(str(wav_path), task="transcribe", **options)
            src_segs = [{"start": float(s.start), "end": float(s.end), "text": (s.text or "").strip()} for s in src_segments]

        # 2) ترجمة Whisper للإنجليزي (من العربي) ثم Argos للعبري
        en_segments, _ = model.transcribe(str(wav_path), task="translate", **options)
        en_segs = [{"start": float(s.start), "end": float(s.end), "text": (s.text or "").strip()} for s in en_segments]

        out_items = []
        for e in en_segs:
            t0, t1 = e["start"], e["end"]
            en_text = e["text"]

            if output == "en":
                out_text = en_text
            elif output == "he":
                out_text = translate_en_to_he(en_text)
            elif output == "src":
                out_text = best_src_text_for_window(src_segs, t0, t1)
            else:  # dual
                src_text = best_src_text_for_window(src_segs, t0, t1)
                he_text = translate_en_to_he(en_text)
                out_text = f"{src_text}\n{he_text}".strip()

            out_items.append({"start": t0, "end": t1, "text": out_text})

        # احفظ VTT + SRT (SRT نحتاجه للحرق داخل الفيديو)
        vtt_text = make_vtt(out_items)
        srt_text = make_srt(out_items)

    (UPLOAD_DIR / f"{vid_id}.vtt").write_text(vtt_text, encoding="utf-8")
    (UPLOAD_DIR / f"{vid_id}.srt").write_text(srt_text, encoding="utf-8")

    return jsonify({"ok": True, "id": vid_id, "vtt": f"/api/vtt/{vid_id}"})


@app.get("/api/video/<vid_id>")
def get_video(vid_id: str):
    video_path = find_video_by_id(vid_id)
    if not video_path:
        return jsonify({"ok": False, "error": "الفيديو مش موجود"}), 404
    return send_file(video_path, as_attachment=False)


@app.get("/api/vtt/<vid_id>")
def get_vtt(vid_id: str):
    vtt_path = UPLOAD_DIR / f"{vid_id}.vtt"
    if not vtt_path.exists():
        return jsonify({"ok": False, "error": "VTT مش موجود"}), 404
    return send_file(vtt_path, mimetype="text/vtt", as_attachment=False)


@app.get("/api/render/<vid_id>")
def render_burned_video(vid_id: str):
    video_path = find_video_by_id(vid_id)
    if not video_path:
        return jsonify({"ok": False, "error": "الفيديو مش موجود"}), 404

    srt_path = UPLOAD_DIR / f"{vid_id}.srt"
    if not srt_path.exists():
        return jsonify({"ok": False, "error": "اعمل CC أولاً ثم جرّب التحميل"}), 400

    try:
        ensure_ffmpeg()
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    out_path = UPLOAD_DIR / f"{vid_id}_subbed.mp4"

    # ✅ على ويندوز: استخدم اسم الملف فقط وخلي ffmpeg يشتغل من داخل uploads
    srt_name = srt_path.name

    vf = f"subtitles='{srt_name}':force_style='FontSize=22,Outline=2,Shadow=1,MarginV=30'"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path)
    ]

    try:
        # ✅ مهم: cwd=UPLOAD_DIR عشان subtitles يلاقي ملف srt بدون مشاكل مسار
        subprocess.run(cmd, check=True, cwd=str(UPLOAD_DIR), capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # ✅ رجّع سبب ffmpeg الحقيقي بدل ما يضل مخفي
        return jsonify({"ok": False, "error": f"FFmpeg error: {e.stderr[-800:]}"}), 500

    return send_file(out_path, as_attachment=True, download_name="video_with_subtitles.mp4")

    """
    يرجّع MP4 جديد مع الترجمة محروقة داخل الفيديو.
    لازم تكون عامل /api/transcribe قبلها (عشان .srt موجود).
    """
    video_path = find_video_by_id(vid_id)
    if not video_path:
        return jsonify({"ok": False, "error": "الفيديو مش موجود"}), 404

    srt_path = UPLOAD_DIR / f"{vid_id}.srt"
    if not srt_path.exists():
        return jsonify({"ok": False, "error": "اعمل CC أولاً ثم جرّب التحميل"}), 400

    try:
        ensure_ffmpeg()
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    out_path = UPLOAD_DIR / f"{vid_id}_subbed.mp4"

    # مهم لويندوز: نخلي المسار forward-slash
    srt_for_ffmpeg = str(srt_path).replace("\\", "/")

    # ستايل بسيط وواضح
    vf = f"subtitles='{srt_for_ffmpeg}':force_style='FontSize=22,Outline=2,Shadow=1,MarginV=30'"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path)
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return jsonify({"ok": False, "error": "فشل تصدير الفيديو بالترجمة. تأكد أن ffmpeg كامل ويدعم subtitles."}), 500

    return send_file(out_path, as_attachment=True, download_name="video_with_subtitles.mp4")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
