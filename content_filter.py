"""Content Filter - Hide shows/movies per Plex user using labels."""
import json
import os
import requests
from config import PLEX

HIDDEN_FILE = os.path.join(os.path.dirname(__file__), "hidden_content.json")


def _load_hidden():
    if os.path.exists(HIDDEN_FILE):
        with open(HIDDEN_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_hidden(data):
    with open(HIDDEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _plex_headers():
    return {"Accept": "application/json", "X-Plex-Token": PLEX["token"]}


def _get_item_labels(rating_key):
    """Get existing labels for a Plex item."""
    r = requests.get(
        f"{PLEX['url']}/library/metadata/{rating_key}",
        params={"X-Plex-Token": PLEX["token"]},
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    meta = r.json()["MediaContainer"]["Metadata"][0]
    return [lbl["tag"] for lbl in meta.get("Label", [])]


def _add_label(rating_key, section_id, label, media_type=2):
    """Add a label to a Plex item."""
    params = {
        "X-Plex-Token": PLEX["token"],
        "type": media_type,
        "id": rating_key,
        "label[0].tag.tag": label,
        "label.locked": 1,
    }
    r = requests.put(
        f"{PLEX['url']}/library/sections/{section_id}/all",
        params=params,
    )
    r.raise_for_status()
    return True


def _remove_label(rating_key, section_id, label, media_type=2):
    """Remove a label from a Plex item. Matches case-insensitively."""
    # Plex may capitalize labels, so find the actual stored label name
    current_labels = _get_item_labels(rating_key)
    actual_label = label
    for l in current_labels:
        if l.lower() == label.lower():
            actual_label = l
            break

    params = {
        "X-Plex-Token": PLEX["token"],
        "type": media_type,
        "id": rating_key,
        "label[].tag.tag-": actual_label,
    }
    r = requests.put(
        f"{PLEX['url']}/library/sections/{section_id}/all",
        params=params,
    )
    r.raise_for_status()
    return True


def _hide_label(username):
    return f"hide-{username.lower()}"


def get_all_shows():
    """Get all TV shows with their current hide labels."""
    shows = []
    size = 100
    start = 0
    while True:
        r = requests.get(
            f"{PLEX['url']}/library/sections/{PLEX['tv_section']}/all",
            params={
                "X-Plex-Token": PLEX["token"],
                "type": 2,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": size,
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()["MediaContainer"]
        batch = data.get("Metadata", [])
        for s in batch:
            labels = [lbl["tag"] for lbl in s.get("Label", [])]
            thumb = s.get("thumb", "")
            if thumb:
                thumb = f"{PLEX['url']}{thumb}?X-Plex-Token={PLEX['token']}"
            shows.append({
                "ratingKey": s["ratingKey"],
                "title": s.get("title", ""),
                "year": s.get("year", ""),
                "thumb": thumb,
                "labels": labels,
                "contentRating": s.get("contentRating", ""),
            })
        if start + size >= data.get("totalSize", 0):
            break
        start += size
    shows.sort(key=lambda x: x["title"].lower())
    return shows


def get_all_movies():
    """Get all movies with their current hide labels."""
    movies = []
    size = 100
    start = 0
    while True:
        r = requests.get(
            f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/all",
            params={
                "X-Plex-Token": PLEX["token"],
                "type": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": size,
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()["MediaContainer"]
        batch = data.get("Metadata", [])
        for m in batch:
            labels = [lbl["tag"] for lbl in m.get("Label", [])]
            thumb = m.get("thumb", "")
            if thumb:
                thumb = f"{PLEX['url']}{thumb}?X-Plex-Token={PLEX['token']}"
            movies.append({
                "ratingKey": m["ratingKey"],
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "thumb": thumb,
                "labels": labels,
                "contentRating": m.get("contentRating", ""),
            })
        if start + size >= data.get("totalSize", 0):
            break
        start += size
    movies.sort(key=lambda x: x["title"].lower())
    return movies


def get_plex_users():
    """Get all Plex accounts (excluding the anonymous one)."""
    r = requests.get(
        f"{PLEX['url']}/accounts",
        params={"X-Plex-Token": PLEX["token"]},
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    accounts = r.json()["MediaContainer"].get("Account", [])
    return [
        {"id": a["id"], "name": a["name"]}
        for a in accounts
        if a.get("name")  # skip anonymous account
    ]


def get_hidden_for_user(username):
    """Get list of ratingKeys hidden for a user."""
    hidden = _load_hidden()
    return hidden.get(username.lower(), [])


def toggle_hide(username, rating_key, section_id, media_type=2):
    """Toggle hiding a show/movie for a user. Returns new hidden state."""
    username = username.lower()
    rating_key = str(rating_key)
    hidden = _load_hidden()

    if username not in hidden:
        hidden[username] = []

    is_hidden = rating_key in hidden[username]
    label = _hide_label(username)

    if is_hidden:
        # Unhide: remove label and tracking
        hidden[username].remove(rating_key)
        _remove_label(rating_key, section_id, label, media_type)
    else:
        # Hide: add label and tracking
        hidden[username].append(rating_key)
        _add_label(rating_key, section_id, label, media_type)

    _save_hidden(hidden)
    return not is_hidden


def bulk_update(username, rating_keys_to_hide, section_id, media_type=2):
    """Set the exact list of hidden items for a user in a section."""
    username = username.lower()
    label = _hide_label(username)
    hidden = _load_hidden()
    currently_hidden = set(hidden.get(username, []))
    new_hidden = set(str(k) for k in rating_keys_to_hide)

    to_unhide = currently_hidden - new_hidden
    to_hide = new_hidden - currently_hidden

    for rk in to_unhide:
        try:
            _remove_label(rk, section_id, label, media_type)
        except Exception:
            pass

    for rk in to_hide:
        try:
            _add_label(rk, section_id, label, media_type)
        except Exception:
            pass

    hidden[username] = list(new_hidden)
    _save_hidden(hidden)
    return {"hidden": len(new_hidden), "added": len(to_hide), "removed": len(to_unhide)}
