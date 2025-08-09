import os
import sys
import json
import time
import math
import requests
import pathlib
import traceback
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import (
    Qt,
    QRunnable,
    QThreadPool,
    QObject,
    pyqtSignal,
)
from PyQt6.QtGui import QPixmap, QFont, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QMessageBox,
    QComboBox,
    QProgressBar,
    QScrollArea,
    QGroupBox,
    QGridLayout,
    QCompleter,
    QGraphicsColorizeEffect,
)

import pyqtgraph as pg
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

# ---- Networking Thread/Worker ----
class WorkerSignals(QObject):
    finished = pyqtSignal(object, str)
    error = pyqtSignal(object, str)


class NetworkWorker(QRunnable):
    def __init__(self, fn, *args, tag="generic", **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.tag = tag

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result, self.tag)
        except Exception as e:
            tb = traceback.format_exc()
            self.signals.error.emit((e, tb), self.tag)

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

# ---- Improved QSS and UI ----
LIGHT_QSS = """
/* Colorful professional light theme */
QWidget {
background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #f6fbff, stop:0.5 #eef7ff, stop:1 #f8f6ff);
color: #17233b;
font-family: "Segoe UI", Arial, sans-serif;
}
QGroupBox {
background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #e8f1f9, stop:1 #e0eaf3); 
border: 1px solid rgba(20,30,50,0.06);
border-radius: 10px; padding: 12px;
}
QPushButton {
background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #5eead4, stop:1 #60a5fa); 
color: white; border-radius: 10px; padding: 8px 12px; font-weight: 600;
}
QPushButton:hover { transform: translateY(-1px); }
QLineEdit { padding: 8px; border-radius: 8px; border: 1px solid rgba(20,30,50,0.08); background: white; }
QLabel#title { font-size: 20px; font-weight: 700; color: #0b254a; }
QProgressBar { border-radius: 8px; height: 12px; background: rgba(10,20,40,0.04); }
QScrollArea { background: transparent; }
QLabel#icon_label { background: rgba(255,255,255,0.03); border-radius: 60px; padding: 8px; }
QLabel.icon-small { background: rgba(255,255,255,0.02); border-radius: 36px; padding: 6px; }
"""

DARK_QSS = """
/* Rich dark theme with improved contrast */
QWidget { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #0b1220, stop:1 #071028); color: #e6eef8; font-family: "Segoe UI", Arial, sans-serif; }
QGroupBox { background: rgba(12,16,24,0.7); border: 1px solid rgba(255,255,255,0.04); border-radius: 12px; padding: 12px; }
QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #7c3aed, stop:1 #06b6d4); color: white; border-radius: 10px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { opacity: 0.95; }
QLineEdit { padding: 8px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.06); background: rgba(255,255,255,0.02); color: #d8e9ff; }
QLabel#title { font-size: 20px; font-weight: 700; color: #bcd7ff; }
QProgressBar { border-radius: 8px; height: 12px; background: rgba(255,255,255,0.03); }
QLabel#icon_label { background: rgba(255,255,255,0.02); border-radius: 60px; padding: 8px; }
QLabel.icon-small { background: rgba(255,255,255,0.01); border-radius: 36px; padding: 6px; }
"""

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


def _weather_main_to_color(main: str) -> str:
    """Return a hex color suitable for the given weather main string."""
    if not main:
        return "#6b7280"  # neutral
    m = main.lower()
    if "clear" in m:
        return "#FFD166"  # warm yellow
    if "rain" in m or "drizzle" in m:
        return "#06b6d4"  # cyan/blue
    if "cloud" in m:
        return "#93c5fd"  # light blue
    if "snow" in m:
        return "#a7f3d0"  # pale mint
    if "thunder" in m:
        return "#7c3aed"  # purple
    if "mist" in m or "fog" in m or "haze" in m:
        return "#94a3b8"  # muted gray
    return "#60a5fa"


class WeatherMainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 740)
        self.threadpool = QThreadPool()
        self.owm = OpenWeather(OPENWEATHER_API_KEY)

        self.units = DEFAULT_UNITS
        self.lang = DEFAULT_LANG
        self.current_location = None
        self.history = []

        self._load_history()
        self._build_ui()
        self.apply_theme("dark")

    def _load_history(self):
        hist_file = pathlib.Path.home() / ".mysky_history.json"
        if hist_file.exists():
            try:
                self.history = json.loads(hist_file.read_text(encoding="utf-8"))
            except Exception:
                self.history = []
        else:
            self.history = []

    def _save_history(self):
        hist_file = pathlib.Path.home() / ".mysky_history.json"
        try:
            hist_file.write_text(json.dumps(self.history[-50:]), encoding="utf-8")
        except Exception:
            pass

    def _build_ui(self):
        main_layout = QVBoxLayout()
        header = QHBoxLayout()

        title = QLabel(APP_NAME)
        title.setObjectName("title")
        title.setFont(QFont("Segoe UI", 16))

        self.city_input = QLineEdit()
        self.city_input.setPlaceholderText("Enter city e.g. 'Paris, FR' or 'Bangalore'...")
        self.completer = QCompleter(self.history)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.city_input.setCompleter(self.completer)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.on_search_clicked)

        self.units_select = QComboBox()
        self.units_select.addItems(["metric (°C)", "imperial (°F)"])
        self.units_select.setCurrentIndex(0 if self.units == "metric" else 1)
        self.units_select.currentIndexChanged.connect(self.on_units_changed)

        self.theme_btn = QPushButton("Toggle Theme")
        self.theme_btn.clicked.connect(self.on_theme_toggle)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.city_input, 2)
        header.addWidget(self.search_btn)
        header.addWidget(self.units_select)
        header.addWidget(self.theme_btn)
        main_layout.addLayout(header)

        body = QHBoxLayout()

        # Left column
        left_col = QVBoxLayout()
        self.card_current = QGroupBox("Current")
        cc_layout = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setObjectName("icon_label")
        self.icon_label.setFixedSize(120, 120)
        self.icon_label.setScaledContents(True)
        info_layout = QVBoxLayout()
        self.lbl_city = QLabel("-")
        self.lbl_temp = QLabel("-- °C")
        self.lbl_desc = QLabel("---")
        self.lbl_extra = QLabel("")
        self.lbl_city.setFont(QFont("Segoe UI", 11, weight=QFont.Weight.DemiBold))
        self.lbl_temp.setFont(QFont("Segoe UI", 20, weight=QFont.Weight.Bold))
        info_layout.addWidget(self.lbl_city)
        info_layout.addWidget(self.lbl_temp)
        info_layout.addWidget(self.lbl_desc)
        info_layout.addWidget(self.lbl_extra)
        cc_layout.addWidget(self.icon_label)
        cc_layout.addLayout(info_layout)
        self.card_current.setLayout(cc_layout)
        left_col.addWidget(self.card_current)

        # Air Quality card enhanced
        self.card_aqi = QGroupBox("Air Quality")
        aqi_layout = QVBoxLayout()
        top_aqi = QHBoxLayout()
        self.lbl_aqi = QLabel("AQI: -")
        self.lbl_aqi.setFont(QFont("Segoe UI", 11, weight=QFont.Weight.DemiBold))
        self.aqi_indicator = QLabel()
        self.aqi_indicator.setFixedSize(18, 18)
        self.aqi_indicator.setStyleSheet("border-radius:9px; background: transparent;")
        self.aqi_status = QLabel("")  # textual label e.g. Good
        top_aqi.addWidget(self.lbl_aqi)
        top_aqi.addSpacing(6)
        top_aqi.addWidget(self.aqi_indicator)
        top_aqi.addWidget(self.aqi_status)
        top_aqi.addStretch(1)
        aqi_layout.addLayout(top_aqi)

        # Progress bar visualizing AQ (scaled from 1..5 -> 0..100)
        self.aqi_progress = QProgressBar()
        self.aqi_progress.setRange(0, 100)
        self.aqi_progress.setValue(0)
        aqi_layout.addWidget(self.aqi_progress)

        # Pollutant list
        self.lbl_pollutants = QLabel("")
        self.lbl_pollutants.setWordWrap(True)
        self.lbl_pollutants.setStyleSheet("font-size: 11px;")
        aqi_layout.addWidget(self.lbl_pollutants)
        self.card_aqi.setLayout(aqi_layout)
        left_col.addWidget(self.card_aqi)

        # Forecast card with increased vertical space
        self.card_forecast = QGroupBox("7-Day Forecast")
        fc_layout = QHBoxLayout()
        self.forecast_scroll = QScrollArea()
        self.forecast_scroll.setWidgetResizable(True)
        self.forecast_scroll.setFixedHeight(320)  # increased Y-axis space
        self.forecast_container = QWidget()
        self.forecast_layout = QHBoxLayout()
        self.forecast_container.setLayout(self.forecast_layout)
        self.forecast_scroll.setWidget(self.forecast_container)
        fc_layout.addWidget(self.forecast_scroll)
        self.card_forecast.setLayout(fc_layout)
        left_col.addWidget(self.card_forecast, 1)

        left_col.addStretch(1)
        body.addLayout(left_col, 3)

        # Right column: charts (improved styles)
        right_col = QVBoxLayout()
        self.card_charts = QGroupBox("Hourly Charts (48h)")
        charts_layout = QGridLayout()

        self.plot_temp = pg.PlotWidget(title="Temperature")
        self.plot_temp.showGrid(x=True, y=True, alpha=0.3)
        self.plot_temp.setMinimumHeight(220)
        charts_layout.addWidget(self.plot_temp, 0, 0)

        self.plot_pop = pg.PlotWidget(title="Precipitation Probability")
        self.plot_pop.showGrid(x=True, y=True, alpha=0.25)
        self.plot_pop.setMinimumHeight(180)
        charts_layout.addWidget(self.plot_pop, 1, 0)

        self.plot_wind = pg.PlotWidget(title="Wind Speed")
        self.plot_wind.showGrid(x=True, y=True, alpha=0.25)
        self.plot_wind.setMinimumHeight(160)
        charts_layout.addWidget(self.plot_wind, 2, 0)

        self.card_charts.setLayout(charts_layout)
        right_col.addWidget(self.card_charts)
        right_col.addStretch(1)
        body.addLayout(right_col, 4)

        main_layout.addLayout(body)
        self.setLayout(main_layout)

        # Status bar like progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        main_layout.addWidget(self.progress)

    # Theme helpers
    def apply_theme(self, which: str):
        if which == "light":
            self.setStyleSheet(LIGHT_QSS)
            self.current_theme = "light"
        else:
            self.setStyleSheet(DARK_QSS)
            self.current_theme = "dark"

    def on_theme_toggle(self):
        self.apply_theme("light" if getattr(self, "current_theme", "dark") == "dark" else "dark")

    def on_units_changed(self, idx: int):
        self.units = "metric" if idx == 0 else "imperial"
        if self.current_location:
            self.fetch_weather_for_location(self.current_location)

    def set_loading(self, loading: bool):
        self.progress.setVisible(loading)

    # Events
    def on_search_clicked(self):
        city = self.city_input.text().strip()
        if not city:
            QMessageBox.warning(self, "Input needed", "Please enter a city name.")
            return
        key = f"geocode::{city}"
        cached = cache_get(key)
        if cached:
            if isinstance(cached, list) and len(cached) > 0:
                chosen = cached[0]
                self._choose_location(chosen)
                return
        self.set_loading(True)
        worker = NetworkWorker(self._do_geocode, city, tag="geocode")
        worker.signals.finished.connect(self._on_network_success)
        worker.signals.error.connect(self._on_network_error)
        self.threadpool.start(worker)

    def _do_geocode(self, city):
        res = self.owm.geocode(city, limit=GEOCODING_LIMIT)
        cache_put(f"geocode::{city}", res)
        return res

    def _on_network_success(self, payload, tag):
        try:
            if tag == "geocode":
                self.set_loading(False)
                candidates = payload or []
                if not candidates:
                    QMessageBox.information(self, "Not found", "No locations found for that query.")
                    return
                suggestions = [f"{c.get('name')}, {c.get('country')}" + (f", {c.get('state')}" if c.get('state') else "") for c in candidates]
                self.completer.model().setStringList(list(dict.fromkeys(suggestions + self.history)))
                chosen = candidates[0]
                self._choose_location(chosen)

            elif tag == "onecall":
                self.set_loading(False)
                data = payload
                lockey = getattr(self, "_last_loc_key", None)
                if lockey:
                    cache_put(lockey, data)
                self._render_onecall(data)

            elif tag == "air":
                self.set_loading(False)
                data = payload
                self._render_air(data)

        except Exception as e:
            self.set_loading(False)
            QMessageBox.critical(self, "Error", f"Processing network result failed: {e}")

    def _on_network_error(self, errtuple, tag):
        self.set_loading(False)
        exc, tb = errtuple
        QMessageBox.warning(self, "Network Error", f"Error during {tag}: {exc}\nSee console for details")
        print(tb)

    # Selection + fetching
    def _choose_location(self, candidate: dict):
        lat = candidate.get("lat")
        lon = candidate.get("lon")
        if lat is None or lon is None:
            QMessageBox.warning(self, "Location error", "Selected location has no coordinates.")
            return
        name = candidate.get("name")
        country = candidate.get("country")
        state = candidate.get("state")
        self.current_location = {"name": name, "lat": lat, "lon": lon, "country": country, "state": state}
        label = f"{name}, {country}" + (f", {state}" if state else "")
        if label not in self.history:
            self.history.append(label)
            self._save_history()
            self.completer.model().setStringList(self.history)

        lockey = f"onecall::{lat:.4f},{lon:.4f}::units={self.units}"
        self._last_loc_key = lockey
        cached = cache_get(lockey)
        if cached:
            self._render_onecall(cached)
            return

        self.set_loading(True)
        worker = NetworkWorker(self._do_onecall, lat, lon, self.units, self.lang, tag="onecall")
        worker.signals.finished.connect(self._on_network_success)
        worker.signals.error.connect(self._on_network_error)
        self.threadpool.start(worker)

    def _do_onecall(self, lat, lon, units, lang):
        return self.owm.onecall(lat, lon, units=units, lang=lang, exclude="minutely")

    def fetch_weather_for_location(self, loc_dict):
        self._choose_location(loc_dict)

    # Rendering
    def _render_onecall(self, data):
        tz_offset = data.get("timezone_offset", 0)
        cur = data.get("current", {})
        hourly = data.get("hourly", [])
        daily = data.get("daily", [])

        loc = self.current_location
        if loc:
            city_label = f"{loc['name']}, {loc['country']}"
            if loc.get("state"):
                city_label += f", {loc['state']}"
            self.lbl_city.setText(city_label)
        temp = cur.get("temp")
        if temp is not None:
            deg = "°C" if self.units == "metric" else "°F"
            self.lbl_temp.setText(f"{temp:.1f} {deg}")
        desc = ""
        weather_main = None
        if cur.get("weather"):
            w0 = cur.get("weather")[0] if isinstance(cur.get("weather"), list) and cur.get("weather") else {}
            desc = w0.get("description", "").capitalize()
            weather_main = w0.get("main") or desc
            icon = w0.get("icon")
            self._set_icon(icon, weather_main)
        self.lbl_desc.setText(desc)

        sunrise_ts = cur.get("sunrise")
        sunset_ts = cur.get("sunset")
        sunrise = utc_to_local(sunrise_ts, tz_offset) if sunrise_ts else None
        sunset = utc_to_local(sunset_ts, tz_offset) if sunset_ts else None
        if sunrise and sunset:
            hours = (sunset - sunrise).total_seconds() / 3600.0
            self.lbl_extra.setText(f"Sunrise: {sunrise.time()}  Sunset: {sunset.time()}  ({hours:.1f}h)")
        else:
            self.lbl_extra.setText("")

        lat, lon = loc["lat"], loc["lon"]
        cache_key_air = f"air::{lat:.4f},{lon:.4f}"
        cached_air = cache_get(cache_key_air)
        if cached_air:
            self._render_air(cached_air)
        else:
            worker = NetworkWorker(self.owm.air_pollution, lat, lon, tag="air")
            worker.signals.finished.connect(self._on_network_success)
            worker.signals.error.connect(self._on_network_error)
            self.threadpool.start(worker)

        self._build_forecast_cards(daily, tz_offset)
        self._plot_hourly(hourly, tz_offset)

    def _render_air(self, airdata):
        try:
            if not airdata:
                self.lbl_aqi.setText("AQI: -")
                self.lbl_pollutants.setText("")
                return
            rec = airdata.get("list", [{}])[0]
            main = rec.get("main", {})
            components = rec.get("components", {})
            aqi = main.get("aqi")
            self.lbl_aqi.setText(f"AQI: {aqi if aqi is not None else '-'}")
            level_text, color = ("-", "#999999")
            if isinstance(aqi, int) and aqi in AQI_LEVELS:
                level_text, color = AQI_LEVELS[aqi]
            self.aqi_status.setText(level_text)
            # colored circular indicator
            self.aqi_indicator.setStyleSheet(f"border-radius:9px; background: {color}; border: 1px solid rgba(0,0,0,0.15);")

            # Map AQI 1-5 to percentage for visual bar
            try:
                perf = 0
                if isinstance(aqi, int):
                    perf = int(round(((aqi - 1) / 4.0) * 100))
                self.aqi_progress.setValue(perf)
                # dynamic chunk color
                chunk_style = f"""
                QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {color}, stop:1 #ffffff22); border-radius:6px; }}
                QProgressBar {{ background: rgba(255,255,255,0.03); border-radius:6px; height:12px; }}
                """
                self.aqi_progress.setStyleSheet(chunk_style)
            except Exception:
                pass

            comp_lines = []
            for k, v in components.items():
                full = POLLUTANT_FULL.get(k.lower(), k.upper())
                comp_lines.append(f"{full}: {v}")
            self.lbl_pollutants.setText('\n'.join(comp_lines))

            loc = self.current_location
            if loc:
                cache_put(f"air::{loc['lat']:.4f},{loc['lon']:.4f}", airdata)
        except Exception as e:
            print("Error rendering air:", e)

    def _set_icon(self, icon_code: str, weather_main: str | None = None):
        """Download & cache icon, then apply a colorize effect depending on weather_main so icons pop from their background."""
        if not icon_code:
            self.icon_label.clear()
            return

        icon_cache = CACHE_DIR / f"icon_{icon_code}.png"
        pix = QPixmap()
        if icon_cache.exists():
            pix.load(str(icon_cache))
            self.icon_label.setPixmap(pix)
        else:
            url = ICON_URL.format(icon=icon_code)
            try:
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    icon_cache.write_bytes(r.content)
                    pix.loadFromData(r.content)
                    self.icon_label.setPixmap(pix)
                else:
                    raise Exception("HTTP %s" % r.status_code)
            except Exception:
                if FALLBACK_ICON_PATH and pathlib.Path(FALLBACK_ICON_PATH).exists():
                    self.icon_label.setPixmap(QPixmap(FALLBACK_ICON_PATH))
                else:
                    self.icon_label.clear()
                    return

        # apply colorize effect to make icon stronger against backgrounds
        color_hex = _weather_main_to_color(weather_main) if weather_main else "#6b7280"
        try:
            effect = QGraphicsColorizeEffect()
            effect.setColor(QColor(color_hex))
            effect.setStrength(0.35)
            self.icon_label.setGraphicsEffect(effect)
        except Exception:
            # If colorize not available, ignore silently
            pass

    def _build_forecast_cards(self, daily, tz_offset_secs):
        # clear old widgets
        for i in reversed(range(self.forecast_layout.count())):
            w = self.forecast_layout.takeAt(i).widget()
            if w:
                w.deleteLater()

        for day in daily[:7]:
            dt = utc_to_local(day.get("dt"), tz_offset_secs)
            day_name = dt.strftime("%a %d %b") if dt else "-"
            temp_min = day.get("temp", {}).get("min", 0.0) or 0.0
            temp_max = day.get("temp", {}).get("max", 0.0) or 0.0
            pop = day.get("pop", 0) * 100
            weather = day.get("weather", [{}])[0]
            icon = weather.get("icon")
            weather_main = weather.get("main") if weather.get("main") else weather.get("description")

            card = QGroupBox(day_name)
            card.setFixedWidth(140)
            v = QVBoxLayout()
            icon_lbl = QLabel()
            icon_lbl.setProperty("class", "icon-small")
            icon_lbl.setFixedSize(72, 72)
            icon_lbl.setScaledContents(True)
            pm = QPixmap()
            if icon:
                try:
                    icon_cache = CACHE_DIR / f"icon_{icon}.png"
                    if icon_cache.exists():
                        pm.load(str(icon_cache))
                        icon_lbl.setPixmap(pm)
                    else:
                        r = requests.get(ICON_URL.format(icon=icon), timeout=6)
                        if r.status_code == 200:
                            icon_cache.write_bytes(r.content)
                            pm.loadFromData(r.content)
                            icon_lbl.setPixmap(pm)
                except Exception:
                    pass

            # colorize small icon to increase contrast
            color_hex = _weather_main_to_color(weather_main)
            try:
                eff = QGraphicsColorizeEffect()
                eff.setColor(QColor(color_hex))
                eff.setStrength(0.4)
                icon_lbl.setGraphicsEffect(eff)
            except Exception:
                pass

            v.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            txt = QLabel(f"<b>{temp_max:.0f}/{temp_min:.0f}</b> {('°C' if self.units=='metric' else '°F')}\nPOP: {pop:.0f}%")
            txt.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt.setWordWrap(True)
            v.addWidget(txt)
            card.setLayout(v)
            self.forecast_layout.addWidget(card)

        self.forecast_layout.addStretch(1)

    def _plot_hourly(self, hourly, tz_offset_secs):
        x = []
        temps = []
        pops = []
        winds = []
        labels = []
        for h in hourly[:48]:
            ts = h.get("dt")
            dt = utc_to_local(ts, tz_offset_secs)
            x.append(dt)
            temps.append(h.get("temp", math.nan))
            pops.append((h.get("pop", 0)) * 100)
            winds.append(h.get("wind_speed", 0))
            labels.append(dt.strftime("%H:%M") if dt else "")

        xs = list(range(len(x)))

        # Temperature plot - purple-blue gradient pen with soft fill
        self.plot_temp.clear()
        if temps:
            pen_temp = pg.mkPen(color=(124,58,237), width=3)  # purple
            fill_brush = pg.mkBrush(124, 58, 237, 80)
            self.plot_temp.plot(xs, temps, pen=pen_temp, symbol='o', symbolSize=6, symbolBrush=(124,58,237))
            self.plot_temp.plot(xs, temps, pen=pen_temp, fillLevel=min(temps)-5, brush=fill_brush)
            ax = self.plot_temp.getAxis('bottom')
            ax.setTicks([[(i, labels[i]) for i in range(0, len(labels), max(1, len(labels)//8))]])
            self.plot_temp.setLabel('left', 'Temperature', units='°C' if self.units=='metric' else '°F')
            self.plot_temp.getPlotItem().showGrid(x=True, y=True, alpha=0.25)

        # POP plot - cyan-blue bar/line with translucent area
        self.plot_pop.clear()
        if pops:
            pen_pop = pg.mkPen(color=(6,182,212), width=2)  # cyan
            brush_pop = pg.mkBrush(6,182,212,90)
            self.plot_pop.plot(xs, pops, pen=pen_pop, fillLevel=0, brush=brush_pop, symbol='t', symbolSize=6)
            ax = self.plot_pop.getAxis('bottom')
            ax.setTicks([[(i, labels[i]) for i in range(0, len(labels), max(1, len(labels)//8))]])
            self.plot_pop.setLabel('left', 'Precipitation', units='%')

        # Wind plot - green/teal
        self.plot_wind.clear()
        if winds:
            pen_w = pg.mkPen(color=(34,197,94), width=2)
            self.plot_wind.plot(xs, winds, pen=pen_w, symbol='x', symbolSize=6)
            ax = self.plot_wind.getAxis('bottom')
            ax.setTicks([[(i, labels[i]) for i in range(0, len(labels), max(1, len(labels)//8))]])
            self.plot_wind.setLabel('left', 'Wind Speed', units='m/s')

# ---- Main ----
def main():
    app = QApplication(sys.argv)
    w = WeatherMainWindow()
    w.show()
    if not OPENWEATHER_API_KEY:
        QMessageBox.warning(None, "API Key missing", "No OpenWeather API key found. Please set OPENWEATHER_API_KEY in your environment or .env file.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()