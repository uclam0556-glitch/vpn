from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "HamaliVpn"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    auto_create_schema: bool = True

    database_url: str = "sqlite+aiosqlite:///./data/hamalivpn.db"
    redis_url: str = "redis://redis:6379/0"
    redis_fallback_url: str = ""

    bot_token: SecretStr = SecretStr("")
    bot_username: str = "HamaliVpn_bot"
    admin_telegram_ids: str = ""
    support_username: str = "Hamali_Support"
    news_channel_username: str = "hamalivpn"

    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("change-me")
    session_secret: SecretStr = SecretStr("change-me-session-secret")
    secure_cookies: bool = False
    portal_session_ttl_seconds: int = 4 * 60 * 60
    portal_auth_attempts: int = 10
    portal_auth_window_seconds: int = 5 * 60
    portal_legacy_bearer_enabled: bool = True

    public_base_url: str = "http://localhost:8080"
    # Public activation endpoints. In production, configure the fallback on a
    # different provider/ASN so an ISP route failure cannot take down both.
    activation_base_url: str = ""
    activation_fallback_base_url: str = ""
    subscription_base_url: str = ""
    subscription_fallback_base_url: str = ""
    panel_base_url: str = "http://localhost:3000"
    remnawave_api_token: SecretStr = SecretStr("")
    remnawave_mock: bool = True
    remnawave_internal_squads: str = ""

    # Trial access is issued once; repeated button taps never move the expiry.
    trial_access_days: Literal[2] = 2
    trial_traffic_gb: int = 0
    trial_device_limit: Literal[1] = 1

    default_plan_code: str = "start"
    subscription_name: str = "HamaliVpn"
    hiddify_enabled: bool = True
    v2raytun_enabled: bool = True
    subscription_probe_timeout_seconds: float = 8
    subscription_health_interval_seconds: int = 300
    subscription_health_batch_size: int = 25
    subscription_probe_user_agent: str = "Happ/4.11.0/ios/2606031844510"
    premium_emoji_json: str = ""
    hysteria_legacy_password: SecretStr = SecretStr("")
    hysteria_legacy_nodes: str = (
        "67.159.56.63,85.137.249.225,103.112.69.188,45.92.218.178,92.119.166.192"
    )
    platega_merchant_id: str = ""
    platega_api_key: SecretStr = SecretStr("")
    platega_api_base_url: str = "https://app.platega.io"

    @property
    def admin_ids(self) -> set[int]:
        return {
            int(item.strip())
            for item in self.admin_telegram_ids.split(",")
            if item.strip().isdigit()
        }

    @property
    def squad_uuids(self) -> list[str]:
        return [item.strip() for item in self.remnawave_internal_squads.split(",") if item.strip()]

    @property
    def hysteria_legacy_node_set(self) -> set[str]:
        return {item.strip() for item in self.hysteria_legacy_nodes.split(",") if item.strip()}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
