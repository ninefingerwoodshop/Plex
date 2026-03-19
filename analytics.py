# Plex Cross-Library Analytics & Year-in-Review
# Spotify-Wrapped style stats for your media server

import json
import os
import re
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from api import (
    plex_get, plex_get_history, plex_get_accounts,
    get_plex_movies, get_plex_shows,
    get_plex_movie_details, get_plex_show_seasons, get_plex_season_episodes,
    tmdb_movie_details, tmdb_image_url,
)
from config import PLEX, ANALYTICS

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

CACHE_FILE = ANALYTICS.get("cache_file", "analytics_cache.json")
CACHE_TTL_SECONDS = 3600  # 1 hour


def _load_cache():
    """Load the analytics cache from disk."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache):
    """Persist the analytics cache to disk."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, default=str)


def _cache_get(key):
    """Return cached value if present and not expired, else None."""
    cache = _load_cache()
    entry = cache.get(key)
    if entry is None:
        return None
    cached_at = datetime.fromisoformat(entry["cached_at"])
    if datetime.now() - cached_at > timedelta(seconds=CACHE_TTL_SECONDS):
        return None
    return entry["data"]


def _cache_set(key, data):
    """Store a value in the cache with a timestamp."""
    cache = _load_cache()
    cache[key] = {"cached_at": datetime.now().isoformat(), "data": data}
    _save_cache(cache)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _account_map():
    """Return {account_id: username} for all Plex accounts."""
    accounts = plex_get_accounts()
    return {str(a.get("id", "")): a.get("name", "Unknown") for a in accounts}


def _parse_viewed_at(entry):
    """Parse the viewedAt timestamp from a history entry."""
    viewed = entry.get("viewedAt")
    if viewed is None:
        return None
    try:
        return datetime.fromtimestamp(int(viewed))
    except (ValueError, TypeError, OSError):
        return None


def _extract_genres(metadata):
    """Pull genre strings from Plex metadata."""
    genres = metadata.get("Genre", [])
    if isinstance(genres, list):
        return [g.get("tag", "") if isinstance(g, dict) else str(g) for g in genres]
    return []


def _extract_directors(metadata):
    """Pull director names from Plex metadata."""
    directors = metadata.get("Director", [])
    if isinstance(directors, list):
        return [d.get("tag", "") if isinstance(d, dict) else str(d) for d in directors]
    return []


def _extract_actors(metadata):
    """Pull actor names from Plex metadata (Role list)."""
    roles = metadata.get("Role", [])
    if isinstance(roles, list):
        return [r.get("tag", "") if isinstance(r, dict) else str(r) for r in roles]
    return []


def _year_to_decade(year):
    """Convert a year int to a decade label like '1990s'."""
    if year is None:
        return "Unknown"
    try:
        decade = (int(year) // 10) * 10
        return f"{decade}s"
    except (ValueError, TypeError):
        return "Unknown"


def _video_resolution_label(media_list):
    """Determine resolution label from Plex Media list."""
    for media in (media_list or []):
        height = media.get("videoResolution", "")
        if height in ("4k", "4K"):
            return "4K"
        try:
            h = int(height)
            if h >= 2160:
                return "4K"
            if h >= 1080:
                return "1080p"
            if h >= 720:
                return "720p"
            return f"{h}p"
        except (ValueError, TypeError):
            # Sometimes Plex stores "1080" or "sd" etc.
            if "1080" in str(height):
                return "1080p"
            if "720" in str(height):
                return "720p"
    return "Unknown"


def _total_file_size_bytes(media_list):
    """Sum file sizes across all parts in a Plex Media list."""
    total = 0
    for media in (media_list or []):
        for part in media.get("Part", []):
            total += int(part.get("size", 0))
    return total


def _personality_type(genre_counter):
    """Determine a fun personality label from the user's top genres."""
    if not genre_counter:
        return "The Casual Viewer"

    top = genre_counter.most_common(3)
    top_genres = {g.lower() for g, _ in top}

    # Check combos first
    if {"horror", "thriller"} & top_genres:
        return "The Thrill Seeker"
    if {"sci-fi", "science fiction"} & top_genres and "fantasy" in top_genres:
        return "The World Builder"
    if {"romance", "comedy"} & top_genres:
        return "The Feel-Good Fanatic"

    # Single-genre archetypes
    primary = top[0][0].lower()
    archetypes = {
        "action": "The Action Hero",
        "adventure": "The Explorer",
        "animation": "The Toon Devotee",
        "anime": "The Anime Sensei",
        "biography": "The True Story Seeker",
        "comedy": "The Laugh Track",
        "crime": "The Detective",
        "documentary": "The Knowledge Seeker",
        "drama": "The Drama Enthusiast",
        "family": "The Family Fun Captain",
        "fantasy": "The Realm Walker",
        "history": "The Time Traveler",
        "horror": "The Fright Night Host",
        "music": "The Rockumentarian",
        "mystery": "The Puzzle Master",
        "romance": "The Hopeless Romantic",
        "sci-fi": "The Sci-Fi Explorer",
        "science fiction": "The Sci-Fi Explorer",
        "sport": "The MVP",
        "thriller": "The Edge-of-Seater",
        "war": "The Strategist",
        "western": "The Gunslinger",
    }
    return archetypes.get(primary, "The Eclectic Cinephile")


def _fun_facts(total_hours):
    """Generate fun comparison facts based on total hours watched."""
    facts = []
    if total_hours <= 0:
        return ["You haven't watched anything yet -- time to start!"]

    # Flight comparisons
    flights_ny_la = total_hours / 5.5
    if flights_ny_la >= 1:
        facts.append(f"You watched enough to fly from New York to LA {flights_ny_la:.1f} times.")

    # Earth circumference at walking speed (~5 km/h, 40075 km circumference)
    walk_km = total_hours * 5
    earth_pct = (walk_km / 40075) * 100
    if earth_pct >= 1:
        facts.append(f"If you walked while watching, you'd cover {earth_pct:.1f}% of Earth's circumference.")

    # Movie marathon
    avg_movie_len = 1.8  # hours
    movies_equiv = total_hours / avg_movie_len
    facts.append(f"That's roughly {int(movies_equiv)} average-length movies back to back.")

    # Days
    days = total_hours / 24
    if days >= 1:
        facts.append(f"You spent {days:.1f} full days (24 h) glued to the screen.")

    # Sleep comparison
    sleep_nights = total_hours / 8
    if sleep_nights >= 7:
        facts.append(f"That equals {int(sleep_nights)} full nights of sleep -- priorities!")

    # International Space Station orbits (~1.5 h each)
    iss_orbits = total_hours / 1.5
    if iss_orbits >= 5:
        facts.append(f"The ISS could orbit Earth {int(iss_orbits)} times in that span.")

    return facts or ["Nice start -- keep streaming!"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_watch_history(days=365):
    """Fetch full watch history from Plex for all users.

    Returns a list of dicts with keys:
        title, type, user, watched_at, duration_ms, rating_key
    """
    cache_key = f"watch_history_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    accounts = _account_map()
    cutoff = datetime.now() - timedelta(days=days)

    # Plex history endpoint supports large limits; paginate to be safe.
    page_size = 500
    all_history = []

    for account_id, username in accounts.items():
        offset = 0
        while True:
            params = {
                "sort": "viewedAt:desc",
                "X-Plex-Container-Start": offset,
                "X-Plex-Container-Size": page_size,
                "accountID": account_id,
            }
            try:
                data = plex_get("/status/sessions/history/all", params)
                container = data.get("MediaContainer", {})
                entries = container.get("Metadata", [])
            except Exception:
                break

            if not entries:
                break

            stop_paging = False
            for e in entries:
                watched_at = _parse_viewed_at(e)
                if watched_at is None:
                    continue
                if watched_at < cutoff:
                    stop_paging = True
                    break

                media_type = e.get("type", "unknown")
                title = e.get("grandparentTitle") or e.get("title", "Unknown")
                episode_title = e.get("title", "") if media_type == "episode" else ""

                all_history.append({
                    "title": title,
                    "episode_title": episode_title,
                    "type": media_type,
                    "user": username,
                    "watched_at": watched_at.isoformat(),
                    "duration_ms": int(e.get("duration", 0)),
                    "rating_key": str(e.get("ratingKey", "")),
                    "grandparent_rating_key": str(e.get("grandparentRatingKey", "")),
                })

            if stop_paging or len(entries) < page_size:
                break
            offset += page_size

    _cache_set(cache_key, all_history)
    return all_history


def get_user_stats(username=None, days=365):
    """Per-user watching statistics.

    If *username* is None, returns stats for every user as {username: stats}.
    Otherwise returns the stats dict for the requested user.
    """
    cache_key = f"user_stats_{username}_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    history = get_all_watch_history(days=days)

    # Group by user
    per_user = defaultdict(list)
    for entry in history:
        per_user[entry["user"]].append(entry)

    if username and username not in per_user:
        return {}

    users_to_process = [username] if username else list(per_user.keys())

    # Pre-fetch metadata for genre/director/actor enrichment.
    # Build a set of rating keys we need details for.
    _metadata_cache = {}

    def _get_metadata(rating_key):
        if rating_key in _metadata_cache:
            return _metadata_cache[rating_key]
        try:
            data = plex_get(f"/library/metadata/{rating_key}")
            meta = data["MediaContainer"]["Metadata"][0]
        except Exception:
            meta = {}
        _metadata_cache[rating_key] = meta
        return meta

    results = {}

    for user in users_to_process:
        entries = per_user.get(user, [])
        if not entries:
            continue

        total_ms = sum(e["duration_ms"] for e in entries)
        total_hours = round(total_ms / 3_600_000, 1)

        movies = [e for e in entries if e["type"] == "movie"]
        episodes = [e for e in entries if e["type"] == "episode"]

        # Most watched show by episode count
        show_counter = Counter()
        for ep in episodes:
            show_counter[ep["title"]] += 1
        most_watched_show = show_counter.most_common(1)[0][0] if show_counter else None

        # Genres, directors, actors from metadata
        genre_counter = Counter()
        director_counter = Counter()
        actor_counter = Counter()
        ratings = []

        seen_keys = set()
        for e in entries:
            rk = e["rating_key"]
            # For episodes, use grandparent (show-level) for genres
            meta_key = e.get("grandparent_rating_key") or rk
            if meta_key in seen_keys:
                continue
            seen_keys.add(meta_key)

            meta = _get_metadata(meta_key)
            for g in _extract_genres(meta):
                if g:
                    genre_counter[g] += 1
            for d in _extract_directors(meta):
                if d:
                    director_counter[d] += 1
            for a in _extract_actors(meta):
                if a:
                    actor_counter[a] += 1
            rating = meta.get("audienceRating") or meta.get("rating")
            if rating is not None:
                try:
                    ratings.append(float(rating))
                except (ValueError, TypeError):
                    pass

        top_5_genres = [g for g, _ in genre_counter.most_common(5)]
        most_watched_genre = top_5_genres[0] if top_5_genres else None

        most_watched_director = director_counter.most_common(1)[0][0] if director_counter else None
        most_watched_actor = actor_counter.most_common(1)[0][0] if actor_counter else None

        average_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

        # Longest binge: most episodes in a single calendar day
        episodes_by_day = defaultdict(int)
        for ep in episodes:
            day = ep["watched_at"][:10]
            episodes_by_day[day] += 1
        longest_binge = max(episodes_by_day.values()) if episodes_by_day else 0

        # Peak watching hour
        hour_counter = Counter()
        day_counter = Counter()
        for e in entries:
            try:
                dt = datetime.fromisoformat(e["watched_at"])
                hour_counter[dt.hour] += 1
                day_counter[dt.strftime("%A")] += 1
            except (ValueError, TypeError):
                pass

        peak_watching_hour = hour_counter.most_common(1)[0][0] if hour_counter else None
        peak_watching_day = day_counter.most_common(1)[0][0] if day_counter else None

        results[user] = {
            "total_hours_watched": total_hours,
            "total_movies": len(movies),
            "total_episodes": len(episodes),
            "most_watched_show": most_watched_show,
            "most_watched_genre": most_watched_genre,
            "top_5_genres": top_5_genres,
            "most_watched_director": most_watched_director,
            "most_watched_actor": most_watched_actor,
            "average_rating": average_rating,
            "longest_binge": longest_binge,
            "peak_watching_hour": peak_watching_hour,
            "peak_watching_day": peak_watching_day,
        }

    out = results.get(username, results) if username else results
    _cache_set(cache_key, out)
    return out


def get_library_stats():
    """Overall library statistics across movies and TV shows."""
    cache_key = "library_stats"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    movies = get_plex_movies()
    shows = get_plex_shows()

    # --- Movies ---
    total_movies = len(movies)
    genre_counter = Counter()
    decade_counter = Counter()
    quality_counter = Counter()
    total_bytes = 0
    rated_movies = []
    newest_additions = []
    oldest_unwatched = []

    now = datetime.now()
    thirty_days_ago = now - timedelta(days=30)

    for m in movies:
        # Genres
        for g in _extract_genres(m):
            if g:
                genre_counter[g] += 1

        # Decade
        year = m.get("year")
        decade_counter[_year_to_decade(year)] += 1

        # Quality
        quality_counter[_video_resolution_label(m.get("Media", []))] += 1

        # File size
        total_bytes += _total_file_size_bytes(m.get("Media", []))

        # Ratings
        audience = m.get("audienceRating")
        if audience is not None:
            try:
                rated_movies.append((m.get("title", ""), float(audience), m.get("year")))
            except (ValueError, TypeError):
                pass

        # Newest additions
        added_at = m.get("addedAt")
        if added_at:
            try:
                added_dt = datetime.fromtimestamp(int(added_at))
                if added_dt >= thirty_days_ago:
                    newest_additions.append({
                        "title": m.get("title", ""),
                        "year": m.get("year"),
                        "added_at": added_dt.isoformat(),
                        "type": "movie",
                    })
            except (ValueError, TypeError, OSError):
                pass

        # Oldest unwatched
        view_count = int(m.get("viewCount", 0))
        if view_count == 0 and added_at:
            try:
                added_dt = datetime.fromtimestamp(int(added_at))
                oldest_unwatched.append({
                    "title": m.get("title", ""),
                    "year": m.get("year"),
                    "added_at": added_dt.isoformat(),
                    "type": "movie",
                })
            except (ValueError, TypeError, OSError):
                pass

    # --- TV Shows & Episodes ---
    total_shows_count = len(shows)
    total_episodes = 0

    for s in shows:
        for g in _extract_genres(s):
            if g:
                genre_counter[g] += 1

        decade_counter[_year_to_decade(s.get("year"))] += 1

        # Count episodes via leaf count (avoids fetching every season)
        leaf = s.get("leafCount")
        if leaf is not None:
            try:
                total_episodes += int(leaf)
            except (ValueError, TypeError):
                pass

        total_bytes += _total_file_size_bytes(s.get("Media", []))

        added_at = s.get("addedAt")
        if added_at:
            try:
                added_dt = datetime.fromtimestamp(int(added_at))
                if added_dt >= thirty_days_ago:
                    newest_additions.append({
                        "title": s.get("title", ""),
                        "year": s.get("year"),
                        "added_at": added_dt.isoformat(),
                        "type": "show",
                    })
            except (ValueError, TypeError, OSError):
                pass

    # Sort helpers
    rated_movies.sort(key=lambda x: x[1], reverse=True)
    top_rated = [
        {"title": t, "rating": r, "year": y}
        for t, r, y in rated_movies[:25]
    ]

    newest_additions.sort(key=lambda x: x["added_at"], reverse=True)
    oldest_unwatched.sort(key=lambda x: x["added_at"])

    total_size_tb = round(total_bytes / (1024 ** 4), 2)

    result = {
        "total_movies": total_movies,
        "total_shows": total_shows_count,
        "total_episodes": total_episodes,
        "total_size_tb": total_size_tb,
        "genre_distribution": dict(genre_counter.most_common()),
        "decade_distribution": dict(decade_counter.most_common()),
        "quality_distribution": dict(quality_counter.most_common()),
        "top_rated_movies": top_rated,
        "newest_additions": newest_additions[:50],
        "oldest_unwatched": oldest_unwatched[:50],
    }

    _cache_set(cache_key, result)
    return result


def get_year_in_review(username=None, year=None):
    """Spotify-Wrapped style year-in-review for a user (or all users).

    Returns a dict of stats and fun narrative pieces.
    """
    year = year or datetime.now().year
    cache_key = f"year_in_review_{username}_{year}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Determine date range for the requested year
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59)
    # Fetch enough days to cover the year
    days_back = (datetime.now() - start).days + 1
    if days_back < 1:
        days_back = 365

    history = get_all_watch_history(days=days_back)

    # Filter to year and optionally user
    filtered = []
    for e in history:
        try:
            dt = datetime.fromisoformat(e["watched_at"])
        except (ValueError, TypeError):
            continue
        if dt < start or dt > end:
            continue
        if username and e["user"] != username:
            continue
        filtered.append({**e, "_dt": dt})

    if not filtered:
        result = {
            "year": year,
            "username": username,
            "total_hours": 0,
            "top_genre": None,
            "top_genres": [],
            "top_show": None,
            "top_movie": None,
            "most_active_month": None,
            "binge_count": 0,
            "fun_facts": ["No watch history found for this period."],
            "personality_type": "The Ghost Viewer",
        }
        _cache_set(cache_key, result)
        return result

    total_ms = sum(e["duration_ms"] for e in filtered)
    total_hours = round(total_ms / 3_600_000, 1)

    # Genre breakdown (fetch metadata)
    genre_counter = Counter()
    seen_meta = set()
    for e in filtered:
        mk = e.get("grandparent_rating_key") or e["rating_key"]
        if mk in seen_meta:
            continue
        seen_meta.add(mk)
        try:
            data = plex_get(f"/library/metadata/{mk}")
            meta = data["MediaContainer"]["Metadata"][0]
        except Exception:
            continue
        for g in _extract_genres(meta):
            if g:
                genre_counter[g] += 1

    top_genres = [g for g, _ in genre_counter.most_common(5)]
    top_genre = top_genres[0] if top_genres else None

    # Top show (by episode count)
    show_counter = Counter()
    for e in filtered:
        if e["type"] == "episode":
            show_counter[e["title"]] += 1
    top_show = show_counter.most_common(1)[0][0] if show_counter else None

    # Top movie (most watched / rewatched or longest single movie)
    movie_counter = Counter()
    for e in filtered:
        if e["type"] == "movie":
            movie_counter[e["title"]] += 1
    top_movie = movie_counter.most_common(1)[0][0] if movie_counter else None

    # Most active month
    month_counter = Counter()
    for e in filtered:
        month_counter[e["_dt"].strftime("%B")] += 1
    most_active_month = month_counter.most_common(1)[0][0] if month_counter else None

    # Binge count: sessions with 3+ episodes in a single calendar day
    episodes_by_user_day = defaultdict(int)
    for e in filtered:
        if e["type"] == "episode":
            key = (e["user"], e["_dt"].date().isoformat())
            episodes_by_user_day[key] += 1
    binge_count = sum(1 for c in episodes_by_user_day.values() if c >= 3)

    facts = _fun_facts(total_hours)
    personality = _personality_type(genre_counter)

    result = {
        "year": year,
        "username": username or "All Users",
        "total_hours": total_hours,
        "top_genre": top_genre,
        "top_genres": top_genres,
        "top_show": top_show,
        "top_movie": top_movie,
        "most_active_month": most_active_month,
        "binge_count": binge_count,
        "fun_facts": facts,
        "personality_type": personality,
    }

    _cache_set(cache_key, result)
    return result


def get_comparative_stats(days=365):
    """Compare all users against each other.

    Returns a dict with cross-user comparisons.
    """
    cache_key = f"comparative_stats_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    user_stats = get_user_stats(username=None, days=days)
    if not user_stats:
        return {}

    history = get_all_watch_history(days=days)

    # Who watched the most (hours)
    who_watched_most = max(user_stats, key=lambda u: user_stats[u]["total_hours_watched"])

    # Most diverse watcher (most unique genres)
    most_diverse = max(user_stats, key=lambda u: len(user_stats[u].get("top_5_genres", [])))

    # Night owl vs early bird per user
    per_user_entries = defaultdict(list)
    for e in history:
        per_user_entries[e["user"]].append(e)

    chronotypes = {}
    for user, entries in per_user_entries.items():
        night_count = 0  # 22:00 - 05:59
        morning_count = 0  # 06:00 - 11:59
        for e in entries:
            try:
                h = datetime.fromisoformat(e["watched_at"]).hour
            except (ValueError, TypeError):
                continue
            if h >= 22 or h < 6:
                night_count += 1
            elif 6 <= h < 12:
                morning_count += 1
        if night_count > morning_count:
            chronotypes[user] = "Night Owl"
        elif morning_count > night_count:
            chronotypes[user] = "Early Bird"
        else:
            chronotypes[user] = "All-Day Streamer"

    # Unique movies per user
    unique_movies = {}
    for user, entries in per_user_entries.items():
        movie_titles = {e["title"] for e in entries if e["type"] == "movie"}
        unique_movies[user] = len(movie_titles)

    result = {
        "who_watched_most": {
            "user": who_watched_most,
            "hours": user_stats[who_watched_most]["total_hours_watched"],
        },
        "most_diverse_watcher": {
            "user": most_diverse,
            "genre_count": len(user_stats[most_diverse].get("top_5_genres", [])),
            "genres": user_stats[most_diverse].get("top_5_genres", []),
        },
        "chronotypes": chronotypes,
        "unique_movies_per_user": unique_movies,
        "user_summaries": {
            user: {
                "hours": stats["total_hours_watched"],
                "movies": stats["total_movies"],
                "episodes": stats["total_episodes"],
                "top_genre": stats["most_watched_genre"],
                "top_show": stats["most_watched_show"],
                "peak_hour": stats["peak_watching_hour"],
            }
            for user, stats in user_stats.items()
        },
    }

    _cache_set(cache_key, result)
    return result
