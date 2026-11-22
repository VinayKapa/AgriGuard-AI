from datetime import date, timedelta
import math


def weather(latitude: float, longitude: float) -> list[dict]:
    """Deterministic synthetic 14-day weather series for a stable, offline demo."""
    seed = abs(latitude * 17 + longitude * 9)
    rows = []
    for day in range(14):
        wave = math.sin((day + seed) * 0.72)
        rows.append({
            "date": (date.today() + timedelta(days=day)).isoformat(),
            "rainfall_mm": round(max(0, 7 + 11 * math.sin(day * 0.64 + seed)), 1),
            "max_temp_c": round(31 + 3.8 * wave, 1),
            "min_temp_c": round(23 + 2.1 * math.sin(day * 0.42 + seed), 1),
        })
    return rows


def historical(latitude: float, longitude: float) -> dict:
    seed = abs(latitude * 17 + longitude * 9)
    return {
        "period": "Illustrative 10-year seasonal baseline",
        "mean_rainfall_mm": round(720 + 120 * math.sin(seed), 0),
        "rainfall_anomaly_pct": round(12 * math.sin(seed * 0.7), 1),
        "hot_days_per_season": round(8 + 6 * abs(math.sin(seed * 0.3))),
        "note": "Synthetic baseline for demo; replace with a verified local climate dataset before operational decisions.",
    }
