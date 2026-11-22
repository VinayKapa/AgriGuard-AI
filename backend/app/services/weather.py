from datetime import date, timedelta
from time import monotonic, time
import json
import httpx
from app.database import connect

_CACHE = {}
_CACHE_TTL_SECONDS = 15 * 60
_STALE_CACHE_TTL_SECONDS = 6 * 60 * 60


def _cache_key(latitude: float, longitude: float) -> str:
    return f"{latitude:.3f},{longitude:.3f}"


def _load_persistent(key: str):
    try:
        with connect() as db:
            row = db.execute("SELECT payload, fetched_at FROM weather_cache WHERE cache_key=?", (key,)).fetchone()
        if row:
            return json.loads(row["payload"]), float(row["fetched_at"])
    except Exception:
        pass
    return None


def _save_persistent(key: str, data: tuple, fetched_at: float) -> None:
    try:
        with connect() as db:
            db.execute(
                "INSERT INTO weather_cache(cache_key,payload,fetched_at) VALUES(?,?,?) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
                (key, json.dumps(data), fetched_at),
            )
    except Exception:
        # Weather remains usable when an optional cache write fails.
        pass


def _with_cache_age(data: tuple, fetched_at: float, now: float) -> tuple:
    forecast, historical, current = data
    historical = dict(historical)
    historical["weather_cache_age_minutes"] = max(1, round((now - fetched_at) / 60))
    return forecast, historical, current

async def get_open_meteo(latitude: float, longitude: float) -> tuple[list[dict], dict, dict]:
    """Fetch forecast plus a comparable historical period when network access is available."""
    key = _cache_key(latitude, longitude)
    now = monotonic()
    cached = _CACHE.get(key)
    if cached and now < cached["fresh_until"]:
        return cached["data"]
    persistent = _load_persistent(key)
    if persistent:
        payload, fetched_at = persistent
        age = time() - fetched_at
        if age < _CACHE_TTL_SECONDS:
            data = _with_cache_age(tuple(payload), fetched_at, time())
            _CACHE[key] = {"data": data, "fresh_until": now + max(1, _CACHE_TTL_SECONDS - age)}
            return data
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=13)).isoformat()
    archive_start = (date.today() - timedelta(days=3650)).isoformat()
    archive_end = (date.today() - timedelta(days=5)).isoformat()
    base = "https://api.open-meteo.com/v1/forecast"
    common = {"latitude": latitude, "longitude": longitude, "timezone": "auto", "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min"}
    async with httpx.AsyncClient(timeout=10) as client:
        forecast_response = await client.get(base, params={**common, "forecast_days": 14})
        try:
            forecast_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                old = persistent or ((cached["data"], cached["fetched_at"]) if cached else None)
                if old and time() - old[1] <= _STALE_CACHE_TTL_SECONDS:
                    return _with_cache_age(tuple(old[0]), old[1], time())
            raise
        raw = forecast_response.json()["daily"]
        forecast = [{"date": raw["time"][i], "rainfall_mm": raw["precipitation_sum"][i] or 0, "max_temp_c": raw["temperature_2m_max"][i], "min_temp_c": raw["temperature_2m_min"][i]} for i in range(len(raw["time"]))]
        try:
            archive_response = await client.get("https://archive-api.open-meteo.com/v1/archive", params={**common, "start_date": archive_start, "end_date": archive_end})
            archive_response.raise_for_status()
            archive = archive_response.json()["daily"]
        except (httpx.HTTPError, KeyError, ValueError):
            summary = {
                "period": None,
                "source": "Open-Meteo archive unavailable",
                "mean_rainfall_mm": None,
                "rainfall_anomaly_pct": None,
                "seasonal_rainfall_normals_mm": {},
                "latest_kharif_rainfall_mm": None,
                "hot_days_per_season": None,
                "note": "Historical archive is temporarily unavailable; only the live forecast is included.",
            }
            current = {"temperature_c": forecast[0]["max_temp_c"], "rainfall_mm": forecast[0]["rainfall_mm"], "humidity_pct": None, "wind_kph": None}
            result = (forecast, summary, current)
            _CACHE[key] = {"data": result, "fresh_until": now + _CACHE_TTL_SECONDS, "fetched_at": time()}
            _save_persistent(key, result, _CACHE[key]["fetched_at"])
            return result
    annual = {}
    seasonal = {"Kharif": {}, "Rabi": {}, "Zaid": {}}
    for i, day in enumerate(archive["time"]):
        year = int(day[:4])
        month = int(day[5:7])
        rainfall = archive["precipitation_sum"][i] or 0
        annual.setdefault(year, 0.0)
        annual[year] += rainfall
        if 6 <= month <= 9:
            seasonal["Kharif"].setdefault(year, 0.0)
            seasonal["Kharif"][year] += rainfall
        if month >= 10 or month <= 3:
            season_year = year + 1 if month >= 10 else year
            seasonal["Rabi"].setdefault(season_year, 0.0)
            seasonal["Rabi"][season_year] += rainfall
        if 3 <= month <= 5:
            seasonal["Zaid"].setdefault(year, 0.0)
            seasonal["Zaid"][year] += rainfall
    values = [value for year, value in annual.items() if year < date.today().year]
    seasonal_normals = {}
    for season_name, season_years in seasonal.items():
        completed = [value for year, value in season_years.items() if year < date.today().year]
        seasonal_normals[season_name] = round(sum(completed[-10:]) / len(completed[-10:]), 1) if completed else None
    current_kharif = seasonal["Kharif"].get(date.today().year)
    kharif_normal = seasonal_normals["Kharif"]
    anomaly_pct = round((current_kharif - kharif_normal) / kharif_normal * 100, 1) if current_kharif is not None and kharif_normal else 0
    summary = {
        "period": f"{archive_start} to {archive_end} · Open-Meteo archive",
        "source": "Open-Meteo archive",
        "mean_rainfall_mm": round(sum(values) / len(values), 0) if values else 0,
        "rainfall_anomaly_pct": anomaly_pct,
        "seasonal_rainfall_normals_mm": seasonal_normals,
        "latest_kharif_rainfall_mm": round(current_kharif, 1) if current_kharif is not None else None,
        "hot_days_per_season": 0,
        "note": "Seasonal totals are calendar-month approximations from archived reanalysis, not official district monsoon records.",
    }
    current = {"temperature_c": forecast[0]["max_temp_c"], "rainfall_mm": forecast[0]["rainfall_mm"], "humidity_pct": None, "wind_kph": None}
    result = (forecast, summary, current)
    fetched_at = time()
    _CACHE[key] = {"data": result, "fresh_until": now + _CACHE_TTL_SECONDS, "fetched_at": fetched_at}
    _save_persistent(key, result, fetched_at)
    if len(_CACHE) > 256:
        oldest_key = min(_CACHE, key=lambda item: _CACHE[item]["fresh_until"])
        _CACHE.pop(oldest_key, None)
    return result
