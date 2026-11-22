from base64 import b64encode
from datetime import date, timedelta
from math import cos, log, pi, radians, tan
from time import time
import httpx
from app.config import settings

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STATS_URL = "https://sh.dataspace.copernicus.eu/statistics/v1"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
_token: str | None = None
_token_expiry = 0.0
_cache: dict[tuple[float, float], tuple[float, dict]] = {}

EVALSCRIPT_STATS = """//VERSION=3
function setup(){return {input:[{bands:["B04","B08","SCL","dataMask"]}],output:[{id:"ndvi",bands:1,sampleType:"FLOAT32"},{id:"dataMask",bands:1}]}}
function evaluatePixel(s){let sum=s.B08+s.B04;let valid=s.dataMask && sum>0 && ![3,6,8,9,10,11].includes(s.SCL);return {ndvi:[valid?(s.B08-s.B04)/sum:0],dataMask:[valid?1:0]}}
"""
EVALSCRIPT_IMAGE = """//VERSION=3
function setup(){return {input:[{bands:["B04","B03","B02","SCL","dataMask"]}],output:{bands:4,sampleType:"UINT8"}}}
function evaluatePixel(s){let valid=s.dataMask && ![3,6,8,9,10,11].includes(s.SCL);return valid?[s.B04*255,s.B03*255,s.B02*255,255]:[0,0,0,0]}
"""


def is_configured() -> bool:
    return bool(settings.cdse_client_id and settings.cdse_client_secret)


async def _get_token(client: httpx.AsyncClient) -> str:
    global _token, _token_expiry
    if _token and time() < _token_expiry - 60:
        return _token
    response = await client.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": settings.cdse_client_id, "client_secret": settings.cdse_client_secret})
    response.raise_for_status()
    data = response.json()
    _token = data["access_token"]
    _token_expiry = time() + int(data.get("expires_in", 300))
    return _token


def _bbox(latitude: float, longitude: float, radius_m: int = 500) -> list[float]:
    radius_earth = 6_378_137
    safe_latitude = max(-85.0, min(85.0, latitude))
    x = radius_earth * radians(longitude)
    y = radius_earth * log(tan(pi / 4 + radians(safe_latitude) / 2))
    return [x - radius_m, y - radius_m, x + radius_m, y + radius_m]


async def get_sentinel_observation(latitude: float, longitude: float) -> dict:
    if not is_configured():
        return {"configured": False, "available": False, "ndvi": None, "source": "Copernicus OAuth credentials not configured", "image_base64": None, "observed_period": None}
    key = (round(latitude, 4), round(longitude, 4))
    cached = _cache.get(key)
    if cached and time() - cached[0] < 6 * 60 * 60:
        return cached[1]
    now = date.today()
    time_range = {"from": f"{(now-timedelta(days=75)).isoformat()}T00:00:00Z", "to": f"{now.isoformat()}T23:59:59Z"}
    bounds = {"bbox": _bbox(latitude, longitude), "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/3857"}}
    async with httpx.AsyncClient(timeout=35) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
        stats_payload = {
            "input": {"bounds": bounds, "data": [{"type": "sentinel-2-l2a", "dataFilter": {"timeRange": time_range, "maxCloudCoverage": 40}}]},
            "aggregation": {"timeRange": time_range, "aggregationInterval": {"of": "P1M"}, "evalscript": EVALSCRIPT_STATS, "resx": 10, "resy": 10},
        }
        stats_response = await client.post(STATS_URL, headers=headers, json=stats_payload)
        stats_response.raise_for_status()
        records = stats_response.json().get("data", [])
        observations = []
        for record in records:
            stats = record.get("outputs", {}).get("ndvi", {}).get("bands", {}).get("B0", {}).get("stats", {})
            mean = stats.get("mean")
            if mean is not None and stats.get("sampleCount", 0) > 0:
                observations.append((record.get("interval", {}).get("to"), float(mean), stats.get("sampleCount", 0)))
        if not observations:
            return {"configured": True, "available": False, "ndvi": None, "source": "Copernicus connected; no clear Sentinel-2 observation in the last 75 days", "image_base64": None, "observed_period": time_range["from"][:10] + " to " + time_range["to"][:10]}
        observed_date, ndvi, pixels = max(observations, key=lambda row: row[0] or "")
        image_payload = {
            "input": {"bounds": bounds, "data": [{"type": "sentinel-2-l2a", "dataFilter": {"timeRange": time_range, "maxCloudCoverage": 40, "mosaickingOrder": "leastCC"}}]},
            "output": {"width": 256, "height": 256, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
            "evalscript": EVALSCRIPT_IMAGE,
        }
        image_response = await client.post(PROCESS_URL, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=image_payload)
        image_response.raise_for_status()
    lat_delta = 500 / 111_320
    lon_delta = 500 / max(1, 111_320 * cos(radians(latitude)))
    image_bounds = [longitude - lon_delta, latitude - lat_delta, longitude + lon_delta, latitude + lat_delta]
    result = {"configured": True, "available": True, "ndvi": round(ndvi, 3), "condition": "Healthy" if ndvi >= .6 else "Watch" if ndvi >= .35 else "Stressed", "source": "Copernicus Sentinel-2 L2A · cloud-masked mean NDVI", "observed_period": observed_date, "sample_pixels": pixels, "image_base64": b64encode(image_response.content).decode("ascii"), "bounds": image_bounds}
    _cache[key] = (time(), result)
    return result
