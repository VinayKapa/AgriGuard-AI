import json
import httpx
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.schemas import FarmRequest, CropSuggestionRequest
from app.auth import router as auth_router, current_user
from app.database import connect, initialize
from app.services.risk_engine import assess
from app.services.weather import get_open_meteo
from app.services.crops import suggest_crops
from app.services.satellite import get_sentinel_observation, is_configured
from app.services.demo_data import weather as sample_weather, historical as sample_historical

app = FastAPI(title="AgriGuard AI API", version="0.1.0", description="Hackathon MVP climate risk and crop impact API")
allowed_origins = [settings.frontend_origin.rstrip("/")]
if settings.app_env.lower() not in {"production", "prod"} and "http://localhost:5173" not in allowed_origins:
    allowed_origins.append("http://localhost:5173")
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization"])


@app.middleware("http")
async def protect_browser_writes(request, call_next):
    # Browser state-changing requests must originate from the configured app domain.
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin.rstrip("/") not in allowed_origins:
        return JSONResponse(status_code=403, content={"detail": "Request origin is not allowed."})
    return await call_next(request)
app.include_router(auth_router)


@app.on_event("startup")
def startup():
    if settings.app_env.lower() in {"production", "prod"} and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required in production. Configure the Supabase PostgreSQL connection string before starting the API.")
    initialize()


@app.get("/api/health")
def health():
    return {"status": "ok", "environment": settings.app_env, "database": "postgresql" if settings.database_url else "sqlite", "open_meteo_enabled": settings.open_meteo_enabled, "copernicus_configured": is_configured()}


@app.get("/api/geocode")
async def geocode(q: str, user=Depends(current_user)):
    if len(q.strip()) < 3:
        raise HTTPException(status_code=422, detail="Enter at least 3 characters.")
    try:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "AgriGuardAI/1.0 (farm location search)"}) as client:
            response = await client.get("https://nominatim.openstreetmap.org/search", params={"q": q, "format": "jsonv2", "limit": 5})
            response.raise_for_status()
            results = response.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Location search is temporarily unavailable. You can still select a point on the map.")
    return [{"name": row["display_name"], "latitude": float(row["lat"]), "longitude": float(row["lon"])} for row in results]


@app.get("/api/farm")
def get_farm(user=Depends(current_user)):
    with connect() as db:
        row = db.execute("SELECT payload FROM farms WHERE user_id=?", (user["id"],)).fetchone()
    if row:
        saved = json.loads(row["payload"])
        if not saved.get("setup_complete"):
            return {"configured": False}
        return {"configured": True, **saved}
    return {"configured": False}


@app.put("/api/farm")
def save_farm(request: FarmRequest, user=Depends(current_user)):
    if len(request.location_name.strip()) < 2:
        raise HTTPException(status_code=422, detail="Choose a farm location before saving.")
    request.setup_complete = True
    with connect() as db:
        db.execute("INSERT INTO farms(user_id,payload,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP", (user["id"], request.model_dump_json()))
    return {"ok": True, "farm": request.model_dump()}


@app.post("/api/crops/suggest")
async def crop_suggestions(request: CropSuggestionRequest, sample: bool = Query(default=False), user=Depends(current_user)):
    if sample:
        forecast = sample_weather(request.latitude, request.longitude)
        historical = sample_historical(request.latitude, request.longitude)
        historical["source"] = "User-selected sample rainfall baseline"
        result = suggest_crops(request, forecast, historical, "sample")
        result["method_note"] = "Sample weather data you selected because the live provider is rate-limiting requests. These values are illustrative and do not represent current field conditions."
        return result
    if not settings.open_meteo_enabled:
        raise HTTPException(status_code=503, detail="Live weather is currently disabled. Please try again later.")
    try:
        forecast, historical, _ = await get_open_meteo(request.latitude, request.longitude)
        cache_age = historical.get("weather_cache_age_minutes")
        return suggest_crops(request, forecast, historical, "cached" if cache_age is not None else "live")
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            raise HTTPException(status_code=503, detail="The live weather provider is temporarily rate-limiting requests. Please wait a few minutes and retry.")
        raise HTTPException(status_code=503, detail="Live weather is temporarily unavailable. Please retry in a few minutes.")
    except Exception:
        raise HTTPException(status_code=502, detail="Weather data could not be processed. Please retry in a few minutes.")


@app.post("/api/assess")
async def create_assessment(request: FarmRequest, user=Depends(current_user)):
    if request.weather_mode == "sample":
        forecast = sample_weather(request.latitude, request.longitude)
        historical = sample_historical(request.latitude, request.longitude)
        historical["source"] = "User-selected sample rainfall baseline"
        current = {"temperature_c": forecast[0]["max_temp_c"], "rainfall_mm": forecast[0]["rainfall_mm"], "humidity_pct": 72, "wind_kph": 10}
        satellite = {"configured": is_configured(), "available": False, "ndvi": None, "source": "Sample weather mode; no current satellite observation loaded", "image_base64": None}
        return assess(request, forecast, historical, current, "sample", satellite)
    if not settings.open_meteo_enabled:
        raise HTTPException(status_code=503, detail="Live weather is currently disabled. Please try again later.")
    try:
        forecast, historical, current = await get_open_meteo(request.latitude, request.longitude)
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            raise HTTPException(status_code=503, detail="The live weather provider is temporarily rate-limiting requests. Please retry shortly.")
        raise HTTPException(status_code=503, detail="Live weather is temporarily unavailable. Please retry shortly.")
    except Exception:
        raise HTTPException(status_code=502, detail="Weather data could not be processed. Please retry shortly.")
    mode = "cached" if historical.get("weather_cache_age_minutes") is not None else "live"
    try:
        satellite = await get_sentinel_observation(request.latitude, request.longitude)
    except Exception as exc:
        satellite = {"configured": is_configured(), "available": False, "ndvi": None, "source": f"Copernicus request failed: {str(exc)[:180]}", "image_base64": None}
    return assess(request, forecast, historical, current, mode, satellite)
