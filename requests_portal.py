# Plex Request Portal - Browse, request, and vote on content
import json
import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from api import (
    radarr_get, radarr_post, sonarr_get, sonarr_post,
    get_plex_movies, get_plex_shows,
    tmdb_image_url,
)
from config import PLEX, REQUESTS

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

DB_FILE = REQUESTS["db_file"]


def _load_db():
    """Load the requests database from disk."""
    if not os.path.exists(DB_FILE):
        return {"requests": [], "votes": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_db(db):
    """Persist the requests database to disk."""
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, default=str)


def _next_id(db):
    """Return the next sequential request id."""
    if not db["requests"]:
        return 1
    return max(r["id"] for r in db["requests"]) + 1


# ---------------------------------------------------------------------------
# Inline HTML templates
# ---------------------------------------------------------------------------

_CSS = """
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background: #141414;
    color: #eee;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    min-height: 100vh;
  }
  a { color: #e5a00d; text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* nav */
  nav {
    background: #1a1a1a;
    padding: 14px 24px;
    display: flex; align-items: center; gap: 28px;
    border-bottom: 2px solid #e5a00d;
    flex-wrap: wrap;
  }
  nav .brand { font-size: 1.3rem; font-weight: 700; color: #e5a00d; }
  nav a { color: #ccc; font-size: .95rem; }
  nav a:hover, nav a.active { color: #e5a00d; }

  /* layout */
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  h1 { color: #e5a00d; margin-bottom: 18px; }
  h2 { color: #e5a00d; margin-bottom: 12px; }

  /* search bar */
  .search-bar {
    display: flex; gap: 10px; margin-bottom: 22px; flex-wrap: wrap;
  }
  .search-bar input, .search-bar select {
    padding: 10px 14px; border-radius: 6px; border: 1px solid #333;
    background: #1a1a1a; color: #eee; font-size: .95rem;
  }
  .search-bar input { flex: 1; min-width: 200px; }
  .search-bar button, .btn {
    padding: 10px 20px; border-radius: 6px; border: none;
    background: #e5a00d; color: #141414; font-weight: 600;
    cursor: pointer; font-size: .95rem;
  }
  .search-bar button:hover, .btn:hover { background: #f5b82e; }
  .btn-danger { background: #c0392b; color: #fff; }
  .btn-danger:hover { background: #e74c3c; }
  .btn-sm { padding: 6px 14px; font-size: .85rem; }

  /* card grid */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 18px;
  }
  .card {
    background: #1a1a1a; border-radius: 8px; overflow: hidden;
    transition: transform .15s, box-shadow .15s;
  }
  .card:hover { transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,.5); }
  .card img {
    width: 100%; aspect-ratio: 2/3; object-fit: cover;
    display: block; background: #222;
  }
  .card .info { padding: 10px; }
  .card .title { font-size: .85rem; font-weight: 600; margin-bottom: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .year { font-size: .75rem; color: #999; }
  .card .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: .7rem; font-weight: 600; margin-top: 4px;
  }
  .badge-movie { background: #e5a00d33; color: #e5a00d; }
  .badge-show  { background: #3498db33; color: #3498db; }

  /* request table */
  table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid #222; }
  th { color: #e5a00d; font-size: .85rem; text-transform: uppercase; }
  td { font-size: .9rem; }
  .status-pending  { color: #f39c12; }
  .status-approved { color: #27ae60; }
  .status-denied   { color: #c0392b; }

  /* vote cards */
  .vote-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 18px;
  }
  .vote-card {
    background: #1a1a1a; border-radius: 8px; overflow: hidden;
    display: flex; flex-direction: column; align-items: center; padding: 14px;
    text-align: center;
  }
  .vote-card img { width: 120px; aspect-ratio: 2/3; object-fit: cover; border-radius: 6px; }
  .vote-card .title { margin: 10px 0 4px; font-weight: 600; }
  .vote-card .count { color: #e5a00d; font-size: 1.1rem; font-weight: 700; margin: 6px 0; }

  /* form */
  .form-group { margin-bottom: 14px; }
  .form-group label { display: block; margin-bottom: 4px; font-size: .85rem; color: #aaa; }
  .form-group input {
    width: 100%; padding: 10px 14px; border-radius: 6px; border: 1px solid #333;
    background: #1a1a1a; color: #eee; font-size: .95rem;
  }

  /* toast */
  .toast {
    position: fixed; bottom: 24px; right: 24px; background: #27ae60;
    color: #fff; padding: 14px 22px; border-radius: 8px; font-weight: 600;
    display: none; z-index: 999; box-shadow: 0 4px 16px rgba(0,0,0,.4);
  }
  .toast.error { background: #c0392b; }

  .empty { text-align: center; padding: 60px 20px; color: #666; font-size: 1.1rem; }
  .loader { text-align: center; padding: 40px; color: #666; }
</style>
"""

_NAV = """
<nav>
  <span class="brand">Plex Requests</span>
  <a href="/">Browse Library</a>
  <a href="/request">Request</a>
  <a href="/vote">Movie Night Vote</a>
  <a href="/admin">Admin</a>
</nav>
"""

# ---- Browse Library ----
BROWSE_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browse Library - Plex Requests</title>
""" + _CSS + """
</head><body>
""" + _NAV + """
<div class="container">
  <h1>Library</h1>
  <div class="search-bar">
    <input id="q" type="text" placeholder="Search movies &amp; shows..." oninput="filterCards()">
    <select id="typeFilter" onchange="filterCards()">
      <option value="all">All</option>
      <option value="movie">Movies</option>
      <option value="show">Shows</option>
    </select>
  </div>
  <div class="grid" id="grid"></div>
  <p class="loader" id="loader">Loading library...</p>
</div>
<script>
let allItems = [];
async function load() {
  try {
    const res = await fetch('/api/library');
    allItems = await res.json();
    renderCards(allItems);
  } catch(e) {
    document.getElementById('loader').textContent = 'Failed to load library.';
  }
}
function renderCards(items) {
  const grid = document.getElementById('grid');
  const loader = document.getElementById('loader');
  loader.style.display = 'none';
  if (!items.length) { grid.innerHTML = '<p class="empty">No items found.</p>'; return; }
  grid.innerHTML = items.map(i => `
    <div class="card" data-title="${i.title.toLowerCase()}" data-type="${i.type}">
      <img src="${i.poster || ''}" alt="" loading="lazy"
           onerror="this.style.background='#333';this.alt='No poster'">
      <div class="info">
        <div class="title" title="${i.title}">${i.title}</div>
        <div class="year">${i.year || ''}</div>
        <span class="badge ${i.type === 'movie' ? 'badge-movie' : 'badge-show'}">${i.type}</span>
      </div>
    </div>`).join('');
}
function filterCards() {
  const q = document.getElementById('q').value.toLowerCase();
  const t = document.getElementById('typeFilter').value;
  const filtered = allItems.filter(i =>
    (t === 'all' || i.type === t) && i.title.toLowerCase().includes(q)
  );
  renderCards(filtered);
}
load();
</script>
</body></html>"""

# ---- Request Page ----
REQUEST_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Request Content - Plex Requests</title>
""" + _CSS + """
</head><body>
""" + _NAV + """
<div class="container">
  <h1>Request Content</h1>
  <div class="form-group">
    <label>Your Name</label>
    <input id="requester" type="text" placeholder="Enter your name">
  </div>
  <div class="search-bar">
    <input id="q" type="text" placeholder="Search for a movie or show...">
    <select id="mediaType">
      <option value="movie">Movie</option>
      <option value="show">TV Show</option>
    </select>
    <button onclick="doSearch()">Search</button>
  </div>
  <div class="grid" id="results"></div>
  <p id="status" class="empty" style="display:none"></p>
</div>
<div class="toast" id="toast"></div>
<script>
function showToast(msg, error) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (error ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}
async function doSearch() {
  const q = document.getElementById('q').value.trim();
  const type = document.getElementById('mediaType').value;
  if (!q) return;
  const st = document.getElementById('status');
  st.style.display = 'block'; st.textContent = 'Searching...';
  document.getElementById('results').innerHTML = '';
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&type=${type}`);
    const items = await res.json();
    st.style.display = 'none';
    if (!items.length) { st.style.display = 'block'; st.textContent = 'No results found.'; return; }
    document.getElementById('results').innerHTML = items.map(i => `
      <div class="card" style="cursor:pointer" onclick="submitRequest(${JSON.stringify(i.title).replace(/"/g,'&quot;')}, '${type}', ${i.id || 0}, '${i.poster || ''}')">
        <img src="${i.poster || ''}" alt="" loading="lazy"
             onerror="this.style.background='#333';this.alt='No poster'">
        <div class="info">
          <div class="title" title="${i.title}">${i.title}</div>
          <div class="year">${i.year || ''}</div>
          <span class="badge ${type === 'movie' ? 'badge-movie' : 'badge-show'}">${type}</span>
        </div>
      </div>`).join('');
  } catch(e) {
    st.style.display = 'block'; st.textContent = 'Search failed.';
  }
}
async function submitRequest(title, type, extId, poster) {
  const requester = document.getElementById('requester').value.trim();
  if (!requester) { showToast('Please enter your name first.', true); return; }
  try {
    const res = await fetch('/api/request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title, type, requester, external_id: extId, poster})
    });
    const data = await res.json();
    if (res.ok) showToast('Request submitted for ' + title);
    else showToast(data.error || 'Failed', true);
  } catch(e) { showToast('Network error', true); }
}
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
</script>
</body></html>"""

# ---- Vote Page ----
VOTE_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movie Night Vote - Plex Requests</title>
""" + _CSS + """
</head><body>
""" + _NAV + """
<div class="container">
  <h1>Movie Night Vote</h1>
  <p style="color:#aaa;margin-bottom:18px;">Pick your favourite for the next movie night! You can vote for multiple titles.</p>
  <div class="form-group">
    <label>Your Name</label>
    <input id="voter" type="text" placeholder="Enter your name">
  </div>
  <div class="vote-grid" id="voteGrid"></div>
  <p class="loader" id="loader">Loading nominees...</p>
</div>
<div class="toast" id="toast"></div>
<script>
function showToast(msg, error) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (error ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}
async function load() {
  try {
    const res = await fetch('/api/votes');
    const data = await res.json();
    const loader = document.getElementById('loader');
    const grid = document.getElementById('voteGrid');
    loader.style.display = 'none';
    const nominees = data.nominees || [];
    const tallies = data.tallies || {};
    if (!nominees.length) { grid.innerHTML = '<p class="empty">No nominees yet. Approved requests appear here automatically.</p>'; return; }
    grid.innerHTML = nominees.map(n => `
      <div class="vote-card">
        <img src="${n.poster || ''}" alt="" onerror="this.style.background='#333'">
        <div class="title">${n.title}</div>
        <div class="count">${tallies[String(n.id)] || 0} votes</div>
        <button class="btn btn-sm" onclick="vote(${n.id})">Vote</button>
      </div>`).join('');
  } catch(e) {
    document.getElementById('loader').textContent = 'Failed to load.';
  }
}
async function vote(id) {
  const voter = document.getElementById('voter').value.trim();
  if (!voter) { showToast('Please enter your name first.', true); return; }
  try {
    const res = await fetch('/api/vote', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({nominee_id: id, voter})
    });
    const data = await res.json();
    if (res.ok) { showToast('Vote recorded!'); load(); }
    else showToast(data.error || 'Failed', true);
  } catch(e) { showToast('Network error', true); }
}
load();
</script>
</body></html>"""

# ---- Admin Page ----
ADMIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin - Plex Requests</title>
""" + _CSS + """
</head><body>
""" + _NAV + """
<div class="container">
  <h1>Admin &ndash; Manage Requests</h1>
  <table>
    <thead><tr>
      <th>Title</th><th>Type</th><th>Requester</th><th>Date</th><th>Status</th><th>Actions</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <p class="loader" id="loader">Loading...</p>

  <h2 style="margin-top:40px;">Manage Vote Nominees</h2>
  <p style="color:#aaa;margin-bottom:10px;">Approved requests are auto-added as nominees. You can also remove nominees below.</p>
  <div id="nomineeList"></div>
</div>
<div class="toast" id="toast"></div>
<script>
function showToast(msg, error) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (error ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}
async function load() {
  try {
    const [reqRes, voteRes] = await Promise.all([fetch('/api/requests'), fetch('/api/votes')]);
    const requests = await reqRes.json();
    const votes = await voteRes.json();
    document.getElementById('loader').style.display = 'none';
    const tbody = document.getElementById('tbody');
    if (!requests.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty">No requests yet.</td></tr>'; }
    else {
      tbody.innerHTML = requests.map(r => `<tr>
        <td>${r.title}</td>
        <td>${r.type}</td>
        <td>${r.requester}</td>
        <td>${new Date(r.timestamp).toLocaleDateString()}</td>
        <td class="status-${r.status}">${r.status}</td>
        <td>${r.status === 'pending' ? `
          <button class="btn btn-sm" onclick="action(${r.id},'approve')">Approve</button>
          <button class="btn btn-sm btn-danger" onclick="action(${r.id},'deny')">Deny</button>
        ` : '&mdash;'}</td>
      </tr>`).join('');
    }
    // nominees
    const nl = document.getElementById('nomineeList');
    const nominees = votes.nominees || [];
    if (!nominees.length) { nl.innerHTML = '<p class="empty">No nominees.</p>'; }
    else {
      nl.innerHTML = '<table><thead><tr><th>Title</th><th>Votes</th><th></th></tr></thead><tbody>' +
        nominees.map(n => `<tr>
          <td>${n.title}</td>
          <td>${(votes.tallies || {})[String(n.id)] || 0}</td>
          <td><button class="btn btn-sm btn-danger" onclick="removeNominee(${n.id})">Remove</button></td>
        </tr>`).join('') + '</tbody></table>';
    }
  } catch(e) {
    document.getElementById('loader').textContent = 'Failed to load.';
  }
}
async function action(id, act) {
  try {
    const res = await fetch('/api/request/' + id + '/' + act, {method:'POST'});
    const data = await res.json();
    if (res.ok) { showToast(data.message || 'Done'); load(); }
    else showToast(data.error || 'Failed', true);
  } catch(e) { showToast('Network error', true); }
}
async function removeNominee(id) {
  try {
    const res = await fetch('/api/nominee/' + id, {method:'DELETE'});
    if (res.ok) { showToast('Removed'); load(); }
    else showToast('Failed', true);
  } catch(e) { showToast('Network error', true); }
}
load();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Flask application factory
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.route("/")
    def browse_page():
        return render_template_string(BROWSE_TEMPLATE)

    @app.route("/request")
    def request_page():
        return render_template_string(REQUEST_TEMPLATE)

    @app.route("/vote")
    def vote_page():
        return render_template_string(VOTE_TEMPLATE)

    @app.route("/admin")
    def admin_page():
        return render_template_string(ADMIN_TEMPLATE)

    # ------------------------------------------------------------------
    # API - Library
    # ------------------------------------------------------------------

    @app.route("/api/library")
    def api_library():
        """Return combined Plex movies + shows for the browse grid."""
        items = []
        try:
            for m in get_plex_movies():
                poster = ""
                thumb = m.get("thumb", "")
                if thumb:
                    poster = f"{PLEX['url']}{thumb}?X-Plex-Token={PLEX['token']}"
                items.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", ""),
                    "type": "movie",
                    "poster": poster,
                })
        except Exception:
            pass
        try:
            for s in get_plex_shows():
                poster = ""
                thumb = s.get("thumb", "")
                if thumb:
                    poster = f"{PLEX['url']}{thumb}?X-Plex-Token={PLEX['token']}"
                items.append({
                    "title": s.get("title", ""),
                    "year": s.get("year", ""),
                    "type": "show",
                    "poster": poster,
                })
        except Exception:
            pass
        items.sort(key=lambda x: x["title"].lower())
        return jsonify(items)

    # ------------------------------------------------------------------
    # API - Search (Radarr / Sonarr lookup)
    # ------------------------------------------------------------------

    @app.route("/api/search")
    def api_search():
        """Search Radarr or Sonarr for content to request."""
        q = request.args.get("q", "").strip()
        media_type = request.args.get("type", "movie")
        if not q:
            return jsonify([])

        results = []
        try:
            if media_type == "movie":
                raw = radarr_get("/movie/lookup", {"term": q})
                for m in raw[:20]:
                    poster = ""
                    for img in m.get("images", []):
                        if img.get("coverType") == "poster":
                            remote = img.get("remoteUrl") or img.get("url", "")
                            if remote:
                                poster = remote
                            break
                    results.append({
                        "id": m.get("tmdbId", 0),
                        "title": m.get("title", ""),
                        "year": m.get("year", ""),
                        "poster": poster,
                    })
            else:
                raw = sonarr_get("/series/lookup", {"term": q})
                for s in raw[:20]:
                    poster = ""
                    for img in s.get("images", []):
                        if img.get("coverType") == "poster":
                            remote = img.get("remoteUrl") or img.get("url", "")
                            if remote:
                                poster = remote
                            break
                    results.append({
                        "id": s.get("tvdbId", 0),
                        "title": s.get("title", ""),
                        "year": s.get("year", ""),
                        "poster": poster,
                    })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify(results)

    # ------------------------------------------------------------------
    # API - Requests CRUD
    # ------------------------------------------------------------------

    @app.route("/api/requests")
    def api_requests_list():
        """Return all requests."""
        db = _load_db()
        return jsonify(db.get("requests", []))

    @app.route("/api/request", methods=["POST"])
    def api_request_create():
        """Submit a new content request."""
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        media_type = data.get("type", "movie")
        requester = data.get("requester", "").strip()
        external_id = data.get("external_id", 0)
        poster = data.get("poster", "")

        if not title or not requester:
            return jsonify({"error": "title and requester are required"}), 400

        db = _load_db()

        # Prevent duplicate pending requests for the same title
        for r in db["requests"]:
            if r["title"].lower() == title.lower() and r["status"] == "pending":
                return jsonify({"error": f"'{title}' has already been requested"}), 409

        entry = {
            "id": _next_id(db),
            "title": title,
            "type": media_type,
            "requester": requester,
            "external_id": external_id,
            "poster": poster,
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
        }
        db["requests"].append(entry)
        _save_db(db)
        return jsonify(entry), 201

    @app.route("/api/request/<int:req_id>/approve", methods=["POST"])
    def api_request_approve(req_id):
        """Approve a request and add to Radarr/Sonarr."""
        db = _load_db()
        entry = next((r for r in db["requests"] if r["id"] == req_id), None)
        if not entry:
            return jsonify({"error": "request not found"}), 404
        if entry["status"] != "pending":
            return jsonify({"error": "request already processed"}), 400

        # Attempt to add to Radarr or Sonarr
        add_error = None
        try:
            if entry["type"] == "movie":
                _add_to_radarr(entry)
            else:
                _add_to_sonarr(entry)
        except Exception as exc:
            add_error = str(exc)

        entry["status"] = "approved"
        entry["approved_at"] = datetime.now().isoformat()
        if add_error:
            entry["add_note"] = f"Auto-add warning: {add_error}"

        # Auto-add as vote nominee
        if "votes" not in db:
            db["votes"] = {}
        nominees = db["votes"].setdefault("nominees", [])
        if not any(n["id"] == entry["id"] for n in nominees):
            nominees.append({
                "id": entry["id"],
                "title": entry["title"],
                "type": entry["type"],
                "poster": entry.get("poster", ""),
            })

        _save_db(db)
        msg = "Approved and added to download queue."
        if add_error:
            msg += f" (Note: {add_error})"
        return jsonify({"message": msg, "request": entry})

    @app.route("/api/request/<int:req_id>/deny", methods=["POST"])
    def api_request_deny(req_id):
        """Deny a request."""
        db = _load_db()
        entry = next((r for r in db["requests"] if r["id"] == req_id), None)
        if not entry:
            return jsonify({"error": "request not found"}), 404
        if entry["status"] != "pending":
            return jsonify({"error": "request already processed"}), 400

        entry["status"] = "denied"
        entry["denied_at"] = datetime.now().isoformat()
        _save_db(db)
        return jsonify({"message": f"Denied request for '{entry['title']}'.", "request": entry})

    # ------------------------------------------------------------------
    # API - Voting
    # ------------------------------------------------------------------

    @app.route("/api/votes")
    def api_votes():
        """Return vote nominees and tallies."""
        db = _load_db()
        votes = db.get("votes", {})
        nominees = votes.get("nominees", [])
        raw_votes = votes.get("raw", [])

        # Tally votes per nominee id
        tallies = {}
        for v in raw_votes:
            nid = str(v.get("nominee_id", ""))
            tallies[nid] = tallies.get(nid, 0) + 1

        return jsonify({"nominees": nominees, "tallies": tallies})

    @app.route("/api/vote", methods=["POST"])
    def api_vote_cast():
        """Cast a vote for a nominee."""
        data = request.get_json(force=True)
        nominee_id = data.get("nominee_id")
        voter = data.get("voter", "").strip()

        if not nominee_id or not voter:
            return jsonify({"error": "nominee_id and voter are required"}), 400

        db = _load_db()
        votes = db.setdefault("votes", {})
        nominees = votes.get("nominees", [])
        if not any(n["id"] == nominee_id for n in nominees):
            return jsonify({"error": "nominee not found"}), 404

        raw = votes.setdefault("raw", [])

        # Prevent same person voting twice for the same nominee
        if any(v["nominee_id"] == nominee_id and v["voter"].lower() == voter.lower() for v in raw):
            return jsonify({"error": "You already voted for this title."}), 409

        raw.append({
            "nominee_id": nominee_id,
            "voter": voter,
            "timestamp": datetime.now().isoformat(),
        })
        _save_db(db)
        return jsonify({"message": "Vote recorded!"})

    @app.route("/api/nominee/<int:nom_id>", methods=["DELETE"])
    def api_nominee_remove(nom_id):
        """Remove a nominee and its associated votes."""
        db = _load_db()
        votes = db.setdefault("votes", {})
        nominees = votes.get("nominees", [])
        votes["nominees"] = [n for n in nominees if n["id"] != nom_id]
        raw = votes.get("raw", [])
        votes["raw"] = [v for v in raw if v["nominee_id"] != nom_id]
        _save_db(db)
        return jsonify({"message": "Nominee removed."})

    return app


# ---------------------------------------------------------------------------
# Radarr / Sonarr add helpers
# ---------------------------------------------------------------------------

def _add_to_radarr(entry):
    """Add a movie to Radarr by TMDb ID."""
    tmdb_id = entry.get("external_id")
    if not tmdb_id:
        raise ValueError("No TMDb ID available for this movie")

    # Look up to get full data needed by Radarr
    results = radarr_get("/movie/lookup", {"term": f"tmdb:{tmdb_id}"})
    if not results:
        raise ValueError(f"Radarr could not find TMDb ID {tmdb_id}")

    movie = results[0]

    # Need root folder and quality profile
    root_folders = radarr_get("/rootfolder")
    if not root_folders:
        raise ValueError("No Radarr root folders configured")
    quality_profiles = radarr_get("/qualityprofile")
    if not quality_profiles:
        raise ValueError("No Radarr quality profiles configured")

    payload = {
        "title": movie["title"],
        "tmdbId": movie["tmdbId"],
        "year": movie.get("year", 0),
        "qualityProfileId": quality_profiles[0]["id"],
        "rootFolderPath": root_folders[0]["path"],
        "monitored": True,
        "addOptions": {"searchForMovie": True},
        "images": movie.get("images", []),
    }
    return radarr_post("/movie", payload)


def _add_to_sonarr(entry):
    """Add a show to Sonarr by TVDB ID."""
    tvdb_id = entry.get("external_id")
    if not tvdb_id:
        raise ValueError("No TVDB ID available for this show")

    results = sonarr_get("/series/lookup", {"term": f"tvdb:{tvdb_id}"})
    if not results:
        raise ValueError(f"Sonarr could not find TVDB ID {tvdb_id}")

    series = results[0]

    root_folders = sonarr_get("/rootfolder")
    if not root_folders:
        raise ValueError("No Sonarr root folders configured")
    quality_profiles = sonarr_get("/qualityprofile")
    if not quality_profiles:
        raise ValueError("No Sonarr quality profiles configured")

    payload = {
        "title": series["title"],
        "tvdbId": series["tvdbId"],
        "year": series.get("year", 0),
        "qualityProfileId": quality_profiles[0]["id"],
        "rootFolderPath": root_folders[0]["path"],
        "monitored": True,
        "seasonFolder": True,
        "addOptions": {"searchForMissingEpisodes": True},
        "images": series.get("images", []),
    }
    return sonarr_post("/series", payload)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    print(f"Request Portal running on http://localhost:{REQUESTS['port']}")
    app.run(host="0.0.0.0", port=REQUESTS["port"], debug=True)
