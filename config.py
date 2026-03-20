# Plex Media Stack - Configuration
# All service endpoints and API keys

PLEX = {
    "url": "http://localhost:32400",
    "token": "9AemmEzDpzYae8rrRHsZ",
    "movie_section": 1,
    "tv_section": 3,
}

SONARR = {
    "url": "http://localhost:8989",
    "api_key": "9e30c604b12343ebac5c489c8bba9cb4",
}

RADARR = {
    "url": "http://localhost:7878",
    "api_key": "50e33828551a44d9b0be847ed1935a0a",
}

NZBGET = {
    "url": "http://nzbget:tegbzn6789@localhost:6789",
}

# Newznab indexers (for direct NZB searches outside Radarr/Sonarr)
INDEXERS = [
    {
        "name": "DrunkenSlug",
        "url": "https://api.drunkenslug.com/api",
        "api_key": "bd9b0ff8cca45556db308e96311e2997",
    },
    {
        "name": "NZBFinder",
        "url": "https://nzbfinder.ws/api",
        "api_key": "0711e3923a0e5f2d7c07a172b76128a4",
    },
    {
        "name": "NZBgeek",
        "url": "https://api.nzbgeek.info/api",
        "api_key": "igOLlPZDZtCsoUc2a55iXoMMwMK7WvcG",
    },
]

# Web app settings
APP = {
    "secret_key": "plex-search-a7f3b9c1e2d4",
    "port": 5050,
    "users": {
        "gibbens": "Kv8#mPlex2026!",
        "melenda": "Qs4$WatchNow!7x",
        "patricia": "Jn9&StreamIt!3w",
    },
}

CLEANUP = {
    "grace_period_hours": 24,
    "attributions_file": "attributions.json",
    "pending_file": "pending_cleanup.json",
    "webhook_port": 5666,
}

# TMDb API (for recommendations, collection posters, analytics)
TMDB = {
    "api_key": "d18715d77e5c5aa359dac8f4fc84d0d0",
    "base_url": "https://api.themoviedb.org/3",
    "image_base": "https://image.tmdb.org/t/p",
}


# Storage drives
STORAGE = {
    "movie_drives": ["O:\\", "K:\\", "D:\\", "L:\\"],
    "tv_drives": ["E:\\", "F:\\", "G:\\", "H:\\", "I:\\", "M:\\", "N:\\"],
}

# Health monitor schedule
HEALTH = {
    "check_interval_minutes": 60,
    "port": 5051,
    "disk_warning_pct": 90,
}

# Request portal
REQUESTS = {
    "port": 5052,
    "db_file": "requests.json",
}

# Analytics
ANALYTICS = {
    "cache_file": "analytics_cache.json",
}

# Seasonal Collections
SEASONAL = {
    "auto_build": True,       # Automatically build collections for the current season
    "auto_clean": True,       # Automatically remove expired seasonal collections
    "digest_file": "arrivals_digest.json",
}

# New Arrivals Digest
DIGEST = {
    "default_days": 7,        # Default lookback period for digest
    "auto_send": False,
}
