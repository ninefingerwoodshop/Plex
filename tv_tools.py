# Plex Media Stack - TV Show Tools
# Collection builder, binge-ready report, next episode tracker

import sys
from collections import Counter
from api import get_plex_shows, get_plex_show_seasons, get_sonarr_series, plex_get, sonarr_get
from config import PLEX
import requests


def add_show_to_collection(name, rating_key):
    """Add a TV show to a collection using tag approach."""
    r = requests.put(
        f"{PLEX['url']}/library/sections/{PLEX['tv_section']}/all",
        params={
            "X-Plex-Token": PLEX["token"],
            "type": 2,  # TV show
            "id": rating_key,
            "collection[0].tag.tag": name,
        },
    )
    return r.status_code == 200


def tv_collection_builder(dry_run=True):
    """Build collections for TV shows."""
    print("\n" + "=" * 60)
    print("  TV COLLECTION BUILDER")
    print("=" * 60)

    shows = get_plex_shows()
    print(f"\n  {len(shows)} shows in library")

    # Genre collections
    genre_groups = {
        "Sci-Fi Shows": "Science Fiction",
        "Comedy Shows": "Comedy",
        "Drama Shows": "Drama",
        "Horror Shows": "Horror",
        "Action Shows": "Action",
        "Animated Shows": "Animation",
        "Crime Shows": "Crime",
        "Documentary Shows": "Documentary",
    }

    # Custom franchise groupings
    franchise_patterns = {
        "Star Trek Universe": ["star trek"],
        "Marvel TV": ["marvel", "daredevil", "jessica jones", "luke cage",
                       "iron fist", "punisher", "agents of s.h.i.e.l.d"],
        "DC Universe": ["batman", "superman", "gotham", "doom patrol",
                         "titans", "swamp thing"],
        "Adult Animation": ["rick and morty", "futurama", "archer",
                            "bob's burgers", "simpsons", "south park",
                            "family guy", "king of the hill", "metalocalypse",
                            "looney tunes", "space ghost"],
    }

    all_collections = {}

    # Genre collections
    print("\n  --- Genre Collections ---")
    for name, genre in genre_groups.items():
        matches = []
        for s in shows:
            show_genres = [g.get("tag", "") for g in s.get("Genre", [])]
            if genre in show_genres:
                matches.append(s)
        if len(matches) >= 2:
            all_collections[name] = matches
            print(f"  [NEW] {name} ({len(matches)} shows)")

    # Franchise collections
    print("\n  --- Franchise Collections ---")
    for name, patterns in franchise_patterns.items():
        matches = []
        for s in shows:
            title = s.get("title", "").lower()
            if any(p in title for p in patterns):
                matches.append(s)
        if len(matches) >= 2:
            all_collections[name] = matches
            print(f"  [NEW] {name} ({len(matches)} shows)")
            for s in matches:
                print(f"    - {s.get('title', '')} ({s.get('year', '')})")

    # Decade collections
    print("\n  --- Decade Collections ---")
    decade_groups = {}
    for s in shows:
        year = s.get("year", 0)
        if year:
            decade = f"{(year // 10) * 10}s Shows"
            if decade not in decade_groups:
                decade_groups[decade] = []
            decade_groups[decade].append(s)

    for name, matches in sorted(decade_groups.items()):
        if len(matches) >= 2:
            all_collections[name] = matches
            print(f"  [NEW] {name} ({len(matches)} shows)")

    print(f"\n  Total: {len(all_collections)} TV collections planned")

    if dry_run:
        print("  DRY RUN -- use --go to create.")
        return all_collections

    # Create collections
    print(f"\n  Creating {len(all_collections)} collections...")
    created = 0
    for name, matches in all_collections.items():
        keys = [s.get("ratingKey") for s in matches if s.get("ratingKey")]
        if not keys:
            continue
        print(f"  Creating '{name}'...", end=" ", flush=True)
        success = all(add_show_to_collection(name, k) for k in keys)
        if success:
            print("OK")
            created += 1
        else:
            print("FAILED")

    print(f"\n  Created {created}/{len(all_collections)} collections")
    return all_collections


def binge_ready_report():
    """Find shows that are fully complete and ready to binge."""
    print("\n" + "=" * 60)
    print("  BINGE-READY REPORT")
    print("=" * 60)

    sonarr = get_sonarr_series()

    complete = []
    airing = []
    ended_incomplete = []

    for s in sonarr:
        title = s.get("title", "")
        status = s.get("status", "")
        stats = s.get("statistics", {})
        total_eps = stats.get("episodeCount", 0)
        have_eps = stats.get("episodeFileCount", 0)
        pct = stats.get("percentOfEpisodes", 0)
        seasons = stats.get("seasonCount", 0)

        entry = {
            "title": title,
            "year": s.get("year", ""),
            "status": status,
            "seasons": seasons,
            "totalEpisodes": total_eps,
            "haveEpisodes": have_eps,
            "percent": pct,
        }

        if total_eps > 0 and have_eps >= total_eps:
            if status == "ended":
                complete.append(entry)
            else:
                airing.append(entry)
        elif status == "ended" and total_eps > 0:
            ended_incomplete.append(entry)
        elif status == "continuing" and have_eps > 0:
            airing.append(entry)

    if complete:
        print(f"\n  [BINGE READY] Complete series -- all episodes available ({len(complete)}):")
        for s in sorted(complete, key=lambda x: x["title"]):
            print(f"    - {s['title']} ({s['year']}) -- {s['seasons']} seasons, {s['totalEpisodes']} episodes")

    if airing:
        print(f"\n  [STILL AIRING] In progress ({len(airing)}):")
        for s in sorted(airing, key=lambda x: x["title"]):
            status_label = "continuing" if s["status"] == "continuing" else s["status"]
            print(f"    - {s['title']} ({s['year']}) -- {s['haveEpisodes']}/{s['totalEpisodes']} eps ({s['percent']:.0f}%) [{status_label}]")

    if ended_incomplete:
        print(f"\n  [ENDED BUT INCOMPLETE] Missing episodes ({len(ended_incomplete)}):")
        for s in sorted(ended_incomplete, key=lambda x: x["percent"]):
            missing = s["totalEpisodes"] - s["haveEpisodes"]
            print(f"    - {s['title']} ({s['year']}) -- {s['haveEpisodes']}/{s['totalEpisodes']} eps ({missing} missing)")

    return {"complete": complete, "airing": airing, "ended_incomplete": ended_incomplete}


def upcoming_episodes():
    """Check Sonarr for upcoming episodes this week."""
    print("\n" + "=" * 60)
    print("  UPCOMING EPISODES")
    print("=" * 60)

    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    week_out = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        calendar = sonarr_get(f"/calendar?start={today}&end={week_out}")
    except Exception:
        calendar = []

    if not calendar:
        print("\n  No episodes airing in the next 7 days.")
        return []

    print(f"\n  {len(calendar)} episodes airing in the next 7 days:\n")
    for ep in sorted(calendar, key=lambda x: x.get("airDateUtc", "")):
        series_title = ep.get("series", {}).get("title", "") or ep.get("seriesTitle", "Unknown")
        season = ep.get("seasonNumber", 0)
        episode = ep.get("episodeNumber", 0)
        title = ep.get("title", "TBA")
        air_date = ep.get("airDateUtc", "")[:10]
        has_file = ep.get("hasFile", False)
        status = "DOWNLOADED" if has_file else "upcoming"
        print(f"  {air_date}  {series_title} S{season:02d}E{episode:02d} \"{title}\" [{status}]")

    return calendar


if __name__ == "__main__":
    dry_run = "--go" not in sys.argv

    if "--binge" in sys.argv:
        binge_ready_report()
    elif "--upcoming" in sys.argv:
        upcoming_episodes()
    else:
        tv_collection_builder(dry_run=dry_run)
        binge_ready_report()
        upcoming_episodes()
