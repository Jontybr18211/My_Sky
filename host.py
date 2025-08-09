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
        st.session_state.theme = "dark"
        
    if 'current_location' not in st.session_state:
        st.session_state.current_location = None
        
    if 'weather_data' not in st.session_state:
        st.session_state.weather_data = None
        
    if 'air_data' not in st.session_state:
        st.session_state.air_data = None

def save_history():
    hist_file = pathlib.Path.home() / ".mysky_history.json"
    try:
        hist_file.write_text(json.dumps(st.session_state.history[-50:]), encoding="utf-8")
    except Exception:
        pass

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
            st.write("No weather icon available")
    
    with col2:
        # Location and temperature
        city_label = f"{loc_dict['name']}, {loc_dict['country']}"
        if loc_dict.get("state"):
            city_label += f", {loc_dict['state']}"
        st.subheader(city_label)
        
        temp = cur.get("temp")
        deg = "°C" if st.session_state.units == "metric" else "°F"
        st.metric("Temperature", f"{temp:.1f} {deg}", delta=None)
        
        # Weather description
        desc = ""
        if cur.get("weather"):
            w0 = cur.get("weather")[0] if isinstance(cur.get("weather"), list) and cur.get("weather") else {}
            desc = w0.get("description", "").capitalize()
        st.caption(desc)
    
    # Sunrise/sunset
    sunrise_ts = cur.get("sunrise")
    sunset_ts = cur.get("sunset")
    sunrise = utc_to_local(sunrise_ts, tz_offset) if sunrise_ts else None
    sunset = utc_to_local(sunset_ts, tz_offset) if sunset_ts else None
    
    if sunrise and sunset:
        hours = (sunset - sunrise).total_seconds() / 3600.0
        st.caption(f"🌅 Sunrise: {sunrise.time()}  🌇 Sunset: {sunset.time()}  ⏱️ Day length: {hours:.1f}h")
    
    # Additional metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Feels Like", f"{cur.get('feels_like', '--'):.1f} {deg}")
    with col2:
        st.metric("Humidity", f"{cur.get('humidity', '--')}%")
    with col3:
        st.metric("Wind Speed", f"{cur.get('wind_speed', '--')} m/s")

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
            
        # AQI progress bar
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
    st.subheader("7-Day Forecast")
    
    cols = st.columns(7)
    for i, day in enumerate(daily[:7]):
        dt = utc_to_local(day.get("dt"), tz_offset_secs)
        day_name = dt.strftime("%a %d %b") if dt else "-"
        temp_min = day.get("temp", {}).get("min", 0.0) or 0.0
        temp_max = day.get("temp", {}).get("max", 0.0) or 0.0
        pop = day.get("pop", 0) * 100
        weather = day.get("weather", [{}])[0]
        icon = weather.get("icon")
        
        with cols[i]:
            st.subheader(day_name)
            
            # Weather icon
            icon_path = get_weather_icon(icon)
            if icon_path:
                st.image(str(icon_path), width=80)
                
            # Temperatures
            deg = "°C" if st.session_state.units == "metric" else "°F"
            st.write(f"▲ {temp_max:.0f}{deg}")
            st.write(f"▼ {temp_min:.0f}{deg}")
            
            # Precipitation probability
            st.progress(pop/100, text=f"💧 {pop:.0f}%")

def plot_hourly_data(hourly, tz_offset_secs):
    st.subheader("Hourly Forecast (48h)")
    
    times = []
    temps = []
    pops = []
    winds = []
    
    for h in hourly[:48]:
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
            line=dict(color='#7c3aed', width=3),
            marker=dict(size=6, color='#7c3aed')
        ))
        deg = "°C" if st.session_state.units == "metric" else "°F"
        fig.update_layout(
            title="Temperature Forecast",
            xaxis_title="Time",
            yaxis_title=f"Temperature ({deg})",
            template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white"
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["Time"], 
            y=df["Precipitation"],
            name='Precipitation',
            marker=dict(color='#06b6d4')
        ))
        fig.update_layout(
            title="Precipitation Probability",
            xaxis_title="Time",
            yaxis_title="Probability (%)",
            template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white"
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["Time"], 
            y=df["Wind Speed"],
            mode='lines+markers',
            name='Wind Speed',
            line=dict(color='#22c55e', width=2),
            marker=dict(size=6, color='#22c55e')
        ))
        fig.update_layout(
            title="Wind Speed Forecast",
            xaxis_title="Time",
            yaxis_title="Wind Speed (m/s)",
            template="plotly_dark" if st.session_state.theme == "dark" else "plotly_white"
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
    
    # Apply custom CSS
    apply_custom_css()
    
    # Check API key
    if not OPENWEATHER_API_KEY:
        st.error("No OpenWeather API key found. Please set OPENWEATHER_API_KEY in your environment or .env file.")
        st.stop()
    
    owm = OpenWeather(OPENWEATHER_API_KEY)
    
    # Header
    st.title(f"{APP_NAME} Weather Dashboard")
    
    # Search controls
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
            st.form_submit_button("Search", on_click=handle_search, args=(owm, city_input))
    
    # Theme toggle
    if st.button("Toggle Theme"):
        st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"
        st.experimental_rerun()
    
    # Display loading indicator
    if st.session_state.get('loading', False):
        with st.spinner("Fetching weather data..."):
            time.sleep(0.5)
    
    # Display weather data if available
    if st.session_state.weather_data and st.session_state.current_location:
        render_current_weather(owm, st.session_state.weather_data, st.session_state.current_location)
        
        if st.session_state.air_data:
            render_air_quality(st.session_state.air_data)
        
        col1, col2 = st.columns([1, 2])
        with col1:
            render_forecast(
                st.session_state.weather_data.get("daily", []),
                st.session_state.weather_data.get("timezone_offset", 0)
            )
        with col2:
            plot_hourly_data(
                st.session_state.weather_data.get("hourly", []),
                st.session_state.weather_data.get("timezone_offset", 0)
            )
    
    # History section
    st.sidebar.subheader("Search History")
    for loc in st.session_state.history[-10:]:
        if st.sidebar.button(loc):
            handle_history_select(owm, loc)

def apply_custom_css():
    st.markdown("""
    <style>
        /* Main content */
        .stApp {
            background: linear-gradient(135deg, #0b1220, #071028);
            color: #e6eef8;
        }
        
        /* Dark theme */
        [data-theme="dark"] .stApp {
            background: linear-gradient(135deg, #0b1220, #071028);
            color: #e6eef8;
        }
        
        /* Light theme */
        [data-theme="light"] .stApp {
            background: linear-gradient(135deg, #f6fbff, #f8f6ff);
            color: #17233b;
        }
        
        /* Cards */
        .stMetric {
            background-color: rgba(12, 16, 24, 0.7) !important;
            border: 1px solid rgba(255, 255, 255, 0.04) !important;
            border-radius: 12px !important;
            padding: 15px !important;
        }
        
        [data-theme="light"] .stMetric {
            background-color: rgba(232, 241, 249, 0.7) !important;
            border: 1px solid rgba(20, 30, 50, 0.06) !important;
        }
        
        /* Buttons */
        .stButton>button {
            background: linear-gradient(135deg, #7c3aed, #06b6d4) !important;
            color: white !important;
            border-radius: 10px !important;
            font-weight: 600 !important;
            border: none !important;
        }
        
        [data-theme="light"] .stButton>button {
            background: linear-gradient(135deg, #5eead4, #60a5fa) !important;
        }
        
        /* Inputs */
        .stTextInput>div>div>input {
            background: rgba(255, 255, 255, 0.02) !important;
            border: 1px solid rgba(255, 255, 255, 0.06) !important;
            color: #d8e9ff !important;
        }
        
        [data-theme="light"] .stTextInput>div>div>input {
            background: white !important;
            border: 1px solid rgba(20, 30, 50, 0.08) !important;
            color: #17233b !important;
        }
        
        /* Progress bars */
        .stProgress>div>div>div {
            background: linear-gradient(90deg, #7c3aed, #06b6d4) !important;
        }
        
        [data-theme="light"] .stProgress>div>div>div {
            background: linear-gradient(90deg, #5eead4, #60a5fa) !important;
        }
    </style>
    """, unsafe_allow_html=True)

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
            country = parts[1]
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
        
        # Fetch weather data
        lockey = f"onecall::{lat:.4f},{lon:.4f}::units={st.session_state.units}"
        cached = cache_get(lockey)
        if cached:
            st.session_state.weather_data = cached
            fetch_air_pollution(owm, lat, lon)
            st.session_state.loading = False
            st.experimental_rerun()
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
        st.experimental_rerun()
        
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

if __name__ == "__main__":
    main()