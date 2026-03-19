# Plex Media Stack - "More Like This" Recommender
# Given a movie you liked, find similar ones in your library

import sys
from collections import Counter
from api import get_plex_movies


def get_similarity_score(movie_a, movie_b):
    """Calculate similarity between two movies based on genres, decade, rating."""
    score = 0

    # Genre overlap (biggest factor)
    genres_a = set(g.get("tag", "").lower() for g in movie_a.get("Genre", []))
    genres_b = set(g.get("tag", "").lower() for g in movie_b.get("Genre", []))
    genre_overlap = len(genres_a & genres_b)
    score += genre_overlap * 30

    # Same decade
    year_a = movie_a.get("year", 0)
    year_b = movie_b.get("year", 0)
    if year_a and year_b:
        year_diff = abs(year_a - year_b)
        if year_diff <= 5:
            score += 15
        elif year_diff <= 10:
            score += 10
        elif year_diff <= 20:
            score += 5

    # Similar rating
    rating_a = movie_a.get("audienceRating") or movie_a.get("rating") or 0
    rating_b = movie_b.get("audienceRating") or movie_b.get("rating") or 0
    if rating_a and rating_b:
        rating_diff = abs(rating_a - rating_b)
        if rating_diff <= 0.5:
            score += 10
        elif rating_diff <= 1.0:
            score += 5

    # Same studio
    if movie_a.get("studio") and movie_a.get("studio") == movie_b.get("studio"):
        score += 5

    # Same director
    directors_a = set(d.get("tag", "") for d in movie_a.get("Director", []))
    directors_b = set(d.get("tag", "") for d in movie_b.get("Director", []))
    if directors_a & directors_b:
        score += 20

    # Same actors
    actors_a = set(r.get("tag", "") for r in movie_a.get("Role", [])[:5])
    actors_b = set(r.get("tag", "") for r in movie_b.get("Role", [])[:5])
    actor_overlap = len(actors_a & actors_b)
    score += actor_overlap * 10

    return score


def find_similar(title, count=10, unwatched_only=False):
    """Find movies similar to the given title."""
    print("\n" + "=" * 60)
    print(f"  MORE LIKE: {title}")
    print("=" * 60)

    movies = get_plex_movies()

    # Find the source movie
    source = None
    for m in movies:
        if m.get("title", "").lower() == title.lower():
            source = m
            break

    if not source:
        # Fuzzy match
        for m in movies:
            if title.lower() in m.get("title", "").lower():
                source = m
                break

    if not source:
        print(f"\n  Movie '{title}' not found in your library.")
        print("  Try one of these:")
        for m in sorted(movies, key=lambda x: x.get("title", ""))[:20]:
            print(f"    - {m.get('title', '')} ({m.get('year', '')})")
        return

    source_genres = [g.get("tag", "") for g in source.get("Genre", [])]
    source_rating = source.get("audienceRating") or source.get("rating") or 0
    print(f"\n  {source.get('title')} ({source.get('year', '')})")
    print(f"  Genres: {', '.join(source_genres)}")
    print(f"  Rating: {source_rating:.1f}")

    # Score all other movies
    scored = []
    for m in movies:
        if m.get("title") == source.get("title") and m.get("year") == source.get("year"):
            continue

        if unwatched_only:
            if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"):
                continue

        score = get_similarity_score(source, m)
        if score > 0:
            scored.append({
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "score": score,
                "genres": [g.get("tag", "") for g in m.get("Genre", [])],
                "rating": m.get("audienceRating") or m.get("rating") or 0,
                "watched": bool(m.get("viewCount") or m.get("lastViewedAt")),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:count]

    if not top:
        print("\n  No similar movies found!")
        return

    print(f"\n  Similar movies in your library:\n")
    for i, m in enumerate(top, 1):
        genres = ", ".join(m["genres"][:3])
        watched = " (watched)" if m["watched"] else ""
        print(f"  {i:>2}. [{m['score']:>3}pts] {m['title']} ({m['year']}) -- {m['rating']:.1f}{watched}")
        print(f"      {genres}")

    return top


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python recommender.py \"Movie Title\" [--unwatched]")
        sys.exit(1)

    title = sys.argv[1]
    unwatched = "--unwatched" in sys.argv
    count = 10
    for arg in sys.argv[1:]:
        if arg.startswith("--count="):
            count = int(arg.split("=")[1])

    find_similar(title, count=count, unwatched_only=unwatched)
