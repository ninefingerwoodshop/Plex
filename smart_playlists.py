# Plex Media Stack - Smart Playlist Generator
# Creates themed playlists in Plex based on genre, decade, rating, etc.

import sys
import requests
from collections import defaultdict
from api import get_plex_movies, get_plex_shows, plex_get
from config import PLEX


def get_machine_id():
    try:
        identity = plex_get("/identity")
        return identity.get("MediaContainer", {}).get("machineIdentifier", "")
    except Exception:
        return ""


def create_playlist(name, rating_keys, playlist_type="video"):
    """Create a playlist in Plex."""
    if not rating_keys:
        return False

    machine_id = get_machine_id()
    key_str = ",".join(str(k) for k in rating_keys)
    uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{key_str}"

    r = requests.post(
        f"{PLEX['url']}/playlists",
        params={
            "X-Plex-Token": PLEX["token"],
            "type": playlist_type,
            "title": name,
            "smart": 0,
            "uri": uri,
        },
    )
    return r.status_code in (200, 201)


def get_existing_playlists():
    """Get all existing Plex playlists."""
    try:
        data = plex_get("/playlists")
        playlists = data.get("MediaContainer", {}).get("Metadata", [])
        return {p.get("title", ""): p for p in playlists}
    except Exception:
        return {}


# --- Playlist generators ---

def genre_decade_marathon(genre, decade_start, decade_end=None):
    """Movies of a genre from a specific decade, sorted by rating."""
    if not decade_end:
        decade_end = decade_start + 9
    movies = get_plex_movies()
    matches = []
    for m in movies:
        year = m.get("year", 0)
        if not (decade_start <= year <= decade_end):
            continue
        genres = [g.get("tag", "").lower() for g in m.get("Genre", [])]
        if genre.lower() in genres:
            rating = m.get("audienceRating") or m.get("rating") or 0
            matches.append({
                "title": m.get("title", ""),
                "year": year,
                "rating": rating,
                "ratingKey": m.get("ratingKey"),
            })
    matches.sort(key=lambda x: x["rating"], reverse=True)
    return matches


def unwatched_by_genre(genre, min_rating=0):
    """Unwatched movies of a genre, sorted by rating."""
    movies = get_plex_movies()
    matches = []
    for m in movies:
        if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"):
            continue
        genres = [g.get("tag", "").lower() for g in m.get("Genre", [])]
        if genre.lower() in genres:
            rating = m.get("audienceRating") or m.get("rating") or 0
            if rating >= min_rating:
                matches.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", ""),
                    "rating": rating,
                    "ratingKey": m.get("ratingKey"),
                })
    matches.sort(key=lambda x: x["rating"], reverse=True)
    return matches


def chronological_franchise(title_patterns):
    """Movies matching patterns sorted by year (franchise order)."""
    movies = get_plex_movies()
    matches = []
    for m in movies:
        title = m.get("title", "").lower()
        for pattern in title_patterns:
            if pattern.lower() in title:
                matches.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", 0),
                    "ratingKey": m.get("ratingKey"),
                })
                break
    matches.sort(key=lambda x: x["year"])
    return matches


def top_rated_unwatched(count=20):
    """Top rated unwatched movies."""
    movies = get_plex_movies()
    unwatched = []
    for m in movies:
        if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"):
            continue
        rating = m.get("audienceRating") or m.get("rating") or 0
        if rating > 0:
            unwatched.append({
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "rating": rating,
                "ratingKey": m.get("ratingKey"),
                "genres": [g.get("tag", "") for g in m.get("Genre", [])],
            })
    unwatched.sort(key=lambda x: x["rating"], reverse=True)
    return unwatched[:count]


def runtime_based(max_minutes):
    """Unwatched movies under a certain runtime."""
    movies = get_plex_movies()
    matches = []
    for m in movies:
        if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"):
            continue
        duration = m.get("duration", 0)
        runtime = duration // 60000 if duration else 0
        if 0 < runtime <= max_minutes:
            rating = m.get("audienceRating") or m.get("rating") or 0
            matches.append({
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "rating": rating,
                "runtime": runtime,
                "ratingKey": m.get("ratingKey"),
            })
    matches.sort(key=lambda x: x["rating"], reverse=True)
    return matches


# --- Predefined smart playlists ---

SMART_PLAYLISTS = {
    "80s Action Marathon": lambda: genre_decade_marathon("Action", 1980),
    "90s Horror Night": lambda: genre_decade_marathon("Horror", 1990),
    "80s Sci-Fi Classics": lambda: genre_decade_marathon("Science Fiction", 1980),
    "Unwatched Horror (Ranked)": lambda: unwatched_by_genre("Horror"),
    "Unwatched Action (Ranked)": lambda: unwatched_by_genre("Action"),
    "Unwatched Comedy (Ranked)": lambda: unwatched_by_genre("Comedy"),
    "Unwatched Sci-Fi (Ranked)": lambda: unwatched_by_genre("Science Fiction"),
    "Unwatched Drama (Top Rated)": lambda: unwatched_by_genre("Drama", min_rating=7.0),
    "Best Unwatched (Top 20)": lambda: top_rated_unwatched(20),
    "Quick Watches (Under 90 min)": lambda: runtime_based(90),
    "Predator Franchise": lambda: chronological_franchise(["predator", "predators", "prey"]),
    "Deadpool Franchise": lambda: chronological_franchise(["deadpool"]),
    "Anime Film Festival": lambda: unwatched_by_genre("Animation"),
}


def generate_all_playlists(dry_run=True):
    """Generate all smart playlists."""
    print("\n" + "=" * 60)
    print("  SMART PLAYLIST GENERATOR")
    print("=" * 60)

    existing = get_existing_playlists()
    print(f"\n  {len(existing)} existing playlists")

    created = 0
    skipped = 0

    for name, generator in SMART_PLAYLISTS.items():
        matches = generator()
        if len(matches) < 2:
            continue

        status = "EXISTS" if name in existing else "NEW"
        print(f"\n  [{status}] {name} ({len(matches)} movies)")
        for m in matches[:5]:
            rating_str = f" ({m['rating']:.1f})" if m.get("rating") else ""
            runtime_str = f" {m['runtime']}min" if m.get("runtime") else ""
            print(f"    - {m['title']} ({m.get('year', '?')}){rating_str}{runtime_str}")
        if len(matches) > 5:
            print(f"    ... and {len(matches) - 5} more")

        if name in existing:
            skipped += 1
            continue

        if not dry_run:
            keys = [m["ratingKey"] for m in matches if m.get("ratingKey")]
            if keys:
                print(f"    Creating...", end=" ", flush=True)
                if create_playlist(name, keys):
                    print("OK")
                    created += 1
                else:
                    print("FAILED")

    print(f"\n  Summary: {created} created, {skipped} already exist")
    if dry_run and created == 0 and skipped < len(SMART_PLAYLISTS):
        print("  Use --go to create playlists in Plex.")


if __name__ == "__main__":
    dry_run = "--go" not in sys.argv
    if dry_run:
        print("  (Dry run -- add --go to create playlists)")
    generate_all_playlists(dry_run=dry_run)
