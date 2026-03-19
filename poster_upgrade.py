# Plex Media Stack - Smart Poster Upgrader
# Refreshes metadata and posters from Plex's own agents (TMDB/TVDB)

import sys
import time
import requests
from api import get_plex_movies, get_plex_shows, plex_get
from config import PLEX


def find_bad_posters():
    """Find movies with missing or potentially low-quality posters."""
    movies = get_plex_movies()
    issues = []

    for m in movies:
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        rating_key = m.get("ratingKey")
        label = f"{title} ({year})"

        has_thumb = bool(m.get("thumb"))
        has_art = bool(m.get("art"))
        has_summary = bool(m.get("summary"))

        if not has_thumb or not has_art or not has_summary:
            issues.append({
                "title": title,
                "year": year,
                "ratingKey": rating_key,
                "label": label,
                "missing_poster": not has_thumb,
                "missing_art": not has_art,
                "missing_summary": not has_summary,
            })

    return issues


def refresh_metadata(rating_key):
    """Trigger a metadata refresh for a specific item."""
    url = f"{PLEX['url']}/library/metadata/{rating_key}/refresh"
    r = requests.put(url, params={"X-Plex-Token": PLEX["token"]})
    return r.status_code == 200


def poster_upgrade_report(fix=False):
    """Report and optionally fix poster/metadata issues."""
    print("\n" + "=" * 60)
    print("  POSTER & METADATA UPGRADER")
    print("=" * 60)

    issues = find_bad_posters()

    missing_poster = [i for i in issues if i["missing_poster"]]
    missing_art = [i for i in issues if i["missing_art"]]
    missing_summary = [i for i in issues if i["missing_summary"]]

    print(f"\n  {len(issues)} movies with metadata issues:")
    print(f"    {len(missing_poster)} missing posters")
    print(f"    {len(missing_art)} missing background art")
    print(f"    {len(missing_summary)} missing summaries")

    if missing_poster:
        print(f"\n  Missing posters ({len(missing_poster)}):")
        for i in sorted(missing_poster, key=lambda x: x["label"]):
            print(f"    - {i['label']}")

    if missing_summary:
        print(f"\n  Missing summaries ({len(missing_summary)}):")
        for i in sorted(missing_summary, key=lambda x: x["label"]):
            print(f"    - {i['label']}")

    if not fix:
        if issues:
            print(f"\n  Use --go to trigger metadata refresh for {len(issues)} items.")
        return issues

    # Refresh metadata for all items with issues
    print(f"\n  Refreshing metadata for {len(issues)} items...")
    refreshed = 0
    for i, item in enumerate(sorted(issues, key=lambda x: x["label"])):
        print(f"  [{i+1}/{len(issues)}] {item['label']}...", end=" ", flush=True)
        if refresh_metadata(item["ratingKey"]):
            print("OK")
            refreshed += 1
        else:
            print("FAILED")
        # Small delay to not overwhelm Plex
        time.sleep(0.5)

    print(f"\n  Refreshed {refreshed}/{len(issues)} items.")
    print("  Plex will re-download posters and metadata in the background.")

    return issues


if __name__ == "__main__":
    fix = "--go" in sys.argv
    if not fix:
        print("  (Dry run -- add --go to trigger metadata refresh)")
    poster_upgrade_report(fix=fix)
