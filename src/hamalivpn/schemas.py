from datetime import datetime

from pydantic import BaseModel, HttpUrl


class RemoteUser(BaseModel):
    uuid: str
    short_uuid: str
    username: str
    subscription_url: str
    expire_at: datetime
    device_limit: int | None = None


class TrialResult(BaseModel):
    subscription_id: str
    access_token: str
    subscription_url: HttpUrl
    connect_url: HttpUrl
    expires_at: datetime
    device_limit: int
    traffic_limit_gb: int
