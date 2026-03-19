# Plex Media Stack - Smart Collection Builder
# Auto-creates Plex collections based on franchises, genres, decades, and themes

import requests
from api import get_plex_movies, plex_get
from config import PLEX

# --- Franchise definitions ---
# Maps collection name -> list of title patterns (case-insensitive substring match)
FRANCHISES = {
    "Predator Collection": ["predator", "prey (2022)", "predators"],
    "Alien Collection": ["alien: romulus", "alien ", "aliens"],
    "Deadpool Collection": ["deadpool"],
    "Marvel Collection": [
        "deadpool", "wolverine", "spider-man", "thunderbolts",
        "fantastic four", "fantastic 4", "spider-man: no way home",
    ],
    "DC Collection": [
        "superman", "the flash", "aquaman", "the suicide squad",
        "batman",
    ],
    "Mission: Impossible Collection": ["mission: impossible", "mission impossible"],
    "Top Gun Collection": ["top gun"],
    "Jurassic Collection": ["jurassic"],
    "The Thing Collection": ["the thing (1982)", "the thing (2011)"],
    "Sisu Collection": ["sisu"],
    "M3GAN Collection": ["m3gan"],
    "28 Days/Years Later": ["28 years later", "28 days later", "28 weeks later"],
    "Zootopia Collection": ["zootopia"],
    "Running Man Collection": ["the running man"],
    "Werewolf Movies": [
        "werewolf", "wolf manor", "wolfen", "wolf (1994)",
        "an american werewolf", "dog soldiers", "ginger snaps",
        "bad moon", "the hallow", "howl (2015)", "wildling",
        "full eclipse", "the beast must die",
    ],
    "Looney Tunes & Cartoons": [
        "looney tunes", "regular show", "futurama", "scooby-doo",
    ],
    "Anime Collection": [
        "akira", "ghost in the shell", "battle angel", "golgo 13",
        "wicked city", "perfect blue", "cowboy bebop",
        "highlander: the search for vengeance", "jujutsu kaisen",
    ],
    "Martial Arts Collection": [
        "crippled", "journey to the west", "47 ronin", "yojimbo",
        "chronicles of the ghostly tribe", "mortal kombat",
        "bureau 749",
    ],
    "B-Movie Madness": [
        "catnado", "crackcoon", "shark side of the moon",
        "cocaine werewolf", "onlyfangs", "big ass spider",
        "frankie freako", "starcrash", "zone troopers",
        "the wild world of batwoman", "murdercise",
    ],
}

# --- Genre-based collections ---
GENRE_COLLECTIONS = {
    "Action Packed": "Action",
    "Horror Vault": "Horror",
    "Comedy Night": "Comedy",
    "Sci-Fi Futures": "Science Fiction",
    "Crime & Thriller": ["Crime", "Thriller"],
    "Animated Features": "Animation",
    "Western Frontier": "Western",
    "Documentary Films": "Documentary",
}

# --- Decade collections ---
DECADE_COLLECTIONS = {
    "80s Classics": (1980, 1989),
    "90s Nostalgia": (1990, 1999),
    "2000s Hits": (2000, 2009),
    "2010s Films": (2010, 2019),
    "New Releases (2020s)": (2020, 2029),
}

# --- Rating-based collections ---
RATING_COLLECTIONS = {
    "Highly Rated (8+)": 8.0,
    "Hidden Gems (7+)": 7.0,
}


def match_franchise(title, year, patterns):
    """Check if a movie matches any franchise pattern."""
    t = title.lower()
    for pattern in patterns:
        p = pattern.lower()
        # Check for year-specific patterns like "prey (2022)"
        if "(" in p:
            if p in f"{t} ({year})".lower():
                return True
        elif p in t:
            return True
    return False


def get_existing_collections():
    """Get all existing Plex collections."""
    try:
        data = plex_get(
            f"/library/sections/{PLEX['movie_section']}/collections"
        )
        collections = data.get("MediaContainer", {}).get("Metadata", [])
        return {c.get("title", ""): c for c in collections}
    except Exception:
        return {}


def create_plex_collection(name, rating_keys):
    """Create or update a collection in Plex."""
    if not rating_keys:
        return False

    # Create collection by adding the first item
    url = f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/collections"
    params = {
        "X-Plex-Token": PLEX["token"],
        "type": 1,  # movie
        "title": name,
        "smart": 0,
        "uri": f"server://{PLEX['token']}/com.plexapp.plugins.library/library/metadata/{','.join(str(k) for k in rating_keys)}",
    }

    # Use the machine ID for proper URI
    try:
        identity = plex_get("/identity")
        machine_id = identity.get("MediaContainer", {}).get("machineIdentifier", "")
    except Exception:
        machine_id = ""

    # Add items to collection using the Plex API
    # First, create with one item, then add the rest
    first_key = rating_keys[0]

    r = requests.post(
        url,
        params={
            "X-Plex-Token": PLEX["token"],
            "type": 1,
            "title": name,
            "smart": 0,
            "uri": f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{first_key}",
        },
    )

    if r.status_code not in (200, 201):
        print(f"    Failed to create collection '{name}': {r.status_code}")
        return False

    # Add remaining items
    if len(rating_keys) > 1:
        # Get the collection key
        collections = get_existing_collections()
        if name in collections:
            collection_key = collections[name].get("ratingKey", "")
            for key in rating_keys[1:]:
                requests.put(
                    f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/all",
                    params={
                        "X-Plex-Token": PLEX["token"],
                        "type": 1,
                        "id": key,
                        "collection[0].tag.tag": name,
                    },
                )

    return True


def add_to_collection_via_tag(name, rating_keys):
    """Add movies to a collection using the tag-based approach (more reliable)."""
    for key in rating_keys:
        r = requests.put(
            f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/all",
            params={
                "X-Plex-Token": PLEX["token"],
                "type": 1,
                "id": key,
                "collection[0].tag.tag": name,
            },
        )
        if r.status_code != 200:
            return False
    return True


def build_collections(dry_run=True):
    """Build all smart collections."""
    print("\n" + "=" * 60)
    print("  COLLECTION BUILDER")
    print("=" * 60)

    movies = get_plex_movies()
    existing = get_existing_collections()

    print(f"\n  {len(movies)} movies in library")
    print(f"  {len(existing)} existing collections")

    all_collections = {}

    # --- Franchise collections ---
    print("\n  --- Franchise Collections ---")
    for name, patterns in FRANCHISES.items():
        matches = []
        for m in movies:
            title = m.get("title", "")
            year = m.get("year", "")
            if match_franchise(title, year, patterns):
                matches.append({
                    "title": title,
                    "year": year,
                    "ratingKey": m.get("ratingKey"),
                })
        if len(matches) >= 2:  # Only create collection with 2+ movies
            all_collections[name] = matches
            status = "EXISTS" if name in existing else "NEW"
            print(f"  [{status}] {name} ({len(matches)} movies)")
            for m in sorted(matches, key=lambda x: x.get("year", 0)):
                print(f"    - {m['title']} ({m['year']})")

    # --- Genre collections ---
    print("\n  --- Genre Collections ---")
    for name, genre_match in GENRE_COLLECTIONS.items():
        if isinstance(genre_match, str):
            genre_match = [genre_match]
        matches = []
        for m in movies:
            movie_genres = [g.get("tag", "") for g in m.get("Genre", [])]
            if any(g in movie_genres for g in genre_match):
                matches.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", ""),
                    "ratingKey": m.get("ratingKey"),
                })
        if matches:
            all_collections[name] = matches
            status = "EXISTS" if name in existing else "NEW"
            print(f"  [{status}] {name} ({len(matches)} movies)")

    # --- Decade collections ---
    print("\n  --- Decade Collections ---")
    for name, (start_year, end_year) in DECADE_COLLECTIONS.items():
        matches = []
        for m in movies:
            year = m.get("year", 0)
            if year and start_year <= year <= end_year:
                matches.append({
                    "title": m.get("title", ""),
                    "year": year,
                    "ratingKey": m.get("ratingKey"),
                })
        if matches:
            all_collections[name] = matches
            status = "EXISTS" if name in existing else "NEW"
            print(f"  [{status}] {name} ({len(matches)} movies)")

    # --- Rating collections ---
    print("\n  --- Rating Collections ---")
    for name, min_rating in RATING_COLLECTIONS.items():
        matches = []
        for m in movies:
            rating = m.get("audienceRating") or m.get("rating") or 0
            if rating >= min_rating:
                matches.append({
                    "title": m.get("title", ""),
                    "year": m.get("year", ""),
                    "rating": rating,
                    "ratingKey": m.get("ratingKey"),
                })
        if matches:
            all_collections[name] = matches
            status = "EXISTS" if name in existing else "NEW"
            print(f"  [{status}] {name} ({len(matches)} movies)")

    # --- Summary ---
    new_collections = {k: v for k, v in all_collections.items() if k not in existing}
    print(f"\n  Total: {len(all_collections)} collections planned")
    print(f"  New: {len(new_collections)} | Already exist: {len(all_collections) - len(new_collections)}")

    if dry_run:
        print("\n  DRY RUN -- no changes made. Use --go to create collections.")
        return all_collections

    # Create new collections
    if new_collections:
        print(f"\n  Creating {len(new_collections)} new collections...")
        created = 0
        for name, matches in new_collections.items():
            keys = [m["ratingKey"] for m in matches if m.get("ratingKey")]
            if not keys:
                continue
            print(f"  Creating '{name}' ({len(keys)} movies)...", end=" ", flush=True)
            success = add_to_collection_via_tag(name, keys)
            if success:
                print("OK")
                created += 1
            else:
                print("FAILED")
        print(f"\n  Created {created}/{len(new_collections)} collections")
    else:
        print("\n  All collections already exist!")

    return all_collections


if __name__ == "__main__":
    import sys
    dry_run = "--go" not in sys.argv
    if dry_run:
        print("  (Dry run -- add --go to create collections in Plex)")
    build_collections(dry_run=dry_run)
