import json, os
from datetime import datetime

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")

def _load():
    if not os.path.exists(WATCHLIST_FILE):
        return {}
    with open(WATCHLIST_FILE) as f:
        return json.load(f)

def _save(data):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def get_watchlist(username):
    return _load().get(username, [])

def add_to_watchlist(username, item):
    """item: {title, type, poster, tmdbId, tvdbId, year}"""
    data = _load()
    if username not in data:
        data[username] = []
    # avoid duplicates by title
    if not any(i["title"].lower() == item["title"].lower() for i in data[username]):
        item["added"] = datetime.now().isoformat()
        item["notified"] = False
        data[username].append(item)
        _save(data)
        return True
    return False

def remove_from_watchlist(username, title):
    data = _load()
    if username in data:
        data[username] = [i for i in data[username] if i["title"].lower() != title.lower()]
        _save(data)

def check_and_notify_watchlist():
    """Check if any watchlist items have been downloaded. Returns list of (username, item) to notify."""
    from api import radarr_get, sonarr_get
    data = _load()
    notifications = []
    changed = False
    try:
        radarr_movies = {m["title"].lower() for m in radarr_get("/movie") if m.get("hasFile")}
    except Exception:
        radarr_movies = set()
    try:
        sonarr_series = {s["title"].lower() for s in sonarr_get("/series") if s.get("statistics", {}).get("episodeFileCount", 0) > 0}
    except Exception:
        sonarr_series = set()

    for username, items in data.items():
        for item in items:
            if item.get("notified"):
                continue
            title_lower = item["title"].lower()
            downloaded = False
            if item.get("type") == "movie" and title_lower in radarr_movies:
                downloaded = True
            elif item.get("type") in ("series", "show", "tv") and title_lower in sonarr_series:
                downloaded = True
            if downloaded:
                item["notified"] = True
                item["notified_at"] = datetime.now().isoformat()
                notifications.append((username, item))
                changed = True
    if changed:
        _save(data)
    return notifications
