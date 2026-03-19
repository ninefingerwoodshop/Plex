# Plex Media Stack - TV File Audit
# Checks TV files for missing files, tiny files, samples, and name mismatches

import os
import re
from collections import defaultdict
from api import get_sonarr_series, sonarr_get


def extract_words(text):
    """Extract meaningful words from a filename."""
    t = re.sub(r"[._\-]", " ", text.lower())
    t = re.sub(r"\.(mkv|mp4|avi|m4v|ts|wmv|flv)$", "", t)
    t = re.sub(
        r"\b(x264|x265|h264|h265|aac|dts|ddp?\d*|atmos|web|dl|rip|bluray|hdtv|"
        r"repack|proper|amzn|nf|hulu|dsnp|hmax|flux|ntb|turg|ethel|draken\d*)\b",
        "", t,
    )
    t = re.sub(r"\b(1080p|720p|480p|2160p|4k)\b", "", t)
    t = re.sub(r"\bs\d+e\d+\b", "", t)
    return set(w for w in t.split() if len(w) > 1)


def tv_file_audit():
    """Audit all TV files for issues."""
    print("\n" + "=" * 60)
    print("  TV FILE AUDIT")
    print("=" * 60)

    series_list = get_sonarr_series()

    missing_files = []
    tiny_files = []
    sample_files = []
    hash_files = []
    checked = 0

    for series in series_list:
        title = series.get("title", "Unknown")
        series_id = series.get("id")

        try:
            ep_files = sonarr_get(f"/episodefile?seriesId={series_id}")
        except Exception:
            continue

        if not ep_files:
            continue

        for ef in ep_files:
            checked += 1
            rel_path = ef.get("relativePath", "")
            abs_path = ef.get("path", "")
            size = ef.get("size", 0)
            size_mb = round(size / 1024 / 1024)
            fn = os.path.basename(rel_path) if rel_path else ""

            # Missing from disk
            if abs_path and not os.path.exists(abs_path):
                missing_files.append({"show": title, "file": abs_path, "size_mb": size_mb})
                continue

            # Tiny files
            if 0 < size_mb < 20:
                tiny_files.append({"show": title, "file": rel_path, "size_mb": size_mb})

            # Sample files
            if "sample" in fn.lower():
                sample_files.append({"show": title, "file": rel_path})

            # Hash-named files
            if re.match(r"^[a-f0-9]{20,}\.(?:mkv|mp4|avi)$", fn.lower()):
                hash_files.append({"show": title, "file": rel_path, "size_mb": size_mb})

    print(f"\n  Checked {checked} episode files across {len(series_list)} shows")

    if missing_files:
        shows = defaultdict(list)
        for f in missing_files:
            shows[f["show"]].append(f)
        print(f"\n  [!!!] FILES MISSING FROM DISK ({len(missing_files)}):")
        for show, files in sorted(shows.items()):
            print(f"    {show} -- {len(files)} missing files")
            for f in files[:3]:
                print(f"      {f['file']}")
            if len(files) > 3:
                print(f"      ... and {len(files) - 3} more")

    if tiny_files:
        print(f"\n  [!!] SUSPICIOUSLY SMALL FILES < 20 MB ({len(tiny_files)}):")
        for f in sorted(tiny_files, key=lambda x: x["size_mb"]):
            print(f"    {f['show']} -- {f['file']} ({f['size_mb']} MB)")

    if sample_files:
        print(f"\n  [!] SAMPLE FILES ({len(sample_files)}):")
        for f in sample_files:
            print(f"    {f['show']} -- {f['file']}")

    if hash_files:
        print(f"\n  [!] HASH-NAMED FILES ({len(hash_files)}):")
        print("    These weren't renamed properly by NZBGet/Sonarr:")
        for f in hash_files:
            print(f"    {f['show']} -- {f['file']} ({f['size_mb']} MB)")

    total = len(missing_files) + len(tiny_files) + len(sample_files) + len(hash_files)
    if total == 0:
        print("\n  All clear! No issues found.")
    else:
        print(f"\n  Summary: {len(missing_files)} missing, {len(tiny_files)} tiny, "
              f"{len(sample_files)} samples, {len(hash_files)} hash-named")

    return {
        "checked": checked,
        "missing": missing_files,
        "tiny": tiny_files,
        "samples": sample_files,
        "hash_files": hash_files,
    }


if __name__ == "__main__":
    tv_file_audit()
