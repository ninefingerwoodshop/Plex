# Plex Media Stack - Movie Night Picker
# Random movie selector with filters for genre, decade, rating, unwatched

import random
import sys
from api import get_plex_movies


def pick_movie(genre=None, decade=None, min_rating=None, unwatched_only=True, count=1):
    """Pick random movies from your library with optional filters."""
    movies = get_plex_movies()

    # Apply filters
    filtered = []
    for m in movies:
        # Unwatched filter
        if unwatched_only:
            if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"):
                continue

        # Genre filter
        if genre:
            movie_genres = [g.get("tag", "").lower() for g in m.get("Genre", [])]
            if genre.lower() not in movie_genres:
                continue

        # Decade filter
        if decade:
            year = m.get("year", 0)
            decade_start = int(decade.replace("s", ""))
            if not (decade_start <= year < decade_start + 10):
                continue

        # Rating filter
        if min_rating:
            rating = m.get("audienceRating") or m.get("rating") or 0
            if rating < min_rating:
                continue

        movie_genres = [g.get("tag", "") for g in m.get("Genre", [])]
        rating = m.get("audienceRating") or m.get("rating") or 0
        duration = m.get("duration", 0)
        runtime_min = duration // 60000 if duration else 0

        filtered.append({
            "title": m.get("title", "Unknown"),
            "year": m.get("year", ""),
            "rating": rating,
            "genres": movie_genres,
            "summary": (m.get("summary", "") or "")[:200],
            "runtime": runtime_min,
            "studio": m.get("studio", ""),
        })

    if not filtered:
        return []

    picks = random.sample(filtered, min(count, len(filtered)))
    return picks


def movie_night(genre=None, decade=None, min_rating=None, unwatched_only=True, count=3):
    """Interactive movie night picker."""
    print("\n" + "=" * 60)
    print("  MOVIE NIGHT PICKER")
    print("=" * 60)

    filters = []
    if genre:
        filters.append(f"Genre: {genre}")
    if decade:
        filters.append(f"Decade: {decade}")
    if min_rating:
        filters.append(f"Min rating: {min_rating}")
    if unwatched_only:
        filters.append("Unwatched only")

    if filters:
        print(f"  Filters: {', '.join(filters)}")

    picks = pick_movie(
        genre=genre,
        decade=decade,
        min_rating=min_rating,
        unwatched_only=unwatched_only,
        count=count,
    )

    if not picks:
        print("\n  No movies match your filters!")
        # Show available genres
        movies = get_plex_movies()
        from collections import Counter
        genres = Counter()
        for m in movies:
            for g in m.get("Genre", []):
                genres[g.get("tag", "")] += 1
        print("  Available genres: " + ", ".join(g for g, _ in genres.most_common()))
        return

    print(f"\n  Tonight's picks:\n")
    for i, m in enumerate(picks, 1):
        genres = ", ".join(m["genres"][:3])
        runtime = f"{m['runtime']} min" if m["runtime"] else "?"
        print(f"  [{i}] {m['title']} ({m['year']})")
        print(f"      Rating: {m['rating']:.1f}  |  {genres}  |  {runtime}")
        if m["summary"]:
            print(f"      {m['summary']}...")
        print()

    return picks


def list_genres():
    """List all available genres with counts."""
    movies = get_plex_movies()
    from collections import Counter
    genres = Counter()
    unwatched_genres = Counter()
    for m in movies:
        is_unwatched = not m.get("viewCount") and not m.get("lastViewedAt")
        for g in m.get("Genre", []):
            tag = g.get("tag", "")
            genres[tag] += 1
            if is_unwatched:
                unwatched_genres[tag] += 1

    print("\n  Available genres (total / unwatched):")
    for genre, total in genres.most_common():
        unwatched = unwatched_genres.get(genre, 0)
        print(f"    {genre:<20} {total:>4} total / {unwatched:>4} unwatched")


if __name__ == "__main__":
    genre = None
    decade = None
    min_rating = None
    unwatched_only = True
    count = 3

    for arg in sys.argv[1:]:
        if arg.startswith("--genre="):
            genre = arg.split("=", 1)[1]
        elif arg.startswith("--decade="):
            decade = arg.split("=", 1)[1]
        elif arg.startswith("--rating="):
            min_rating = float(arg.split("=")[1])
        elif arg == "--all":
            unwatched_only = False
        elif arg.startswith("--count="):
            count = int(arg.split("=")[1])
        elif arg == "--genres":
            list_genres()
            sys.exit(0)

    movie_night(
        genre=genre,
        decade=decade,
        min_rating=min_rating,
        unwatched_only=unwatched_only,
        count=count,
    )
