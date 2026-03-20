# Plex Media Stack - New Arrivals Digest
# Generates a weekly digest of newly added movies and TV shows
#
# Usage:
#   python new_arrivals_digest.py                    # Print digest to console
#   python new_arrivals_digest.py --days=7           # Custom lookback period
#   python new_arrivals_digest.py --save             # Save digest to JSON

import sys
import json
import os
from datetime import datetime, timedelta
from api import plex_get, tmdb_search_movie, tmdb_search_tv, tmdb_image_url
from config import PLEX, TMDB

DIGEST_FILE = os.path.join(os.path.dirname(__file__), "arrivals_digest.json")


def get_recently_added(days=7, limit=200):
    """Get all content added to Plex in the last N days."""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp())

    movies = []
    shows = []
    episodes = []

    # Get recently added movies
    try:
        data = plex_get(
            f"/library/sections/{PLEX['movie_section']}/all",
            {
                "sort": "addedAt:desc",
                "X-Plex-Container-Start": 0,
                "X-Plex-Container-Size": limit,
            },
        )
        for m in data.get("MediaContainer", {}).get("Metadata", []):
            added_at = m.get("addedAt", 0)
            if added_at >= cutoff_ts:
                # Get poster from TMDb for better quality
                poster = ""
                if TMDB.get("api_key"):
                    try:
                        results = tmdb_search_movie(m.get("title", ""), m.get("year"))
                        if results.get("results"):
                            poster = tmdb_image_url(results["results"][0].get("poster_path"))
                    except Exception:
                        pass

                # Fallback to Plex thumb
                if not poster and m.get("thumb"):
                    poster = f"{PLEX['url']}{m['thumb']}?X-Plex-Token={PLEX['token']}"

                # Get media info
                media_info = {}
                media_list = m.get("Media", [])
                if media_list:
                    media = media_list[0]
                    media_info = {
                        "resolution": media.get("videoResolution", ""),
                        "codec": media.get("videoCodec", ""),
                        "container": media.get("container", ""),
                    }

                movies.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", ""),
                    "rating": m.get("audienceRating") or m.get("rating") or 0,
                    "summary": (m.get("summary", "") or "")[:200],
                    "genres": [g.get("tag", "") for g in m.get("Genre", [])],
                    "poster": poster,
                    "addedAt": added_at,
                    "addedDate": datetime.fromtimestamp(added_at).strftime("%b %d"),
                    "ratingKey": m.get("ratingKey"),
                    "duration": round(m.get("duration", 0) / 60000),  # minutes
                    "media": media_info,
                })
    except Exception as e:
        print(f"  Error fetching movies: {e}")

    # Get recently added TV episodes (grouped by show)
    try:
        data = plex_get(
            f"/library/sections/{PLEX['tv_section']}/all",
            {
                "sort": "addedAt:desc",
                "X-Plex-Container-Start": 0,
                "X-Plex-Container-Size": limit,
                "type": 4,  # episodes
            },
        )
        # Group episodes by show
        show_episodes = {}
        for ep in data.get("MediaContainer", {}).get("Metadata", []):
            added_at = ep.get("addedAt", 0)
            if added_at < cutoff_ts:
                continue

            show_title = ep.get("grandparentTitle", "Unknown")
            if show_title not in show_episodes:
                show_poster = ""
                if ep.get("grandparentThumb"):
                    show_poster = f"{PLEX['url']}{ep['grandparentThumb']}?X-Plex-Token={PLEX['token']}"

                # Try TMDb for better poster
                if TMDB.get("api_key"):
                    try:
                        results = tmdb_search_tv(show_title)
                        if results.get("results"):
                            tmdb_poster = tmdb_image_url(results["results"][0].get("poster_path"))
                            if tmdb_poster:
                                show_poster = tmdb_poster
                    except Exception:
                        pass

                show_episodes[show_title] = {
                    "show": show_title,
                    "poster": show_poster,
                    "episodes": [],
                    "addedAt": added_at,
                    "ratingKey": ep.get("grandparentRatingKey"),
                }

            show_episodes[show_title]["episodes"].append({
                "title": ep.get("title", ""),
                "season": ep.get("parentIndex", 0),
                "episode": ep.get("index", 0),
                "addedAt": added_at,
                "addedDate": datetime.fromtimestamp(added_at).strftime("%b %d"),
            })

            # Keep the most recent addedAt
            if added_at > show_episodes[show_title]["addedAt"]:
                show_episodes[show_title]["addedAt"] = added_at

        # Format episode ranges per show
        for show_title, data in show_episodes.items():
            eps = sorted(data["episodes"], key=lambda x: (x["season"], x["episode"]))
            data["episode_count"] = len(eps)
            data["addedDate"] = datetime.fromtimestamp(data["addedAt"]).strftime("%b %d")

            # Build a nice summary like "S02E01-E08" or "S01E05, S02E01"
            ranges = []
            current_season = None
            current_start = None
            current_end = None
            for ep in eps:
                s, e = ep["season"], ep["episode"]
                if current_season == s and current_end is not None and e == current_end + 1:
                    current_end = e
                else:
                    if current_season is not None:
                        if current_start == current_end:
                            ranges.append(f"S{current_season:02d}E{current_start:02d}")
                        else:
                            ranges.append(f"S{current_season:02d}E{current_start:02d}-E{current_end:02d}")
                    current_season = s
                    current_start = e
                    current_end = e

            if current_season is not None:
                if current_start == current_end:
                    ranges.append(f"S{current_season:02d}E{current_start:02d}")
                else:
                    ranges.append(f"S{current_season:02d}E{current_start:02d}-E{current_end:02d}")

            data["episode_summary"] = ", ".join(ranges)
            shows.append(data)

        shows.sort(key=lambda x: x["addedAt"], reverse=True)

    except Exception as e:
        print(f"  Error fetching TV episodes: {e}")

    return {"movies": movies, "shows": shows}


def generate_digest(days=7):
    """Generate a full digest with stats."""
    arrivals = get_recently_added(days=days)
    movies = arrivals["movies"]
    shows = arrivals["shows"]

    total_episodes = sum(s.get("episode_count", 0) for s in shows)

    # Genre breakdown for movies
    genre_counts = {}
    for m in movies:
        for g in m.get("genres", []):
            genre_counts[g] = genre_counts.get(g, 0) + 1
    top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    digest = {
        "generated": datetime.now().isoformat(),
        "period_days": days,
        "period_start": (datetime.now() - timedelta(days=days)).strftime("%b %d"),
        "period_end": datetime.now().strftime("%b %d, %Y"),
        "stats": {
            "new_movies": len(movies),
            "new_shows": len(shows),
            "new_episodes": total_episodes,
            "top_genres": [{"genre": g, "count": c} for g, c in top_genres],
        },
        "movies": sorted(movies, key=lambda x: x.get("addedAt", 0), reverse=True),
        "shows": shows,
    }

    return digest


def save_digest(digest):
    """Save digest to JSON file."""
    with open(DIGEST_FILE, "w") as f:
        json.dump(digest, f, indent=2)
    print(f"  Digest saved to {DIGEST_FILE}")


def load_last_digest():
    """Load the last saved digest."""
    if os.path.exists(DIGEST_FILE):
        with open(DIGEST_FILE, "r") as f:
            return json.load(f)
    return None


def print_digest(digest):
    """Pretty-print the digest to console."""
    print("\n" + "=" * 60)
    print("  NEW ARRIVALS DIGEST")
    print("=" * 60)
    print(f"\n  Period: {digest['period_start']} - {digest['period_end']}")

    stats = digest["stats"]
    print(f"\n  New movies: {stats['new_movies']}")
    print(f"  New shows with episodes: {stats['new_shows']}")
    print(f"  Total new episodes: {stats['new_episodes']}")

    if stats["top_genres"]:
        genres_str = ", ".join(f"{g['genre']} ({g['count']})" for g in stats["top_genres"])
        print(f"  Top genres: {genres_str}")

    # Movies
    if digest["movies"]:
        print(f"\n  --- New Movies ({len(digest['movies'])}) ---")
        for m in digest["movies"]:
            genres = ", ".join(m.get("genres", [])[:2])
            rating = f"[{m['rating']:.1f}]" if m.get("rating") else ""
            duration = f"{m['duration']}min" if m.get("duration") else ""
            res = m.get("media", {}).get("resolution", "")
            print(f"    {m['addedDate']} | {m['title']} ({m['year']}) {rating} {genres} {duration} {res}")

    # TV Shows
    if digest["shows"]:
        print(f"\n  --- New TV Episodes ({stats['new_episodes']} episodes across {len(digest['shows'])} shows) ---")
        for s in digest["shows"]:
            print(f"    {s['addedDate']} | {s['show']} - {s['episode_summary']} ({s['episode_count']} eps)")

    print()


if __name__ == "__main__":
    days = 7
    save = False

    for arg in sys.argv[1:]:
        if arg.startswith("--days="):
            days = int(arg.split("=")[1])
        elif arg == "--save":
            save = True

    digest = generate_digest(days=days)
    print_digest(digest)

    if save:
        save_digest(digest)
