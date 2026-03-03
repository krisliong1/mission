from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
import tempfile, os, re, subprocess, time, logging, traceback
from collections import defaultdict

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Server-side logging (errors go to log file, NOT to client responses)
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

DENO = "/usr/local/bin/deno"
PROXY = "http://127.0.0.1:7890"  # mihomo global proxy

# ── Rate Limiting ─────────────────────────────────────────────────────────────
# Threat model: Whisper large-v3-turbo on CPU is expensive (~30s per file).
# Without rate limiting, a single IP can monopolise the server or run up costs.
# Mitigation: sliding-window counter — 3 requests per IP per 60 seconds.
_rate_store: dict = defaultdict(list)
RATE_LIMIT = 3      # max requests per window
RATE_WINDOW = 60.0  # window size in seconds

def check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.time()
    # Evict timestamps outside the window
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        return False
    _rate_store[ip].append(now)
    return True

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_youtube(url: str) -> bool:
    return bool(re.search(r'youtube\.com|youtu\.be', url, re.I))

def get_ydl_audio_opts(tmpdir: str, url: str) -> dict:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "quiet": True,
        "proxy": PROXY,
    }
    if is_youtube(url) and os.path.exists(DENO):
        opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}
    return opts

def transcribe_audio(audio_path: str, diarize: bool = False):
    """
    Transcribe audio using faster-whisper.

    diarize=True enables word_timestamps, which returns per-word timing
    data useful for downstream speaker-diarization pipelines (e.g. pyannote).
    faster-whisper itself does not do speaker labelling, but word timestamps
    are the prerequisite for any diarization post-processing.
    """
    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        word_timestamps=diarize,   # ← enabled when diarize=True
    )
    segs = list(segments)
    text = " ".join([seg.text.strip() for seg in segs])
    duration_sec = int(info.duration) if hasattr(info, "duration") else 0
    mins, secs = divmod(duration_sec, 60)
    word_count = len(text.split())
    lang = info.language if hasattr(info, "language") else "auto"
    return text, f"{mins}:{secs:02d}", word_count, lang

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/transcribe/youtube")
async def transcribe_url(
    request: Request,
    url: str = Form(...),
    diarize: bool = Form(False),
):
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    try:
        import yt_dlp
        with tempfile.TemporaryDirectory() as tmpdir:
            with yt_dlp.YoutubeDL(get_ydl_audio_opts(tmpdir, url)) as ydl:
                ydl.download([url])
            files = [f for f in os.listdir(tmpdir) if f.endswith(".mp3")]
            if not files:
                raise HTTPException(status_code=500, detail="Audio download failed")
            text, duration, word_count, lang = transcribe_audio(
                os.path.join(tmpdir, files[0]), diarize=diarize
            )
            return {
                "text": text,
                "meta": {"duration": duration, "word_count": word_count, "language": lang},
            }
    except HTTPException:
        raise  # re-raise our own HTTP errors unchanged
    except Exception:
        # ── SECURITY: Never expose raw exception to client ────────────────
        # Threat: str(e) can reveal internal paths, library versions, and
        # stack frames that help attackers fingerprint the server.
        logger.error("transcribe_url failed:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="处理失败，请稍后重试")


@app.post("/api/transcribe/audio")
async def transcribe_audio_upload(
    request: Request,
    file: UploadFile = File(...),
    diarize: bool = Form(False),
):
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    tmp_path = None
    try:
        ext = os.path.splitext(file.filename)[1] or ".mp3"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        text, duration, word_count, lang = transcribe_audio(tmp_path, diarize=diarize)
        return {
            "text": text,
            "meta": {"duration": duration, "word_count": word_count, "language": lang},
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("transcribe_audio_upload failed:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="处理失败，请稍后重试")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/api/transcribe/video")
async def transcribe_video_upload(
    request: Request,
    file: UploadFile = File(...),
    diarize: bool = Form(False),
):
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    tmp_path = None
    audio_path = None
    try:
        ext = os.path.splitext(file.filename)[1] or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        audio_path = tmp_path + ".mp3"

        # ── SECURITY FIX: subprocess.run() list form — NO shell=True ─────
        # Threat: os.system(f"ffmpeg -i '{tmp_path}' ...") is vulnerable to
        # command injection.  If tmp_path contains shell metacharacters
        # (e.g. "; rm -rf /", "`id`", "$(cmd)"), the shell will execute them.
        # Even with single-quote wrapping a filename with an embedded ' can
        # break out of the quote context.
        # Fix: subprocess.run() with a plain list bypasses the shell entirely.
        # The OS passes each element as a separate argv[], so no injection
        # is possible — the filename is treated as a literal string.
        result = subprocess.run(
            [
                "ffmpeg", "-i", tmp_path,
                "-vn", "-acodec", "mp3", "-q:a", "2",
                audio_path,
                "-y", "-loglevel", "quiet",
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(audio_path):
            raise HTTPException(status_code=500, detail="Audio extraction failed")

        text, duration, word_count, lang = transcribe_audio(audio_path, diarize=diarize)
        return {
            "text": text,
            "meta": {"duration": duration, "word_count": word_count, "language": lang},
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("transcribe_video_upload failed:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="处理失败，请稍后重试")
    finally:
        # Always clean up temp files — even on exception paths
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)


@app.get("/health")
async def health():
    return {"status": "ok"}
