from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SOS_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./sos.db"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 8883
    mqtt_user: str = "backend"
    mqtt_password: str = ""
    mqtt_tls: bool = True
    mqtt_ca: str = ""
    app_id: str = ""              # ChirpStack application id (uuid)
    jwt_secret: str = "CHANGE_ME"
    jwt_hours: int = 12
    line_channel_token: str = ""
    line_channel_secret: str = ""
    public_url: str = "https://sos.example.org"
    tz: str = "Asia/Bangkok"
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_password: str = ""


env = Env()
