# Plex Media Stack - API Helpers
import requests
from config import PLEX, SONARR, RADARR, TMDB, NOTIFY


def plex_get(endpoint, params=None):
    """GET request to Plex API."""
    p = params or {}
    p["X-Plex-Token"] = PLEX["token"]
    r = requests.get(f"{PLEX['url']}{endpoint}", params=p, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def sonarr_get(endpoint, params=None):
    """GET request to Sonarr API v3."""
    r = requests.get(
        f"{SONARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": SONARR["api_key"]},
        params=params,
    )
    r.raise_for_status()
    return r.json()


def sonarr_post(endpoint, json_data):
    """POST request to Sonarr API v3."""
    r = requests.post(
        f"{SONARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": SONARR["api_key"]},
        json=json_data,
    )
    r.raise_for_status()
    return r.json()


def radarr_get(endpoint, params=None):
    """GET request to Radarr API v3."""
    r = requests.get(
        f"{RADARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": RADARR["api_key"]},
        params=params,
    )
    r.raise_for_status()
    return r.json()


def radarr_post(endpoint, json_data):
    """POST request to Radarr API v3."""
    r = requests.post(
        f"{RADARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": RADARR["api_key"]},
        json=json_data,
    )
    r.raise_for_status()
    return r.json()


# --- Data fetchers ---

def get_plex_movies():
    """Get all movies from Plex with metadata."""
    movies = []
    size = 100
    start = 0
    while True:
        data = plex_get(
            f"/library/sections/{PLEX['movie_section']}/all",
            {"X-Plex-Container-Start": start, "X-Plex-Container-Size": size},
        )
        container = data["MediaContainer"]
        batch = container.get("Metadata", [])
        movies.extend(batch)
        if start + size >= container.get("totalSize", 0):
            break
        start += size
    return movies


def get_plex_shows():
    """Get all TV shows from Plex with metadata."""
    shows = []
    size = 100
    start = 0
    while True:
        data = plex_get(
            f"/library/sections/{PLEX['tv_section']}/all",
            {"X-Plex-Container-Start": start, "X-Plex-Container-Size": size},
        )
        container = data["MediaContainer"]
        batch = container.get("Metadata", [])
        shows.extend(batch)
        if start + size >= container.get("totalSize", 0):
            break
        start += size
    return shows


def get_plex_movie_details(rating_key):
    """Get detailed info for a single Plex movie (includes media/stream info)."""
    data = plex_get(f"/library/metadata/{rating_key}")
    return data["MediaContainer"]["Metadata"][0]


def get_plex_show_seasons(rating_key):
    """Get seasons for a Plex show."""
    data = plex_get(f"/library/metadata/{rating_key}/children")
    return data["MediaContainer"].get("Metadata", [])


def get_plex_season_episodes(rating_key):
    """Get episodes for a Plex season."""
    data = plex_get(f"/library/metadata/{rating_key}/children")
    return data["MediaContainer"].get("Metadata", [])


def sonarr_put(endpoint, json_data):
    """PUT request to Sonarr API v3."""
    r = requests.put(
        f"{SONARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": SONARR["api_key"]},
        json=json_data,
    )
    r.raise_for_status()
    return r.json()


def sonarr_delete(endpoint):
    """DELETE request to Sonarr API v3."""
    r = requests.delete(
        f"{SONARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": SONARR["api_key"]},
    )
    r.raise_for_status()
    return r


def radarr_put(endpoint, json_data):
    """PUT request to Radarr API v3."""
    r = requests.put(
        f"{RADARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": RADARR["api_key"]},
        json=json_data,
    )
    r.raise_for_status()
    return r.json()


def radarr_delete(endpoint):
    """DELETE request to Radarr API v3."""
    r = requests.delete(
        f"{RADARR['url']}/api/v3{endpoint}",
        headers={"X-Api-Key": RADARR["api_key"]},
    )
    r.raise_for_status()
    return r


def get_radarr_movies():
    """Get all movies from Radarr."""
    return radarr_get("/movie")


def get_sonarr_series():
    """Get all series from Sonarr."""
    return sonarr_get("/series")


# --- TMDb helpers ---

def tmdb_get(endpoint, params=None):
    """GET request to TMDb API."""
    if not TMDB["api_key"]:
        return {}
    p = params or {}
    p["api_key"] = TMDB["api_key"]
    r = requests.get(f"{TMDB['base_url']}{endpoint}", params=p, timeout=10)
    r.raise_for_status()
    return r.json()


def tmdb_search_movie(query, year=None):
    """Search TMDb for movies."""
    params = {"query": query}
    if year:
        params["year"] = year
    return tmdb_get("/search/movie", params)


def tmdb_search_tv(query):
    """Search TMDb for TV shows."""
    return tmdb_get("/search/tv", {"query": query})


def tmdb_movie_details(tmdb_id):
    """Get TMDb movie details including credits and recommendations."""
    return tmdb_get(f"/movie/{tmdb_id}", {"append_to_response": "credits,recommendations,similar"})


def tmdb_tv_details(tmdb_id):
    """Get TMDb TV details including recommendations."""
    return tmdb_get(f"/tv/{tmdb_id}", {"append_to_response": "recommendations,similar"})


def tmdb_collection(collection_id):
    """Get TMDb collection details (for franchise posters)."""
    return tmdb_get(f"/collection/{collection_id}")


def tmdb_trending(media_type="all", time_window="week"):
    """Get trending content from TMDb."""
    return tmdb_get(f"/trending/{media_type}/{time_window}")


def tmdb_discover_movies(params=None):
    """Discover movies on TMDb with filters."""
    return tmdb_get("/discover/movie", params)


def tmdb_image_url(path, size="w500"):
    """Build full TMDb image URL."""
    if not path:
        return ""
    return f"{TMDB['image_base']}/{size}{path}"


# --- Plex extended helpers ---

def plex_get_history(account_id=None, limit=100):
    """Get Plex watch history."""
    params = {"sort": "viewedAt:desc", "X-Plex-Container-Size": limit}
    if account_id:
        params["accountID"] = account_id
    try:
        data = plex_get("/status/sessions/history/all", params)
        return data.get("MediaContainer", {}).get("Metadata", [])
    except Exception:
        return []


def plex_get_sessions():
    """Get current active Plex sessions."""
    try:
        data = plex_get("/status/sessions")
        return data.get("MediaContainer", {}).get("Metadata", [])
    except Exception:
        return []


def plex_get_accounts():
    """Get Plex accounts/users."""
    try:
        data = plex_get("/accounts")
        return data.get("MediaContainer", {}).get("Account", [])
    except Exception:
        return []


def plex_put(endpoint, params=None):
    """PUT request to Plex API."""
    p = params or {}
    p["X-Plex-Token"] = PLEX["token"]
    r = requests.put(f"{PLEX['url']}{endpoint}", params=p)
    r.raise_for_status()
    return r


def plex_post(endpoint, params=None, data=None):
    """POST request to Plex API."""
    p = params or {}
    p["X-Plex-Token"] = PLEX["token"]
    r = requests.post(f"{PLEX['url']}{endpoint}", params=p, data=data)
    r.raise_for_status()
    return r


# --- Notification helpers ---

def send_discord(message, title=None):
    """Send a Discord webhook notification."""
    if not NOTIFY["enabled"] or not NOTIFY["discord_webhook"]:
        return
    embed = {"description": message, "color": 15105570}
    if title:
        embed["title"] = title
    try:
        requests.post(NOTIFY["discord_webhook"], json={"embeds": [embed]}, timeout=5)
    except Exception:
        pass
