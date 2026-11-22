from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "demo"
    frontend_origin: str = "http://localhost:5173"
    database_path: str = ""
    open_meteo_enabled: bool = True
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
