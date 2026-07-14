from urllib.parse import urlparse

from .config import Settings


PRODUCTION_PUBLIC_BASE_URL = "https://portal.hamali.ru"
PRODUCTION_SUBSCRIPTION_BASE_URL = "https://sub.hamali.ru"


def _normalize_base_url(value: str | None) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid public URL: {value}")
    return value


def public_connect_base_urls(settings: Settings) -> tuple[str, ...]:
    """Return ordered, unique customer-facing activation origins."""
    primary = _normalize_base_url(settings.activation_base_url)
    if not primary:
        primary = (
            PRODUCTION_PUBLIC_BASE_URL
            if settings.is_production
            else _normalize_base_url(settings.public_base_url) or PRODUCTION_PUBLIC_BASE_URL
        )
    fallback = _normalize_base_url(settings.activation_fallback_base_url)
    return tuple(dict.fromkeys(url for url in (primary, fallback) if url))


def public_connect_base_url(settings: Settings) -> str:
    return public_connect_base_urls(settings)[0]


def public_subscription_base_urls(settings: Settings) -> tuple[str, ...]:
    """Return direct, browser-independent subscription origins."""
    primary = _normalize_base_url(settings.subscription_base_url)
    if not primary:
        primary = (
            PRODUCTION_SUBSCRIPTION_BASE_URL
            if settings.is_production
            else _normalize_base_url(settings.public_base_url) or PRODUCTION_SUBSCRIPTION_BASE_URL
        )
    fallback = _normalize_base_url(settings.subscription_fallback_base_url)
    return tuple(dict.fromkeys(url for url in (primary, fallback) if url))


def matching_public_base_url(settings: Settings, request_url: str) -> str:
    """Keep imports on the same reachable origin that served activation UI."""
    request_host = urlparse(request_url).hostname
    for base in public_connect_base_urls(settings):
        if urlparse(base).hostname == request_host:
            return base
    return public_connect_base_url(settings)
