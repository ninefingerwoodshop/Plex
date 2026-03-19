# Plex Media Stack - AI Taste Profile & TMDB Recommendation Engine
# Analyzes watched/rated movies to build a taste profile, then recommends from TMDB trending

import sys
import json
import os
from collections import Counter, defaultdict
from api import get_plex_movies, plex_get
from config import PLEX

TMDB_API_KEY = None  # Set via --tmdb-key= or config.py
PROFILE_FILE = os.path.join(os.path.dirname(__file__), "taste_profile.json")


def get_tmdb_key():
    global TMDB_API_KEY
    if TMDB_API_KEY:
        return TMDB_API_KEY
    # Try config
    try:
        from config import TMDB
        key = TMDB.get("api_key", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("TMDB_API_KEY", "")


def build_taste_profile():
    """Analyze watched/rated movies to build a taste profile."""
    movies = get_plex_movies()

    watched = [m for m in movies if m.get("viewCount", 0) > 0 or m.get("lastViewedAt")]
    rated = [m for m in movies if m.get("userRating")]

    # Genre preferences (weighted by rating and watch count)
    genre_scores = Counter()
    genre_counts = Counter()
    decade_scores = Counter()
    director_scores = Counter()
    actor_scores = Counter()
    studio_scores = Counter()
    keyword_scores = Counter()

    for m in watched:
        weight = 1.0
        user_rating = m.get("userRating")
        audience_rating = m.get("audienceRating") or m.get("rating") or 5
        if user_rating:
            weight = user_rating / 5.0  # normalize 1-10 to 0.2-2.0
        elif audience_rating:
            weight = audience_rating / 7.0

        # Boost if watched multiple times
        view_count = m.get("viewCount", 1)
        if view_count > 1:
            weight *= 1.0 + (view_count - 1) * 0.3

        # Genres
        for g in m.get("Genre", []):
            tag = g.get("tag", "")
            genre_scores[tag] += weight
            genre_counts[tag] += 1

        # Decade
        year = m.get("year", 0)
        if year:
            decade = f"{(year // 10) * 10}s"
            decade_scores[decade] += weight

        # Directors
        for d in m.get("Director", []):
            director_scores[d.get("tag", "")] += weight

        # Actors (top 5 billed)
        for r in m.get("Role", [])[:5]:
            actor_scores[r.get("tag", "")] += weight

        # Studio
        studio = m.get("studio", "")
        if studio:
            studio_scores[studio] += weight

    # Normalize scores
    total_watched = len(watched) or 1

    profile = {
        "total_movies": len(movies),
        "total_watched": len(watched),
        "total_rated": len(rated),
        "genres": {
            g: {
                "score": round(s / total_watched * 100, 1),
                "count": genre_counts[g],
                "raw": round(s, 1),
            }
            for g, s in genre_scores.most_common(15)
        },
        "decades": {
            d: round(s / total_watched * 100, 1)
            for d, s in decade_scores.most_common(10)
        },
        "top_directors": [
            d for d, _ in director_scores.most_common(15)
        ],
        "top_actors": [
            a for a, _ in actor_scores.most_common(20)
        ],
        "top_studios": [
            s for s, _ in studio_scores.most_common(10)
        ],
        "avg_rating": round(
            sum(m.get("audienceRating") or m.get("rating") or 0 for m in watched) / total_watched, 1
        ),
    }

    # Save profile
    with open(PROFILE_FILE, "w") as f:
        json.dump(profile, f, indent=2)

    return profile


def display_profile(profile):
    """Display the taste profile."""
    print("\n" + "=" * 60)
    print("  YOUR TASTE PROFILE")
    print("=" * 60)

    print(f"\n  Based on {profile['total_watched']} watched movies "
          f"({profile['total_rated']} rated)")
    print(f"  Average rating: {profile['avg_rating']}")

    print(f"\n  --- Genre Preferences ---")
    for genre, data in profile["genres"].items():
        bar_len = int(data["score"] / 3)
        bar = "#" * bar_len
        print(f"  {genre:<20} {data['score']:>5.1f}% ({data['count']} movies) {bar}")

    print(f"\n  --- Decade Preferences ---")
    for decade, score in profile["decades"].items():
        bar_len = int(score / 3)
        bar = "#" * bar_len
        print(f"  {decade:<10} {score:>5.1f}% {bar}")

    print(f"\n  --- Favorite Directors ---")
    for d in profile["top_directors"][:10]:
        print(f"    - {d}")

    print(f"\n  --- Favorite Actors ---")
    for a in profile["top_actors"][:10]:
        print(f"    - {a}")


def score_tmdb_movie(movie, profile):
    """Score a TMDB movie against the taste profile."""
    score = 0

    # Genre match
    genre_map = {
        28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
        80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
        14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
        9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
        10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    }

    movie_genres = [genre_map.get(gid, "") for gid in movie.get("genre_ids", [])]
    for g in movie_genres:
        if g in profile["genres"]:
            score += profile["genres"][g]["score"]

    # Year/decade match
    release = movie.get("release_date", "")
    if release and len(release) >= 4:
        year = int(release[:4])
        decade = f"{(year // 10) * 10}s"
        if decade in profile["decades"]:
            score += profile["decades"][decade] * 0.3

    # TMDB rating boost
    tmdb_rating = movie.get("vote_average", 0)
    if tmdb_rating >= 7.5:
        score += 10
    elif tmdb_rating >= 7.0:
        score += 5

    # Popularity boost (but not too much)
    popularity = movie.get("popularity", 0)
    if popularity > 100:
        score += 3
    elif popularity > 50:
        score += 1

    return round(score, 1)


def get_tmdb_trending(page=1):
    """Fetch trending movies from TMDB."""
    import requests
    key = get_tmdb_key()
    if not key:
        return []
    r = requests.get(
        "https://api.themoviedb.org/3/trending/movie/week",
        params={"api_key": key, "page": page},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def get_tmdb_popular(page=1):
    """Fetch popular movies from TMDB."""
    import requests
    key = get_tmdb_key()
    if not key:
        return []
    r = requests.get(
        "https://api.themoviedb.org/3/movie/popular",
        params={"api_key": key, "page": page},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def get_tmdb_upcoming(page=1):
    """Fetch upcoming movies from TMDB."""
    import requests
    key = get_tmdb_key()
    if not key:
        return []
    r = requests.get(
        "https://api.themoviedb.org/3/movie/upcoming",
        params={"api_key": key, "page": page},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def get_tmdb_by_genre(genre_id, page=1):
    """Fetch movies from TMDB filtered by genre."""
    import requests
    key = get_tmdb_key()
    if not key:
        return []
    r = requests.get(
        "https://api.themoviedb.org/3/discover/movie",
        params={
            "api_key": key,
            "with_genres": genre_id,
            "sort_by": "popularity.desc",
            "vote_average.gte": 6.5,
            "vote_count.gte": 100,
            "page": page,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def recommend(count=20, source="trending"):
    """Get personalized recommendations based on taste profile."""
    print("\n" + "=" * 60)
    print("  AI RECOMMENDATIONS")
    print("=" * 60)

    # Load or build profile
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE) as f:
            profile = json.load(f)
        print(f"  Using saved taste profile ({profile['total_watched']} movies analyzed)")
    else:
        print("  Building taste profile...")
        profile = build_taste_profile()
        display_profile(profile)

    key = get_tmdb_key()
    if not key:
        print("\n  TMDB API key required for recommendations.")
        print("  Get a free key at: https://www.themoviedb.org/settings/api")
        print("  Then run: python plexhealth.py recommend --tmdb-key=YOUR_KEY")
        print("\n  Showing taste profile only (above).")
        return

    # Get existing library titles to exclude
    plex_movies = get_plex_movies()
    existing_titles = set()
    for m in plex_movies:
        existing_titles.add(m.get("title", "").lower())

    # Fetch candidates from TMDB
    print(f"\n  Fetching {source} movies from TMDB...")
    candidates = []

    if source == "trending":
        for page in range(1, 4):
            candidates.extend(get_tmdb_trending(page))
    elif source == "popular":
        for page in range(1, 4):
            candidates.extend(get_tmdb_popular(page))
    elif source == "upcoming":
        for page in range(1, 4):
            candidates.extend(get_tmdb_upcoming(page))
    elif source == "genre":
        # Get top genre from profile
        genre_name_to_id = {
            "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
            "Crime": 80, "Documentary": 99, "Drama": 18, "Horror": 27,
            "Science Fiction": 878, "Thriller": 53, "Western": 37,
        }
        top_genres = list(profile["genres"].keys())[:3]
        for g in top_genres:
            gid = genre_name_to_id.get(g)
            if gid:
                candidates.extend(get_tmdb_by_genre(gid))

    # Filter out movies already in library
    filtered = []
    for m in candidates:
        title = m.get("title", "").lower()
        if title not in existing_titles:
            filtered.append(m)

    # Score and rank
    scored = []
    for m in filtered:
        score = score_tmdb_movie(m, profile)
        scored.append({
            "title": m.get("title", ""),
            "year": m.get("release_date", "")[:4] if m.get("release_date") else "?",
            "tmdb_rating": m.get("vote_average", 0),
            "score": score,
            "overview": (m.get("overview", "") or "")[:150],
            "tmdb_id": m.get("id"),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:count]

    if not top:
        print("\n  No new recommendations found!")
        return

    genre_map_rev = {
        28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
        80: "Crime", 99: "Documentary", 18: "Drama", 27: "Horror",
        878: "Sci-Fi", 53: "Thriller", 37: "Western",
    }

    print(f"\n  Top {len(top)} recommendations for you:\n")
    for i, m in enumerate(top, 1):
        print(f"  {i:>2}. [{m['score']:>5.1f} pts] {m['title']} ({m['year']}) "
              f"-- TMDB: {m['tmdb_rating']:.1f}")
        if m["overview"]:
            print(f"      {m['overview']}...")
        print()

    return {"profile": profile, "recommendations": top}


if __name__ == "__main__":
    action = "recommend"
    source = "trending"
    count = 20

    for arg in sys.argv[1:]:
        if arg == "--profile":
            action = "profile"
        elif arg.startswith("--source="):
            source = arg.split("=")[1]
        elif arg.startswith("--count="):
            count = int(arg.split("=")[1])
        elif arg.startswith("--tmdb-key="):
            TMDB_API_KEY = arg.split("=", 1)[1]

    if action == "profile":
        profile = build_taste_profile()
        display_profile(profile)
    else:
        recommend(count=count, source=source)
