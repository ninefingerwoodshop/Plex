# Plex Media Stack - Sync Audit
# Compares Plex libraries against Radarr/Sonarr to find discrepancies

from api import get_plex_movies, get_plex_shows, get_radarr_movies, get_sonarr_series


def normalize(title):
    """Normalize a title for fuzzy matching."""
    import re
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def movie_sync_audit():
    """Compare Plex movies vs Radarr movies."""
    print("\n" + "=" * 60)
    print("  MOVIE SYNC AUDIT -- Plex vs Radarr")
    print("=" * 60)

    plex_movies = get_plex_movies()
    radarr_movies = get_radarr_movies()

    # Build lookup sets using TMDB IDs where possible, fall back to title matching
    plex_by_tmdb = {}
    plex_by_title = {}
    for m in plex_movies:
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        key = f"{normalize(title)}|{year}"
        plex_by_title[key] = m
        # Check for TMDB guid
        for guid in m.get("Guid", []):
            gid = guid.get("id", "")
            if gid.startswith("tmdb://"):
                plex_by_tmdb[gid.replace("tmdb://", "")] = m

    radarr_by_tmdb = {}
    radarr_by_title = {}
    for m in radarr_movies:
        tmdb_id = str(m.get("tmdbId", ""))
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        key = f"{normalize(title)}|{year}"
        radarr_by_tmdb[tmdb_id] = m
        radarr_by_title[key] = m

    # --- In Radarr but NOT in Plex ---
    in_radarr_not_plex = []
    for m in radarr_movies:
        tmdb_id = str(m.get("tmdbId", ""))
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        key = f"{normalize(title)}|{year}"
        found = tmdb_id in plex_by_tmdb or key in plex_by_title
        if not found:
            has_file = m.get("hasFile", False)
            monitored = m.get("monitored", False)
            in_radarr_not_plex.append({
                "title": title,
                "year": year,
                "hasFile": has_file,
                "monitored": monitored,
                "tmdbId": tmdb_id,
            })

    # --- In Plex but NOT in Radarr ---
    in_plex_not_radarr = []
    for m in plex_movies:
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        key = f"{normalize(title)}|{year}"
        found_tmdb = False
        for guid in m.get("Guid", []):
            gid = guid.get("id", "")
            if gid.startswith("tmdb://"):
                tid = gid.replace("tmdb://", "")
                if tid in radarr_by_tmdb:
                    found_tmdb = True
                    break
        found_title = key in radarr_by_title
        if not found_tmdb and not found_title:
            in_plex_not_radarr.append({
                "title": title,
                "year": year,
            })

    # --- Report ---
    if in_radarr_not_plex:
        downloaded = [m for m in in_radarr_not_plex if m["hasFile"]]
        missing = [m for m in in_radarr_not_plex if not m["hasFile"]]

        if downloaded:
            print(f"\n[!] In Radarr (downloaded) but NOT in Plex ({len(downloaded)}):")
            print("  These files exist on disk but Plex hasn't picked them up.")
            for m in sorted(downloaded, key=lambda x: x["title"]):
                print(f"    - {m['title']} ({m['year']})")

        if missing:
            monitored = [m for m in missing if m["monitored"]]
            unmonitored = [m for m in missing if not m["monitored"]]
            if monitored:
                print(f"\n- In Radarr (monitored, no file) -- waiting for download ({len(monitored)}):")
                for m in sorted(monitored, key=lambda x: x["title"]):
                    print(f"    - {m['title']} ({m['year']})")
            if unmonitored:
                print(f"\n- In Radarr (unmonitored, no file) -- not actively seeking ({len(unmonitored)}):")
                for m in sorted(unmonitored, key=lambda x: x["title"]):
                    print(f"    - {m['title']} ({m['year']})")
    else:
        print("\n[ok] All Radarr movies are in Plex!")

    if in_plex_not_radarr:
        print(f"\n- In Plex but NOT in Radarr ({len(in_plex_not_radarr)}):")
        print("  These won't get upgrades or monitoring.")
        for m in sorted(in_plex_not_radarr, key=lambda x: x["title"]):
            print(f"    - {m['title']} ({m['year']})")
    else:
        print("\n[ok] All Plex movies are tracked in Radarr!")

    print(f"\nSummary: {len(plex_movies)} in Plex, {len(radarr_movies)} in Radarr")
    return {
        "plex_count": len(plex_movies),
        "radarr_count": len(radarr_movies),
        "in_radarr_not_plex": in_radarr_not_plex,
        "in_plex_not_radarr": in_plex_not_radarr,
    }


def tv_sync_audit():
    """Compare Plex TV shows vs Sonarr series."""
    print("\n" + "=" * 60)
    print("  TV SHOW SYNC AUDIT -- Plex vs Sonarr")
    print("=" * 60)

    plex_shows = get_plex_shows()
    sonarr_series = get_sonarr_series()

    # Build lookup sets using TVDB IDs where possible
    plex_by_tvdb = {}
    plex_by_title = {}
    for s in plex_shows:
        title = s.get("title", "Unknown")
        plex_by_title[normalize(title)] = s
        for guid in s.get("Guid", []):
            gid = guid.get("id", "")
            if gid.startswith("tvdb://"):
                plex_by_tvdb[gid.replace("tvdb://", "")] = s

    sonarr_by_tvdb = {}
    sonarr_by_title = {}
    for s in sonarr_series:
        tvdb_id = str(s.get("tvdbId", ""))
        title = s.get("title", "Unknown")
        sonarr_by_tvdb[tvdb_id] = s
        sonarr_by_title[normalize(title)] = s

    # --- In Sonarr but NOT in Plex ---
    in_sonarr_not_plex = []
    for s in sonarr_series:
        tvdb_id = str(s.get("tvdbId", ""))
        title = s.get("title", "Unknown")
        found = tvdb_id in plex_by_tvdb or normalize(title) in plex_by_title
        if not found:
            ep_count = s.get("statistics", {}).get("episodeFileCount", 0)
            monitored = s.get("monitored", False)
            in_sonarr_not_plex.append({
                "title": title,
                "year": s.get("year", ""),
                "episodeFileCount": ep_count,
                "monitored": monitored,
            })

    # --- In Plex but NOT in Sonarr ---
    in_plex_not_sonarr = []
    for s in plex_shows:
        title = s.get("title", "Unknown")
        found_tvdb = False
        for guid in s.get("Guid", []):
            gid = guid.get("id", "")
            if gid.startswith("tvdb://"):
                tid = gid.replace("tvdb://", "")
                if tid in sonarr_by_tvdb:
                    found_tvdb = True
                    break
        found_title = normalize(title) in sonarr_by_title
        if not found_tvdb and not found_title:
            in_plex_not_sonarr.append({
                "title": title,
                "year": s.get("year", ""),
            })

    # --- Report ---
    if in_sonarr_not_plex:
        with_files = [s for s in in_sonarr_not_plex if s["episodeFileCount"] > 0]
        no_files = [s for s in in_sonarr_not_plex if s["episodeFileCount"] == 0]

        if with_files:
            print(f"\n[!] In Sonarr (has episodes) but NOT in Plex ({len(with_files)}):")
            for s in sorted(with_files, key=lambda x: x["title"]):
                print(f"    - {s['title']} ({s['year']}) -- {s['episodeFileCount']} episodes on disk")

        if no_files:
            monitored = [s for s in no_files if s["monitored"]]
            unmonitored = [s for s in no_files if not s["monitored"]]
            if monitored:
                print(f"\n- In Sonarr (monitored, no episodes yet) ({len(monitored)}):")
                for s in sorted(monitored, key=lambda x: x["title"]):
                    print(f"    - {s['title']} ({s['year']})")
            if unmonitored:
                print(f"\n- In Sonarr (unmonitored, no episodes) ({len(unmonitored)}):")
                for s in sorted(unmonitored, key=lambda x: x["title"]):
                    print(f"    - {s['title']} ({s['year']})")
    else:
        print("\n[ok] All Sonarr shows are in Plex!")

    if in_plex_not_sonarr:
        print(f"\n- In Plex but NOT in Sonarr ({len(in_plex_not_sonarr)}):")
        print("  These won't get new episodes automatically.")
        for s in sorted(in_plex_not_sonarr, key=lambda x: x["title"]):
            print(f"    - {s['title']} ({s['year']})")
    else:
        print("\n[ok] All Plex shows are tracked in Sonarr!")

    print(f"\nSummary: {len(plex_shows)} in Plex, {len(sonarr_series)} in Sonarr")
    return {
        "plex_count": len(plex_shows),
        "sonarr_count": len(sonarr_series),
        "in_sonarr_not_plex": in_sonarr_not_plex,
        "in_plex_not_sonarr": in_plex_not_sonarr,
    }
