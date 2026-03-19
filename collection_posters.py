"""Automated Collection Poster Management for Plex.

Fetches collection/franchise artwork from TMDb, generates fallback posters
with Pillow, and uploads them to Plex collections via the Plex API.
"""

import os
import io
import re
import logging
import requests
from api import (
    plex_get, plex_post, plex_put,
    tmdb_get, tmdb_collection, tmdb_image_url, tmdb_search_movie,
    get_plex_movies,
)
from config import PLEX, TMDB

log = logging.getLogger(__name__)

# Attempt to import Pillow; poster generation degrades gracefully without it.
try:
    from PIL import Image, ImageDraw, ImageFont

    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False
    log.warning("Pillow is not installed — fallback poster generation will be skipped.")


# ---------------------------------------------------------------------------
# Plex helpers
# ---------------------------------------------------------------------------

def get_plex_collections():
    """List all collections from the Plex movie library (section 1).

    Returns a list of dicts with keys: key, title, thumb.
    """
    section = PLEX.get("movie_section", 1)
    try:
        data = plex_get(
            f"/library/sections/{section}/collections",
            {"X-Plex-Container-Size": 500},
        )
    except requests.HTTPError:
        # Fallback: some Plex versions use a different endpoint
        data = plex_get(
            f"/library/sections/{section}/all",
            {"type": 18, "X-Plex-Container-Size": 500},
        )

    container = data.get("MediaContainer", {})
    metadata = container.get("Metadata", [])

    collections = []
    for item in metadata:
        rating_key = item.get("ratingKey", "")
        thumb = item.get("thumb")
        # Build a full thumb URL if it's a relative path
        if thumb and not thumb.startswith("http"):
            thumb = f"{PLEX['url']}{thumb}?X-Plex-Token={PLEX['token']}"
        collections.append({
            "key": rating_key,
            "title": item.get("title", ""),
            "thumb": thumb,
        })
    return collections


# ---------------------------------------------------------------------------
# TMDb artwork lookup
# ---------------------------------------------------------------------------

def _search_tmdb_collection(query):
    """Search TMDb /search/collection for *query*, return first result or None."""
    if not TMDB.get("api_key"):
        return None
    try:
        params = {"query": query, "api_key": TMDB["api_key"]}
        resp = requests.get(
            f"{TMDB['base_url']}/search/collection",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except Exception as exc:
        log.debug("TMDb collection search failed for %r: %s", query, exc)
        return None


def find_tmdb_collection_art(collection_name):
    """Search TMDb for artwork matching *collection_name*.

    Tries several query strategies:
      1. Exact name
      2. Name with " Collection" appended (if not already present)
      3. Name with " Collection" stripped

    Returns a dict with ``poster_url`` and ``backdrop_url`` (may be empty
    strings if nothing was found).
    """
    if not TMDB.get("api_key"):
        log.warning("No TMDb API key configured — skipping artwork lookup.")
        return {"poster_url": "", "backdrop_url": ""}

    queries = [collection_name]
    # If the name doesn't already end with "Collection", try adding it
    if not re.search(r"\bcollection\b", collection_name, re.IGNORECASE):
        queries.append(f"{collection_name} Collection")
    # If the name ends with "Collection", also try without it
    stripped = re.sub(r"\s+collection\s*$", "", collection_name, flags=re.IGNORECASE).strip()
    if stripped and stripped != collection_name:
        queries.append(stripped)

    for query in queries:
        result = _search_tmdb_collection(query)
        if result:
            poster_path = result.get("poster_path", "")
            backdrop_path = result.get("backdrop_path", "")

            # Optionally fetch full collection details for higher-res images
            coll_id = result.get("id")
            if coll_id:
                try:
                    details = tmdb_collection(coll_id)
                    poster_path = details.get("poster_path", poster_path) or poster_path
                    backdrop_path = details.get("backdrop_path", backdrop_path) or backdrop_path
                except Exception:
                    pass

            return {
                "poster_url": tmdb_image_url(poster_path, "w500") if poster_path else "",
                "backdrop_url": tmdb_image_url(backdrop_path, "w1280") if backdrop_path else "",
            }

    # Last resort: search for a movie with the collection name and check if it
    # belongs to a TMDb collection.
    try:
        movie_results = tmdb_search_movie(stripped or collection_name)
        for movie in movie_results.get("results", [])[:5]:
            movie_id = movie.get("id")
            if not movie_id:
                continue
            detail = tmdb_get(f"/movie/{movie_id}")
            belongs = detail.get("belongs_to_collection")
            if belongs:
                coll_id = belongs["id"]
                coll = tmdb_collection(coll_id)
                pp = coll.get("poster_path", "")
                bp = coll.get("backdrop_path", "")
                return {
                    "poster_url": tmdb_image_url(pp, "w500") if pp else "",
                    "backdrop_url": tmdb_image_url(bp, "w1280") if bp else "",
                }
    except Exception as exc:
        log.debug("Fallback movie-collection search failed: %s", exc)

    return {"poster_url": "", "backdrop_url": ""}


# ---------------------------------------------------------------------------
# Poster generation (Pillow)
# ---------------------------------------------------------------------------

def _wrap_text(draw, text, font, max_width):
    """Word-wrap *text* so each line fits within *max_width* pixels."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def generate_poster(title, output_path=None):
    """Generate a collection poster image with a dark gradient background.

    The poster is 1000 x 1500 px (2:3 ratio) with:
      - A vertical gradient from #1a1a2e (top) to #0f3460 (bottom)
      - The collection title rendered in white/gold text, centred
      - A subtle gold border

    Parameters
    ----------
    title : str
        The collection / franchise name to render.
    output_path : str, optional
        If given, the image is saved to this path *and* the bytes are returned.

    Returns
    -------
    bytes or None
        PNG image data, or ``None`` if Pillow is not available.
    """
    if not _HAS_PILLOW:
        log.warning("Pillow not installed — cannot generate poster for %r.", title)
        return None

    width, height = 1000, 1500
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # --- gradient background ---
    top_color = (26, 26, 46)       # #1a1a2e
    bottom_color = (15, 52, 96)    # #0f3460
    for y in range(height):
        ratio = y / height
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # --- subtle gold border ---
    border_color = (212, 175, 55)  # gold
    border_width = 4
    for i in range(border_width):
        draw.rectangle(
            [i, i, width - 1 - i, height - 1 - i],
            outline=border_color,
        )

    # --- decorative line beneath the top border ---
    line_y = 60
    draw.line([(80, line_y), (width - 80, line_y)], fill=border_color, width=2)
    draw.line([(80, height - line_y), (width - 80, height - line_y)], fill=border_color, width=2)

    # --- title text ---
    # Try to load a nice font; fall back to the default bitmap font.
    font_size = 72
    font = None
    for candidate in [
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ]:
        if os.path.isfile(candidate):
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except Exception:
                continue

    if font is None:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    # Word-wrap the title
    max_text_width = width - 160
    lines = _wrap_text(draw, title.upper(), font, max_text_width)

    # Calculate total text block height
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
    line_spacing = 20
    total_text_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Draw each line centred vertically and horizontally
    y_offset = (height - total_text_height) // 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = (width - lw) // 2

        # Shadow for depth
        draw.text((x + 3, y_offset + 3), line, font=font, fill=(0, 0, 0, 180))
        # Main text in gold/white gradient effect (gold on top line, fading to white)
        text_color = (
            212 + int((255 - 212) * (i / max(len(lines) - 1, 1))),
            175 + int((255 - 175) * (i / max(len(lines) - 1, 1))),
            55 + int((255 - 55) * (i / max(len(lines) - 1, 1))),
        )
        draw.text((x, y_offset), line, font=font, fill=text_color)
        y_offset += line_heights[i] + line_spacing

    # --- small "COLLECTION" label below title ---
    try:
        label_font = ImageFont.truetype(
            font.path if hasattr(font, "path") and font.path else "arial.ttf", 32
        )
    except Exception:
        label_font = font
    label = "C O L L E C T I O N"
    lbbox = draw.textbbox((0, 0), label, font=label_font)
    lw = lbbox[2] - lbbox[0]
    draw.text(
        ((width - lw) // 2, y_offset + 40),
        label,
        font=label_font,
        fill=(180, 180, 200),
    )

    # --- export ---
    buf = io.BytesIO()
    img.save(buf, format="PNG", quality=95)
    png_bytes = buf.getvalue()

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(png_bytes)
        log.info("Poster saved to %s", output_path)

    return png_bytes


# ---------------------------------------------------------------------------
# Plex poster upload
# ---------------------------------------------------------------------------

def apply_poster_to_collection(collection_key, image_url=None, image_bytes=None):
    """Upload a poster to a Plex collection.

    Provide either *image_url* (a publicly reachable URL that Plex can fetch)
    or *image_bytes* (raw image data uploaded directly).

    Parameters
    ----------
    collection_key : str
        The Plex ``ratingKey`` for the collection.
    image_url : str, optional
        URL to a poster image (e.g. a TMDb URL).
    image_bytes : bytes, optional
        Raw poster image data (PNG or JPEG).

    Returns
    -------
    bool
        ``True`` if the upload succeeded.
    """
    if not collection_key:
        log.error("No collection key provided.")
        return False

    endpoint = f"/library/metadata/{collection_key}/posters"

    try:
        if image_url:
            # Tell Plex to fetch the poster from a URL
            plex_post(endpoint, params={"url": image_url})
            log.info("Applied poster URL to collection %s", collection_key)
            return True

        if image_bytes:
            # Upload the raw image bytes directly
            headers = {
                "X-Plex-Token": PLEX["token"],
                "Content-Type": "image/png",
            }
            resp = requests.post(
                f"{PLEX['url']}{endpoint}",
                headers=headers,
                data=image_bytes,
                params={"X-Plex-Token": PLEX["token"]},
            )
            resp.raise_for_status()
            log.info("Uploaded poster bytes to collection %s", collection_key)
            return True

        log.error("No image_url or image_bytes provided.")
        return False

    except Exception as exc:
        log.error("Failed to apply poster to collection %s: %s", collection_key, exc)
        return False


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _process_single(collection, dry_run=False):
    """Process one collection, returning a report dict."""
    title = collection["title"]
    key = collection["key"]
    report = {
        "collection": title,
        "key": key,
        "had_poster": bool(collection.get("thumb")),
        "source": None,
        "applied": False,
        "skipped": False,
        "error": None,
    }

    # 1. Try TMDb artwork
    art = find_tmdb_collection_art(title)
    poster_url = art.get("poster_url", "")

    if poster_url:
        report["source"] = "tmdb"
        if dry_run:
            log.info("[DRY RUN] Would apply TMDb poster to '%s'", title)
            report["skipped"] = True
        else:
            ok = apply_poster_to_collection(key, image_url=poster_url)
            report["applied"] = ok
            if not ok:
                report["error"] = "Plex upload failed (TMDb URL)"
        return report

    # 2. Fallback: generate a poster with Pillow
    if _HAS_PILLOW:
        report["source"] = "generated"
        poster_bytes = generate_poster(title)
        if poster_bytes:
            if dry_run:
                log.info("[DRY RUN] Would apply generated poster to '%s'", title)
                report["skipped"] = True
            else:
                ok = apply_poster_to_collection(key, image_bytes=poster_bytes)
                report["applied"] = ok
                if not ok:
                    report["error"] = "Plex upload failed (generated)"
            return report

    # 3. Nothing available
    report["source"] = "none"
    report["skipped"] = True
    log.info("No poster source available for '%s'", title)
    return report


def auto_poster_all(dry_run=False):
    """Iterate all Plex collections, find TMDb art or generate fallback posters.

    Parameters
    ----------
    dry_run : bool
        If ``True``, no posters are actually uploaded — the function just
        reports what *would* happen.

    Returns
    -------
    list[dict]
        A report list with one entry per collection describing the action taken.
    """
    collections = get_plex_collections()
    log.info("Found %d collections in Plex.", len(collections))

    report = []
    for coll in collections:
        entry = _process_single(coll, dry_run=dry_run)
        report.append(entry)

    # Summary log
    applied = sum(1 for r in report if r["applied"])
    skipped = sum(1 for r in report if r["skipped"])
    errors = sum(1 for r in report if r["error"])
    log.info(
        "Poster run complete: %d applied, %d skipped, %d errors out of %d collections.",
        applied, skipped, errors, len(report),
    )
    return report


def auto_poster_single(collection_name, dry_run=False):
    """Apply a poster to a single Plex collection identified by name.

    Parameters
    ----------
    collection_name : str
        The exact (or partial) name of the collection in Plex.
    dry_run : bool
        If ``True``, no poster is uploaded.

    Returns
    -------
    dict or None
        A report dict for the matched collection, or ``None`` if not found.
    """
    collections = get_plex_collections()
    # Exact match first, then case-insensitive substring match
    target = None
    name_lower = collection_name.lower()
    for coll in collections:
        if coll["title"] == collection_name:
            target = coll
            break
    if target is None:
        for coll in collections:
            if name_lower in coll["title"].lower():
                target = coll
                break
    if target is None:
        log.warning("Collection %r not found in Plex.", collection_name)
        return None

    return _process_single(target, dry_run=dry_run)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Plex Collection Poster Manager")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading")
    parser.add_argument("--collection", type=str, default=None, help="Process a single collection by name")
    parser.add_argument("--generate-only", type=str, default=None, help="Generate a poster to a file path")
    args = parser.parse_args()

    if args.generate_only:
        title = args.collection or "Sample Collection"
        result = generate_poster(title, output_path=args.generate_only)
        if result:
            print(f"Poster saved to {args.generate_only}")
        else:
            print("Poster generation failed (is Pillow installed?).")
    elif args.collection:
        result = auto_poster_single(args.collection, dry_run=args.dry_run)
        if result:
            print(f"Result: {result}")
        else:
            print(f"Collection '{args.collection}' not found.")
    else:
        results = auto_poster_all(dry_run=args.dry_run)
        for r in results:
            status = "APPLIED" if r["applied"] else ("SKIPPED" if r["skipped"] else "ERROR")
            src = r["source"] or "n/a"
            print(f"  [{status}] {r['collection']} (source: {src})")
        applied = sum(1 for r in results if r["applied"])
        print(f"\nDone. {applied}/{len(results)} posters applied.")
