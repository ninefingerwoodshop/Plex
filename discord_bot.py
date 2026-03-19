# Plex Media Stack - Discord Bot
# Posts library updates and responds to commands in Discord
#
# Setup:
#   1. Create a Discord webhook in your server (Server Settings > Integrations > Webhooks)
#   2. Set DISCORD_WEBHOOK below or pass via --webhook=URL
#   3. Run: python discord_bot.py --webhook=YOUR_URL
#
# For a full interactive bot (responds to !commands), you need a Discord bot token.
# This simple version uses webhooks for notifications only.

import sys
import json
import time
import requests
from datetime import datetime

DISCORD_WEBHOOK = None  # Set your webhook URL here


def send_discord(message, webhook_url=None):
    """Send a message to Discord via webhook."""
    url = webhook_url or DISCORD_WEBHOOK
    if not url:
        print(f"  [NO WEBHOOK] {message}")
        return False
    try:
        r = requests.post(url, json={"content": message}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  Discord error: {e}")
        return False


def send_embed(title, description, fields=None, color=0xE5A00D, webhook_url=None):
    """Send a rich embed to Discord."""
    url = webhook_url or DISCORD_WEBHOOK
    if not url:
        print(f"  [NO WEBHOOK] {title}: {description}")
        return False

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "Plex Health Dashboard"},
    }
    if fields:
        embed["fields"] = fields

    try:
        r = requests.post(url, json={"embeds": [embed]}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  Discord error: {e}")
        return False


def send_daily_report(webhook_url=None):
    """Send a daily health summary to Discord."""
    from api import get_plex_movies, get_plex_shows, get_radarr_movies, get_sonarr_series

    movies = get_plex_movies()
    shows = get_plex_shows()
    radarr = get_radarr_movies()
    sonarr = get_sonarr_series()

    watched = sum(1 for m in movies if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"))
    pending = sum(1 for m in radarr if not m.get("hasFile") and m.get("monitored"))

    fields = [
        {"name": "Movies", "value": f"{len(movies)} total\n{watched} watched", "inline": True},
        {"name": "TV Shows", "value": f"{len(shows)} in Plex\n{len(sonarr)} in Sonarr", "inline": True},
        {"name": "Radarr", "value": f"{len(radarr)} tracked\n{pending} pending", "inline": True},
    ]

    send_embed(
        "Daily Plex Report",
        f"Library status as of {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        fields=fields,
        webhook_url=webhook_url,
    )
    print("  Daily report sent to Discord!")


def send_movie_pick(webhook_url=None):
    """Send a random movie pick to Discord."""
    from movie_picker import pick_movie
    picks = pick_movie(unwatched_only=True, count=1)
    if picks:
        m = picks[0]
        genres = ", ".join(m["genres"][:3])
        send_embed(
            "Tonight's Movie Pick",
            f"**{m['title']}** ({m['year']})\n"
            f"Rating: {m['rating']:.1f} | {genres} | {m['runtime']} min\n\n"
            f"{m['summary']}...",
            color=0x27AE60,
            webhook_url=webhook_url,
        )
        print(f"  Sent pick: {m['title']}")


def send_new_additions(webhook_url=None):
    """Check for recently added content and notify."""
    from api import plex_get
    data = plex_get("/library/recentlyAdded", {
        "X-Plex-Container-Start": 0,
        "X-Plex-Container-Size": 5,
    })
    items = data.get("MediaContainer", {}).get("Metadata", [])

    if not items:
        return

    lines = []
    for item in items:
        title = item.get("title", "?")
        year = item.get("year", "")
        media_type = item.get("type", "")
        if media_type == "movie":
            lines.append(f"- **{title}** ({year})")
        elif media_type == "episode":
            show = item.get("grandparentTitle", "")
            lines.append(f"- **{show}** - {title}")

    if lines:
        send_embed(
            "Recently Added to Plex",
            "\n".join(lines),
            color=0x2980B9,
            webhook_url=webhook_url,
        )


if __name__ == "__main__":
    webhook = None
    action = "report"

    for arg in sys.argv[1:]:
        if arg.startswith("--webhook="):
            webhook = arg.split("=", 1)[1]
        elif arg == "--pick":
            action = "pick"
        elif arg == "--new":
            action = "new"
        elif arg == "--report":
            action = "report"

    if webhook:
        DISCORD_WEBHOOK = webhook

    if action == "report":
        send_daily_report(webhook_url=webhook)
    elif action == "pick":
        send_movie_pick(webhook_url=webhook)
    elif action == "new":
        send_new_additions(webhook_url=webhook)
