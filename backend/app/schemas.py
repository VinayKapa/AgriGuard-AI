from datetime import date
from pydantic import BaseModel, Field


class FarmRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    location_name: str = "Selected farm"
    crop: str = "rice"
    season: str = "Kharif"
    soil_moisture: float | None = Field(default=None, ge=0, le=100)
    soil_ph: float | None = Field(default=None, ge=0, le=14)
    ndvi: float | None = Field(default=None, ge=-1, le=1)
    planting_date: str = Field(default_factory=lambda: date.today().isoformat())
    setup_complete: bool = False


class WeatherDay(BaseModel):
    date: str
    rainfall_mm: float
    max_temp_c: float
    min_temp_c: float


class Risk(BaseModel):
    level: str
    score: int
    label: str
    detail: str


class Assessment(BaseModel):
    location: dict
    crop: str
    season: str
    generated_at: str
    crop_stage: dict
    source_mode: str
    data_sources: list[dict]
    current_weather: dict
    historical_summary: dict
    satellite: dict
    soil: dict
    risks: dict[str, Risk]
    yield_estimate: dict = Field(alias="yield")
    recommendations: list[dict]
    forecast: list[WeatherDay]


class CropSuggestionRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    season: str = Field(default="Kharif", pattern="^(Kharif|Rabi|Zaid)$")
