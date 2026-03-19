# Plex Media Stack - Missing Episodes Report
# Uses Sonarr to find shows with missing or incomplete seasons

from api import get_sonarr_series, sonarr_get


def missing_episodes_report():
    """Find shows with missing episodes in Sonarr."""
    print(f"\n{'='*60}")
    print("  MISSING EPISODES REPORT (from Sonarr)")
    print(f"{'='*60}")

    series_list = get_sonarr_series()

    shows_with_gaps = []
    incomplete_seasons = []
    fully_missing_seasons = []

    for series in series_list:
        title = series.get("title", "Unknown")
        monitored = series.get("monitored", False)
        stats = series.get("statistics", {})
        total_eps = stats.get("episodeCount", 0)       # total aired episodes
        file_count = stats.get("episodeFileCount", 0)   # episodes we have
        pct = stats.get("percentOfEpisodes", 0)

        if total_eps == 0:
            continue  # no aired episodes yet

        missing = total_eps - file_count
        if missing <= 0:
            continue  # fully complete

        # Get season breakdown
        seasons = series.get("seasons", [])
        show_entry = {
            "title": title,
            "monitored": monitored,
            "totalEpisodes": total_eps,
            "haveEpisodes": file_count,
            "missing": missing,
            "percent": pct,
            "seasons": [],
        }

        for season in seasons:
            s_num = season.get("seasonNumber", 0)
            if s_num == 0:
                continue  # skip specials
            s_stats = season.get("statistics", {})
            s_total = s_stats.get("episodeCount", 0)    # aired in this season
            s_have = s_stats.get("episodeFileCount", 0)
            if s_total == 0:
                continue

            s_missing = s_total - s_have
            if s_missing > 0:
                season_info = {
                    "season": s_num,
                    "total": s_total,
                    "have": s_have,
                    "missing": s_missing,
                }
                show_entry["seasons"].append(season_info)

                if s_have == 0:
                    fully_missing_seasons.append(f"{title} -- Season {s_num} ({s_total} episodes)")
                else:
                    incomplete_seasons.append(
                        f"{title} -- Season {s_num}: have {s_have}/{s_total} "
                        f"(missing {s_missing})"
                    )

        if show_entry["seasons"]:
            shows_with_gaps.append(show_entry)

    # --- Report ---
    if fully_missing_seasons:
        print(f"\n- Fully missing seasons ({len(fully_missing_seasons)}):")
        for s in sorted(fully_missing_seasons):
            print(f"    - {s}")

    if incomplete_seasons:
        print(f"\n- Incomplete seasons ({len(incomplete_seasons)}):")
        for s in sorted(incomplete_seasons):
            print(f"    - {s}")

    # Show-level summary for shows with significant gaps
    big_gaps = [s for s in shows_with_gaps if s["missing"] >= 10]
    if big_gaps:
        print(f"\n- Shows with 10+ missing episodes:")
        for s in sorted(big_gaps, key=lambda x: x["missing"], reverse=True):
            status = "monitored" if s["monitored"] else "unmonitored"
            print(
                f"    - {s['title']} -- {s['haveEpisodes']}/{s['totalEpisodes']} "
                f"({s['missing']} missing, {s['percent']:.0f}% complete, {status})"
            )

    if not shows_with_gaps:
        print("\n[ok] All monitored shows are complete! No missing episodes.")
    else:
        total_missing = sum(s["missing"] for s in shows_with_gaps)
        print(
            f"\nSummary: {len(shows_with_gaps)} shows with gaps, "
            f"{total_missing} total missing episodes"
        )

    return {
        "shows_with_gaps": len(shows_with_gaps),
        "fully_missing_seasons": fully_missing_seasons,
        "incomplete_seasons": incomplete_seasons,
        "details": shows_with_gaps,
    }
