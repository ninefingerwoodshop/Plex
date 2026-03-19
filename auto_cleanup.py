# Plex Auto-Cleanup: watch-and-remove with 24h grace period
# Receives Plex webhooks, tracks watched episodes/movies per user,
# and removes + unmonitors them after the grace period expires.
#
# Usage:
#   python auto_cleanup.py serve          - Start webhook server + cleanup scheduler
#   python auto_cleanup.py assign <title> <user>  - Assign a show/movie to a user
#   python auto_cleanup.py unassign <title>       - Remove an assignment
#   python auto_cleanup.py list                   - Show all assignments
#   python auto_cleanup.py pending                - Show pending cleanups
#   python auto_cleanup.py process                - Manually process pending cleanups now

import json
import os
import sys
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string

from config import PLEX, SONARR, RADARR, CLEANUP
from api import (
    sonarr_get, sonarr_put, sonarr_delete,
    radarr_get, radarr_put, radarr_delete,
    plex_get,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ATTR_FILE = os.path.join(SCRIPT_DIR, CLEANUP["attributions_file"])
PENDING_FILE = os.path.join(SCRIPT_DIR, CLEANUP["pending_file"])
GRACE_HOURS = CLEANUP["grace_period_hours"]

app = Flask(__name__)


# --- JSON persistence ---

def load_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# --- Attribution management ---

def get_attributions():
    return load_json(ATTR_FILE, {"tv": {}, "movies": {}})


def save_attributions(attr):
    save_json(ATTR_FILE, attr)


def find_attribution(title, media_type):
    """Find which users a title is attributed to. Returns list of usernames or None."""
    attr = get_attributions()
    category = "tv" if media_type == "episode" else "movies"
    title_lower = title.lower()
    for stored_title, users in attr.get(category, {}).items():
        if stored_title.lower() == title_lower:
            if isinstance(users, list):
                return users
            return [users]
    return None


# --- Sonarr helpers ---

def find_sonarr_series_by_title(title):
    """Find a Sonarr series by title (case-insensitive)."""
    series_list = sonarr_get("/series")
    title_lower = title.lower()
    for s in series_list:
        if s["title"].lower() == title_lower:
            return s
    return None


def find_sonarr_episode(series_id, season_num, episode_num):
    """Find a specific episode in Sonarr."""
    episodes = sonarr_get("/episode", {"seriesId": series_id})
    for ep in episodes:
        if ep["seasonNumber"] == season_num and ep["episodeNumber"] == episode_num:
            return ep
    return None


def unmonitor_and_delete_episode(series_id, season_num, episode_num):
    """Unmonitor an episode in Sonarr and delete its file."""
    ep = find_sonarr_episode(series_id, season_num, episode_num)
    if not ep:
        print(f"  Episode S{season_num:02d}E{episode_num:02d} not found in Sonarr")
        return False

    # Unmonitor
    ep["monitored"] = False
    sonarr_put(f"/episode/{ep['id']}", ep)
    print(f"  Unmonitored S{season_num:02d}E{episode_num:02d}")

    # Delete file if it exists
    if ep.get("episodeFileId", 0) > 0:
        sonarr_delete(f"/episodefile/{ep['episodeFileId']}")
        print(f"  Deleted episode file (id={ep['episodeFileId']})")
    else:
        print(f"  No file to delete for S{season_num:02d}E{episode_num:02d}")

    return True


# --- Radarr helpers ---

def find_radarr_movie_by_title(title):
    """Find a Radarr movie by title (case-insensitive)."""
    movies = radarr_get("/movie")
    title_lower = title.lower()
    for m in movies:
        if m["title"].lower() == title_lower:
            return m
    return None


def unmonitor_and_delete_movie(radarr_movie):
    """Unmonitor a movie in Radarr and delete its file."""
    radarr_movie["monitored"] = False
    radarr_put(f"/movie/{radarr_movie['id']}", radarr_movie)
    print(f"  Unmonitored movie: {radarr_movie['title']}")

    if radarr_movie.get("movieFile"):
        file_id = radarr_movie["movieFile"]["id"]
        radarr_delete(f"/moviefile/{file_id}")
        print(f"  Deleted movie file (id={file_id})")
    else:
        print(f"  No file to delete for {radarr_movie['title']}")

    return True


# --- Pending cleanup queue ---

def add_pending(entry):
    """Add a watched item to the pending cleanup queue with a grace period."""
    pending = load_json(PENDING_FILE, [])
    cleanup_at = (datetime.now() + timedelta(hours=GRACE_HOURS)).isoformat()
    entry["cleanup_at"] = cleanup_at
    entry["added"] = datetime.now().isoformat()

    # Deduplicate: don't add if same item already pending
    for p in pending:
        if p.get("type") == entry["type"] and p.get("title") == entry["title"]:
            if entry["type"] == "episode":
                if (p.get("season") == entry.get("season") and
                        p.get("episode") == entry.get("episode")):
                    print(f"  Already pending: {entry['title']} S{entry['season']:02d}E{entry['episode']:02d}")
                    return
            else:
                print(f"  Already pending: {entry['title']}")
                return

    pending.append(entry)
    save_json(PENDING_FILE, pending)
    print(f"  Queued for cleanup at {cleanup_at}")


def process_pending():
    """Process all pending cleanups whose grace period has expired."""
    pending = load_json(PENDING_FILE, [])
    if not pending:
        return

    now = datetime.now()
    remaining = []
    processed = 0

    for entry in pending:
        cleanup_at = datetime.fromisoformat(entry["cleanup_at"])
        if now < cleanup_at:
            remaining.append(entry)
            continue

        print(f"\nProcessing cleanup: {entry['title']}")

        if entry["type"] == "episode":
            series = find_sonarr_series_by_title(entry["title"])
            if series:
                success = unmonitor_and_delete_episode(
                    series["id"], entry["season"], entry["episode"]
                )
                if success:
                    processed += 1
                else:
                    remaining.append(entry)
            else:
                print(f"  Series '{entry['title']}' not found in Sonarr, skipping")
                remaining.append(entry)

        elif entry["type"] == "movie":
            movie = find_radarr_movie_by_title(entry["title"])
            if movie:
                success = unmonitor_and_delete_movie(movie)
                if success:
                    processed += 1
                else:
                    remaining.append(entry)
            else:
                print(f"  Movie '{entry['title']}' not found in Radarr, skipping")
                remaining.append(entry)

    save_json(PENDING_FILE, remaining)
    if processed:
        print(f"\nProcessed {processed} cleanup(s), {len(remaining)} still pending")


# --- Plex webhook handler ---

@app.route("/webhook", methods=["POST"])
def plex_webhook():
    """Handle Plex webhook events."""
    try:
        payload = json.loads(request.form.get("payload", "{}"))
    except (json.JSONDecodeError, TypeError):
        return "bad payload", 400

    event = payload.get("event", "")
    if event != "media.scrobble":
        return "ignored", 200

    metadata = payload.get("Metadata", {})
    account = payload.get("Account", {})
    plex_user = account.get("title", "")

    media_type = metadata.get("type", "")
    show_title = metadata.get("grandparentTitle", "")  # for episodes
    movie_title = metadata.get("title", "")  # for movies

    if media_type == "episode":
        title = show_title
        attributed_users = find_attribution(title, "episode")
        if attributed_users and plex_user.lower() in [u.lower() for u in attributed_users]:
            season = metadata.get("parentIndex", 0)
            episode = metadata.get("index", 0)
            print(f"\n[WATCHED] {plex_user} finished {title} S{season:02d}E{episode:02d}")
            add_pending({
                "type": "episode",
                "title": title,
                "season": season,
                "episode": episode,
                "user": plex_user,
            })
        return "ok", 200

    elif media_type == "movie":
        title = movie_title
        attributed_users = find_attribution(title, "movie")
        if attributed_users and plex_user.lower() in [u.lower() for u in attributed_users]:
            print(f"\n[WATCHED] {plex_user} finished movie: {title}")
            add_pending({
                "type": "movie",
                "title": title,
                "user": plex_user,
            })
        return "ok", 200

    return "ignored", 200


# --- Web UI ---

WEB_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>Plex Auto-Cleanup</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #1a1a2e; color: #eee; padding: 20px; }
  h1 { color: #e5a00d; margin-bottom: 20px; }
  .container { max-width: 1000px; margin: 0 auto; }
  .card { background: #16213e; border-radius: 8px; padding: 20px; margin-bottom: 15px; }
  .filter-bar { display: flex; gap: 10px; margin-bottom: 15px; align-items: center; flex-wrap: wrap; }
  .filter-bar input { flex: 1; min-width: 200px; padding: 10px 14px; border-radius: 6px; border: 1px solid #333;
                      background: #0f3460; color: #eee; font-size: 14px; }
  .filter-bar input::placeholder { color: #888; }
  .filter-bar select { padding: 10px; border-radius: 6px; border: 1px solid #333;
                        background: #0f3460; color: #eee; font-size: 14px; }
  .btn { padding: 10px 20px; border-radius: 6px; border: none; cursor: pointer;
         font-size: 14px; font-weight: bold; }
  .btn-gold { background: #e5a00d; color: #1a1a2e; }
  .btn-gold:hover { background: #f0b429; }
  .btn-green { background: #4caf50; color: white; }
  .btn-green:hover { background: #66bb6a; }
  .btn-red { background: #e53935; color: white; }
  .btn-red:hover { background: #ef5350; }
  .btn-orange { background: #ff9800; color: #1a1a2e; }
  .btn-orange:hover { background: #ffa726; }
  .btn-sm { padding: 6px 14px; font-size: 13px; }
  .type-badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #533483; margin-left: 8px; }
  .lib-item { display: flex; align-items: center; padding: 8px 14px; background: #0f3460;
              border-radius: 6px; margin-bottom: 4px; gap: 12px; }
  .lib-item:hover { background: #133a6a; }
  .lib-item input[type="checkbox"] { width: 18px; height: 18px; accent-color: #e5a00d; cursor: pointer; flex-shrink: 0; }
  .lib-item .title { flex: 1; font-weight: 500; }
  .lib-item .assigned-to { color: #4caf50; font-size: 12px; margin-left: 8px; }
  .attr-item { display: flex; justify-content: space-between; align-items: center;
               padding: 10px 14px; background: #0f3460; border-radius: 6px; margin-bottom: 6px; }
  .pending-item { padding: 10px 14px; background: #0f3460; border-radius: 6px; margin-bottom: 6px;
                  display: flex; justify-content: space-between; align-items: center; }
  .time-left { color: #aaa; font-size: 12px; }
  .empty { color: #666; padding: 15px; text-align: center; }
  .status-msg { padding: 10px; margin-bottom: 10px; border-radius: 6px; display: none; }
  .status-msg.success { background: #1b5e20; display: block; }
  .status-msg.error { background: #b71c1c; display: block; }
  .tabs { display: flex; gap: 5px; margin-bottom: 20px; }
  .tab { padding: 10px 20px; border-radius: 6px 6px 0 0; border: none; cursor: pointer;
         font-size: 14px; font-weight: 500; }
  .tab.active { background: #16213e; color: #e5a00d; }
  .tab:not(.active) { background: #0f3460; color: #888; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .bulk-bar { display: flex; gap: 10px; align-items: center; padding: 12px 14px;
              background: #1a1a2e; border-radius: 6px; margin-bottom: 12px; flex-wrap: wrap; }
  .bulk-bar label { font-size: 13px; color: #aaa; }
  .bulk-bar .user-checks { display: flex; gap: 8px; flex-wrap: wrap; }
  .bulk-bar .user-check { display: flex; align-items: center; gap: 4px; font-size: 13px;
                           background: #0f3460; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
  .bulk-bar .user-check input { accent-color: #e5a00d; cursor: pointer; }
  .select-controls { display: flex; gap: 8px; margin-bottom: 10px; }
  .select-controls button { padding: 4px 12px; border-radius: 4px; border: 1px solid #333;
                             background: #0f3460; color: #aaa; cursor: pointer; font-size: 12px; }
  .select-controls button:hover { color: #eee; border-color: #e5a00d; }
  .count-badge { background: #e5a00d; color: #1a1a2e; padding: 2px 8px; border-radius: 10px;
                 font-size: 11px; font-weight: bold; margin-left: 6px; }
  .loading { text-align: center; padding: 30px; color: #888; }
</style>
</head>
<body>
<div class="container">
  <h1>Plex Auto-Cleanup</h1>
  <div id="status" class="status-msg"></div>

  <div class="tabs">
    <button class="tab active" onclick="showTab('library', this)">Library</button>
    <button class="tab" onclick="showTab('current', this)">Assignments</button>
    <button class="tab" onclick="showTab('pending', this)">Pending Cleanup</button>
  </div>

  <div id="tab-library" class="tab-content active">
    <div class="card">
      <div class="filter-bar">
        <input type="text" id="lib-filter" placeholder="Filter by title..." oninput="filterLibrary()">
        <select id="lib-type-filter" onchange="filterLibrary()">
          <option value="all">All</option>
          <option value="tv">TV Shows</option>
          <option value="movie">Movies</option>
        </select>
      </div>
      <div class="bulk-bar" id="bulk-bar" style="display:none;">
        <span id="selected-count">0 selected</span>
        <label>Assign to:</label>
        <div class="user-checks" id="user-checks"></div>
        <button class="btn btn-green btn-sm" onclick="bulkAssign()">Assign Selected</button>
      </div>
      <div class="select-controls">
        <button onclick="selectAll()">Select All Visible</button>
        <button onclick="selectNone()">Select None</button>
      </div>
      <div id="library-list"><div class="loading">Loading library...</div></div>
    </div>
  </div>

  <div id="tab-current" class="tab-content">
    <div class="card">
      <div id="attributions"></div>
    </div>
  </div>

  <div id="tab-pending" class="tab-content">
    <div class="card">
      <div id="pending"></div>
    </div>
  </div>
</div>

<script>
const USERS = {{users|tojson}};
let libraryData = [];
let currentAttr = {tv:{}, movies:{}};

document.addEventListener('DOMContentLoaded', () => {
  // Build user checkboxes for bulk assign
  const uc = document.getElementById('user-checks');
  uc.innerHTML = USERS.map(u =>
    '<label class="user-check"><input type="checkbox" value="' + u + '"> ' + u + '</label>'
  ).join('');
  loadLibrary();
});

function showTab(name, btn) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  if (name === 'library') loadLibrary();
  if (name === 'current') loadAttributions();
  if (name === 'pending') loadPending();
}

function showStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status-msg ' + type;
  setTimeout(() => { el.className = 'status-msg'; }, 3000);
}

async function loadLibrary() {
  const [libRes, attrRes] = await Promise.all([
    fetch('/api/library'),
    fetch('/api/attributions')
  ]);
  libraryData = (await libRes.json()).library || [];
  currentAttr = await attrRes.json();
  filterLibrary();
}

function getAssignedUser(title, type) {
  const cat = type === 'tv' ? 'tv' : 'movies';
  const entries = currentAttr[cat] || {};
  for (const [t, u] of Object.entries(entries)) {
    if (t.toLowerCase() === title.toLowerCase()) return u;
  }
  return null;
}

function filterLibrary() {
  const q = document.getElementById('lib-filter').value.toLowerCase().trim();
  const typeFilter = document.getElementById('lib-type-filter').value;
  const el = document.getElementById('library-list');

  let filtered = libraryData;
  if (q) filtered = filtered.filter(r => r.title.toLowerCase().includes(q));
  if (typeFilter !== 'all') filtered = filtered.filter(r => r.type === typeFilter);

  if (filtered.length === 0) {
    el.innerHTML = '<div class="empty">No items match</div>';
    updateBulkBar();
    return;
  }

  el.innerHTML = filtered.map((r, i) => {
    const badge = r.type === 'tv' ? 'TV' : 'Movie';
    const user = getAssignedUser(r.title, r.type);
    const assignedHtml = user ? '<span class="assigned-to">[' + user + ']</span>' : '';
    const safeTitle = r.title.replace(/"/g, '&quot;');
    return '<div class="lib-item">' +
      '<input type="checkbox" class="lib-check" data-title="' + safeTitle + '" data-type="' + r.type + '" onchange="updateBulkBar()">' +
      '<span class="title">' + r.title + assignedHtml + '</span>' +
      '<span class="type-badge">' + badge + '</span>' +
      '</div>';
  }).join('');
  updateBulkBar();
}

function getChecked() {
  return Array.from(document.querySelectorAll('.lib-check:checked')).map(cb => ({
    title: cb.dataset.title, type: cb.dataset.type
  }));
}

function updateBulkBar() {
  const checked = getChecked();
  const bar = document.getElementById('bulk-bar');
  const count = document.getElementById('selected-count');
  if (checked.length > 0) {
    bar.style.display = 'flex';
    count.textContent = checked.length + ' selected';
  } else {
    bar.style.display = 'none';
  }
}

function selectAll() {
  document.querySelectorAll('.lib-check').forEach(cb => cb.checked = true);
  updateBulkBar();
}

function selectNone() {
  document.querySelectorAll('.lib-check').forEach(cb => cb.checked = false);
  updateBulkBar();
}

async function bulkAssign() {
  const checked = getChecked();
  const selectedUsers = Array.from(document.querySelectorAll('#user-checks input:checked')).map(cb => cb.value);
  if (checked.length === 0) { showStatus('No items selected', 'error'); return; }
  if (selectedUsers.length === 0) { showStatus('No users selected', 'error'); return; }

  const res = await fetch('/api/bulk-assign', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({items: checked, users: selectedUsers})
  });
  const data = await res.json();
  if (data.ok) {
    showStatus('Assigned ' + data.count + ' item(s) to ' + selectedUsers.join(', '), 'success');
    selectNone();
    loadLibrary();
  } else {
    showStatus(data.error || 'Failed', 'error');
  }
}

async function loadAttributions() {
  const res = await fetch('/api/attributions');
  const data = await res.json();
  currentAttr = data;
  const el = document.getElementById('attributions');
  const items = [];
  for (const [title, users] of Object.entries(data.tv || {})) {
    const u = Array.isArray(users) ? users.join(', ') : users;
    items.push({title, user: u, type: 'tv'});
  }
  for (const [title, users] of Object.entries(data.movies || {})) {
    const u = Array.isArray(users) ? users.join(', ') : users;
    items.push({title, user: u, type: 'movies'});
  }
  if (items.length === 0) {
    el.innerHTML = '<div class="empty">No assignments yet. Go to Library tab to assign.</div>';
    return;
  }
  el.innerHTML = items.map((a, i) => {
    const badge = a.type === 'tv' ? 'TV' : 'Movie';
    return '<div class="attr-item"><span><span class="title">' + a.title +
      '</span><span class="type-badge">' + badge + '</span> &rarr; <strong>' + a.user + '</strong></span>' +
      '<button class="btn btn-red btn-sm" data-idx="' + i + '">Remove</button></div>';
  }).join('');
  // Bind remove buttons via data attributes to avoid quote escaping issues
  document.querySelectorAll('.attr-item .btn-red').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx);
      doUnassign(items[idx].title);
    });
  });
}

async function doUnassign(title) {
  const res = await fetch('/api/unassign', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: title})
  });
  const data = await res.json();
  if (data.ok) { showStatus('Removed "' + title + '"', 'success'); loadAttributions(); }
  else showStatus(data.error || 'Failed', 'error');
}

async function loadPending() {
  const res = await fetch('/api/pending');
  const data = await res.json();
  const el = document.getElementById('pending');
  if (!data.pending || data.pending.length === 0) {
    el.innerHTML = '<div class="empty">No pending cleanups</div>';
    return;
  }
  el.innerHTML = data.pending.map((p, i) => {
    let label = p.title;
    if (p.type === 'episode') label += ' S' + String(p.season).padStart(2,'0') + 'E' + String(p.episode).padStart(2,'0');
    const dt = new Date(p.cleanup_at);
    const timeStr = dt.toLocaleString();
    return '<div class="pending-item"><span>' + label +
      ' <span class="time-left">(cleanup: ' + timeStr + ', user: ' + p.user + ')</span></span>' +
      '<button class="btn btn-orange btn-sm" onclick="doCancel(' + i + ')">Cancel</button></div>';
  }).join('');
}

async function doCancel(idx) {
  const res = await fetch('/api/cancel', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: idx})
  });
  const data = await res.json();
  if (data.ok) { showStatus('Cancelled cleanup', 'success'); loadPending(); }
  else showStatus(data.error || 'Failed', 'error');
}
</script>
</body>
</html>
"""


def get_plex_users():
    """Get list of Plex user names."""
    try:
        data = plex_get("/accounts")
        accounts = data.get("MediaContainer", {}).get("Account", [])
        return [a["name"] for a in accounts if a.get("name")]
    except Exception:
        return ["gibbens", "Melinda", "Jack", "Kate", "Patricia",
                "Christian M Ackman", "Bizzie", "ACKMAN", "katherinegibbens", "Emily Lehnes"]


@app.route("/")
def web_ui():
    users = get_plex_users()
    return render_template_string(WEB_TEMPLATE, users=users)


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").lower().strip()
    if not q:
        return jsonify({"results": []})

    results = []
    try:
        series_list = sonarr_get("/series")
        for s in series_list:
            if q in s["title"].lower():
                results.append({"title": s["title"], "type": "tv"})
    except Exception:
        pass

    try:
        movies = radarr_get("/movie")
        for m in movies:
            if q in m["title"].lower():
                results.append({"title": m["title"], "type": "movie"})
    except Exception:
        pass

    results.sort(key=lambda r: r["title"].lower())
    return jsonify({"results": results[:30]})


@app.route("/api/library")
def api_library():
    """Return all shows from Sonarr and movies from Radarr that have files on disk."""
    library = []
    try:
        series_list = sonarr_get("/series")
        for s in series_list:
            if s.get("statistics", {}).get("episodeFileCount", 0) > 0:
                library.append({"title": s["title"], "type": "tv"})
    except Exception:
        pass
    try:
        movies = radarr_get("/movie")
        for m in movies:
            if m.get("hasFile", False):
                library.append({"title": m["title"], "type": "movie"})
    except Exception:
        pass
    library.sort(key=lambda r: r["title"].lower())
    return jsonify({"library": library})


@app.route("/api/assign", methods=["POST"])
def api_assign():
    data = request.get_json()
    title = data.get("title", "")
    media_type = data.get("type", "")
    user = data.get("user", "")
    if not title or not user:
        return jsonify({"ok": False, "error": "Missing title or user"})

    attr = get_attributions()
    category = "tv" if media_type == "tv" else "movies"
    attr[category][title] = user
    save_attributions(attr)
    return jsonify({"ok": True})


@app.route("/api/bulk-assign", methods=["POST"])
def api_bulk_assign():
    data = request.get_json()
    items = data.get("items", [])
    users = data.get("users", [])
    if not items or not users:
        return jsonify({"ok": False, "error": "No items or users selected"})

    attr = get_attributions()
    count = 0
    for item in items:
        title = item.get("title", "")
        media_type = item.get("type", "")
        if not title:
            continue
        category = "tv" if media_type == "tv" else "movies"
        # Store as list if multiple users, string if single
        attr[category][title] = users if len(users) > 1 else users[0]
        count += 1
    save_attributions(attr)
    return jsonify({"ok": True, "count": count})


@app.route("/api/unassign", methods=["POST"])
def api_unassign():
    data = request.get_json()
    title = data.get("title", "").lower()
    attr = get_attributions()
    for category in ["tv", "movies"]:
        for stored_title in list(attr.get(category, {}).keys()):
            if stored_title.lower() == title:
                del attr[category][stored_title]
                save_attributions(attr)
                return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Not found"})


@app.route("/api/attributions")
def api_attributions():
    return jsonify(get_attributions())


@app.route("/api/pending")
def api_pending():
    return jsonify({"pending": load_json(PENDING_FILE, [])})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    data = request.get_json()
    idx = data.get("index", -1)
    pending = load_json(PENDING_FILE, [])
    if 0 <= idx < len(pending):
        removed = pending.pop(idx)
        save_json(PENDING_FILE, pending)
        return jsonify({"ok": True, "removed": removed})
    return jsonify({"ok": False, "error": "Invalid index"})


# --- Background scheduler ---

def cleanup_scheduler():
    """Run cleanup processing every 15 minutes."""
    while True:
        time.sleep(900)
        try:
            process_pending()
        except Exception as e:
            print(f"Cleanup scheduler error: {e}")


# --- CLI ---

def cli_assign(args):
    if len(args) < 2:
        print("Usage: auto_cleanup.py assign <title> <username>")
        return

    title = args[0]
    user = args[1]
    attr = get_attributions()

    # Determine if it's a TV show or movie by checking Sonarr first, then Radarr
    series = find_sonarr_series_by_title(title)
    if series:
        attr["tv"][series["title"]] = user
        save_attributions(attr)
        print(f"Assigned TV show '{series['title']}' to user '{user}'")
        return

    movie = find_radarr_movie_by_title(title)
    if movie:
        attr["movies"][movie["title"]] = user
        save_attributions(attr)
        print(f"Assigned movie '{movie['title']}' to user '{user}'")
        return

    print(f"'{title}' not found in Sonarr or Radarr.")
    resp = input("Add as (t)v or (m)ovie? [t/m]: ").strip().lower()
    if resp == "t":
        attr["tv"][title] = user
    elif resp == "m":
        attr["movies"][title] = user
    else:
        print("Cancelled.")
        return
    save_attributions(attr)
    print(f"Assigned '{title}' to user '{user}'")


def cli_unassign(args):
    if len(args) < 1:
        print("Usage: auto_cleanup.py unassign <title>")
        return

    title = args[0]
    attr = get_attributions()
    title_lower = title.lower()

    for category in ["tv", "movies"]:
        for stored_title in list(attr.get(category, {}).keys()):
            if stored_title.lower() == title_lower:
                del attr[category][stored_title]
                save_attributions(attr)
                print(f"Removed attribution for '{stored_title}'")
                return

    print(f"No attribution found for '{title}'")


def cli_list():
    attr = get_attributions()
    tv = attr.get("tv", {})
    movies = attr.get("movies", {})

    if not tv and not movies:
        print("No attributions configured.")
        return

    if tv:
        print("TV Shows:")
        for title, user in sorted(tv.items()):
            print(f"  {title} -> {user}")
    if movies:
        print("Movies:")
        for title, user in sorted(movies.items()):
            print(f"  {title} -> {user}")


def cli_pending():
    pending = load_json(PENDING_FILE, [])
    if not pending:
        print("No pending cleanups.")
        return

    print(f"{len(pending)} pending cleanup(s):")
    for entry in pending:
        if entry["type"] == "episode":
            print(f"  [{entry['type']}] {entry['title']} S{entry['season']:02d}E{entry['episode']:02d}"
                  f" (user: {entry['user']}, cleanup at: {entry['cleanup_at']})")
        else:
            print(f"  [{entry['type']}] {entry['title']}"
                  f" (user: {entry['user']}, cleanup at: {entry['cleanup_at']})")


def cli_serve():
    print(f"Starting auto-cleanup webhook server on port {CLEANUP['webhook_port']}")
    print(f"Grace period: {GRACE_HOURS} hours")
    print(f"Webhook URL: http://localhost:{CLEANUP['webhook_port']}/webhook")
    print(f"\nConfigure this URL in Plex Settings > Webhooks")
    print("Processing pending cleanups every 15 minutes\n")

    # Start background cleanup scheduler
    t = threading.Thread(target=cleanup_scheduler, daemon=True)
    t.start()

    # Process any existing pending items on startup
    process_pending()

    app.run(host="0.0.0.0", port=CLEANUP["webhook_port"], debug=False)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: auto_cleanup.py <command> [args]")
        print("Commands: serve, assign, unassign, list, pending, process")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "serve":
        cli_serve()
    elif command == "assign":
        cli_assign(sys.argv[2:])
    elif command == "unassign":
        cli_unassign(sys.argv[2:])
    elif command == "list":
        cli_list()
    elif command == "pending":
        cli_pending()
    elif command == "process":
        process_pending()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
