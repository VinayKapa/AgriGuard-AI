from datetime import date, datetime, timezone
from app.schemas import FarmRequest

CROP_BASELINE = {"rice": 4.2, "maize": 5.1, "cotton": 2.1, "groundnut": 2.4, "wheat": 3.6, "sugarcane": 72.0, "millet": 1.8, "soybean": 2.7}
CROP_DAYS = {"rice": 120, "maize": 110, "cotton": 160, "groundnut": 110, "wheat": 125, "sugarcane": 330, "millet": 95, "soybean": 110}


def _risk(score: float, label: str, detail: str, available: bool = True) -> dict:
    score = round(max(0, min(100, score)))
    level = "high" if score >= 67 else "moderate" if score >= 35 else "low"
    return {"score": score, "level": level, "label": label, "detail": detail, "available": available}


def assess(req: FarmRequest, forecast: list[dict], historical: dict, current: dict, mode: str, satellite: dict | None = None) -> dict:
    try:
        days_after_planting = max(0, (date.today() - date.fromisoformat(req.planting_date)).days)
    except ValueError:
        days_after_planting = 0
    growth_days = CROP_DAYS.get(req.crop.lower(), 120)
    growth_fraction = min(1, days_after_planting / growth_days)
    stage = "Establishment" if growth_fraction < .15 else "Vegetative growth" if growth_fraction < .40 else "Flowering / reproduction" if growth_fraction < .65 else "Grain or fruit fill" if growth_fraction < .88 else "Maturity / harvest window"
    rain_next = sum(item["rainfall_mm"] for item in forecast[:7])
    max_temp = max(item["max_temp_c"] for item in forecast[:7])
    heat = min(100, max(0, (max_temp - 29) * 11))
    anomaly = historical.get("rainfall_anomaly_pct", 0) or 0
    soil_dryness = max(0, 46 - req.soil_moisture) * 1.8 if req.soil_moisture is not None else 0
    drought = min(100, max(0, soil_dryness + max(0, -anomaly) * 0.8 + max(0, 55 - rain_next) * 0.38))
    excess = min(100, max(0, (rain_next - 65) * 0.8 + max(0, anomaly) * 0.8))
    soil_stress = min(100, (abs(req.soil_ph - 6.5) * 16 if req.soil_ph is not None else 0) + (max(0, 35 - req.soil_moisture) * 0.8 if req.soil_moisture is not None else 0))
    soil_detail = ", ".join((([f"pH {req.soil_ph:.1f}"] if req.soil_ph is not None else []) + ([f"moisture {req.soil_moisture:.0f}%"] if req.soil_moisture is not None else [])))
    ndvi = satellite.get("ndvi") if satellite else None
    if ndvi is None:
        ndvi = req.ndvi
    risks = {
        "drought": _risk(drought, "Dry spell risk", f"{rain_next:.0f} mm forecast over 7 days; soil moisture {req.soil_moisture:.0f}%" if req.soil_moisture is not None else f"{rain_next:.0f} mm forecast over 7 days; no field moisture reading supplied"),
        "heat": _risk(heat, "Heat stress", f"Peak forecast temperature {max_temp:.1f}°C"),
        "excess_rain": _risk(excess, "Heavy rain risk", f"{rain_next:.0f} mm forecast over 7 days"),
        "soil": _risk(soil_stress, "Soil readings", soil_detail if soil_detail else "No soil sensor or manual reading connected", req.soil_ph is not None or req.soil_moisture is not None),
    }
    # Interpretable proxy, deliberately not presented as trained or validated yield forecasting.
    climate_penalty = min(38, 0.20 * drought + 0.18 * heat + 0.16 * excess + 0.12 * soil_stress)
    vegetation_bonus = max(-8, min(5, (ndvi - 0.55) * 18)) if ndvi is not None else 0
    yield_risk = round(max(0, min(80, climate_penalty - vegetation_bonus)))
    baseline = CROP_BASELINE.get(req.crop.lower(), 3.0)
    estimated = round(baseline * (1 - yield_risk / 100), 2)
    recommendations = []
    if drought >= 35:
        recommendations.append({"priority": "High" if drought >= 67 else "Medium", "title": "Protect soil moisture", "body": "Check field moisture before irrigating; prioritize critical crop stages and consider mulching or alternate furrow irrigation where suitable."})
    if heat >= 35:
        recommendations.append({"priority": "High" if heat >= 67 else "Medium", "title": "Reduce heat stress", "body": "Schedule irrigation for cooler hours and inspect the crop during flowering; follow local extension guidance for heat protection."})
    if excess >= 35:
        recommendations.append({"priority": "High" if excess >= 67 else "Medium", "title": "Prepare field drainage", "body": "Clear field drains and avoid fertilizer application immediately before heavy rain; inspect low-lying plots after rainfall."})
    if soil_stress >= 30:
        recommendations.append({"priority": "Medium", "title": "Review soil test", "body": "Confirm pH and nutrient status with a local soil test before amending; the dashboard's soil signal is an estimate."})
    if not recommendations:
        recommendations.append({"priority": "Low", "title": "Keep monitoring", "body": "Conditions look relatively stable in this outlook. Re-check rainfall and field moisture after the next forecast update."})
    return {
        "location": {"name": req.location_name, "latitude": req.latitude, "longitude": req.longitude},
        "crop": req.crop, "season": req.season, "generated_at": datetime.now(timezone.utc).isoformat(),
        "crop_stage": {"stage": stage, "days_after_planting": days_after_planting, "estimated_season_progress_pct": round(growth_fraction * 100)},
        "source_mode": mode,
        "data_sources": [
            {"name": "Weather forecast", "status": "Live" if mode == "live" else "Recent" if mode == "cached" else "Sample" if mode == "sample" else "Unavailable", "detail": "Open-Meteo daily forecast" if mode == "live" else "Recent cached Open-Meteo forecast" if mode == "cached" else "User-selected sample data" if mode == "sample" else "Live forecast unavailable"},
            {"name": "Historical rainfall", "status": "Live" if historical.get("source") == "Open-Meteo archive" else "Sample" if mode == "sample" else "Unavailable", "detail": historical.get("source", "Historical archive unavailable")},
            {"name": "Copernicus Sentinel-2", "status": "Live" if satellite and satellite.get("available") else "Configured · no clear scene" if satellite and satellite.get("configured") else "Not connected", "detail": satellite.get("source", "Copernicus OAuth credentials not configured") if satellite else "Copernicus OAuth credentials not configured"},
            {"name": "ISRO Bhuvan", "status": "Reference layers", "detail": "OGC thematic services; current NDVI imagery is not wired into this farm view"},
            {"name": "Soil", "status": "Manual input" if req.soil_moisture is not None or req.soil_ph is not None else "Not provided", "detail": "User-entered reading; no automatic sensor feed configured" if req.soil_moisture is not None or req.soil_ph is not None else "No reading supplied; no sensor feed configured"},
        ],
        "current_weather": current, "historical_summary": historical,
        "satellite": satellite or {"ndvi": ndvi, "condition": "Healthy" if ndvi is not None and ndvi >= 0.6 else "Watch" if ndvi is not None and ndvi >= 0.35 else "Stressed" if ndvi is not None else "Not connected", "source": "User-entered NDVI" if req.ndvi is not None else "Copernicus Sentinel-2 credentials not configured"},
        "soil": {"moisture_pct": req.soil_moisture, "ph": req.soil_ph}, "risks": risks,
        "yield": {"risk_pct": yield_risk, "estimated_yield_t_ha": estimated, "baseline_yield_t_ha": baseline, "confidence": "Low · illustrative proxy", "method": "Transparent heuristic using climate stress and available satellite signals; not a trained or validated agronomic model."},
        "recommendations": recommendations, "forecast": forecast,
    }
