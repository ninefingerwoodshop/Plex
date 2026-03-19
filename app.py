"""Plex Search & Download - Web UI for Radarr/Sonarr/NZBGet."""
import sys
import os
import json
import re
import shutil
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

import requests as req
import time
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
from functools import wraps
from config import APP, NZBGET, PLEX, STORAGE, TMDB, INDEXERS
from api import (
    sonarr_get, sonarr_post, sonarr_delete,
    radarr_get, radarr_post, radarr_delete,
    plex_get, plex_get_sessions, plex_get_accounts, plex_get_history,
    tmdb_image_url, tmdb_movie_details, tmdb_tv_details, tmdb_search_tv,
)

# Feature modules
from recommendations import get_watch_profile, get_recommendations, get_trending_not_in_library
from analytics import get_user_stats, get_library_stats, get_year_in_review, get_comparative_stats
from health_monitor import run_full_health_check, get_latest_report, check_disk_space
from upgrade_tracker import (
    get_quality_distribution, find_upgrade_candidates, get_upgrade_history,
    get_stale_qualities, trigger_search_for_upgrades,
)
from collection_posters import get_plex_collections, find_tmdb_collection_art, auto_poster_all, auto_poster_single
from storage_balancer import get_drive_usage, get_media_per_drive, suggest_moves, get_largest_items, get_balance_report
from content_filter import get_all_shows, get_all_movies, get_plex_users, get_hidden_for_user, toggle_hide, bulk_update
from seasonal_collections import get_seasonal_summary, get_all_seasons_status, find_seasonal_movies, build_seasonal_collections, clean_expired_collections
from new_arrivals_digest import generate_digest, load_last_digest, save_digest, send_digest_discord

app = Flask(__name__, static_folder="static")
app.secret_key = APP["secret_key"]


@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


@app.route("/icon-180.png")
def icon_180():
    return app.send_static_file("icon-180.png")


@app.route("/icon-192.png")
def icon_192():
    return app.send_static_file("icon-192.png")


@app.route("/icon-512.png")
def icon_512():
    return app.send_static_file("icon-512.png")


# Cache for quality profiles and root folders
_cache = {}

# User downloads tracking
DOWNLOADS_FILE = os.path.join(os.path.dirname(__file__), "user_downloads.json")


def _load_downloads():
    if os.path.exists(DOWNLOADS_FILE):
        with open(DOWNLOADS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_downloads(data):
    with open(DOWNLOADS_FILE, "w") as f:
        json.dump(data, f, indent=2)


WEEKLY_LIMIT = 10
UNLIMITED_USERS = {"gibbens"}


def _get_tickets_remaining(username):
    """Count how many tickets a user has left this week (rolling 7 days)."""
    if username in UNLIMITED_USERS:
        return None  # None signals unlimited
    downloads = _load_downloads()
    items = downloads.get(username, [])
    cutoff = datetime.now().timestamp() - (7 * 24 * 60 * 60)
    recent = 0
    for d in items:
        added = d.get("added", "")
        try:
            ts = datetime.fromisoformat(added).timestamp()
            if ts > cutoff:
                recent += 1
        except (ValueError, TypeError):
            pass
    return max(0, WEEKLY_LIMIT - recent)


def _track_download(username, content_type, title, year, poster, tmdb_id=None, tvdb_id=None):
    downloads = _load_downloads()
    if username not in downloads:
        downloads[username] = []
    # Avoid duplicates
    for d in downloads[username]:
        if d.get("title") == title and d.get("type") == content_type:
            return
    downloads[username].append({
        "type": content_type,
        "title": title,
        "year": year,
        "poster": poster,
        "tmdbId": tmdb_id,
        "tvdbId": tvdb_id,
        "added": datetime.now().isoformat(),
    })
    _save_downloads(downloads)


def _nzbget_call(method, params=None):
    """Call NZBGet JSON-RPC API."""
    payload = {"method": method, "params": params or []}
    r = req.post(NZBGET["url"] + "/jsonrpc", json=payload, timeout=5)
    r.raise_for_status()
    return r.json().get("result")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _pick_root_folder(folders, preferred_drive="O:\\"):
    """Pick root folder on the preferred drive, fallback to first available."""
    for f in folders:
        if f["path"].upper().startswith(preferred_drive.upper()):
            return f["path"]
    return folders[0]["path"]


def get_radarr_defaults():
    if "radarr" not in _cache:
        profiles = radarr_get("/qualityprofile")
        folders = radarr_get("/rootfolder")
        _cache["radarr"] = {
            "qualityProfileId": profiles[0]["id"],
            "rootFolderPath": _pick_root_folder(folders),
        }
    return _cache["radarr"]


def get_sonarr_defaults():
    if "sonarr" not in _cache:
        profiles = sonarr_get("/qualityprofile")
        folders = sonarr_get("/rootfolder")
        _cache["sonarr"] = {
            "qualityProfileId": profiles[0]["id"],
            "rootFolderPath": _pick_root_folder(folders),
        }
    return _cache["sonarr"]


# --- Routes ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if username in APP["users"] and APP["users"][username] == password:
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    session.pop("username", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session.get("username", ""))


@app.route("/downloads")
@login_required
def downloads_page():
    return render_template("downloads.html", username=session.get("username", ""))


@app.route("/api/search")
@login_required
def search():
    term = request.args.get("q", "").strip()
    if not term:
        return jsonify([])

    # Get existing library to filter out
    try:
        existing_movies = {m.get("tmdbId") for m in radarr_get("/movie") if m.get("hasFile")}
    except Exception:
        existing_movies = set()

    try:
        existing_shows = {s.get("tvdbId") for s in sonarr_get("/series")
                         if s.get("statistics", {}).get("episodeFileCount", 0) > 0}
    except Exception:
        existing_shows = set()

    results = []

    try:
        movies = radarr_get("/movie/lookup", params={"term": term})
        for m in movies[:15]:
            if m.get("tmdbId") in existing_movies:
                continue
            poster = m.get("remotePoster", "")
            if not poster:
                for img in m.get("images", []):
                    if img.get("coverType") == "poster":
                        poster = img.get("remoteUrl", "")
                        break
            results.append({
                "type": "movie",
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "overview": m.get("overview", ""),
                "poster": poster,
                "tmdbId": m.get("tmdbId"),
                "imdbId": m.get("imdbId", ""),
                "payload": m,
            })
    except Exception as e:
        print(f"Radarr lookup error: {e}")

    try:
        shows = sonarr_get("/series/lookup", params={"term": term})
        for s in shows[:15]:
            if s.get("tvdbId") in existing_shows:
                continue
            poster = ""
            for img in s.get("images", []):
                if img.get("coverType") == "poster":
                    poster = img.get("remoteUrl", "")
                    break
            results.append({
                "type": "series",
                "title": s.get("title", ""),
                "year": s.get("year", ""),
                "overview": s.get("overview", ""),
                "poster": poster,
                "tvdbId": s.get("tvdbId"),
                "payload": s,
            })
    except Exception as e:
        print(f"Sonarr lookup error: {e}")

    return jsonify(results)


@app.route("/api/similar")
@login_required
def similar():
    """Get similar/recommended titles from TMDb for a given movie or show."""
    tmdb_id = request.args.get("tmdb_id", type=int)
    media_type = request.args.get("type", "movie")  # "movie" or "series"
    title = request.args.get("title", "")

    if not tmdb_id and not title:
        return jsonify([])

    # Get existing library to filter out
    try:
        existing_movies = {m.get("tmdbId") for m in radarr_get("/movie") if m.get("tmdbId")}
    except Exception:
        existing_movies = set()
    try:
        existing_shows = {s.get("tvdbId") for s in sonarr_get("/series") if s.get("tvdbId")}
    except Exception:
        existing_shows = set()

    results = []

    try:
        if media_type == "movie" and tmdb_id:
            details = tmdb_movie_details(tmdb_id)
            similar_list = details.get("recommendations", {}).get("results", [])
            if len(similar_list) < 5:
                similar_list += details.get("similar", {}).get("results", [])

            seen_ids = set()
            for item in similar_list:
                tid = item.get("id")
                if not tid or tid in seen_ids or tid in existing_movies:
                    continue
                seen_ids.add(tid)
                poster_path = item.get("poster_path")
                results.append({
                    "type": "movie",
                    "title": item.get("title", ""),
                    "year": (item.get("release_date") or "")[:4],
                    "overview": item.get("overview", ""),
                    "poster": tmdb_image_url(poster_path) if poster_path else "",
                    "tmdbId": tid,
                    "vote_average": item.get("vote_average", 0),
                })
        else:
            # For TV, use tmdb_id if available, otherwise search by title
            tv_tmdb_id = tmdb_id
            if not tv_tmdb_id and title:
                search = tmdb_search_tv(title)
                sr = search.get("results", [])
                if sr:
                    tv_tmdb_id = sr[0].get("id")

            if tv_tmdb_id:
                details = tmdb_tv_details(tv_tmdb_id)
                similar_list = details.get("recommendations", {}).get("results", [])
                if len(similar_list) < 5:
                    similar_list += details.get("similar", {}).get("results", [])

                seen_ids = set()
                for item in similar_list:
                    tid = item.get("id")
                    if not tid or tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    poster_path = item.get("poster_path")
                    results.append({
                        "type": "series",
                        "title": item.get("name", ""),
                        "year": (item.get("first_air_date") or "")[:4],
                        "overview": item.get("overview", ""),
                        "poster": tmdb_image_url(poster_path) if poster_path else "",
                        "tmdbId": tid,
                        "vote_average": item.get("vote_average", 0),
                    })
    except Exception as e:
        print(f"Similar lookup error: {e}")

    # Sort by vote_average descending, limit to 20
    results.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
    return jsonify(results[:20])


@app.route("/api/tickets")
@login_required
def get_tickets():
    username = session.get("username", "")
    remaining = _get_tickets_remaining(username)
    if remaining is None:
        return jsonify({"remaining": None, "total": None, "unlimited": True})
    return jsonify({"remaining": remaining, "total": WEEKLY_LIMIT, "unlimited": False})


@app.route("/api/add", methods=["POST"])
@login_required
def add():
    username = session.get("username", "unknown")
    data = request.get_json()
    content_type = data.get("type")
    payload = data.get("payload", {})
    poster = data.get("poster", "")

    # Check ticket limit
    remaining = _get_tickets_remaining(username)
    if remaining is not None and remaining <= 0:
        return jsonify({
            "status": "error",
            "message": "No tickets left this week. Your tickets reset in 7 days.",
        }), 429

    try:
        if content_type == "movie":
            result = _add_movie(payload)
        elif content_type == "series":
            result = _add_series(payload)
        else:
            return jsonify({"status": "error", "message": "Unknown type"}), 400

        # Track the download for the user
        _track_download(
            username,
            content_type,
            payload.get("title", ""),
            payload.get("year", ""),
            poster,
            tmdb_id=payload.get("tmdbId"),
            tvdb_id=payload.get("tvdbId"),
        )

        return result
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/remove", methods=["POST"])
@login_required
def remove():
    """Remove a movie/show from Radarr/Sonarr and the user's tracked downloads."""
    username = session.get("username", "unknown")
    data = request.get_json()
    content_type = data.get("type")
    title = data.get("title", "")
    tmdb_id = data.get("tmdbId")
    tvdb_id = data.get("tvdbId")

    try:
        if content_type == "movie" and tmdb_id:
            existing = radarr_get("/movie")
            for m in existing:
                if m.get("tmdbId") == tmdb_id:
                    radarr_delete(f"/movie/{m['id']}?deleteFiles=true")
                    break

        elif content_type == "series" and tvdb_id:
            existing = sonarr_get("/series")
            for s in existing:
                if s.get("tvdbId") == tvdb_id:
                    sonarr_delete(f"/series/{s['id']}?deleteFiles=true")
                    break

        # Remove from tracked downloads
        downloads = _load_downloads()
        if username in downloads:
            downloads[username] = [
                d for d in downloads[username]
                if not (d.get("title") == title and d.get("type") == content_type)
            ]
            _save_downloads(downloads)

        return jsonify({
            "status": "removed",
            "message": f"'{title}' removed",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/downloads")
@login_required
def get_downloads():
    user = request.args.get("user", "").strip().lower()
    downloads = _load_downloads()

    if user:
        return jsonify(downloads.get(user, []))

    return jsonify(downloads)


def _build_statuses(user):
    """Build status list for a user's tracked downloads."""
    downloads = _load_downloads()
    items = downloads.get(user, []) if user else []

    try:
        radarr_movies = radarr_get("/movie")
    except Exception:
        radarr_movies = []

    try:
        sonarr_series = sonarr_get("/series")
    except Exception:
        sonarr_series = []

    try:
        radarr_queue = radarr_get("/queue", params={"pageSize": 200})
        radarr_queue_records = radarr_queue.get("records", [])
    except Exception:
        radarr_queue_records = []

    try:
        sonarr_queue = sonarr_get("/queue", params={"pageSize": 200})
        sonarr_queue_records = sonarr_queue.get("records", [])
    except Exception:
        sonarr_queue_records = []

    statuses = []
    for item in items:
        status = {
            "title": item.get("title"),
            "type": item.get("type"),
            "state": "unknown",
            "pct": 0,
            "detail": "",
        }

        if item.get("type") == "movie":
            tmdb_id = item.get("tmdbId")
            for m in radarr_movies:
                if m.get("tmdbId") == tmdb_id:
                    if m.get("hasFile"):
                        status["state"] = "downloaded"
                        status["pct"] = 100
                        status["detail"] = "Downloaded"
                        try:
                            plex_results = plex_get(
                                f"/library/sections/{PLEX['movie_section']}/search",
                                {"query": item.get("title", "")},
                            )
                            if plex_results.get("MediaContainer", {}).get("Metadata", []):
                                status["state"] = "on_plex"
                                status["detail"] = "On Plex"
                        except Exception:
                            pass
                    else:
                        in_queue = False
                        for qr in radarr_queue_records:
                            if qr.get("movieId") == m.get("id"):
                                in_queue = True
                                sizeleft = qr.get("sizeleft", 0)
                                size = qr.get("size", 1)
                                pct = int((1 - sizeleft / size) * 100) if size > 0 else 0
                                time_left = qr.get("timeleft", "")
                                status["state"] = "downloading"
                                status["pct"] = pct
                                status["detail"] = f"{pct}% - {time_left} left" if time_left else f"{pct}%"
                                status["sizeMB"] = round(size / 1024 / 1024, 1)
                                status["downloadedMB"] = round((size - sizeleft) / 1024 / 1024, 1)
                                break
                        if not in_queue:
                            status["state"] = "waiting"
                            status["detail"] = "Searching for download"
                    break

        elif item.get("type") == "series":
            tvdb_id = item.get("tvdbId")
            for s in sonarr_series:
                if s.get("tvdbId") == tvdb_id:
                    stats = s.get("statistics", {})
                    total_eps = stats.get("totalEpisodeCount", 0)
                    have_eps = stats.get("episodeFileCount", 0)
                    pct = int(have_eps / total_eps * 100) if total_eps > 0 else 0

                    if have_eps == total_eps and total_eps > 0:
                        status["state"] = "downloaded"
                        status["pct"] = 100
                        status["detail"] = f"All {total_eps} episodes"
                        try:
                            plex_results = plex_get(
                                f"/library/sections/{PLEX['tv_section']}/search",
                                {"query": item.get("title", "")},
                            )
                            if plex_results.get("MediaContainer", {}).get("Metadata", []):
                                status["state"] = "on_plex"
                                status["detail"] = f"On Plex - {total_eps} episodes"
                        except Exception:
                            pass
                    elif have_eps > 0:
                        in_queue = False
                        for qr in sonarr_queue_records:
                            if qr.get("seriesId") == s.get("id"):
                                in_queue = True
                                sizeleft = qr.get("sizeleft", 0)
                                size = qr.get("size", 1)
                                dl_pct = int((1 - sizeleft / size) * 100) if size > 0 else 0
                                status["state"] = "downloading"
                                status["pct"] = pct
                                status["detail"] = f"{have_eps}/{total_eps} eps - downloading ({dl_pct}%)"
                                break
                        if not in_queue:
                            status["state"] = "partial"
                            status["pct"] = pct
                            status["detail"] = f"{have_eps}/{total_eps} episodes"
                    else:
                        in_queue = False
                        for qr in sonarr_queue_records:
                            if qr.get("seriesId") == s.get("id"):
                                in_queue = True
                                sizeleft = qr.get("sizeleft", 0)
                                size = qr.get("size", 1)
                                dl_pct = int((1 - sizeleft / size) * 100) if size > 0 else 0
                                status["state"] = "downloading"
                                status["pct"] = dl_pct
                                status["detail"] = f"Downloading ({dl_pct}%)"
                                break
                        if not in_queue:
                            status["state"] = "waiting"
                            status["detail"] = "Searching for downloads"
                    break

        statuses.append(status)

    return statuses


@app.route("/api/status")
@login_required
def download_status():
    user = request.args.get("user", "").strip().lower()
    return jsonify(_build_statuses(user))


@app.route("/api/status/stream")
@login_required
def status_stream():
    """SSE endpoint - pushes status updates every 5 seconds."""
    user = request.args.get("user", "").strip().lower()

    def generate():
        last_data = None
        while True:
            try:
                statuses = _build_statuses(user)
                data = json.dumps(statuses)
                # Only send if data changed
                if data != last_data:
                    yield f"data: {data}\n\n"
                    last_data = data
                else:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
            except GeneratorExit:
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _add_movie(payload):
    tmdb_id = payload.get("tmdbId")
    defaults = get_radarr_defaults()

    existing = radarr_get("/movie")
    for m in existing:
        if m.get("tmdbId") == tmdb_id:
            radarr_post("/command", {
                "name": "MoviesSearch",
                "movieIds": [m["id"]],
            })
            return jsonify({
                "status": "searched",
                "message": f"'{m['title']}' already in Radarr - search triggered",
            })

    radarr_post("/movie", {
        "title": payload.get("title"),
        "tmdbId": tmdb_id,
        "qualityProfileId": defaults["qualityProfileId"],
        "rootFolderPath": defaults["rootFolderPath"],
        "monitored": True,
        "addOptions": {"searchForMovie": True},
    })

    return jsonify({
        "status": "added",
        "message": f"'{payload.get('title')}' added and searching for downloads",
    })


def _add_series(payload):
    tvdb_id = payload.get("tvdbId")
    defaults = get_sonarr_defaults()

    existing = sonarr_get("/series")
    for s in existing:
        if s.get("tvdbId") == tvdb_id:
            sonarr_post("/command", {
                "name": "SeriesSearch",
                "seriesId": s["id"],
            })
            return jsonify({
                "status": "searched",
                "message": f"'{s['title']}' already in Sonarr - search triggered",
            })

    sonarr_post("/series", {
        "title": payload.get("title"),
        "tvdbId": tvdb_id,
        "qualityProfileId": defaults["qualityProfileId"],
        "rootFolderPath": defaults["rootFolderPath"],
        "monitored": True,
        "seasons": payload.get("seasons", []),
        "addOptions": {"searchForMissingEpisodes": True},
    })

    return jsonify({
        "status": "added",
        "message": f"'{payload.get('title')}' added and searching for downloads",
    })


# ============================================================
#  DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session.get("username", ""))


@app.route("/api/dashboard/stats")
@login_required
def dashboard_stats():
    """Quick stats for dashboard header."""
    try:
        movies = radarr_get("/movie")
        total_movies = sum(1 for m in movies if m.get("hasFile"))
    except Exception:
        movies = []
        total_movies = 0
    try:
        series = sonarr_get("/series")
        total_shows = len(series)
        total_episodes = sum(s.get("statistics", {}).get("episodeFileCount", 0) for s in series)
    except Exception:
        total_shows = 0
        total_episodes = 0
    try:
        sessions = plex_get_sessions()
        active_streams = len(sessions)
    except Exception:
        active_streams = 0
    try:
        disks = check_disk_space()
        total_storage_tb = round(sum(d["used_gb"] for d in disks) / 1024, 1)
    except Exception:
        total_storage_tb = 0
    return jsonify({
        "total_movies": total_movies,
        "total_shows": total_shows,
        "total_episodes": total_episodes,
        "active_streams": active_streams,
        "total_storage_tb": total_storage_tb,
    })


@app.route("/api/dashboard/sessions")
@login_required
def dashboard_sessions():
    """Current active Plex sessions."""
    sessions = plex_get_sessions()
    result = []
    for s in sessions:
        user = s.get("User", {}).get("title", "Unknown")
        title = s.get("grandparentTitle", s.get("title", "Unknown"))
        if s.get("type") == "episode":
            title += f" - S{s.get('parentIndex', 0):02d}E{s.get('index', 0):02d}"
        media = s.get("Media", [{}])[0]
        stream = s.get("Session", {})
        transcode = s.get("TranscodeSession", {})
        progress = 0
        if s.get("duration"):
            progress = round((s.get("viewOffset", 0) / s["duration"]) * 100)
        result.append({
            "user": user,
            "title": title,
            "progress": progress,
            "state": s.get("Player", {}).get("state", "unknown"),
            "device": s.get("Player", {}).get("device", ""),
            "product": s.get("Player", {}).get("product", ""),
            "quality": media.get("videoResolution", ""),
            "transcode": bool(transcode),
            "transcode_speed": transcode.get("speed", 0),
            "bandwidth": stream.get("bandwidth", 0),
        })
    return jsonify(result)


@app.route("/api/dashboard/stream")
@login_required
def dashboard_stream():
    """SSE endpoint for live dashboard updates."""
    def generate():
        last_data = None
        while True:
            try:
                sessions = plex_get_sessions()
                data = json.dumps([{
                    "user": s.get("User", {}).get("title", "Unknown"),
                    "title": (s.get("grandparentTitle", "") + " " + s.get("title", "")).strip(),
                    "progress": round((s.get("viewOffset", 0) / s["duration"]) * 100) if s.get("duration") else 0,
                    "state": s.get("Player", {}).get("state", "unknown"),
                    "device": s.get("Player", {}).get("device", ""),
                    "quality": s.get("Media", [{}])[0].get("videoResolution", ""),
                    "transcode": bool(s.get("TranscodeSession")),
                } for s in sessions])
                if data != last_data:
                    yield f"data: {data}\n\n"
                    last_data = data
                else:
                    yield ": heartbeat\n\n"
            except GeneratorExit:
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(5)
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/dashboard/disks")
@login_required
def dashboard_disks():
    """Disk space for all drives, keyed by drive letter (e.g. 'O:')."""
    raw = check_disk_space()
    result = {}
    for entry in raw:
        drive = entry.get("drive", "")
        key = drive.rstrip("\\/")  # "O:\\" -> "O:"
        if not key.endswith(":"):
            key += ":"
        result[key] = {
            "percent": entry.get("pct_used", 0),
            "used_gb": entry.get("used_gb", 0),
            "total_gb": entry.get("total_gb", 0),
            "free_gb": entry.get("free_gb", 0),
            "warning": entry.get("warning", False),
        }
    return jsonify(result)


@app.route("/api/dashboard/upcoming")
@login_required
def dashboard_upcoming():
    """Upcoming releases from Radarr/Sonarr."""
    upcoming = []
    try:
        cal = radarr_get("/calendar", {"includeSeries": "false", "unmonitored": "false"})
        for m in cal[:15]:
            upcoming.append({
                "title": m.get("title", ""),
                "type": "movie",
                "release_date": m.get("physicalRelease", m.get("digitalRelease", "")),
                "poster": m.get("remotePoster", ""),
            })
    except Exception:
        pass
    try:
        cal = sonarr_get("/calendar")
        for ep in cal[:15]:
            upcoming.append({
                "title": f"{ep.get('series', {}).get('title', '')} S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}",
                "type": "episode",
                "release_date": ep.get("airDateUtc", ""),
                "subtitle": ep.get("title", ""),
            })
    except Exception:
        pass
    upcoming.sort(key=lambda x: x.get("release_date", ""))
    return jsonify(upcoming[:20])


@app.route("/api/dashboard/recent")
@login_required
def dashboard_recent():
    """Recent downloads from Radarr/Sonarr history."""
    recent = []
    try:
        hist = radarr_get("/history", {"pageSize": 10, "sortKey": "date", "sortDirection": "descending"})
        for r in hist.get("records", [])[:10]:
            if r.get("eventType") == "downloadFolderImported":
                quality = r.get("quality", {}).get("quality", {}).get("name", "")
                recent.append({
                    "title": r.get("movie", {}).get("title", r.get("sourceTitle", "")),
                    "type": "movie",
                    "quality": quality,
                    "date": r.get("date", ""),
                })
    except Exception:
        pass
    try:
        hist = sonarr_get("/history", {"pageSize": 10, "sortKey": "date", "sortDirection": "descending"})
        for r in hist.get("records", [])[:10]:
            if r.get("eventType") == "downloadFolderImported":
                quality = r.get("quality", {}).get("quality", {}).get("name", "")
                recent.append({
                    "title": f"{r.get('series', {}).get('title', '')} S{r.get('episode', {}).get('seasonNumber', 0):02d}E{r.get('episode', {}).get('episodeNumber', 0):02d}",
                    "type": "episode",
                    "quality": quality,
                    "date": r.get("date", ""),
                })
    except Exception:
        pass
    recent.sort(key=lambda x: x.get("date", ""), reverse=True)
    return jsonify(recent[:10])


# ============================================================
#  RECOMMENDATIONS
# ============================================================

@app.route("/recommendations")
@login_required
def recommendations_page():
    return render_template("recommendations.html", username=session.get("username", ""))


@app.route("/api/recommendations/profile")
@login_required
def rec_profile():
    username = request.args.get("username", session.get("username", ""))
    try:
        profile = get_watch_profile()
        return jsonify(profile)
    except Exception as e:
        return jsonify({"error": str(e), "genres": [], "directors": [], "actors": []})


@app.route("/api/recommendations/picks")
@login_required
def rec_picks():
    try:
        recs = get_recommendations(limit=30)
        return jsonify(recs)
    except Exception as e:
        return jsonify({"error": str(e), "items": []})


@app.route("/api/recommendations/trending")
@login_required
def rec_trending():
    try:
        trending = get_trending_not_in_library(limit=20)
        return jsonify(trending)
    except Exception as e:
        return jsonify({"error": str(e), "items": []})


@app.route("/api/recommendations/add", methods=["POST"])
@login_required
def rec_add():
    """Add a recommended item to Radarr/Sonarr."""
    data = request.get_json()
    content_type = data.get("type", "")
    tmdb_id = data.get("tmdb_id")
    title = data.get("title", "")
    try:
        if content_type == "movie":
            defaults = get_radarr_defaults()
            # Check if already exists
            existing = radarr_get("/movie")
            for m in existing:
                if m.get("tmdbId") == tmdb_id:
                    return jsonify({"status": "exists", "message": f"'{title}' already in Radarr"})
            # Lookup and add
            results = radarr_get("/movie/lookup", {"term": f"tmdb:{tmdb_id}"})
            if results:
                payload = results[0]
                radarr_post("/movie", {
                    "title": payload.get("title", title),
                    "tmdbId": tmdb_id,
                    "qualityProfileId": defaults["qualityProfileId"],
                    "rootFolderPath": defaults["rootFolderPath"],
                    "monitored": True,
                    "addOptions": {"searchForMovie": True},
                })
                return jsonify({"status": "added", "message": f"'{title}' added to Radarr"})
        elif content_type == "tv":
            defaults = get_sonarr_defaults()
            results = sonarr_get("/series/lookup", {"term": title})
            if results:
                payload = results[0]
                existing = sonarr_get("/series")
                for s in existing:
                    if s.get("tvdbId") == payload.get("tvdbId"):
                        return jsonify({"status": "exists", "message": f"'{title}' already in Sonarr"})
                sonarr_post("/series", {
                    "title": payload.get("title", title),
                    "tvdbId": payload.get("tvdbId"),
                    "qualityProfileId": defaults["qualityProfileId"],
                    "rootFolderPath": defaults["rootFolderPath"],
                    "monitored": True,
                    "seasons": payload.get("seasons", []),
                    "addOptions": {"searchForMissingEpisodes": True},
                })
                return jsonify({"status": "added", "message": f"'{title}' added to Sonarr"})
        return jsonify({"status": "error", "message": "Could not find content to add"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================
#  ANALYTICS
# ============================================================

@app.route("/analytics")
@login_required
def analytics_page():
    return render_template("analytics.html", username=session.get("username", ""))


@app.route("/api/analytics/user")
@login_required
def analytics_user():
    username = request.args.get("username", session.get("username", ""))
    days = int(request.args.get("days", 365))
    try:
        stats = get_user_stats(username=username, days=days)
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/analytics/library")
@login_required
def analytics_library():
    try:
        stats = get_library_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/analytics/year-in-review")
@login_required
def analytics_year_review():
    username = request.args.get("username", session.get("username", ""))
    year = request.args.get("year", datetime.now().year, type=int)
    try:
        review = get_year_in_review(username=username, year=year)
        return jsonify(review)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/analytics/leaderboard")
@login_required
def analytics_leaderboard():
    try:
        stats = get_comparative_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)})


# ============================================================
#  HEALTH MONITOR
# ============================================================

@app.route("/api/health")
@login_required
def health_status():
    try:
        report = get_latest_report()
        if not report:
            report = run_full_health_check()
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)})


# ============================================================
#  UPGRADE TRACKER
# ============================================================

@app.route("/api/upgrades/distribution")
@login_required
def upgrades_distribution():
    try:
        return jsonify(get_quality_distribution())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/upgrades/candidates")
@login_required
def upgrades_candidates():
    try:
        return jsonify(find_upgrade_candidates())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/upgrades/history")
@login_required
def upgrades_history():
    days = int(request.args.get("days", 30))
    try:
        return jsonify(get_upgrade_history(days=days))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/upgrades/stale")
@login_required
def upgrades_stale():
    days = int(request.args.get("days", 180))
    try:
        return jsonify(get_stale_qualities(days=days))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/upgrades/search", methods=["POST"])
@login_required
def upgrades_search():
    data = request.get_json()
    movie_ids = data.get("movie_ids")
    try:
        result = trigger_search_for_upgrades(movie_ids=movie_ids)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
#  COLLECTION POSTERS
# ============================================================

@app.route("/api/collections")
@login_required
def api_collections():
    try:
        return jsonify(get_plex_collections())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/collections/poster", methods=["POST"])
@login_required
def api_collection_poster():
    data = request.get_json()
    name = data.get("name", "")
    dry_run = data.get("dry_run", False)
    try:
        result = auto_poster_single(name, dry_run=dry_run)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/collections/poster-all", methods=["POST"])
@login_required
def api_collection_poster_all():
    data = request.get_json() or {}
    dry_run = data.get("dry_run", True)
    try:
        results = auto_poster_all(dry_run=dry_run)
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
#  STORAGE BALANCER
# ============================================================

@app.route("/api/storage/usage")
@login_required
def storage_usage():
    try:
        return jsonify(get_drive_usage())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/storage/media")
@login_required
def storage_media():
    try:
        return jsonify(get_media_per_drive())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/storage/suggest")
@login_required
def storage_suggest():
    strategy = request.args.get("strategy", "balance")
    try:
        return jsonify(suggest_moves(strategy=strategy))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/storage/largest")
@login_required
def storage_largest():
    media_type = request.args.get("type", "movies")
    limit = int(request.args.get("limit", 20))
    try:
        return jsonify(get_largest_items(media_type=media_type, limit=limit))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/storage/report")
@login_required
def storage_report():
    try:
        return jsonify(get_balance_report())
    except Exception as e:
        return jsonify({"error": str(e)})


# ============================================================
#  CONTENT FILTER (per-user show/movie hiding)
# ============================================================

@app.route("/filter")
@login_required
def filter_page():
    return render_template("filter.html", username=session.get("username", ""))


@app.route("/filter/userscript")
@login_required
def filter_userscript():
    return app.send_static_file("plex-hide-userscript.js"), 200, {"Content-Type": "text/javascript"}


@app.route("/api/filter/users")
@login_required
def filter_users():
    try:
        return jsonify(get_plex_users())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filter/shows")
@login_required
def filter_shows():
    username = request.args.get("username", "").strip().lower()
    try:
        shows = get_all_shows()
        hide_label = f"hide-{username}" if username else ""
        for s in shows:
            s["hidden"] = any(l.lower() == hide_label for l in s.get("labels", []))
        return jsonify(shows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filter/movies")
@login_required
def filter_movies():
    username = request.args.get("username", "").strip().lower()
    try:
        movies = get_all_movies()
        hide_label = f"hide-{username}" if username else ""
        for m in movies:
            m["hidden"] = any(l.lower() == hide_label for l in m.get("labels", []))
        return jsonify(movies)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filter/toggle", methods=["POST", "OPTIONS"])
def filter_toggle():
    # Handle CORS preflight
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Filter-Token"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        return resp

    # Auth: either session login OR X-Filter-Token header matching Plex token
    token = request.headers.get("X-Filter-Token", "")
    if not session.get("logged_in") and token != PLEX["token"]:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json()
    username = data.get("username", "").strip().lower()
    rating_key = data.get("ratingKey")
    media_type = data.get("mediaType", "show")
    if not username or not rating_key:
        return jsonify({"error": "Missing username or ratingKey"}), 400
    section_id = PLEX["tv_section"] if media_type == "show" else PLEX["movie_section"]
    plex_type = 2 if media_type == "show" else 1
    try:
        is_hidden = toggle_hide(username, rating_key, section_id, plex_type)
        resp = jsonify({"hidden": is_hidden})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
#  SEASONAL COLLECTIONS
# ============================================================

@app.route("/seasonal")
@login_required
def seasonal_page():
    return render_template("seasonal.html", username=session.get("username", ""))


@app.route("/api/seasonal/summary")
@login_required
def seasonal_summary():
    try:
        return jsonify(get_seasonal_summary())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/seasonal/preview")
@login_required
def seasonal_preview():
    season_key = request.args.get("season")
    try:
        if season_key:
            return jsonify(find_seasonal_movies(season_key=season_key))
        return jsonify(find_seasonal_movies())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/seasonal/build", methods=["POST"])
@login_required
def seasonal_build():
    try:
        results = build_seasonal_collections(dry_run=False)
        return jsonify({"status": "ok", "built": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/seasonal/clean", methods=["POST"])
@login_required
def seasonal_clean():
    try:
        removed = clean_expired_collections(dry_run=False)
        return jsonify({"status": "ok", "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
#  NEW ARRIVALS DIGEST
# ============================================================

@app.route("/arrivals")
@login_required
def arrivals_page():
    return render_template("arrivals.html", username=session.get("username", ""))


@app.route("/api/arrivals/digest")
@login_required
def arrivals_digest():
    days = int(request.args.get("days", 7))
    try:
        digest = generate_digest(days=days)
        save_digest(digest)
        return jsonify(digest)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/arrivals/last")
@login_required
def arrivals_last():
    try:
        digest = load_last_digest()
        if digest:
            return jsonify(digest)
        return jsonify({"error": "No saved digest found"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/arrivals/discord", methods=["POST"])
@login_required
def arrivals_discord():
    data = request.get_json() or {}
    webhook_url = data.get("webhook_url", "")
    days = data.get("days", 7)
    if not webhook_url:
        return jsonify({"error": "No webhook URL provided"}), 400
    try:
        digest = generate_digest(days=days)
        success = send_digest_discord(digest, webhook_url)
        return jsonify({"status": "sent" if success else "failed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500




# ============================================================
#  HEALTH MONITOR BACKGROUND THREAD
# ============================================================

def _start_health_monitor():
    """Start health monitor in background thread."""
    from health_monitor import monitor_loop
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    _start_health_monitor()
    app.run(host="0.0.0.0", port=APP["port"], debug=True)
