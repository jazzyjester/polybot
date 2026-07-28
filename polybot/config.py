"""Target markets for the weather pilot.

Cities were picked from Polymarket's daily "highest temperature" markets:
the flagship cities (Hong Kong, Wellington, Seoul) already trade $150-270k
and are the most likely to already have bots watching them closely, so the
pilot targets the thinner $50-70k tier instead, where a real forecast edge
is more likely to survive unexploited.

`slug` is the city fragment Polymarket uses in its event slugs:
    highest-temperature-in-{slug}-on-{month}-{day}-{year}
"""

CITIES = {
    "toronto": {
        "slug": "toronto",
        "lat": 43.6532,
        "lon": -79.3832,
        "timezone": "America/Toronto",
        "unit": "C",
    },
    "denver": {
        "slug": "denver",
        "lat": 39.7392,
        "lon": -104.9903,
        "timezone": "America/Denver",
        "unit": "F",
    },
    "munich": {
        "slug": "munich",
        "lat": 48.1351,
        "lon": 11.5820,
        "timezone": "Europe/Berlin",
        "unit": "C",
    },
    "chongqing": {
        "slug": "chongqing",
        "lat": 29.5630,
        "lon": 106.5516,
        "timezone": "Asia/Shanghai",
        "unit": "C",
    },
    "taipei": {
        "slug": "taipei",
        "lat": 25.0330,
        "lon": 121.5654,
        "timezone": "Asia/Taipei",
        "unit": "C",
    },
    "paris": {
        "slug": "paris",
        "lat": 48.8566,
        "lon": 2.3522,
        "timezone": "Europe/Paris",
        "unit": "C",
    },
}

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DB_PATH = "data/polybot.sqlite3"

# Minimum |model_probability - market_price| to flag as a candidate edge.
EDGE_THRESHOLD = 0.08
