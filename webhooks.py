# Plex Media Stack - Webhook Listener
# Receives Plex webhooks and sends notifications via Discord/console

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import threading

# Discord webhook URL (set this to get Discord notifications)
DISCORD_WEBHOOK_URL = None  # e.g., "https://discord.com/api/webhooks/..."

LISTEN_PORT = 5555


class PlexWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            # Plex sends webhooks as multipart form data
            content_type = self.headers.get("Content-Type", "")
            if "multipart" in content_type:
                # Parse multipart -- extract the payload field
                import email.parser
                import io
                msg = email.message.EmailMessage()
                msg["Content-Type"] = content_type
                msg.set_payload(body)
                if msg.is_multipart():
                    for part in msg.iter_parts():
                        if part.get_content_type() == "application/json":
                            payload = json.loads(part.get_content())
                            self.handle_webhook(payload)
                            break
                else:
                    payload = json.loads(body)
                    self.handle_webhook(payload)
            else:
                payload = json.loads(body)
                self.handle_webhook(payload)
        except Exception as e:
            print(f"  Error parsing webhook: {e}")
            # Try raw JSON parse as fallback
            try:
                payload = json.loads(body)
                self.handle_webhook(payload)
            except Exception:
                pass

        self.send_response(200)
        self.end_headers()

    def handle_webhook(self, payload):
        event = payload.get("event", "unknown")
        account = payload.get("Account", {}).get("title", "?")
        server = payload.get("Server", {}).get("title", "?")
        metadata = payload.get("Metadata", {})

        title = metadata.get("title", "Unknown")
        media_type = metadata.get("type", "")
        year = metadata.get("year", "")
        show_title = metadata.get("grandparentTitle", "")

        # Format message based on event type
        if event == "media.play":
            if show_title:
                msg = f"[PLAYING] {account} started: {show_title} - {title}"
            else:
                msg = f"[PLAYING] {account} started: {title} ({year})"

        elif event == "media.stop":
            if show_title:
                msg = f"[STOPPED] {account} stopped: {show_title} - {title}"
            else:
                msg = f"[STOPPED] {account} stopped: {title} ({year})"

        elif event == "media.scrobble":
            if show_title:
                msg = f"[WATCHED] {account} finished: {show_title} - {title}"
            else:
                msg = f"[WATCHED] {account} finished: {title} ({year})"

        elif event == "library.new":
            if show_title:
                msg = f"[NEW] Added to library: {show_title} - {title}"
            else:
                msg = f"[NEW] Added to library: {title} ({year})"

        elif event == "library.on.deck":
            msg = f"[ON DECK] {title} is on deck for {account}"

        elif event == "admin.database.backup":
            msg = "[BACKUP] Plex database backup completed"

        elif event == "device.new":
            msg = f"[DEVICE] New device connected: {payload.get('Player', {}).get('title', '?')}"

        else:
            msg = f"[{event}] {title} ({year}) -- {account}"

        print(f"  {msg}")
        self.send_discord(msg)

    def send_discord(self, message):
        if not DISCORD_WEBHOOK_URL:
            return
        try:
            import requests
            requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
        except Exception as e:
            print(f"  Discord send failed: {e}")

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging


def start_webhook_server(port=None, discord_url=None, background=False):
    """Start the Plex webhook listener."""
    global DISCORD_WEBHOOK_URL, LISTEN_PORT
    if discord_url:
        DISCORD_WEBHOOK_URL = discord_url
    if port:
        LISTEN_PORT = port

    print("\n" + "=" * 60)
    print("  PLEX WEBHOOK LISTENER")
    print("=" * 60)
    print(f"\n  Listening on port {LISTEN_PORT}")
    print(f"  Discord notifications: {'enabled' if DISCORD_WEBHOOK_URL else 'disabled'}")
    print(f"\n  Configure in Plex: Settings > Webhooks > Add")
    print(f"  URL: http://localhost:{LISTEN_PORT}")
    print(f"\n  Waiting for events...\n")

    server = HTTPServer(("0.0.0.0", LISTEN_PORT), PlexWebhookHandler)

    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  Webhook listener stopped.")
            server.shutdown()


if __name__ == "__main__":
    port = LISTEN_PORT
    discord = None

    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            port = int(arg.split("=")[1])
        elif arg.startswith("--discord="):
            discord = arg.split("=", 1)[1]

    start_webhook_server(port=port, discord_url=discord)
