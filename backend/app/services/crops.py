from app.schemas import CropSuggestionRequest

# A transparent screening table, not a trained crop-yield model. The ranges are
# broad agronomic heuristics; regional calendars and extension guidance are needed
# before these suggestions should be used for actual planting decisions.
CROP_PROFILES = [
    {"crop": "Rice", "seasons": ["Kharif", "Rabi"], "temp": (20, 35), "rain": (700, 2400), "water": "high"},
    {"crop": "Maize", "seasons": ["Kharif", "Rabi", "Zaid"], "temp": (18, 34), "rain": (250, 1400), "water": "medium"},
    {"crop": "Cotton", "seasons": ["Kharif"], "temp": (21, 38), "rain": (450, 1300), "water": "medium"},
    {"crop": "Groundnut", "seasons": ["Kharif", "Zaid"], "temp": (20, 35), "rain": (300, 1100), "water": "medium"},
    {"crop": "Wheat", "seasons": ["Rabi"], "temp": (8, 27), "rain": (100, 650), "water": "low"},
    {"crop": "Millet", "seasons": ["Kharif", "Zaid"], "temp": (24, 42), "rain": (100, 1000), "water": "low"},
    {"crop": "Soybean", "seasons": ["Kharif"], "temp": (20, 34), "rain": (400, 1300), "water": "medium"},
    {"crop": "Sugarcane", "seasons": ["Kharif", "Rabi", "Zaid"], "temp": (20, 38), "rain": (700, 2400), "water": "high"},
    {"crop": "Chickpea", "seasons": ["Rabi"], "temp": (10, 30), "rain": (100, 600), "water": "low"},
]


def suggest_crops(request: CropSuggestionRequest, forecast: list[dict], historical: dict, source_mode: str) -> dict:
    next_days = forecast[:14]
    if not next_days:
        raise ValueError("Weather provider returned an empty forecast")
    average_temp = sum((row["max_temp_c"] + row["min_temp_c"]) / 2 for row in next_days) / len(next_days)
    rain_next = sum(row["rainfall_mm"] for row in next_days)
    season_normal = (historical.get("seasonal_rainfall_normals_mm") or {}).get(request.season)
    season_normal = season_normal if season_normal is not None else historical.get("mean_rainfall_mm", 0) / 3
    anomaly = historical.get("rainfall_anomaly_pct", 0)
    ranked = []
    for profile in CROP_PROFILES:
        low_temp, high_temp = profile["temp"]
        low_rain, high_rain = profile["rain"]
        temp_gap = max(0, low_temp - average_temp, average_temp - high_temp)
        temp_score = max(0, 100 - temp_gap * 5)
        rain_gap = max(0, low_rain - season_normal, season_normal - high_rain)
        rain_score = max(0, 100 - rain_gap / max(1, high_rain - low_rain) * 100)
        season_score = 100 if request.season in profile["seasons"] else 52
        score = round(.40 * season_score + .35 * temp_score + .25 * rain_score)
        reasons = [f"{average_temp:.1f}°C average temperature across the next 14 days"]
        if season_normal:
            reasons.append(f"{season_normal:.0f} mm typical {request.season} rainfall in the historical archive")
        if anomaly:
            reasons.append(f"latest Kharif rainfall anomaly: {anomaly:+.0f}% against its archive mean")
        if request.season not in profile["seasons"]:
            reasons.append(f"{profile['crop']} is usually better aligned with {', '.join(profile['seasons'])}")
        if profile["water"] == "high" and rain_next < 20:
            reasons.append("little rain is forecast in the next 14 days; reliable irrigation would be important")
        ranked.append({"crop": profile["crop"], "score": score, "water_need": profile["water"], "reasons": reasons})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {
        "season": request.season,
        "source_mode": source_mode,
        "weather_source": "Open-Meteo live forecast" if source_mode == "live" else "Synthetic demo forecast",
        "historical_source": historical.get("source", "Illustrative seasonal baseline"),
        "weather_signals": {"average_temperature_14d_c": round(average_temp, 1), "rainfall_14d_mm": round(rain_next, 1), "seasonal_rainfall_normal_mm": round(season_normal, 1) if season_normal else None, "latest_kharif_anomaly_pct": anomaly},
        "suggestions": ranked[:5],
        "method_note": "Weather-fit screening from live forecast, archived seasonal rainfall, broad temperature/rainfall bands, and selected season. This is a shortlist, not an AI-trained agronomic prescription; soil, irrigation, variety and local crop calendars must also be checked.",
    }
