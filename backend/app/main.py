import json
import httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.schemas import FarmRequest, CropSuggestionRequest
from app.auth import router as auth_router, current_user
from app.database import connect, initialize
from app.services.demo_data import weather as demo_weather, historical as demo_historical
from app.services.risk_engine import assess
from app.services.weather import get_open_meteo
from app.services.crops import suggest_crops
from app.services.satellite import get_sentinel_observation, is_configured

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
async def crop_suggestions(request: CropSuggestionRequest, user=Depends(current_user)):
    if not settings.open_meteo_enabled:
        forecast = demo_weather(request.latitude, request.longitude)
        historical = demo_historical(request.latitude, request.longitude)
        return suggest_crops(request, forecast, historical, "demo")
    try:
        forecast, historical, _ = await get_open_meteo(request.latitude, request.longitude)
        return suggest_crops(request, forecast, historical, "live")
    except httpx.HTTPError as exc:
        forecast = demo_weather(request.latitude, request.longitude)
        historical = demo_historical(request.latitude, request.longitude)
        result = suggest_crops(request, forecast, historical, "demo")
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            result["warning"] = "The live weather provider is temporarily rate-limiting requests. These clearly labeled demo values let you continue; try again later for a live forecast."
        else:
            result["warning"] = "Live weather is temporarily unavailable. These clearly labeled demo values let you continue; try again later for a live forecast."
        return result
    except Exception:
        raise HTTPException(status_code=502, detail="Weather data could not be processed. Please retry in a few minutes.")


@app.post("/api/assess")
async def create_assessment(request: FarmRequest, user=Depends(current_user)):
    forecast = demo_weather(request.latitude, request.longitude)
    historical = demo_historical(request.latitude, request.longitude)
    current = {"temperature_c": forecast[0]["max_temp_c"], "rainfall_mm": forecast[0]["rainfall_mm"], "humidity_pct": 72, "wind_kph": 10}
    mode = "demo"
    if settings.open_meteo_enabled:
        try:
            forecast, historical, current = await get_open_meteo(request.latitude, request.longitude)
            mode = "live"
        except Exception:
            # Keep the local demo available if a remote provider is unavailable.
            pass
    try:
        satellite = await get_sentinel_observation(request.latitude, request.longitude)
    except Exception as exc:
        satellite = {"configured": is_configured(), "available": False, "ndvi": None, "source": f"Copernicus request failed: {str(exc)[:180]}", "image_base64": None}
    return assess(request, forecast, historical, current, mode, satellite)
