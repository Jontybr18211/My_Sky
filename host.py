# Try in test.py before changing the main file
import os
import sys
import json
import time
import math
import requests
import pathlib
import traceback
from datetime import datetime, timedelta, timezone
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv

# ---- Configuration & constants ----
APP_NAME = "MySky⛅"
CACHE_DIR = pathlib.Path.home() / ".mysky_cache"
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
GEOCODING_LIMIT = 5
DEFAULT_UNITS = "metric"
DEFAULT_LANG = "en"
ICON_URL = "https://openweathermap.org/img/wn/{icon}@2x.png"
FALLBACK_ICON_PATH = None

load_dotenv()
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY") or os.environ.get("API_KEY")

if not CACHE_DIR.exists():
    try:
        CACHE_DIR.mkdir(parents=True)
    except Exception:
        pass

# ---- Utilities ----
def utc_to_local(timestamp, tz_offset_seconds):
    if timestamp is None:
        return None
    utc_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    local_dt = utc_dt + timedelta(seconds=tz_offset_seconds)
    return local_dt.replace(tzinfo=None)


def safe_filename(s: str) -> str:
    import re
    if s is None:
        return "noname"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s).strip("_ ").lower()


def cache_get(key: str):
    path = CACHE_DIR / (safe_filename(key) + ".json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data.get("__cached_at", 0)
        if (time.time() - ts) > CACHE_TTL_SECONDS:
            return None
        return data.get("payload")
    except Exception:
        return None


def cache_put(key: str, payload):
    path = CACHE_DIR / (safe_filename(key) + ".json")
    try:
        obj = {"__cached_at": int(time.time()), "payload": payload}
        path.write_text(json.dumps(obj), encoding="utf-8")
    except Exception:
        pass

# ---- OpenWeather (OneCall + fallback) ----
class OpenWeather:
    GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/direct"
    ONECALL_URL = "https://api.openweathermap.org/data/2.5/onecall"
    CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
    FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
    AIR_POLLUTION_URL = "https://api.openweathermap.org/data/2.5/air_pollution"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocode(self, city_name: str, limit: int = GEOCODING_LIMIT):
        if not self.api_key:
            raise RuntimeError("OpenWeather API key not configured")
        params = {"q": city_name, "limit": limit, "appid": self.api_key}
        r = requests.get(self.GEOCODING_URL, params=params, timeout=12)
        r.raise_for_status()
        return r.json()

    def onecall(self, lat: float, lon: float, units: str = DEFAULT_UNITS, lang: str = DEFAULT_LANG, exclude="minutely"):
        if not self.api_key:
            raise RuntimeError("OpenWeather API key not configured")
        params = {"lat": lat, "lon": lon, "appid": self.api_key, "units": units, "lang": lang, "exclude": exclude}
        try:
            r = requests.get(self.ONECALL_URL, params=params, timeout=14)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            try:
                return self._fallback_onecall(lat, lon, units, lang)
            except Exception:
                raise
        except requests.RequestException:
            try:
                return self._fallback_onecall(lat, lon, units, lang)
            except Exception:
                raise

    def _fallback_onecall(self, lat: float, lon: float, units: str = DEFAULT_UNITS, lang: str = DEFAULT_LANG):
        if not self.api_key:
            raise RuntimeError("OpenWeather API key not configured")

        cur_params = {"lat": lat, "lon": lon, "appid": self.api_key, "units": units, "lang": lang}
        rcur = requests.get(self.CURRENT_URL, params=cur_params, timeout=12)
        rcur.raise_for_status()
        cur = rcur.json()

        fparams = {"lat": lat, "lon": lon, "appid": self.api_key, "units": units, "lang": lang}
        rfc = requests.get(self.FORECAST_URL, params=fparams, timeout=14)
        rfc.raise_for_status()
        fc = rfc.json()

        timezone_offset = cur.get("timezone", 0)

        hourly = []
        try:
            current_item = {
                "dt": cur.get("dt"),
                "temp": cur.get("main", {}).get("temp"),
                "feels_like": cur.get("main", {}).get("feels_like"),
                "pressure": cur.get("main", {}).get("pressure"),
                "humidity": cur.get("main", {}).get("humidity"),
                "dew_point": None,
                "uvi": None,
                "clouds": cur.get("clouds", {}).get("all") if cur.get("clouds") else None,
                "visibility": cur.get("visibility"),
                "wind_speed": cur.get("wind", {}).get("speed"),
                "wind_deg": cur.get("wind", {}).get("deg"),
                "pop": 0.0,
                "weather": cur.get("weather", []),
            }
            hourly.append(current_item)
        except Exception:
            pass

        flist = fc.get("list", []) or []
        for item in flist:
            if len(hourly) >= 48:
                break
            hourly.append({
                "dt": item.get("dt"),
                "temp": item.get("main", {}).get("temp"),
                "feels_like": item.get("main", {}).get("feels_like"),
                "pressure": item.get("main", {}).get("pressure"),
                "humidity": item.get("main", {}).get("humidity"),
                "dew_point": None,
                "uvi": None,
                "clouds": item.get("clouds", {}).get("all") if item.get("clouds") else None,
                "visibility": item.get("visibility"),
                "wind_speed": item.get("wind", {}).get("speed"),
                "wind_deg": item.get("wind", {}).get("deg"),
                "pop": item.get("pop", 0),
                "weather": item.get("weather", []),
            })

        daily_map = {}
        for h in hourly:
            dt = h.get("dt")
            if dt is None:
                continue
            local_dt = datetime.fromtimestamp(dt + timezone_offset, tz=timezone.utc)
            daykey = local_dt.date().isoformat()
            if daykey not in daily_map:
                daily_map[daykey] = {
                    "dt": int(datetime(local_dt.year, local_dt.month, local_dt.day, tzinfo=timezone.utc).timestamp()) - timezone_offset,
                    "temps_min": [],
                    "temps_max": [],
                    "pops": [],
                    "weathers": [],
                }
            try:
                if h.get("temp") is not None:
                    daily_map[daykey]["temps_min"].append(h.get("temp"))
                    daily_map[daykey]["temps_max"].append(h.get("temp"))
                if h.get("pop") is not None:
                    daily_map[daykey]["pops"].append(h.get("pop"))
                if h.get("weather"):
                    daily_map[daykey]["weathers"].append(h.get("weather")[0])
            except Exception:
                pass

        daily = []
        for daykey, val in list(daily_map.items())[:8]:
            temps_min = val["temps_min"] or [math.nan]
            temps_max = val["temps_max"] or [math.nan]
            pop_val = max(val["pops"]) if val["pops"] else 0.0
            weather_sample = val["weathers"][0] if val["weathers"] else {}
            daily.append({
                "dt": val["dt"],
                "sunrise": None,
                "sunset": None,
                "temp": {
                    "min": float(min(temps_min)) if temps_min else None,
                    "max": float(max(temps_max)) if temps_max else None,
                    "day": float(sum(temps_max)/len(temps_max)) if temps_max else None,
                },
                "pressure": None,
                "humidity": None,
                "wind_speed": None,
                "weather": [weather_sample] if weather_sample else [],
                "clouds": None,
                "pop": pop_val,
            })

        synthesized = {
            "lat": lat,
            "lon": lon,
            "timezone_offset": timezone_offset,
            "timezone": cur.get("timezone", "UTC"),
            "current": {
                "dt": cur.get("dt"),
                "sunrise": cur.get("sys", {}).get("sunrise"),
                "sunset": cur.get("sys", {}).get("sunset"),
                "temp": cur.get("main", {}).get("temp"),
                "feels_like": cur.get("main", {}).get("feels_like"),
                "pressure": cur.get("main", {}).get("pressure"),
                "humidity": cur.get("main", {}).get("humidity"),
                "dew_point": None,
                "uvi": None,
                "clouds": cur.get("clouds", {}).get("all") if cur.get("clouds") else None,
                "visibility": cur.get("visibility"),
                "wind_speed": cur.get("wind", {}).get("speed"),
                "wind_deg": cur.get("wind", {}).get("deg"),
                "weather": cur.get("weather", []),
            },
            "hourly": hourly,
            "daily": daily,
        }
        return synthesized

    def air_pollution(self, lat: float, lon: float):
        if not self.api_key:
            raise RuntimeError("OpenWeather API key not configured")
        params = {"lat": lat, "lon": lon, "appid": self.api_key}
        r = requests.get(self.AIR_POLLUTION_URL, params=params, timeout=12)
        r.raise_for_status()
        return r.json()

# Map OWM AQI value (1..5) to (label, color hex)
AQI_LEVELS = {
    1: ("Good", "#2ecc71"),
    2: ("Fair", "#f1c40f"),
    3: ("Moderate", "#e67e22"),
    4: ("Poor", "#e74c3c"),
    5: ("Very Poor", "#8e44ad"),
}

POLLUTANT_FULL = {
    "co": "Carbon monoxide (CO)",
    "no": "Nitric oxide (NO)",
    "no2": "Nitrogen dioxide (NO₂)",
    "o3": "Ozone (O₃)",
    "so2": "Sulfur dioxide (SO₂)",
    "pm2_5": "PM2.5 (fine particulates)",
    "pm10": "PM10 (coarse particulates)",
    "nh3": "Ammonia (NH₃)",
}

def get_weather_icon(icon_code: str):
    """Download & cache icon"""
    if not icon_code:
        return None

    icon_cache = CACHE_DIR / f"icon_{icon_code}.png"
    if icon_cache.exists():
        return icon_cache
    else:
        url = ICON_URL.format(icon=icon_code)
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                icon_cache.write_bytes(r.content)
                return icon_cache
        except Exception:
            pass
    return None

def init_session_state():
    if 'history' not in st.session_state:
        hist_file = pathlib.Path.home() / ".mysky_history.json"
        if hist_file.exists():
            try:
                st.session_state.history = json.loads(hist_file.read_text(encoding="utf-8"))
            except Exception:
                st.session_state.history = []
        else:
            st.session_state.history = []
    
    if 'units' not in st.session_state:
        st.session_state.units = DEFAULT_UNITS
        
    if 'theme' not in st.session_state:
        st.session_state.theme = "dark"  # keep dark as default; user can toggle to light
        
    if 'current_location' not in st.session_state:
        st.session_state.current_location = None
        
    if 'weather_data' not in st.session_state:
        st.session_state.weather_data = None
        
    if 'air_data' not in st.session_state:
        st.session_state.air_data = None
        
    if 'loading' not in st.session_state:
        st.session_state.loading = False

def save_history():
    hist_file = pathlib.Path.home() / ".mysky_history.json"
    try:
        hist_file.write_text(json.dumps(st.session_state.history[-50:]), encoding="utf-8")
    except Exception:
        pass

def apply_custom_css(theme: str):
    """
    Apply improved, professional sky-blue pastel light theme (avoid pure white),
    with stronger contrast for cards and widgets so the UI feels professional.
    """
    if theme == "light":
        css = """
        <style>
        :root{
            --bg-top: #dff6ff;        /* soft sky top */
            --bg-bottom: #f7fbff;     /* gentle bottom */
            --content: #f8fdff;       /* content area (very light blue, not pure white) */
            --card: #ffffff;          /* cards use near-white but we'll use subtle alpha so visually not pure */
            --card-tint: rgba(11, 91, 144, 0.03); /* faint blue tint for card border */
            --accent: #0ea5e9;        /* primary sky-blue (refined) */
            --accent-2: #60a5fa;      /* secondary accent */
            --text: #07224a;          /* deep navy for high contrast */
            --muted: #4b5563;         /* muted text */
            --input-bg: #f3fbff;      /* slightly off-white for inputs */
            --shadow: 0 10px 30px rgba(11,91,144,0.06);
            --card-border: rgba(7,34,74,0.06);
            --table-head-start: #cfeeff;
            --table-head-end: #eff9ff;
        }

        /* overall app gradient */
        .stApp, .block-container {
            background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)) !important;
            color: var(--text) !important;
            font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
        }

        /* central content container - subtle lifted panel */
        .block-container {
            padding: 36px 48px !important;
        }

        /* Cards / metrics - make them stand out */
        .main .stMetric, .stMetric, .stCard {
            background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(248,253,255,0.98)) !important;
            color: var(--text) !important;
            border-radius: 14px !important;
            padding: 14px !important;
            box-shadow: var(--shadow) !important;
            border: 1px solid var(--card-border) !important;
        }

        /* Inputs / selects */
        .stTextInput>div>div>input, .stSelectbox>div>div>div, .stSelectbox>div>div>select, .stTextArea>div>div>textarea {
            background: var(--input-bg) !important;
            border: 1px solid rgba(7,34,74,0.06) !important;
            color: var(--text) !important;
            border-radius: 10px !important;
            padding: 10px !important;
            box-shadow: none !important;
        }

        /* Buttons - refined sky blue primary with good contrast */
        .stButton>button {
            background: linear-gradient(180deg, var(--accent), var(--accent-2)) !important;
            color: white !important;
            font-weight: 700 !important;
            border-radius: 10px !important;
            padding: 8px 14px !important;
            border: none !important;
            box-shadow: 0 8px 20px rgba(14,165,233,0.12);
        }

        /* Sidebar styling - slightly darker to create separation */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(10,14,24,0.88), rgba(6,10,16,0.92)) !important;
            color: #ffffff !important;
            padding: 24px 12px 24px 12px !important;
            border-right: 1px solid rgba(255,255,255,0.03);
        }
        /* make sidebar buttons softer and smaller */
        [data-testid="stSidebar"] .stButton>button {
            background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.04)) !important;
            color: white !important;
            border-radius: 12px !important;
            padding: 10px 12px !important;
            text-align: left !important;
            min-width: 180px;
        }
        [data-testid="stSidebar"] .stButton>button:hover {
            background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.06)) !important;
        }

        /* Headings */
        h1, h2, h3, .stTitle {
            color: var(--text) !important;
        }

        /* Sunrise text bigger and stronger */
        .sunrise-sunset {
            font-size: 18px;
            font-weight: 700;
            color: var(--text);
            margin-bottom: 8px;
        }

        /* Sunrise progress - thicker, refined gradient */
        .sun-progress {
            width:100%;
            background: linear-gradient(90deg, rgba(7,34,74,0.05), rgba(7,34,74,0.02));
            border-radius: 14px;
            height: 18px;
            overflow: hidden;
            box-shadow: inset 0 1px 2px rgba(7,34,74,0.02);
            margin-top: 8px;
            margin-bottom: 6px;
        }
        .sun-progress > .bar {
            height:100%;
            width:0%;
            background: linear-gradient(90deg, #7dd3fc, #0ea5e9);
            border-radius: 14px;
            transition: width 0.6s ease;
        }

        /* Forecast table: scoped container and clearer header */
        .forecast-table .dataframe {
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid rgba(7,34,74,0.04);
            background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,253,255,0.98));
            box-shadow: 0 6px 18px rgba(11,91,144,0.03);
        }
        .forecast-table .dataframe thead th {
            background: linear-gradient(90deg, var(--table-head-start), var(--table-head-end));
            color: var(--text);
            font-weight: 700;
            border-bottom: 1px solid rgba(7,34,74,0.06);
            padding: 10px 12px;
        }
        .forecast-table .dataframe tbody tr td {
            padding: 10px 12px;
            vertical-align: middle;
            color: var(--text);
        }

        /* Plotly charts: transparent backgrounds to sit on panel */
        .stPlotlyChart>div>div>div {
            background: transparent !important;
        }

        /* small muted helper text */
        .muted { color: var(--muted) !important; font-size: 13px; }

        /* ensure metric labels are readable */
        .stMetric>div>div>p {
            color: var(--muted) !important;
        }
        .stMetric>div>div>div>div {
            color: var(--text) !important;
            font-weight:700 !important;
        }
        </style>
        """
    else:
        # keep your dark theme as before
        css = """
        <style>
        :root{
            --bg:#0b1220;
            --card:#0f1724;
            --accent1:#7c3aed;
            --accent2:#06b6d4;
            --text:#e6eef8;
            --muted:#94a3b8;
        }
        .stApp, .block-container {
            background: linear-gradient(180deg, var(--bg), #071028) !important;
            color: var(--text) !important;
        }
        .main .stMetric, .stMetric {
            background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)) !important;
            color: var(--text) !important;
            border-radius: 10px !important;
            padding: 10px !important;
        }
        .stTextInput>div>div>input, .stSelectbox>div>div>div {
            background: rgba(255,255,255,0.02) !important;
            border: 1px solid rgba(255,255,255,0.04) !important;
            color: var(--text) !important;
            border-radius: 8px !important;
        }
        .stButton>button {
            background: linear-gradient(90deg, var(--accent1), var(--accent2)) !important;
            color: white !important;
            font-weight: 600 !important;
            border-radius: 10px !important;
            padding: 6px 12px !important;
            border: none !important;
        }
        .sunrise-sunset {
            font-size: 18px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 8px;
        }
        .sun-progress {
            width:100%;
            background: rgba(255,255,255,0.04);
            border-radius: 14px;
            height: 16px;
            overflow: hidden;
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.4);
        }
        .sun-progress > .bar {
            height:100%;
            width:0%;
            background: linear-gradient(90deg, #ffd6a5, #ffb4a2);
            border-radius: 14px;
            transition: width 0.7s ease;
        }
        .forecast-table .dataframe thead th {
            background: linear-gradient(90deg,#2b2f4a,#2b394f);
            color: #e6eef8;
        }
        </style>
        """

    st.markdown(css, unsafe_allow_html=True)


def handle_search(owm, city):
    if not city:
        st.warning("Please enter a city name")
        return

    key = f"geocode::{city}"
    cached = cache_get(key)
    if cached:
        if isinstance(cached, list) and len(cached) > 0:
            st.session_state.loading = True
            handle_location_select(owm, cached[0])
            st.session_state.loading = False
            return

    st.session_state.loading = True
    try:
        res = owm.geocode(city, limit=GEOCODING_LIMIT)
        cache_put(f"geocode::{city}", res)
        candidates = res or []
        if not candidates:
            st.warning("No locations found for that query")
            st.session_state.loading = False
            return
        st.session_state.loading = True
        handle_location_select(owm, candidates[0])
    except Exception as e:
        st.error(f"Geocoding error: {e}")
        st.session_state.loading = False


def handle_history_select(owm, location_str):
    # Find the location in history
    for loc in st.session_state.history:
        if loc == location_str:
            # Recreate the location dict from the history string
            parts = location_str.split(", ")
            name = parts[0]
            country = parts[1] if len(parts) > 1 else ""
            state = parts[2] if len(parts) > 2 else None

            # We need to geocode again to get lat/lon
            key = f"geocode::{location_str}"
            cached = cache_get(key)
            if cached and isinstance(cached, list) and len(cached) > 0:
                candidate = cached[0]
                handle_location_select(owm, candidate)
                return

            # If not cached, do a new search
            st.session_state.loading = True
            try:
                res = owm.geocode(location_str, limit=1)
                if res and len(res) > 0:
                    handle_location_select(owm, res[0])
                else:
                    st.error("Location not found")
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                st.session_state.loading = False
            return


def handle_location_select(owm, candidate):
    """
    Fetches and stores weather & air data in session_state.
    IMPORTANT: removed st.rerun() calls; logic updated to be reactive via session_state.
    """
    try:
        lat = candidate.get("lat")
        lon = candidate.get("lon")
        if lat is None or lon is None:
            st.warning("Selected location has no coordinates")
            st.session_state.loading = False
            return

        name = candidate.get("name")
        country = candidate.get("country")
        state = candidate.get("state")
        st.session_state.current_location = {
            "name": name,
            "lat": lat,
            "lon": lon,
            "country": country,
            "state": state
        }

        label = f"{name}, {country}" + (f", {state}" if state else "")
        if label not in st.session_state.history:
            st.session_state.history.append(label)
            save_history()

        # Fetch weather data (use cache if available)
        lockey = f"onecall::{lat:.4f},{lon:.4f}::units={st.session_state.units}"
        cached = cache_get(lockey)
        if cached:
            st.session_state.weather_data = cached
            fetch_air_pollution(owm, lat, lon)
            st.session_state.loading = False
            return

        st.session_state.weather_data = owm.onecall(
            lat,
            lon,
            units=st.session_state.units,
            lang=DEFAULT_LANG
        )
        cache_put(lockey, st.session_state.weather_data)
        fetch_air_pollution(owm, lat, lon)
        st.session_state.loading = False

    except Exception as e:
        st.error(f"Error fetching weather data: {e}")
        st.session_state.loading = False


def fetch_air_pollution(owm, lat, lon):
    try:
        cache_key_air = f"air::{lat:.4f},{lon:.4f}"
        cached_air = cache_get(cache_key_air)
        if cached_air:
            st.session_state.air_data = cached_air
            return

        st.session_state.air_data = owm.air_pollution(lat, lon)
        cache_put(cache_key_air, st.session_state.air_data)
    except Exception as e:
        st.warning(f"Couldn't fetch air pollution data: {e}")


def render_current_weather(owm, data, loc_dict):
    tz_offset = data.get("timezone_offset", 0)
    cur = data.get("current", {})

    col1, col2 = st.columns([1, 3])
    with col1:
        # Weather icon
        icon_code = None
        if cur.get("weather"):
            w0 = cur.get("weather")[0] if isinstance(cur.get("weather"), list) and cur.get("weather") else {}
            icon_code = w0.get("icon")

        icon_path = get_weather_icon(icon_code)
        if icon_path:
            st.image(str(icon_path), width=120)
        else:
            st.write("")

    with col2:
        # Location and temperature
        city_label = f"{loc_dict['name']}, {loc_dict['country']}"
        if loc_dict.get("state"):
            city_label += f", {loc_dict['state']}"
        st.subheader(city_label)

        temp = cur.get("temp")
        if temp is None:
            temp_display = "--"
        else:
            temp_display = f"{temp:.1f}"
        deg = "°C" if st.session_state.units == "metric" else "°F"
        st.metric("Temperature", f"{temp_display} {deg}", delta=None)

        # Weather description
        desc = ""
        if cur.get("weather"):
            w0 = cur.get("weather")[0] if isinstance(cur.get("weather"), list) and cur.get("weather") else {}
            desc = w0.get("description", "").capitalize()
        st.caption(desc)

    # Sunrise/sunset (bigger text + larger custom progress bar)
    sunrise_ts = cur.get("sunrise")
    sunset_ts = cur.get("sunset")
    sunrise = utc_to_local(sunrise_ts, tz_offset) if sunrise_ts else None
    sunset = utc_to_local(sunset_ts, tz_offset) if sunset_ts else None

    if sunrise and sunset:
        hours = (sunset - sunrise).total_seconds() / 3600.0
        # show larger text via styled markdown
        st.markdown(
            f"<div class='sunrise-sunset'>🌅 Sunrise: {sunrise.strftime('%H:%M:%S')} &nbsp;&nbsp; "
            f"🌇 Sunset: {sunset.strftime('%H:%M:%S')} &nbsp;&nbsp; ⏱️ Day length: {hours:.1f}h</div>",
            unsafe_allow_html=True
        )

        # Calculate progress and render a custom larger progress bar
        now = datetime.utcnow() + timedelta(seconds=tz_offset)  # local now
        total_seconds = (sunset - sunrise).total_seconds()
        elapsed_seconds = (now - sunrise).total_seconds()
        progress = max(0.0, min(1.0, elapsed_seconds / total_seconds)) if total_seconds > 0 else 0.0
        pct = int(round(progress * 100))

        # HTML/CSS progress bar (bigger and styled)
        st.markdown(
            f"""
            <div class="sun-progress" aria-hidden="true">
                <div class="bar" style="width: {pct}%;"></div>
            </div>
            <div style="font-size:13px; color: rgba(7,34,74,0.7); margin-top:6px;">Local time progress: {pct}%</div>
            """,
            unsafe_allow_html=True,
        )

    # Additional metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        feels = cur.get('feels_like')
        if feels is None:
            feels_display = "--"
        else:
            feels_display = f"{feels:.1f} {deg}"
        st.metric("Feels Like", feels_display)
    with col2:
        humidity = cur.get('humidity')
        st.metric("Humidity", f"{humidity if humidity is not None else '--'}%")
    with col3:
        wind = cur.get('wind_speed')
        st.metric("Wind Speed", f"{wind if wind is not None else '--'} m/s")


def render_air_quality(airdata):
    st.subheader("Air Quality")
    if not airdata:
        st.warning("Air quality data not available")
        return

    try:
        rec = airdata.get("list", [{}])[0]
        main = rec.get("main", {})
        components = rec.get("components", {})
        aqi = main.get("aqi")

        level_text, color = ("-", "#999999")
        if isinstance(aqi, int) and aqi in AQI_LEVELS:
            level_text, color = AQI_LEVELS[aqi]

        # AQI progress bar percent (1..5 mapped to 0..100)
        perf = 0
        if isinstance(aqi, int):
            perf = int(round(((aqi - 1) / 4.0) * 100))

        st.progress(perf, text=f"AQI: {aqi} - {level_text}")

        # Pollutant details
        pollutant_cols = st.columns(4)
        i = 0
        for k, v in components.items():
            full = POLLUTANT_FULL.get(k.lower(), k.upper())
            with pollutant_cols[i % 4]:
                st.metric(full, f"{v}")
            i += 1

    except Exception as e:
        st.error(f"Error rendering air quality: {e}")


def render_forecast(daily, tz_offset_secs):
    """
    Build a tidy DataFrame for 7-day forecast and display as a styled dataframe
    to avoid column overflow / overlap that happens with many narrow st.columns.
    """
    st.subheader("7-Day Forecast")

    rows = []
    for day in (daily or [])[:7]:
        dt = utc_to_local(day.get("dt"), tz_offset_secs)
        date_str = dt.strftime("%a, %d %b") if dt else "-"
        tmin = day.get("temp", {}).get("min", None)
        tmax = day.get("temp", {}).get("max", None)
        pop = (day.get("pop", 0) * 100) if day.get("pop", 0) is not None else None
        weather = day.get("weather", [{}])[0]
        desc = weather.get("description", "").capitalize() if weather else ""
        icon = weather.get("icon") if weather else None
        rows.append({
            "Date": date_str,
            "Min (°C)" if st.session_state.units == "metric" else "Min (°F)": f"{tmin:.1f}" if isinstance(tmin, (int, float)) else "--",
            "Max (°C)" if st.session_state.units == "metric" else "Max (°F)": f"{tmax:.1f}" if isinstance(tmax, (int, float)) else "--",
            "Precip (%)": f"{pop:.0f}%" if isinstance(pop, (int, float)) else "--",
            "Condition": desc,
            "Icon": icon or ""
        })

    if not rows:
        st.info("No forecast available")
        return

    df = pd.DataFrame(rows)
    # Remove Icon column from visible df (we can keep it but it's not necessary)
    df_display = df.drop(columns=["Icon"])

    # Show styled dataframe with container width; CSS earlier will style table
    st.markdown('<div class="forecast-table">', unsafe_allow_html=True)
    st.dataframe(df_display, use_container_width=True, height=260)
    st.markdown('</div>', unsafe_allow_html=True)


def plot_hourly_data(hourly, tz_offset_secs):
    st.subheader("Hourly Forecast (48h)")

    times = []
    temps = []
    pops = []
    winds = []

    for h in (hourly or [])[:48]:
        ts = h.get("dt")
        dt = utc_to_local(ts, tz_offset_secs)
        times.append(dt)
        temps.append(h.get("temp", math.nan))
        pops.append((h.get("pop", 0)) * 100)
        winds.append(h.get("wind_speed", 0))

    # Create DataFrame
    df = pd.DataFrame({
        "Time": times,
        "Temperature": temps,
        "Precipitation": pops,
        "Wind Speed": winds
    })

    # Create tabs for each chart
    tab1, tab2, tab3 = st.tabs(["Temperature", "Precipitation", "Wind Speed"])

    with tab1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["Time"],
            y=df["Temperature"],
            mode='lines+markers',
            name='Temperature',
            line=dict(color='#0369a1', width=3),
            marker=dict(size=6, color='#0369a1')
        ))
        deg = "°C" if st.session_state.units == "metric" else "°F"
        fig.update_layout(
            title="Temperature Forecast",
            xaxis_title="Time",
            yaxis_title=f"Temperature ({deg})",
            template="plotly_white" if st.session_state.theme == "light" else "plotly_dark",
            plot_bgcolor = "rgba(0,0,0,0)",
            paper_bgcolor = "rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["Time"],
            y=df["Precipitation"],
            name='Precipitation',
            marker=dict(color='#60a5fa')
        ))
        fig.update_layout(
            title="Precipitation Probability",
            xaxis_title="Time",
            yaxis_title="Probability (%)",
            template="plotly_white" if st.session_state.theme == "light" else "plotly_dark",
            plot_bgcolor = "rgba(0,0,0,0)",
            paper_bgcolor = "rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["Time"],
            y=df["Wind Speed"],
            mode='lines+markers',
            name='Wind Speed',
            line=dict(color='#0ea5a6', width=2),
            marker=dict(size=6, color='#0ea5a6')
        ))
        fig.update_layout(
            title="Wind Speed Forecast",
            xaxis_title="Time",
            yaxis_title="Wind Speed (m/s)",
            template="plotly_white" if st.session_state.theme == "light" else "plotly_dark",
            plot_bgcolor = "rgba(0,0,0,0)",
            paper_bgcolor = "rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)


def main():
    # Initialize session state
    init_session_state()

    # Set page config
    st.set_page_config(
        page_title=APP_NAME,
        page_icon="⛅",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Theme toggle widget (no st.rerun required; Streamlit reruns automatically)
    theme_col1, theme_col2 = st.columns([3, 1])
    with theme_col1:
        st.title(f"{APP_NAME} Weather Dashboard")
    with theme_col2:
        # checkbox acts like a toggle: checked => light theme
        light_checked = st.checkbox("Light theme (pastel sky)", value=(st.session_state.theme == "light"))
        # update theme reactively
        st.session_state.theme = "light" if light_checked else "dark"

    # Apply custom CSS depending on theme
    apply_custom_css(st.session_state.theme)

    # Check API key
    if not OPENWEATHER_API_KEY:
        st.error("No OpenWeather API key found. Please set OPENWEATHER_API_KEY in your environment or .env file.")
        st.stop()

    owm = OpenWeather(OPENWEATHER_API_KEY)

    # Search controls (use a synchronous form submit)
    with st.form(key="search_form"):
        col1, col2, col3 = st.columns([4, 2, 1])

        with col1:
            city_input = st.text_input("Enter city", placeholder="e.g., 'Paris, FR' or 'Bangalore'")

        with col2:
            units = st.selectbox(
                "Units",
                ["metric (°C)", "imperial (°F)"],
                index=0 if st.session_state.units == "metric" else 1
            )
            st.session_state.units = "metric" if units == "metric (°C)" else "imperial"

        with col3:
            submitted = st.form_submit_button("Search")

    if submitted:
        handle_search(owm, city_input)

    # Display loading indicator
    if st.session_state.get('loading', False):
        with st.spinner("Fetching weather data..."):
            time.sleep(0.2)

    # Display weather data if available
    if st.session_state.weather_data and st.session_state.current_location:
        render_current_weather(owm, st.session_state.weather_data, st.session_state.current_location)

        if st.session_state.air_data:
            render_air_quality(st.session_state.air_data)

        # Layout: hourly charts and forecast dataframe below to avoid overlap
        st.markdown("---")
        col_left, col_right = st.columns([2, 3])
        with col_left:
            # keep hourly charts here
            plot_hourly_data(
                st.session_state.weather_data.get("hourly", []),
                st.session_state.weather_data.get("timezone_offset", 0)
            )
        with col_right:
            # Put forecast dataframe here for better spacing (avoid 7 small columns)
            render_forecast(
                st.session_state.weather_data.get("daily", []),
                st.session_state.weather_data.get("timezone_offset", 0)
            )

    # History section in sidebar
    st.sidebar.subheader("Search History")
    for loc in reversed(st.session_state.history[-10:]):
        if st.sidebar.button(loc):
            handle_history_select(owm, loc)


if __name__ == "__main__":
    main()
