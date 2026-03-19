# Plex Media Stack - Watch History & Stats
# Analyzes viewing patterns, unwatched library, and recommendations

from collections import Counter, defaultdict
from api import get_plex_movies, get_plex_shows, plex_get
from config import PLEX


def get_watch_history():
    """Get recently watched items from Plex."""
    data = plex_get("/status/sessions/history/all", {
        "X-Plex-Container-Start": 0,
        "X-Plex-Container-Size": 500,
    })
    return data.get("MediaContainer", {}).get("Metadata", [])


def movie_stats():
    """Comprehensive movie library statistics."""
    print("\n" + "=" * 60)
    print("  LIBRARY STATISTICS")
    print("=" * 60)

    movies = get_plex_movies()

    # Basic counts
    total = len(movies)
    watched = [m for m in movies if m.get("viewCount", 0) > 0 or m.get("lastViewedAt")]
    unwatched = [m for m in movies if not m.get("viewCount") and not m.get("lastViewedAt")]

    print(f"\n  --- Movie Library ---")
    print(f"  Total:     {total}")
    print(f"  Watched:   {len(watched)} ({100*len(watched)//total}%)")
    print(f"  Unwatched: {len(unwatched)} ({100*len(unwatched)//total}%)")

    # Genre breakdown
    genres = Counter()
    watched_genres = Counter()
    unwatched_genres = Counter()
    for m in movies:
        for g in m.get("Genre", []):
            tag = g.get("tag", "")
            genres[tag] += 1
            if m in watched:
                watched_genres[tag] += 1
            else:
                unwatched_genres[tag] += 1

    print(f"\n  --- Genre Breakdown ---")
    print(f"  {'Genre':<20} {'Total':>6} {'Watched':>8} {'Unwatched':>10}")
    print(f"  {'-'*20} {'-'*6} {'-'*8} {'-'*10}")
    for genre, count in genres.most_common():
        w = watched_genres.get(genre, 0)
        u = unwatched_genres.get(genre, 0)
        print(f"  {genre:<20} {count:>6} {w:>8} {u:>10}")

    # Decade breakdown
    decades = Counter()
    for m in movies:
        year = m.get("year", 0)
        if year:
            decade = f"{(year // 10) * 10}s"
            decades[decade] += 1

    print(f"\n  --- By Decade ---")
    for decade, count in sorted(decades.items()):
        bar = "#" * (count // 3)
        print(f"  {decade}: {count:>4} {bar}")

    # Quality breakdown
    resolutions = Counter()
    for m in movies:
        for media in m.get("Media", []):
            h = media.get("height", 0)
            if h >= 2160:
                resolutions["4K"] += 1
            elif h >= 1080:
                resolutions["1080p"] += 1
            elif h >= 720:
                resolutions["720p"] += 1
            elif h > 0:
                resolutions["SD"] += 1
            break

    print(f"\n  --- Quality Distribution ---")
    for res in ["4K", "1080p", "720p", "SD"]:
        count = resolutions.get(res, 0)
        pct = 100 * count // total if total else 0
        bar = "#" * (count // 3)
        print(f"  {res:>6}: {count:>4} ({pct}%) {bar}")

    # Total library size
    total_size = 0
    for m in movies:
        for media in m.get("Media", []):
            for part in media.get("Part", []):
                total_size += part.get("size", 0)
    total_gb = total_size / (1024 ** 3)
    print(f"\n  --- Storage ---")
    print(f"  Total size: {total_gb:.1f} GB ({total_gb/1024:.2f} TB)")
    if total:
        print(f"  Average per movie: {total_gb/total:.1f} GB")

    # Top rated unwatched (recommendations)
    unwatched_rated = []
    for m in unwatched:
        rating = m.get("audienceRating") or m.get("rating") or 0
        if rating >= 7.0:
            unwatched_rated.append({
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "rating": rating,
                "genres": [g.get("tag", "") for g in m.get("Genre", [])],
            })

    if unwatched_rated:
        unwatched_rated.sort(key=lambda x: x["rating"], reverse=True)
        print(f"\n  --- Top Unwatched (Your Library's Hidden Gems) ---")
        for m in unwatched_rated[:15]:
            genres = ", ".join(m["genres"][:3])
            print(f"  {m['rating']:>4.1f}  {m['title']} ({m['year']}) -- {genres}")

    return {
        "total": total,
        "watched": len(watched),
        "unwatched": len(unwatched),
        "genres": {k: v for k, v in genres.items()},
        "quality": dict(resolutions),
        "total_gb": round(total_gb, 1),
    }


def tv_stats():
    """TV show library statistics."""
    shows = get_plex_shows()

    print(f"\n  --- TV Show Library ---")
    print(f"  Total shows: {len(shows)}")

    watched_shows = [s for s in shows if s.get("viewedLeafCount", 0) > 0]
    print(f"  Started watching: {len(watched_shows)}")
    print(f"  Never watched: {len(shows) - len(watched_shows)}")

    # Genre breakdown
    genres = Counter()
    for s in shows:
        for g in s.get("Genre", []):
            genres[g.get("tag", "")] += 1

    print(f"\n  --- TV Genres ---")
    for genre, count in genres.most_common(10):
        print(f"  {genre:<20} {count:>4}")

    return {"total_shows": len(shows), "watched": len(watched_shows)}


if __name__ == "__main__":
    movie_stats()
    tv_stats()
