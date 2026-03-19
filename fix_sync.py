# Plex Media Stack - Sync Fixer
# Triggers Plex library scan and identifies mismatch causes

from api import plex_get, get_plex_movies, get_radarr_movies
from config import PLEX
import requests
import re


def normalize(t):
    t = t.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def trigger_plex_scan(section_id=None):
    """Trigger a Plex library scan."""
    if section_id:
        url = f"{PLEX['url']}/library/sections/{section_id}/refresh"
    else:
        url = f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/refresh"
    r = requests.get(url, params={"X-Plex-Token": PLEX["token"]})
    r.raise_for_status()
    print(f"  Triggered scan for section {section_id or PLEX['movie_section']}")


def diagnose_mismatches():
    """Find and explain why Radarr movies aren't matching Plex entries."""
    print("\n" + "=" * 60)
    print("  SYNC MISMATCH DIAGNOSIS")
    print("=" * 60)

    plex = get_plex_movies()
    radarr = get_radarr_movies()

    # Build Plex lookup
    plex_by_norm = {}
    plex_by_tmdb = {}
    for m in plex:
        title = m.get("title", "")
        year = m.get("year", "")
        norm = normalize(title)
        plex_by_norm[norm] = m
        plex_by_norm[f"{norm}|{year}"] = m
        for g in m.get("Guid", []):
            gid = g.get("id", "")
            if gid.startswith("tmdb://"):
                plex_by_tmdb[gid.replace("tmdb://", "")] = m

    title_mismatches = []
    year_mismatches = []
    genuinely_missing = []

    for m in radarr:
        if not m.get("hasFile"):
            continue
        tmdb = str(m.get("tmdbId", ""))
        title = m.get("title", "")
        year = m.get("year", "")
        key = f"{normalize(title)}|{year}"

        # Already matched
        if tmdb in plex_by_tmdb or key in plex_by_norm:
            continue

        # Check for fuzzy title match (ignoring year)
        norm = normalize(title)
        if norm in plex_by_norm:
            plex_entry = plex_by_norm[norm]
            plex_year = plex_entry.get("year", "")
            year_mismatches.append({
                "radarr_title": title,
                "radarr_year": year,
                "plex_title": plex_entry.get("title", ""),
                "plex_year": plex_year,
                "path": m.get("path", ""),
            })
            continue

        # Check for partial matches
        found = False
        for pnorm, pentry in plex_by_norm.items():
            if "|" in pnorm:
                continue
            # Check if one contains the other or they share significant words
            r_words = set(norm.split())
            p_words = set(pnorm.split())
            common = r_words & p_words
            if len(common) >= 2 and len(common) / max(len(r_words), len(p_words)) > 0.5:
                title_mismatches.append({
                    "radarr_title": title,
                    "radarr_year": year,
                    "plex_title": pentry.get("title", ""),
                    "plex_year": pentry.get("year", ""),
                    "path": m.get("path", ""),
                })
                found = True
                break

        if not found:
            genuinely_missing.append({
                "title": title,
                "year": year,
                "path": m.get("path", ""),
                "file": m.get("movieFile", {}).get("relativePath", ""),
            })

    if title_mismatches:
        print(f"\n[TITLE MISMATCH] Same movie, different naming ({len(title_mismatches)}):")
        for m in title_mismatches:
            print(f"  Radarr: {m['radarr_title']} ({m['radarr_year']})")
            print(f"  Plex:   {m['plex_title']} ({m['plex_year']})")
            print(f"  Path:   {m['path']}")
            print()

    if year_mismatches:
        print(f"\n[YEAR MISMATCH] Same title, different year ({len(year_mismatches)}):")
        for m in year_mismatches:
            print(f"  Radarr: {m['radarr_title']} ({m['radarr_year']})")
            print(f"  Plex:   {m['plex_title']} ({m['plex_year']})")
            print(f"  Path:   {m['path']}")
            print()

    if genuinely_missing:
        print(f"\n[MISSING FROM PLEX] Not found at all ({len(genuinely_missing)}):")
        print("  These files exist but Plex hasn't scanned them.")
        for m in genuinely_missing:
            print(f"  - {m['title']} ({m['year']})")
            print(f"    Path: {m['path']}")
            print(f"    File: {m['file']}")
            print()
        print("  Triggering Plex library scan to pick them up...")
        trigger_plex_scan(PLEX["movie_section"])

    if not title_mismatches and not year_mismatches and not genuinely_missing:
        print("\n  All synced! No mismatches found.")

    return {
        "title_mismatches": title_mismatches,
        "year_mismatches": year_mismatches,
        "genuinely_missing": genuinely_missing,
    }


if __name__ == "__main__":
    diagnose_mismatches()
