# Smart Recommendations Engine for Plex
# Analyzes watch history, cross-references TMDb, scores and ranks suggestions.

import time
import logging
from collections import Counter

from api import (
    plex_get, plex_get_history, get_plex_movies, get_plex_shows,
    tmdb_get, tmdb_movie_details, tmdb_tv_details, tmdb_trending,
    tmdb_discover_movies, tmdb_image_url, tmdb_search_movie, tmdb_search_tv,
    radarr_get, sonarr_get,
)
from config import PLEX, TMDB

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache with TTL
# ---------------------------------------------------------------------------

_cache = {}
_CACHE_TTL = 900  # 15 minutes


def _cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < _CACHE_TTL:
        return entry["val"]
    return None


def _cache_set(key, val):
    _cache[key] = {"val": val, "ts": time.time()}


def cache_clear():
    """Flush all cached data (useful after library changes)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# TMDb availability check
# ---------------------------------------------------------------------------

def _tmdb_available():
    return bool(TMDB.get("api_key"))


def _unavailable_response(label="recommendations"):
    return {
        "items": [],
        "message": f"TMDb API key is not configured -- {label} require it. "
                   "Set TMDB['api_key'] in config.py.",
    }


# ---------------------------------------------------------------------------
# Library ID helpers
# ---------------------------------------------------------------------------

def _build_library_ids():
    """Return dicts of tmdb_ids and tvdb_ids already tracked by Radarr/Sonarr.

    Returns:
        dict with keys:
            radarr_tmdb  - set of int TMDb IDs (movies)
            sonarr_tvdb  - set of int TVDB IDs (shows)
            sonarr_tmdb  - set of int TMDb IDs for shows (if available from Sonarr)
            movie_titles - set of lowercase movie titles (fallback matching)
            show_titles  - set of lowercase show titles (fallback matching)
    """
    cached = _cache_get("library_ids")
    if cached is not None:
        return cached

    radarr_tmdb = set()
    sonarr_tvdb = set()
    sonarr_tmdb = set()
    movie_titles = set()
    show_titles = set()

    # Radarr movies
    try:
        for m in radarr_get("/movie"):
            if m.get("tmdbId"):
                radarr_tmdb.add(int(m["tmdbId"]))
            if m.get("title"):
                movie_titles.add(m["title"].lower())
    except Exception as exc:
        log.warning("Could not fetch Radarr library: %s", exc)

    # Sonarr series
    try:
        for s in sonarr_get("/series"):
            if s.get("tvdbId"):
                sonarr_tvdb.add(int(s["tvdbId"]))
            if s.get("tmdbId"):
                sonarr_tmdb.add(int(s["tmdbId"]))
            if s.get("title"):
                show_titles.add(s["title"].lower())
    except Exception as exc:
        log.warning("Could not fetch Sonarr library: %s", exc)

    result = {
        "radarr_tmdb": radarr_tmdb,
        "sonarr_tvdb": sonarr_tvdb,
        "sonarr_tmdb": sonarr_tmdb,
        "movie_titles": movie_titles,
        "show_titles": show_titles,
    }
    _cache_set("library_ids", result)
    return result


def _in_library(tmdb_id, media_type, title, lib_ids):
    """Check whether a title is already in Radarr/Sonarr."""
    if media_type == "movie":
        if tmdb_id and int(tmdb_id) in lib_ids["radarr_tmdb"]:
            return True
        if title and title.lower() in lib_ids["movie_titles"]:
            return True
    else:
        if tmdb_id and int(tmdb_id) in lib_ids["sonarr_tmdb"]:
            return True
        if title and title.lower() in lib_ids["show_titles"]:
            return True
    return False


# ---------------------------------------------------------------------------
# Watch-history analysis
# ---------------------------------------------------------------------------

def get_watch_profile(account_id=None):
    """Analyse Plex watch history and return the user's viewing profile.

    Returns a dict with:
        genres   - Counter of genre names
        directors - Counter of director names
        actors   - Counter of actor names
        total_watched - int count of history items analysed
        top_genres    - list of (genre, count) tuples, descending
        top_directors - list of (director, count) tuples, descending
        top_actors    - list of (actor, count) tuples, descending
    """
    cache_key = f"watch_profile_{account_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    genres = Counter()
    directors = Counter()
    actors = Counter()

    # Pull up to 500 recent history items
    history = plex_get_history(account_id=account_id, limit=500)

    for item in history:
        # Genres come as a list of dicts with 'tag' key
        for g in item.get("Genre", []):
            genres[g.get("tag", "Unknown")] += 1

        # Director
        for d in item.get("Director", []):
            directors[d.get("tag", "Unknown")] += 1

        # Actors / Roles
        for r in item.get("Role", []):
            actors[r.get("tag", "Unknown")] += 1

    # If the basic history didn't include metadata, try enriching from the
    # library.  Plex history entries for movies typically include Genre,
    # Director, and Role when fetched with enough detail, but episodes may
    # lack them.  We do a best-effort enrichment for the items that had no
    # genre attached.
    items_without_genre = [h for h in history if not h.get("Genre")]
    for item in items_without_genre[:100]:  # cap to avoid hammering API
        rk = item.get("ratingKey") or item.get("parentRatingKey") or item.get("grandparentRatingKey")
        if not rk:
            continue
        try:
            detail = plex_get(f"/library/metadata/{rk}")
            meta = detail.get("MediaContainer", {}).get("Metadata", [{}])[0]
            for g in meta.get("Genre", []):
                genres[g.get("tag", "Unknown")] += 1
            for d in meta.get("Director", []):
                directors[d.get("tag", "Unknown")] += 1
            for r in meta.get("Role", []):
                actors[r.get("tag", "Unknown")] += 1
        except Exception:
            continue

    profile = {
        "genres": genres,
        "directors": directors,
        "actors": actors,
        "total_watched": len(history),
        "top_genres": genres.most_common(15),
        "top_directors": directors.most_common(15),
        "top_actors": actors.most_common(30),
    }
    _cache_set(cache_key, profile)
    return profile


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_candidate(candidate, profile):
    """Score a single TMDb result against the user's watch profile.

    candidate is a dict with at least: genre_ids or genres, and optionally
    credits (cast/crew).

    Returns (score: float, reasons: list[str]).
    """
    score = 0.0
    reasons = []
    genre_counts = profile["genres"]
    director_counts = profile["directors"]
    actor_counts = profile["actors"]

    # --- Genre overlap ---
    # TMDb genre IDs to names mapping (common ones)
    tmdb_genre_map = _get_tmdb_genre_map()
    cand_genre_names = set()

    for gid in candidate.get("genre_ids", []):
        name = tmdb_genre_map.get(gid, "")
        if name:
            cand_genre_names.add(name)
    # Some detailed results use 'genres' list of dicts
    for g in candidate.get("genres", []):
        cand_genre_names.add(g.get("name", ""))

    for gname in cand_genre_names:
        count = genre_counts.get(gname, 0)
        if count:
            weight = min(count / 10.0, 5.0)
            score += weight
            if count >= 5:
                reasons.append(f"You watch a lot of {gname}")

    # --- Popularity / vote boost ---
    vote_avg = candidate.get("vote_average", 0) or 0
    if vote_avg >= 7.5:
        score += 2.0
        reasons.append(f"Highly rated ({vote_avg:.1f}/10)")
    elif vote_avg >= 6.5:
        score += 1.0

    popularity = candidate.get("popularity", 0) or 0
    if popularity >= 100:
        score += 1.5
    elif popularity >= 40:
        score += 0.5

    # --- Director / cast overlap (only if credits available) ---
    credits = candidate.get("credits", {})
    crew = credits.get("crew", [])
    cast = credits.get("cast", [])

    for person in crew:
        if person.get("job") == "Director":
            name = person.get("name", "")
            count = director_counts.get(name, 0)
            if count:
                score += min(count / 2.0, 5.0)
                reasons.append(f"Directed by {name}")

    for person in cast[:10]:  # top-billed cast
        name = person.get("name", "")
        count = actor_counts.get(name, 0)
        if count >= 2:
            score += min(count / 3.0, 4.0)
            reasons.append(f"Stars {name}")

    # --- Recency boost ---
    release = candidate.get("release_date") or candidate.get("first_air_date") or ""
    if release:
        try:
            year = int(release[:4])
            if year >= 2025:
                score += 1.5
                reasons.append("Recent release")
            elif year >= 2022:
                score += 0.5
        except ValueError:
            pass

    if not reasons:
        reasons.append("Matches your viewing tastes")

    return round(score, 2), reasons


def _get_tmdb_genre_map():
    """Return a mapping of TMDb genre ID -> name for movies and TV."""
    cached = _cache_get("tmdb_genre_map")
    if cached is not None:
        return cached

    mapping = {}
    if not _tmdb_available():
        return mapping

    try:
        movie_genres = tmdb_get("/genre/movie/list")
        for g in movie_genres.get("genres", []):
            mapping[g["id"]] = g["name"]
    except Exception:
        pass
    try:
        tv_genres = tmdb_get("/genre/tv/list")
        for g in tv_genres.get("genres", []):
            mapping[g["id"]] = g["name"]
    except Exception:
        pass

    # Fallback common genres if API failed
    if not mapping:
        mapping = {
            28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
            80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
            14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
            9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
            10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
            10759: "Action & Adventure", 10762: "Kids", 10763: "News",
            10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Soap",
            10767: "Talk", 10768: "War & Politics",
        }

    _cache_set("tmdb_genre_map", mapping)
    return mapping


# ---------------------------------------------------------------------------
# Candidate gathering
# ---------------------------------------------------------------------------

def _gather_tmdb_candidates(profile, media_type="movie", pages=3):
    """Query TMDb discover/recommendations to build a candidate pool.

    Uses the user's top genres to do targeted discover queries and also
    pulls TMDb recommendations for recently-watched titles.
    """
    candidates = {}  # tmdb_id -> dict

    top_genre_ids = _genre_names_to_ids(
        [g for g, _ in profile["top_genres"][:5]]
    )

    # 1) Discover by top genres ------------------------------------------
    for page in range(1, pages + 1):
        params = {
            "sort_by": "popularity.desc",
            "vote_average.gte": 6.0,
            "vote_count.gte": 50,
            "page": page,
        }
        if top_genre_ids:
            params["with_genres"] = ",".join(str(g) for g in top_genre_ids[:3])

        try:
            if media_type == "movie":
                data = tmdb_discover_movies(params)
            else:
                data = tmdb_get("/discover/tv", params)
            for item in data.get("results", []):
                tid = item.get("id")
                if tid:
                    item["_media_type"] = media_type
                    candidates[tid] = item
        except Exception as exc:
            log.warning("TMDb discover page %d failed: %s", page, exc)

    # 2) Get recommendations/similar from TMDb for user's favourites -----
    #    We look at genres the user watches most and search TMDb for popular
    #    titles in those genres, then pull their recommendations.
    top_directors = [d for d, _ in profile["top_directors"][:3]]
    for director in top_directors:
        try:
            if media_type == "movie":
                search = tmdb_get("/search/person", {"query": director})
            else:
                continue  # directors are less useful for TV recs
            for person in search.get("results", [])[:1]:
                pid = person.get("id")
                if not pid:
                    continue
                known = person.get("known_for", [])
                for kf in known:
                    tid = kf.get("id")
                    mt = kf.get("media_type", media_type)
                    if tid and mt == media_type:
                        kf["_media_type"] = media_type
                        candidates[tid] = kf
                # Also pull director's filmography via discover
                disc = tmdb_discover_movies({
                    "with_crew": pid,
                    "sort_by": "vote_average.desc",
                    "vote_count.gte": 50,
                })
                for item in disc.get("results", [])[:10]:
                    tid = item.get("id")
                    if tid:
                        item["_media_type"] = media_type
                        candidates[tid] = item
        except Exception:
            continue

    return candidates


def _genre_names_to_ids(names):
    """Convert genre name strings to TMDb genre IDs."""
    genre_map = _get_tmdb_genre_map()
    reverse = {v.lower(): k for k, v in genre_map.items()}
    ids = []
    for n in names:
        gid = reverse.get(n.lower())
        if gid:
            ids.append(gid)
    return ids


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_recommendations(account_id=None, limit=30):
    """Return a scored list of recommended movies and shows not in the library.

    Each item dict contains:
        title, year, type (movie/tv), tmdb_id, poster_url,
        overview, score, reason, vote_average
    """
    if not _tmdb_available():
        return _unavailable_response("recommendations")

    cache_key = f"recommendations_{account_id}_{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    profile = get_watch_profile(account_id=account_id)
    lib_ids = _build_library_ids()

    all_scored = []

    for media_type in ("movie", "tv"):
        candidates = _gather_tmdb_candidates(profile, media_type=media_type)
        for tmdb_id, cand in candidates.items():
            title = cand.get("title") or cand.get("name") or ""
            if _in_library(tmdb_id, media_type, title, lib_ids):
                continue

            score, reasons = _score_candidate(cand, profile)
            release = cand.get("release_date") or cand.get("first_air_date") or ""
            year = release[:4] if release else ""

            all_scored.append({
                "title": title,
                "year": year,
                "type": media_type,
                "tmdb_id": tmdb_id,
                "poster_url": tmdb_image_url(cand.get("poster_path")),
                "backdrop_url": tmdb_image_url(cand.get("backdrop_path"), size="w780"),
                "overview": cand.get("overview", ""),
                "score": score,
                "reason": "; ".join(reasons[:3]),
                "vote_average": cand.get("vote_average", 0),
                "popularity": cand.get("popularity", 0),
                "genre_ids": cand.get("genre_ids", []),
            })

    # Sort by score descending, then by vote_average as tiebreak
    all_scored.sort(key=lambda x: (x["score"], x["vote_average"]), reverse=True)
    items = all_scored[:limit]

    result = {"items": items, "profile_summary": _profile_summary(profile)}
    _cache_set(cache_key, result)
    return result


def get_trending_not_in_library(limit=20):
    """Return TMDb trending movies and shows filtered against Radarr/Sonarr.

    Each item dict contains: title, year, type, tmdb_id, poster_url,
    overview, vote_average, popularity.
    """
    if not _tmdb_available():
        return _unavailable_response("trending")

    cache_key = f"trending_{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    lib_ids = _build_library_ids()
    items = []

    for media_type in ("movie", "tv"):
        try:
            data = tmdb_trending(media_type=media_type, time_window="week")
        except Exception as exc:
            log.warning("TMDb trending %s failed: %s", media_type, exc)
            continue

        for item in data.get("results", []):
            tmdb_id = item.get("id")
            title = item.get("title") or item.get("name") or ""
            if _in_library(tmdb_id, media_type, title, lib_ids):
                continue

            release = item.get("release_date") or item.get("first_air_date") or ""
            year = release[:4] if release else ""

            items.append({
                "title": title,
                "year": year,
                "type": media_type,
                "tmdb_id": tmdb_id,
                "poster_url": tmdb_image_url(item.get("poster_path")),
                "backdrop_url": tmdb_image_url(item.get("backdrop_path"), size="w780"),
                "overview": item.get("overview", ""),
                "vote_average": item.get("vote_average", 0),
                "popularity": item.get("popularity", 0),
                "genre_ids": item.get("genre_ids", []),
            })

    # Already ranked by TMDb trending order; just trim
    items = items[:limit]

    result = {"items": items, "message": "Trending this week on TMDb, not yet in your library."}
    _cache_set(cache_key, result)
    return result


def _profile_summary(profile):
    """Build a human-readable summary of the user's watch profile."""
    parts = []
    if profile["top_genres"]:
        top3 = ", ".join(g for g, _ in profile["top_genres"][:3])
        parts.append(f"Favourite genres: {top3}")
    if profile["top_directors"]:
        top2 = ", ".join(d for d, _ in profile["top_directors"][:2])
        parts.append(f"Top directors: {top2}")
    if profile["top_actors"]:
        top3 = ", ".join(a for a, _ in profile["top_actors"][:3])
        parts.append(f"Most-watched actors: {top3}")
    parts.append(f"Based on {profile['total_watched']} watched items")
    return " | ".join(parts)
