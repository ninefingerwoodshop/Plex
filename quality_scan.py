# Plex Media Stack - Library Quality Scanner
# Checks for missing metadata, low resolution, missing subtitles, bad posters

from api import get_plex_movies, get_plex_shows, get_plex_movie_details, plex_get


def scan_movie_quality():
    """Scan all Plex movies for quality issues."""
    print(f"\n{'='*60}")
    print("  MOVIE QUALITY SCAN")
    print(f"{'='*60}")

    movies = get_plex_movies()

    missing_summary = []
    missing_poster = []
    missing_art = []
    low_resolution = []
    no_subtitles = []
    low_bitrate = []
    no_rating = []

    for i, m in enumerate(movies):
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        label = f"{title} ({year})"

        # Missing summary/description
        if not m.get("summary"):
            missing_summary.append(label)

        # Missing poster
        if not m.get("thumb"):
            missing_poster.append(label)

        # Missing background art
        if not m.get("art"):
            missing_art.append(label)

        # No rating
        if not m.get("rating") and not m.get("audienceRating"):
            no_rating.append(label)

        # Check media details (resolution, subtitles, bitrate)
        media_list = m.get("Media", [])
        if media_list:
            media = media_list[0]
            width = media.get("width", 0)
            height = media.get("height", 0)
            bitrate = media.get("bitrate", 0)

            # Low resolution (below 1080p)
            if height < 1080 and height > 0:
                res = f"{height}p" if height else f"{width}x?"
                low_resolution.append(f"{label} -- {res}")

            # Low bitrate (below 3 Mbps for movies is suspect)
            if bitrate and bitrate < 3000:
                low_bitrate.append(f"{label} -- {bitrate} kbps")

            # Check for subtitles in parts/streams
            has_subs = False
            for part in media.get("Part", []):
                for stream in part.get("Stream", []):
                    if stream.get("streamType") == 3:  # 3 = subtitle
                        has_subs = True
                        break
                if has_subs:
                    break
            if not has_subs:
                no_subtitles.append(label)

    # --- Report ---
    if missing_summary:
        print(f"\n- Missing summary/description ({len(missing_summary)}):")
        for m in sorted(missing_summary):
            print(f"    - {m}")

    if missing_poster:
        print(f"\n-  Missing poster ({len(missing_poster)}):")
        for m in sorted(missing_poster):
            print(f"    - {m}")

    if missing_art:
        print(f"\n- Missing background art ({len(missing_art)}):")
        for m in sorted(missing_art):
            print(f"    - {m}")

    if no_rating:
        print(f"\n[?] No ratings ({len(no_rating)}):")
        for m in sorted(no_rating):
            print(f"    - {m}")

    if low_resolution:
        print(f"\n- Below 1080p ({len(low_resolution)}):")
        for m in sorted(low_resolution):
            print(f"    - {m}")

    if low_bitrate:
        print(f"\n- Low bitrate (< 3 Mbps) ({len(low_bitrate)}):")
        for m in sorted(low_bitrate):
            print(f"    - {m}")

    if no_subtitles:
        print(f"\n- No subtitles ({len(no_subtitles)}):")
        for m in sorted(no_subtitles):
            print(f"    - {m}")

    total_issues = (
        len(missing_summary) + len(missing_poster) + len(missing_art)
        + len(no_rating) + len(low_resolution) + len(low_bitrate) + len(no_subtitles)
    )
    if total_issues == 0:
        print("\n[ok] All movies look great! No quality issues found.")
    else:
        print(f"\nTotal: {total_issues} issues across {len(movies)} movies")

    return {
        "total_movies": len(movies),
        "missing_summary": missing_summary,
        "missing_poster": missing_poster,
        "missing_art": missing_art,
        "no_rating": no_rating,
        "low_resolution": low_resolution,
        "low_bitrate": low_bitrate,
        "no_subtitles": no_subtitles,
    }


def scan_show_quality():
    """Scan all Plex TV shows for quality issues."""
    print(f"\n{'='*60}")
    print("  TV SHOW QUALITY SCAN")
    print(f"{'='*60}")

    shows = get_plex_shows()

    missing_summary = []
    missing_poster = []
    missing_art = []
    no_rating = []

    for s in shows:
        title = s.get("title", "Unknown")
        year = s.get("year", "")
        label = f"{title} ({year})"

        if not s.get("summary"):
            missing_summary.append(label)
        if not s.get("thumb"):
            missing_poster.append(label)
        if not s.get("art"):
            missing_art.append(label)
        if not s.get("rating") and not s.get("audienceRating"):
            no_rating.append(label)

    # --- Report ---
    if missing_summary:
        print(f"\n- Missing summary ({len(missing_summary)}):")
        for s in sorted(missing_summary):
            print(f"    - {s}")

    if missing_poster:
        print(f"\n-  Missing poster ({len(missing_poster)}):")
        for s in sorted(missing_poster):
            print(f"    - {s}")

    if missing_art:
        print(f"\n- Missing background art ({len(missing_art)}):")
        for s in sorted(missing_art):
            print(f"    - {s}")

    if no_rating:
        print(f"\n[?] No ratings ({len(no_rating)}):")
        for s in sorted(no_rating):
            print(f"    - {s}")

    total_issues = len(missing_summary) + len(missing_poster) + len(missing_art) + len(no_rating)
    if total_issues == 0:
        print("\n[ok] All shows look great! No quality issues found.")
    else:
        print(f"\nTotal: {total_issues} issues across {len(shows)} shows")

    return {
        "total_shows": len(shows),
        "missing_summary": missing_summary,
        "missing_poster": missing_poster,
        "missing_art": missing_art,
        "no_rating": no_rating,
    }
