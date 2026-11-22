# AgriGuard AI — Predictive Climate & Micro-Location Yield Engine

AgriGuard is a local hackathon MVP for farm setup, weather-informed crop screening, climate-risk monitoring and an illustrative yield-risk estimate. Create an account, set a location, compare crop suggestions, and inspect the field dashboard.

## Architecture

```text
React + Vite + Leaflet dashboard
  ├─ Account sign-in and required farm onboarding
  ├─ OpenStreetMap farm picker and geocoding
  ├─ Forecast-based crop shortlist and weather charts
  └─ Risk indicators, source status, and recommendations
          │ authenticated API
          ▼
FastAPI + SQLite
  ├─ Open-Meteo forecast and historical archive adapter
  ├─ Optional Copernicus Sentinel-2 NDVI and imagery adapter
  ├─ Transparent crop/risk screening rules
  └─ Per-account local farm profiles
```

## Folder structure

```text
agriguard/
├── README.md
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   └── app/
│       ├── main.py, auth.py, config.py, database.py, schemas.py
│       └── services/
│           ├── demo_data.py
│           ├── weather.py
│           ├── crops.py
│           ├── satellite.py
│           └── risk_engine.py
└── frontend/
    ├── package.json
    └── src/ (React pages, farm setup and dashboard styles)
```

## Run locally

Requirements: Python 3.10+ and Node.js 20+.

### 1. Start the API

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

API health: <http://localhost:8000/api/health> · API docs: <http://localhost:8000/docs>

### 2. Start the dashboard in a second terminal

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `demo` | Runtime label. |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Allowed local web origin. |
| `DATABASE_PATH` | `backend/agriguard.sqlite3` | Optional absolute path for durable SQLite storage. |
| `OPEN_METEO_ENABLED` | `true` | Use live forecast/archive when enabled; no API key required. |
| `CDSE_CLIENT_ID` | empty | Optional Copernicus Data Space OAuth client ID. |
| `CDSE_CLIENT_SECRET` | empty | Optional Copernicus Data Space OAuth secret; backend only. |
| `VITE_API_URL` | `http://localhost:8000` | Frontend API address, if different. |

Keep Copernicus credentials in `backend/.env` only. Register a Copernicus Data Space OAuth client, add both credentials, then restart the API to request Sentinel-2 L2A observations. The app will clearly show unavailable when credentials are absent or when there is no usable clear scene. The dashboard refreshes every 15 minutes while open and when the browser regains focus.

To check Copernicus OAuth from the backend environment without printing credentials or tokens, run `python check_copernicus.py` from the `backend` directory.

## Demo flow

1. Create an account or sign in.
2. Search for a village/town or click the map to pin the farm; select a growing season.
3. Click **Suggest crops from live weather**. Review the forecast and historical rainfall signals, then choose a crop.
4. Optionally enter measured soil moisture and pH; leave them blank if you have no field reading.
5. Save the farm and inspect the forecast, climate indicators, map, source labels, yield-risk proxy and recommendations.
6. Move the map pin or update the crop/season/soil readings to refresh the assessment. Sign out and back in to confirm that the local farm profile persists.

For an offline demo, set `OPEN_METEO_ENABLED=false` in `backend/.env` and restart the API. The UI labels crop suggestions as demo weather in this mode. If live weather fails while enabled, crop suggestions display an error and can be retried; the dashboard may show its labeled fallback.

## Data and modeling boundaries

- Open-Meteo supplies live daily forecast and historical reanalysis precipitation when reachable. Approximate seasonal sums are grouped by calendar months; these are not official district monsoon records.
- Crop recommendations are explainable screening rules based on selected season, broad temperature/rain ranges, next-14-day weather, and archived seasonal rainfall. They are not a trained AI model or agronomic prescription.
- Sentinel-2 observation and map imagery are requested from Copernicus Data Space only when backend OAuth credentials are configured. A clear scene is not guaranteed.
- ISRO Bhuvan is listed as a reference service; no live per-farm Bhuvan NDVI feed is integrated here. Soil moisture and pH are user-entered, not connected sensor telemetry.
- Risk scores and yield estimate are transparent illustrative heuristics, not validated predictions. Do not use the yield figure as a planting or financial decision.
- The API adapters provide clear seams for verified local monsoon datasets, sensor integration, and a future trained scikit-learn/XGBoost model.

## Deploy on Render

The included `render.yaml` creates a FastAPI web service and a React static site, connects their public URLs, enables HTTPS through Render, and mounts a persistent disk for the SQLite database. The API uses HttpOnly secure session cookies in production, limits repeated login/registration attempts, and accepts browser write requests only from the configured frontend origin.

1. Push the `agriguard` folder to a GitHub repository. In Render, choose **New → Blueprint**, connect that repository, and select the folder containing `render.yaml` if the repository has other projects.
2. Create the services from the Blueprint. The API uses a paid Starter service because SQLite needs a persistent disk; the disk is mounted at `/var/data`.
3. When prompted, add the Copernicus `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET` values as Render secrets if you want satellite queries. Leave both unset if you only need live Open-Meteo weather and historical rainfall.
4. Deploy both services. Open the static site URL shown by Render and create an account. The Blueprint wires the frontend API address and the API's allowed frontend origin automatically.
5. Confirm the API health check is green at `/api/health`, then try sign-in, farm setup, crop suggestions, and the dashboard. Copernicus credentials can be valid while no cloud-free Sentinel scene is available for a given field/date.

Never commit `.env`, credentials, or database files. The local `.gitignore` excludes them. Rotating a Copernicus secret in Render requires a new API deploy. The session cookie is marked Secure and HttpOnly in production; the app remains configured for one API instance because SQLite is not intended for multi-instance scaling. Before opening registration broadly, configure account recovery/email delivery and an operational backup process; these require a mail provider and storage policy that this project does not assume. Yield and crop-risk outputs remain screening estimates and should not be treated as agronomic advice.

### Local frontend configuration

For local use, leave `VITE_API_URL` unset (it defaults to `http://localhost:8000`). For another frontend host, set it at build time to the full public API URL, including `https://`. Set `FRONTEND_ORIGIN` on the API to the exact public frontend origin, without a trailing slash.
