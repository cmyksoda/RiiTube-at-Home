#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 cmyksoda
"""RiiTube at Home: prepare seekable YouTube videos for WiiMC over a home LAN."""

import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("RIIRIITUBE_DATA", ROOT / "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)


def lan_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.168.0.1", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


HOST = os.environ.get("RIIRIITUBE_HOST", lan_address())
PORT = int(os.environ.get("RIIRIITUBE_PORT", "8877"))
BASE = os.environ.get("RIIRIITUBE_URL", f"http://{HOST}:{PORT}").rstrip("/")
YTDLP = [
    sys.executable,
    "-m",
    "yt_dlp",
    "--ignore-config",
    "--js-runtimes",
    "node",
    "--no-playlist",
    "--socket-timeout",
    "30",
]
SUBTITLE_STYLE = (
    "FontName=Noto Sans,FontSize=20,PrimaryColour=&H0000FFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=1.6,Shadow=0,"
    "Alignment=2,MarginV=16,MarginL=20,MarginR=20,Bold=0"
)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4096
lock = threading.RLock()
jobs = {}
metadata = {}
search_cache = {}
SEARCH_PAGE_SIZE = 12
pending = queue.Queue()
worker_started = False


def video_id(value):
    value = str(value).strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Paste a YouTube video URL or its 11-character ID.")
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        candidate = parse_qs(parsed.query).get("v", [""])[0]
        if not candidate and parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            candidate = parsed.path.split("/")[2]
    else:
        raise ValueError("Only YouTube video URLs are supported.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError("That URL does not contain a YouTube video ID.")
    return candidate


def clean(value, limit=220):
    return " ".join(str(value).split())[:limit]


def patch_job(vid, **values):
    with lock:
        jobs[vid].update(values, updated=time.time())
        folder = DATA / vid
        folder.mkdir(exist_ok=True)
        temp = folder / "job.tmp"
        temp.write_text(json.dumps(jobs[vid], ensure_ascii=False), encoding="utf-8")
        temp.replace(folder / "job.json")
        return dict(jobs[vid])


def load_jobs():
    for file in DATA.glob("*/job.json"):
        try:
            job = json.loads(file.read_text())
            vid = video_id(file.parent.name)
            job["id"] = vid
            missing_video = not (file.parent / "video.mp4").is_file()
            if job.get("wii_format") == "mkv":
                missing_video |= not (file.parent / "video.mkv").is_file()
            if job["status"] == "ready" and missing_video:
                job.update(status="error", detail="Cached video is missing; prepare it again.")
            elif job["status"] != "ready":
                job.update(status="error", detail="Preparation was interrupted; prepare it again.")
            jobs[vid] = job
        except (ValueError, KeyError, OSError):
            logging.exception("Could not load cached job %s", file)


def enqueue(vid, hardsubs=True):
    global worker_started
    with lock:
        if not worker_started:
            threading.Thread(target=worker, name="video-worker", daemon=True).start()
            worker_started = True
        if vid in jobs and jobs[vid]["status"] != "error":
            existing = jobs[vid]
            if existing.get("hardsubs", True) == hardsubs:
                return dict(existing)
            if existing["status"] != "ready":
                raise ValueError(
                    "This video is already preparing. Wait until it finishes before changing subtitles."
                )
        info = metadata.get(vid, {})
        jobs[vid] = {
            "id": vid,
            "title": info.get("title", vid),
            "duration": info.get("duration"),
            "status": "queued",
            "detail": "Waiting for the preparation worker.",
            "hardsubs": hardsubs,
        }
        result = patch_job(vid)
        pending.put(vid)
        return result


def run_logged(command, logfile, progress=None, cwd=None):
    with logfile.open("a", encoding="utf-8") as log:
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            cwd=cwd,
        ) as proc:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if progress:
                    progress(line.strip())
            if proc.wait():
                raise RuntimeError(f"{Path(command[0]).name} failed; see {logfile.name} in data.")


def manual_english_track(info):
    # YouTube's automatic captions and translations live in a separate mapping.
    tracks = info.get("subtitles") or {}
    english = [
        language
        for language, formats in tracks.items()
        if formats and re.fullmatch(r"en(?:-[A-Za-z0-9]+)*", language)
    ]
    preference = {"en": 0, "en-GB": 1, "en-US": 2}
    return min(english, key=lambda language: (preference.get(language, 3), language), default=None)


def download_captions(info, infofile, folder, logfile):
    language = manual_english_track(info)
    target = folder / "english.srt"
    target.unlink(missing_ok=True)
    if not language:
        return None
    run_logged(
        YTDLP
        + [
            "--load-info-json",
            str(infofile),
            "--skip-download",
            "--write-subs",
            "--no-write-auto-subs",
            "--sub-langs",
            f"^{re.escape(language)}$",
            "--sub-format",
            "srt/vtt/best",
            "--convert-subs",
            "srt",
            "-o",
            str(folder / "captions.%(ext)s"),
        ],
        logfile,
    )
    downloaded = folder / f"captions.{language}.srt"
    if not downloaded.is_file() or "-->" not in downloaded.read_text(encoding="utf-8-sig"):
        raise RuntimeError(
            "The creator's English subtitles could not be downloaded; retry preparation."
        )
    shutil.copyfile(downloaded, target)
    return language


def video_filter(captions=False):
    filters = (
        "scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30"
    )
    if captions:
        filters += f",subtitles=filename=english.srt:force_style='{SUBTITLE_STYLE}'"
    return filters


def prepare(vid):
    folder = DATA / vid
    logfile = folder / "prepare.log"
    patch_job(vid, status="downloading", detail="Resolving YouTube and downloading video…")
    last_update = 0

    def download_progress(line):
        nonlocal last_update
        if "[download]" in line and time.monotonic() - last_update > 1:
            patch_job(vid, detail=clean(line))
            last_update = time.monotonic()

    # Keep a resumable source; never expose a growing file as a seekable video.
    run_logged(
        YTDLP
        + [
            "--newline",
            "--no-colors",
            "--write-info-json",
            "--no-write-playlist-metafiles",
            "--match-filters",
            "!is_live",
            "-f",
            "bv[height<=480][vcodec^=avc1]+ba[ext=m4a]/b[height<=480]/bv[height<=480]+ba",
            "--merge-output-format",
            "mkv",
            "-o",
            str(folder / "source.%(ext)s"),
            f"https://www.youtube.com/watch?v={vid}",
        ],
        logfile,
        download_progress,
    )
    infofile = folder / "source.info.json"
    if not infofile.exists():
        raise RuntimeError("No downloadable video found. Live streams are not supported.")
    info = json.loads(infofile.read_text())
    if info.get("is_live"):
        raise RuntimeError("Live streams are not supported; choose a finished video.")
    use_hardsubs = jobs[vid].get("hardsubs", True)
    caption_language = None
    if use_hardsubs:
        patch_job(vid, detail="Checking for creator-provided English subtitles…")
        caption_language = download_captions(info, infofile, folder, logfile)
    patch_job(
        vid,
        title=info.get("title", vid),
        duration=info.get("duration"),
        status="converting",
        detail="Converting to Wii-friendly H.264 / AAC…",
        captions={"language": caption_language, "kind": "manual"} if caption_language else None,
    )
    sources = [
        p for p in folder.glob("source.*") if p.suffix in {".mp4", ".mkv", ".webm", ".mov", ".flv"}
    ]
    if not sources:
        raise RuntimeError("Download did not produce a complete source video.")
    source = max(sources, key=lambda p: p.stat().st_mtime)
    duration = info.get("duration") or 0

    def encode_progress(line):
        if line.startswith("out_time_us=") and duration:
            try:
                percent = min(100, int(line.split("=", 1)[1]) / 10000 / duration)
                patch_job(vid, detail=f"Converting for Wii: {percent:.0f}%")
            except ValueError:
                pass

    output = folder / "video.pending.mp4"
    run_logged(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            video_filter(bool(caption_language)),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-profile:v",
            "baseline",
            "-level:v",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-maxrate",
            "1200k",
            "-bufsize",
            "2400k",
            "-g",
            "60",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ],
        logfile,
        encode_progress,
        cwd=folder,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    actual = float(json.loads(probe.stdout)["format"]["duration"])
    if actual <= 0 or (duration and actual < duration - max(3, duration * 0.01)):
        raise RuntimeError("Converted video is incomplete; refusing to publish it.")
    wii_output = folder / "video.pending.mkv"
    run_logged(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-i",
            str(output),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-map_metadata",
            "-1",
            "-c",
            "copy",
            str(wii_output),
        ],
        logfile,
    )
    output.replace(folder / "video.mp4")
    wii_output.replace(folder / "video.mkv")
    caption_note = (
        "creator-provided English hardsubs"
        if caption_language
        else "no creator-provided English subtitles available"
    )
    if not use_hardsubs:
        caption_note = "hardsubs turned off"
    patch_job(
        vid, status="ready", detail=f"Ready — {caption_note}.", duration=actual, wii_format="mkv"
    )
    source.unlink(missing_ok=True)
    infofile.unlink(missing_ok=True)


def worker():
    while True:
        vid = pending.get()
        try:
            prepare(vid)
        except Exception as exc:
            logging.exception("Preparation failed for %s", vid)
            patch_job(vid, status="error", detail=str(exc))
        finally:
            pending.task_done()


def search(query):
    return search_page(query)[0]


def search_page(query, page=1):
    query = query.strip()
    if not query or len(query) > 200:
        raise ValueError("Enter a search of 1–200 characters, or paste a YouTube URL.")
    if page < 1:
        raise ValueError("Choose a page number of 1 or higher.")
    try:
        vid = video_id(query)
    except ValueError:
        vid = None
    if vid:
        with lock:
            results = [dict(metadata.get(vid, {"id": vid, "title": f"YouTube video {vid}"}))]
            return (results if page == 1 else []), False
    cache_key = (query, page)
    with lock:
        cached = search_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
    start = (page - 1) * SEARCH_PAGE_SIZE + 1
    end = page * SEARCH_PAGE_SIZE
    # One extra result tells WiiMC whether another page exists.
    result = subprocess.run(
        YTDLP
        + [
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-start",
            str(start),
            "--playlist-end",
            str(end + 1),
            f"ytsearch{end + 1}:{query}",
        ],
        capture_output=True,
        text=True,
        timeout=50,
    )
    if result.returncode:
        raise RuntimeError("YouTube search failed. Try again, or paste a video URL.")
    entries = []
    raw_entries = json.loads(result.stdout).get("entries") or []
    has_next = len(raw_entries) > SEARCH_PAGE_SIZE
    for item in raw_entries[:SEARCH_PAGE_SIZE]:
        if not item or not re.fullmatch(r"[A-Za-z0-9_-]{11}", item.get("id", "")):
            continue
        entries.append(
            {
                "id": item["id"],
                "title": item.get("title", item["id"]),
                "duration": item.get("duration"),
                "channel": item.get("channel", ""),
            }
        )
    with lock:
        metadata.update({item["id"]: item for item in entries})
        search_cache[cache_key] = (time.monotonic(), (entries, has_next))
    return entries, has_next


@app.before_request
def lan_only():
    address = ipaddress.ip_address(request.remote_addr)
    if not (address.is_private or address.is_loopback):
        abort(403)
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        abort(403)


@app.after_request
def log_wii_request(response):
    if request.path.startswith(("/media/", "/wiimc/")):
        logging.info(
            "client=%s method=%s path=%s range=%s status=%s bytes=%s monotonic=%.3f",
            request.remote_addr,
            request.method,
            request.path,
            request.headers.get("Range", "-"),
            response.status_code,
            response.headers.get("Content-Length", "-"),
            time.monotonic(),
        )
    return response


@app.get("/")
def home():
    return render_template("index.html", base=BASE)


@app.get("/favicon.ico")
def favicon():
    return send_file(ROOT / "static/favicon.ico", mimetype="image/vnd.microsoft.icon")


@app.get("/api/search")
def api_search():
    try:
        return jsonify(search(request.args.get("q", "")))
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/jobs")
def api_jobs():
    with lock:
        return jsonify(sorted(jobs.values(), key=lambda item: item.get("updated", 0), reverse=True))


@app.post("/api/prepare")
def api_prepare():
    try:
        data = request.get_json() or {}
        hardsubs = data.get("hardsubs", True)
        if not isinstance(hardsubs, bool):
            raise ValueError("The hardsubs option must be true or false.")
        return jsonify(enqueue(video_id(data.get("video", "")), hardsubs=hardsubs)), 202
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.delete("/api/videos/<vid>")
def remove_video(vid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        return jsonify(error="Invalid video ID."), 400
    with lock:
        job = jobs.get(vid)
        if job is None:
            return jsonify(error="That video is no longer in your library."), 404
        if job["status"] not in {"ready", "error"}:
            return jsonify(error="Wait until preparation finishes before removing this video."), 409
        folder = DATA / vid
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError:
            logging.exception("Could not remove video %s", vid)
            return jsonify(error="Could not remove all video files. Please retry."), 500
        del jobs[vid]
        metadata.pop(vid, None)
    return jsonify(removed=vid)


@app.get("/media/<vid>.<extension>")
def media(vid, extension):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) or extension not in {"mp4", "mkv", "ts"}:
        abort(404)
    with lock:
        if jobs.get(vid, {}).get("status") != "ready":
            abort(404)
        path = DATA / vid / f"video.{extension}"
        if not path.is_file():
            abort(404)
        mime = {"mp4": "video/mp4", "mkv": "video/x-matroska", "ts": "video/mp2t"}[extension]
        response = send_file(path, mimetype=mime, conditional=True)
    # MPlayer checks this on the initial 200 response before enabling seeking.
    response.headers["Accept-Ranges"] = "bytes"
    return response


@app.get("/thumb/<vid>.jpg")
def thumb(vid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        abort(404)
    folder = DATA / vid
    target = folder / "thumb.jpg"
    with lock:
        if target.exists():
            return send_file(target, mimetype="image/jpeg", max_age=86400)
    try:
        with urlopen(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", timeout=10) as response:
            raw = response.read(2 * 1024 * 1024)
        picture = Image.open(io.BytesIO(raw)).convert("RGB")
        picture.thumbnail((320, 180))
        output = io.BytesIO()
        picture.save(output, "JPEG", quality=85, progressive=False)
        # A thumbnail request finishing after deletion must not recreate the folder.
        with lock:
            if vid in jobs:
                folder.mkdir(exist_ok=True)
                target.write_bytes(output.getvalue())
    except Exception:
        output = io.BytesIO()
        Image.new("RGB", (320, 180), "#393345").save(output, "JPEG")
    return send_file(io.BytesIO(output.getvalue()), mimetype="image/jpeg", max_age=3600)


def plx(entries):
    lines = ["version=1", "title=RiiTube at Home"]
    for kind, name, path, vid in entries:
        lines += ["", f"type={kind}", f"name={clean(name)}", f"URL={BASE}{path}"]
        if vid:
            lines.append(f"thumb={BASE}/thumb/{vid}.jpg")
    return (
        "\n".join(lines) + "\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
    )


@app.get("/wiimc/home.plx")
def wii_home():
    return plx(
        [
            ("search", "Search YouTube / paste video ID", "/wiimc/search.plx?q=", None),
            ("playlist", "Prepared videos and progress", "/wiimc/library.plx", None),
        ]
    )


@app.get("/wiimc/search.plx")
def wii_search():
    query = request.args.get("q", "").strip()
    try:
        page = int(request.args.get("page", "1"))
        if page < 1:
            raise ValueError("Choose a page number of 1 or higher.")
    except ValueError:
        return plx([("playlist", "Invalid page — back to search", "/wiimc/home.plx", None)])

    def page_url(number):
        return "/wiimc/search.plx?" + urlencode({"q": query, "page": number})

    entries = []
    if page > 1:
        entries.append(("playlist", f"Previous page ({page - 1})", page_url(page - 1), None))
    try:
        results, has_next = search_page(query, page)
        entries.extend(
            ("playlist", item["title"], f"/wiimc/video/{item['id']}.plx", item["id"])
            for item in results
        )
        if has_next:
            entries.append(("playlist", f"Next page ({page + 1})", page_url(page + 1), None))
        if not results:
            entries.append(("playlist", "No results — back to search", "/wiimc/home.plx", None))
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        entries.append(("playlist", f"{clean(exc)} — select to retry", page_url(page), None))
    entries.append(("search", "New search", "/wiimc/search.plx?q=", None))
    return plx(entries)


@app.get("/wiimc/video/<vid>.plx")
def wii_video(vid):
    try:
        vid = video_id(vid)
    except ValueError:
        abort(400)
    with lock:
        job = dict(jobs.get(vid, {}))
        title = job.get("title", metadata.get(vid, {}).get("title", vid))
    if job.get("status") == "ready":
        extension = "mkv" if (DATA / vid / "video.mkv").is_file() else "mp4"
        entries = [("video", f"Play: {title}", f"/media/{vid}.{extension}", vid)]
        if (DATA / vid / "video.ts").is_file():
            entries.append(("video", "Compatibility playback", f"/media/{vid}.ts", vid))
        return plx(entries)
    if not job or job.get("status") == "error":
        label = "Prepare for Wii" if not job else "Retry preparation"
        entries = [("playlist", f"{label}: {title}", f"/wiimc/prepare/{vid}.plx", vid)]
        if job:
            entries.append(("playlist", job["detail"], f"/wiimc/video/{vid}.plx", None))
        return plx(entries)
    return plx(
        [
            (
                "playlist",
                f"{job['status']}: {job['detail']} — select to refresh",
                f"/wiimc/video/{vid}.plx",
                vid,
            ),
            ("playlist", "Back to library", "/wiimc/library.plx", None),
        ]
    )


@app.get("/wiimc/prepare/<vid>.plx")
def wii_prepare(vid):
    try:
        enqueue(video_id(vid))
    except ValueError:
        abort(400)
    return wii_video(vid)


@app.get("/wiimc/library.plx")
def wii_library():
    with lock:
        entries = [
            ("playlist", f"[{job['status']}] {job['title']}", f"/wiimc/video/{vid}.plx", vid)
            for vid, job in jobs.items()
        ]
    entries.append(
        ("playlist", "Refresh library / nothing prepared yet", "/wiimc/library.plx", None)
    )
    return plx(entries)


@app.get("/onlinemedia.xml")
def online_media():
    root = ET.Element("file", app="WiiMC", version="3.0.0")
    folder = ET.SubElement(root, "folder", name="RiiTube at Home")
    ET.SubElement(
        folder, "link", name="Search YouTube", addr=f"{BASE}/wiimc/search.plx?q=", type="search"
    )
    ET.SubElement(
        folder,
        "link",
        name="Prepared videos and progress",
        addr=f"{BASE}/wiimc/library.plx",
        type="playlist",
    )
    return (
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        200,
        {
            "Content-Type": "application/xml",
            "Content-Disposition": 'attachment; filename="onlinemedia.xml"',
        },
    )


load_jobs()
if __name__ == "__main__":
    from waitress import serve

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(f"RiiTube at Home: {BASE}\nWiiMC configuration: {BASE}/onlinemedia.xml", flush=True)
    serve(app, host=HOST, port=PORT, threads=8)
