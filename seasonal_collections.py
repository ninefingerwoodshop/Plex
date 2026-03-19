# Plex Media Stack - Seasonal Auto-Collections
# Automatically curates themed collections based on the calendar
# Halloween horror in October, Christmas movies in December, summer blockbusters, etc.
#
# Usage:
#   python seasonal_collections.py                # Dry run - show what would be created
#   python seasonal_collections.py --go           # Create/update seasonal collections
#   python seasonal_collections.py --clean        # Remove out-of-season collections

import sys
import re
from datetime import datetime, date
from api import get_plex_movies, get_plex_shows, plex_get
from config import PLEX
from collections_builder import add_to_collection_via_tag, get_existing_collections

# --- Seasonal definitions ---
# Each season has: months it's active, collection name, and match rules.
# Rules can match by genre, title keyword, or TMDb keyword.

SEASONS = {
    "halloween": {
        "name": "Halloween Collection",
        "months": [10],  # October
        "icon": "jack-o-lantern",
        "description": "Spooky season favorites - horror, thrillers, and creature features",
        "rules": {
            "genres": ["Horror"],
            "title_keywords": [
                "halloween", "scream", "nightmare", "exorcist", "conjuring",
                "annabelle", "insidious", "sinister", "purge", "saw",
                "freddy", "jason", "chucky", "ghostbusters", "hocus pocus",
                "beetlejuice", "corpse bride", "monster house", "coraline",
                "paranorman", "addams family", "casper", "witch", "zombie",
                "dracula", "frankenstein", "mummy", "werewolf", "vampire",
                "haunted", "haunting", "poltergeist", "evil dead", "cabin",
                "it (", "us (2019)", "get out", "nope", "midsommar",
                "hereditary", "the ring", "grudge", "candyman", "hellraiser",
            ],
        },
    },
    "thanksgiving": {
        "name": "Thanksgiving Collection",
        "months": [11],  # November
        "icon": "turkey",
        "description": "Family favorites for Turkey Day",
        "rules": {
            "genres": [],
            "title_keywords": [
                "thanksgiving", "turkey", "planes trains",
                "free birds", "charlie brown thanksgiving",
                "home for the holidays", "dutch (1991)",
                "pieces of april", "the blind side",
                "instant family", "soul food",
            ],
            "family_picks": True,  # Also pull highly-rated family/comedy
        },
    },
    "christmas": {
        "name": "Christmas Collection",
        "months": [12],  # December
        "icon": "christmas-tree",
        "description": "Holiday classics and festive favorites",
        "rules": {
            "genres": [],
            "title_keywords": [
                "christmas", "xmas", "santa", "grinch", "elf (2003)",
                "home alone", "die hard", "polar express", "miracle on 34th",
                "it's a wonderful life", "a christmas carol", "scrooged",
                "the holiday", "love actually", "jingle", "rudolph",
                "frosty", "nutcracker", "nightmare before christmas",
                "bad santa", "fred claus", "four christmases",
                "the night before", "office christmas party", "noelle",
                "klaus", "arthur christmas", "rise of the guardians",
                "krampus", "silent night", "black christmas", "gremlins",
                "lethal weapon", "trading places", "rocky iv",
                "edward scissorhands", "while you were sleeping",
                "the family stone", "national lampoon",
            ],
        },
    },
    "new_years": {
        "name": "New Year's Collection",
        "months": [1],  # January
        "icon": "party",
        "description": "Fresh starts and new beginnings",
        "rules": {
            "genres": [],
            "title_keywords": [
                "new year", "the holiday", "when harry met sally",
                "bridget jones", "an affair to remember",
                "the poseidon adventure", "about time",
                "forrest gump", "groundhog day",
            ],
        },
    },
    "valentines": {
        "name": "Valentine's Day Collection",
        "months": [2],  # February
        "icon": "heart",
        "description": "Romance, date night picks, and love stories",
        "rules": {
            "genres": ["Romance"],
            "title_keywords": [
                "valentine", "love", "notebook", "titanic", "pretty woman",
                "when harry met sally", "sleepless in seattle",
                "you've got mail", "jerry maguire", "ghost (1990)",
                "dirty dancing", "grease", "la la land", "crazy rich asians",
                "pride and prejudice", "about time", "50 first dates",
                "wedding", "bride", "proposal",
            ],
        },
    },
    "st_patricks": {
        "name": "St. Patrick's Day Collection",
        "months": [3],  # March
        "icon": "shamrock",
        "description": "Irish films and lucky picks",
        "rules": {
            "genres": [],
            "title_keywords": [
                "irish", "ireland", "dublin", "boondock saints",
                "the departed", "in bruges", "the guard",
                "waking ned devine", "the commitments", "once (2007)",
                "brooklyn (2015)", "the banshees", "leprechaun",
                "darby o'gill", "far and away", "gangs of new york",
                "the wind that shakes the barley", "calvary",
            ],
        },
    },
    "summer_blockbusters": {
        "name": "Summer Blockbusters",
        "months": [6, 7, 8],  # June-August
        "icon": "sun",
        "description": "Big action, explosions, and popcorn movies",
        "rules": {
            "genres": ["Action", "Adventure"],
            "min_rating": 6.5,
            "title_keywords": [
                "jurassic", "transformers", "avengers", "spider-man",
                "mission impossible", "fast & furious", "fast and furious",
                "top gun", "independence day", "jaws", "indiana jones",
                "pirates of the caribbean", "guardians of the galaxy",
                "mad max", "john wick",
            ],
        },
    },
    "back_to_school": {
        "name": "Back to School Collection",
        "months": [9],  # September
        "icon": "books",
        "description": "School comedies, coming-of-age, and campus classics",
        "rules": {
            "genres": [],
            "title_keywords": [
                "school", "college", "university", "high school",
                "back to school", "breakfast club", "ferris bueller",
                "mean girls", "clueless", "legally blonde", "animal house",
                "old school", "pitch perfect", "superbad", "booksmart",
                "lady bird", "dazed and confused", "fast times",
                "dead poets society", "good will hunting", "accepted",
                "21 jump street", "revenge of the nerds", "rudy",
                "remember the titans", "grease",
            ],
        },
    },
    "spring_break": {
        "name": "Spring Break Collection",
        "months": [4],  # April
        "icon": "palm-tree",
        "description": "Road trips, beach vibes, and adventure",
        "rules": {
            "genres": [],
            "title_keywords": [
                "spring break", "road trip", "eurotrip", "beach",
                "surf", "vacation", "hangover", "weekend at bernie",
                "forgetting sarah marshall", "couples retreat",
                "grown ups", "blue crush", "point break",
            ],
        },
    },
    "star_wars_day": {
        "name": "May the 4th Collection",
        "months": [5],  # May
        "icon": "star",
        "description": "Star Wars, sci-fi epics, and space adventures",
        "rules": {
            "genres": ["Science Fiction"],
            "title_keywords": [
                "star wars", "star trek", "guardians of the galaxy",
                "alien", "aliens", "interstellar", "the martian",
                "arrival", "blade runner", "dune", "avatar",
                "gravity", "moon (2009)", "ex machina", "district 9",
                "edge of tomorrow", "pacific rim", "ender's game",
            ],
        },
    },
}


def get_active_seasons(target_date=None):
    """Return which seasons are currently active."""
    d = target_date or date.today()
    month = d.month
    active = {}
    for key, season in SEASONS.items():
        if month in season["months"]:
            active[key] = season
    return active


def get_all_seasons_status(target_date=None):
    """Return all seasons with their active/inactive status."""
    d = target_date or date.today()
    month = d.month
    result = []
    for key, season in SEASONS.items():
        result.append({
            "key": key,
            "name": season["name"],
            "icon": season["icon"],
            "description": season["description"],
            "months": season["months"],
            "active": month in season["months"],
        })
    return result


def match_movie_to_season(movie, rules):
    """Check if a movie matches a season's rules."""
    title = movie.get("title", "").lower()
    year = movie.get("year", "")
    movie_genres = [g.get("tag", "") for g in movie.get("Genre", [])]
    rating = movie.get("audienceRating") or movie.get("rating") or 0

    # Check title keywords first (highest priority)
    for kw in rules.get("title_keywords", []):
        kw_lower = kw.lower()
        # Handle year-specific patterns like "elf (2003)"
        if "(" in kw_lower:
            if kw_lower in f"{title} ({year})".lower():
                return True
        elif kw_lower in title:
            return True

    # Check genre match
    matched_genres = rules.get("genres", [])
    if matched_genres:
        if any(g in movie_genres for g in matched_genres):
            # If there's a min_rating filter, apply it
            min_rating = rules.get("min_rating", 0)
            if min_rating and rating < min_rating:
                return False
            return True

    return False


def find_seasonal_movies(season_key=None, target_date=None):
    """Find movies matching active (or specified) seasonal collections."""
    movies = get_plex_movies()

    if season_key:
        seasons = {season_key: SEASONS[season_key]}
    else:
        seasons = get_active_seasons(target_date)

    results = {}
    for key, season in seasons.items():
        matches = []
        seen_keys = set()
        for m in movies:
            rk = m.get("ratingKey")
            if rk in seen_keys:
                continue
            if match_movie_to_season(m, season["rules"]):
                seen_keys.add(rk)
                matches.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", ""),
                    "ratingKey": rk,
                    "rating": m.get("audienceRating") or m.get("rating") or 0,
                    "genres": [g.get("tag", "") for g in m.get("Genre", [])],
                    "thumb": m.get("thumb", ""),
                })

        # Sort by rating descending
        matches.sort(key=lambda x: x.get("rating", 0), reverse=True)
        results[key] = {
            "name": season["name"],
            "icon": season["icon"],
            "description": season["description"],
            "months": season["months"],
            "count": len(matches),
            "movies": matches,
        }

    return results


def build_seasonal_collections(dry_run=True, target_date=None):
    """Create seasonal Plex collections for active seasons."""
    print("\n" + "=" * 60)
    print("  SEASONAL COLLECTION BUILDER")
    print("=" * 60)

    active = get_active_seasons(target_date)
    if not active:
        print("\n  No active seasons for this month.")
        return {}

    d = target_date or date.today()
    print(f"\n  Date: {d.strftime('%B %d, %Y')}")
    print(f"  Active seasons: {', '.join(s['name'] for s in active.values())}")

    existing = get_existing_collections()
    results = find_seasonal_movies(target_date=target_date)

    for key, data in results.items():
        name = data["name"]
        movies = data["movies"]
        status = "EXISTS" if name in existing else "NEW"
        print(f"\n  [{status}] {name} ({data['count']} movies)")

        # Show top picks
        for m in movies[:8]:
            genres = ", ".join(m["genres"][:2])
            print(f"    - {m['title']} ({m['year']}) [{m['rating']:.1f}] {genres}")
        if len(movies) > 8:
            print(f"    ... and {len(movies) - 8} more")

    if dry_run:
        print("\n  DRY RUN -- no changes made. Use --go to create collections.")
        return results

    # Create collections
    created = 0
    for key, data in results.items():
        name = data["name"]
        movies = data["movies"]
        if not movies:
            continue

        keys = [m["ratingKey"] for m in movies if m.get("ratingKey")]
        if not keys:
            continue

        if name in existing:
            print(f"\n  '{name}' already exists, updating...")
        else:
            print(f"\n  Creating '{name}' ({len(keys)} movies)...", end=" ", flush=True)

        success = add_to_collection_via_tag(name, keys)
        if success:
            print("OK")
            created += 1
        else:
            print("FAILED")

    print(f"\n  Created/updated {created} seasonal collections")
    return results


def clean_expired_collections(dry_run=True, target_date=None):
    """Remove collections for seasons that are no longer active."""
    print("\n" + "=" * 60)
    print("  SEASONAL CLEANUP")
    print("=" * 60)

    active = get_active_seasons(target_date)
    active_names = {s["name"] for s in active.values()}
    all_seasonal_names = {s["name"] for s in SEASONS.values()}
    expired_names = all_seasonal_names - active_names

    existing = get_existing_collections()
    to_remove = [name for name in expired_names if name in existing]

    if not to_remove:
        print("\n  No expired seasonal collections to clean up.")
        return []

    print(f"\n  Found {len(to_remove)} expired seasonal collections:")
    for name in to_remove:
        print(f"    - {name}")

    if dry_run:
        print("\n  DRY RUN -- no changes made. Use --clean --go to remove.")
        return to_remove

    # Remove expired collections
    removed = 0
    for name in to_remove:
        collection = existing.get(name)
        if not collection:
            continue
        rating_key = collection.get("ratingKey")
        if not rating_key:
            continue

        import requests
        r = requests.delete(
            f"{PLEX['url']}/library/collections/{rating_key}",
            params={"X-Plex-Token": PLEX["token"]},
        )
        if r.status_code in (200, 204):
            print(f"    Removed '{name}' - OK")
            removed += 1
        else:
            print(f"    Removed '{name}' - FAILED ({r.status_code})")

    print(f"\n  Removed {removed}/{len(to_remove)} expired collections")
    return to_remove


def get_seasonal_summary(target_date=None):
    """Get a summary of seasonal collections for the API/dashboard."""
    d = target_date or date.today()
    active = get_active_seasons(d)
    all_status = get_all_seasons_status(d)

    # Get movie counts for active seasons
    active_data = {}
    if active:
        active_data = find_seasonal_movies(target_date=d)

    return {
        "current_month": d.strftime("%B"),
        "seasons": all_status,
        "active": {
            key: {
                "name": data["name"],
                "icon": data["icon"],
                "description": data["description"],
                "count": data["count"],
                "top_picks": data["movies"][:12],
            }
            for key, data in active_data.items()
        },
    }


if __name__ == "__main__":
    go = "--go" in sys.argv
    clean = "--clean" in sys.argv

    # Allow testing with --month=N
    test_date = None
    for arg in sys.argv[1:]:
        if arg.startswith("--month="):
            month = int(arg.split("=")[1])
            test_date = date(date.today().year, month, 15)

    if clean:
        clean_expired_collections(dry_run=not go, target_date=test_date)
    else:
        if not go:
            print("  (Dry run -- add --go to create collections in Plex)")
        build_seasonal_collections(dry_run=not go, target_date=test_date)
