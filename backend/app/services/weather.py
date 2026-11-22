from datetime import date, timedelta
from time import monotonic
import httpx
from app.services.demo_data import historical as demo_historical

_CACHE = {}
_CACHE_TTL_SECONDS = 15 * 60
_STALE_TTL_SECONDS = 6 * 60 * 60

async def get_open_meteo(latitude: float, longitude: float) -> tuple[list[dict], dict, dict]:
    """Fetch forecast plus a comparable historical period when network access is available."""
    key = (round(latitude, 3), round(longitude, 3))
    now = monotonic()
    cached = _CACHE.get(key)
    if cached and now < cached["fresh_until"]:
        return cached["data"]
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
            if exc.response.status_code == 429 and cached and now < cached["stale_until"]:
                return cached["data"]
            raise
        raw = forecast_response.json()["daily"]
        forecast = [{"date": raw["time"][i], "rainfall_mm": raw["precipitation_sum"][i] or 0, "max_temp_c": raw["temperature_2m_max"][i], "min_temp_c": raw["temperature_2m_min"][i]} for i in range(len(raw["time"]))]
        try:
            archive_response = await client.get("https://archive-api.open-meteo.com/v1/archive", params={**common, "start_date": archive_start, "end_date": archive_end})
            archive_response.raise_for_status()
            archive = archive_response.json()["daily"]
        except (httpx.HTTPError, KeyError, ValueError):
            summary = demo_historical(latitude, longitude)
            summary["source"] = "Illustrative demo baseline · archive unavailable"
            return forecast, summary, {"temperature_c": forecast[0]["max_temp_c"], "rainfall_mm": forecast[0]["rainfall_mm"], "humidity_pct": None, "wind_kph": None}
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
    _CACHE[key] = {"data": result, "fresh_until": now + _CACHE_TTL_SECONDS, "stale_until": now + _STALE_TTL_SECONDS}
    if len(_CACHE) > 256:
        oldest_key = min(_CACHE, key=lambda item: _CACHE[item]["fresh_until"])
        _CACHE.pop(oldest_key, None)
    return result
