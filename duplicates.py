# Plex Media Stack - Duplicate Finder
# Finds movies that exist in multiple qualities or on multiple drives

from api import get_plex_movies
from collections import defaultdict


def find_duplicates():
    """Find duplicate movies in Plex (same title on different drives or qualities)."""
    print("\n" + "=" * 60)
    print("  DUPLICATE FINDER")
    print("=" * 60)

    movies = get_plex_movies()

    # Group by normalized title+year
    by_title = defaultdict(list)
    for m in movies:
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        key = f"{title}|{year}"

        for media in m.get("Media", []):
            for part in media.get("Part", []):
                file_path = part.get("file", "")
                size_mb = round(part.get("size", 0) / 1024 / 1024) if part.get("size") else 0
                by_title[key].append({
                    "title": title,
                    "year": year,
                    "file": file_path,
                    "width": media.get("width", 0),
                    "height": media.get("height", 0),
                    "bitrate": media.get("bitrate", 0),
                    "videoResolution": media.get("videoResolution", "?"),
                    "container": media.get("container", "?"),
                    "size_mb": size_mb,
                    "drive": file_path[:3] if file_path else "?",
                })

    # Find entries with multiple files
    duplicates = {k: v for k, v in by_title.items() if len(v) > 1}

    if not duplicates:
        print("\n  No duplicates found!")
        return {"duplicates": []}

    total_wasted = 0
    print(f"\n  Found {len(duplicates)} movies with multiple copies:\n")

    for key, copies in sorted(duplicates.items()):
        title = copies[0]["title"]
        year = copies[0]["year"]
        print(f"  {title} ({year}) -- {len(copies)} copies:")

        # Sort by resolution (highest first)
        copies_sorted = sorted(copies, key=lambda x: x["height"], reverse=True)
        best = copies_sorted[0]

        for i, c in enumerate(copies_sorted):
            marker = " [BEST]" if i == 0 and len(copies_sorted) > 1 else ""
            res = f"{c['height']}p" if c["height"] else "?"
            print(f"    {c['drive']} {res} {c['bitrate']}kbps {c['size_mb']}MB{marker}")
            print(f"      {c['file']}")

            if i > 0:
                total_wasted += c["size_mb"]
        print()

    print(f"  Total potential space savings: {total_wasted / 1024:.1f} GB")
    print(f"  (by removing lower-quality duplicates)")

    return {"duplicates": duplicates, "wasted_mb": total_wasted}


if __name__ == "__main__":
    find_duplicates()
