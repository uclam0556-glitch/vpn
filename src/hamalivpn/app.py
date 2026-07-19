from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from .auth import ensure_csrf, verify_credentials, verify_csrf
from .config import get_settings
from .db import create_schema, get_session
from .deeplinks import (
    happ_deeplink,
    hiddify_deeplink,
    incy_deeplink,
    incy_subscription_url,
    streisand_deeplink,
    v2raytun_deeplink,
)
from .device_slots import (
    DeviceLimitReached,
    active_device_slot_count,
    device_subscription_url,
    ensure_device_slot,
)
from .models import AuditLog, Customer, Subscription, SubscriptionDevice, as_utc
from .qr import qr_data_uri
from .remnawave import make_remnawave_gateway
from .services import (
    SubscriptionNotFoundError,
    dashboard_metrics,
    disable_subscription,
    get_subscription_by_token,
    refresh_subscription_access,
    subscription_short_code,
)

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.auto_create_schema:
        await create_schema()
    yield


app = FastAPI(
    title="HamaliVpn Control",
    version="0.1.0",
    docs_url="/api/docs" if settings.debug else None,
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    https_only=settings.secure_cookies,
    same_site="strict",
    max_age=60 * 60 * 12,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def admin_guard(request: Request) -> RedirectResponse | None:
    if request.session.get("admin") is not True:
        return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


def client_ip(request: Request) -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )


def _connect_import_path(access_token: str, client: str) -> str:
    return f"/{access_token}/import/{client}"


def _connect_links(access_token: str, subscription_url: str) -> dict[str, str]:
    if not subscription_url:
        return {
            "hiddify_link": _connect_import_path(access_token, "hiddify"),
            "v2raytun_link": _connect_import_path(access_token, "v2raytun"),
            "happ_link": _connect_import_path(access_token, "happ"),
            "incy_link": _connect_import_path(access_token, "incy"),
            "streisand_link": _connect_import_path(access_token, "streisand"),
            "manual_link": _connect_import_path(access_token, "manual"),
        }
    return {
        "hiddify_link": hiddify_deeplink(subscription_url, settings.subscription_name),
        "v2raytun_link": v2raytun_deeplink(subscription_url),
        "happ_link": happ_deeplink(subscription_url),
        "incy_link": incy_deeplink(subscription_url, settings.subscription_name),
        "streisand_link": streisand_deeplink(subscription_url),
        "manual_link": "",
    }


def _connect_response(
    request: Request,
    access_token: str,
    subscription: Subscription,
    *,
    subscription_url: str = "",
    slot: SubscriptionDevice | None = None,
    expired: bool = False,
    limit_reached: bool = False,
    device_slots_used: int = 0,
) -> HTMLResponse:
    public_code = subscription_short_code(subscription)
    links = _connect_links(public_code, subscription_url)
    return templates.TemplateResponse(
        request,
        "connect.html",
        {
            "subscription": subscription,
            "subscription_url": subscription_url,
            "incy_subscription_url": incy_subscription_url(subscription_url),
            "qr": qr_data_uri(subscription_url) if subscription_url else None,
            **links,
            "expired": expired,
            "limit_reached": limit_reached,
            "activation_pending": bool(not expired and not limit_reached and not subscription_url),
            "device_slot": slot,
            "device_slots_used": device_slots_used,
            "support_username": settings.support_username,
            "health_status": subscription.health_status,
            "health_message": subscription.health_message,
            "endpoint_count": subscription.health_endpoint_count,
        },
    )


def _set_slot_cookie(
    response: HTMLResponse | RedirectResponse, subscription: Subscription, slot: SubscriptionDevice
) -> None:
    max_age = max(60, int((as_utc(subscription.expires_at) - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        f"hamali_slot_{subscription.id}",
        slot.device_token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )


@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "bot_username": settings.bot_username.lstrip("@"),
            "support_username": settings.support_username.lstrip("@"),
        },
    )


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "hamalivpn-control"}


@app.get("/connect/{access_token}/import/{client_name}", response_class=HTMLResponse)
async def connect_import_page(
    request: Request,
    access_token: str,
    client_name: str,
    session: SessionDep,
):
    subscription = await get_subscription_by_token(session, access_token)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    expires_at = as_utc(subscription.expires_at)
    expired = expires_at <= datetime.now(UTC)
    if expired:
        return _connect_response(request, access_token, subscription, expired=True)

    gateway = make_remnawave_gateway(settings)
    try:
        slot = await ensure_device_slot(
            session,
            gateway,
            settings,
            subscription,
            existing_token=request.cookies.get(f"hamali_slot_{subscription.id}"),
            client_ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
        )
        subscription_url = device_subscription_url(settings, subscription, slot)
        await session.commit()
    except DeviceLimitReached:
        device_slots_used = await active_device_slot_count(session, subscription.id)
        return _connect_response(
            request,
            access_token,
            subscription,
            limit_reached=True,
            device_slots_used=device_slots_used,
        )

    links = _connect_links(access_token, subscription_url)
    client = client_name.lower().strip()
    deeplink = {
        "hiddify": links["hiddify_link"],
        "v2raytun": links["v2raytun_link"],
        "happ": links["happ_link"],
        "incy": links["incy_link"],
        "streisand": links["streisand_link"],
    }.get(client, "")

    if client == "manual" or not deeplink:
        response = _connect_response(
            request,
            access_token,
            subscription,
            subscription_url=subscription_url,
            slot=slot,
        )
    else:
        response = RedirectResponse(url=deeplink, status_code=status.HTTP_302_FOUND)
    _set_slot_cookie(response, subscription, slot)
    return response


@app.get("/connect/{access_token}", response_class=HTMLResponse)
async def connect_page(
    request: Request,
    access_token: str,
    session: SessionDep,
) -> HTMLResponse:
    subscription = await get_subscription_by_token(session, access_token)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    expires_at = as_utc(subscription.expires_at)
    expired = expires_at <= datetime.now(UTC)
    subscription_url = ""
    slot = None
    limit_reached = False
    device_slots_used = 0

    if not expired:
        existing_token = request.cookies.get(f"hamali_slot_{subscription.id}")
        if existing_token:
            existing_slot = await session.scalar(
                select(SubscriptionDevice).where(
                    SubscriptionDevice.device_token == existing_token,
                    SubscriptionDevice.subscription_id == subscription.id,
                    SubscriptionDevice.is_active.is_(True),
                )
            )
            if existing_slot:
                gateway = make_remnawave_gateway(settings)
                try:
                    slot = await ensure_device_slot(
                        session,
                        gateway,
                        settings,
                        subscription,
                        existing_token=existing_token,
                        client_ip=client_ip(request),
                        user_agent=request.headers.get("User-Agent", ""),
                    )
                    subscription_url = device_subscription_url(settings, subscription, slot)
                    await session.commit()
                except DeviceLimitReached:
                    limit_reached = True
                    device_slots_used = await active_device_slot_count(session, subscription.id)

    response = _connect_response(
        request,
        access_token,
        subscription,
        subscription_url=subscription_url,
        slot=slot,
        expired=expired,
        limit_reached=limit_reached,
        device_slots_used=device_slots_used,
    )
    if slot:
        _set_slot_cookie(response, subscription, slot)
    return response


@app.get("/demo/sub/{short_uuid}", response_class=PlainTextResponse)
async def demo_subscription(short_uuid: str) -> PlainTextResponse:
    return PlainTextResponse(
        f"# HamaliVpn mock subscription {short_uuid}\n"
        "# Подключите реальную Remnawave-ноду, чтобы здесь появились конфигурации.\n",
        headers={
            "profile-title": "base64:SGFtYWxpVnBu",
            "subscription-userinfo": "upload=0; download=0; total=32212254720",
        },
    )


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"csrf": ensure_csrf(request), "error": None},
    )


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
) -> HTMLResponse:
    verify_csrf(request, csrf)
    if not verify_credentials(settings, username=username, password=password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"csrf": ensure_csrf(request), "error": "Неверный логин или пароль"},
            status_code=401,
        )
    request.session.clear()
    request.session["admin"] = True
    ensure_csrf(request)
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/logout")
async def admin_logout(request: Request, csrf: str = Form(...)) -> RedirectResponse:
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    session: SessionDep,
) -> HTMLResponse:
    redirect = admin_guard(request)
    if redirect:
        return redirect
    metrics = await dashboard_metrics(session)
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .options(selectinload(Subscription.customer))
            .order_by(desc(Subscription.created_at))
            .limit(12)
        )
    ).all()
    events = (
        await session.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(10))
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "metrics": metrics,
            "subscriptions": subscriptions,
            "events": events,
            "csrf": ensure_csrf(request),
            "now": datetime.now(UTC),
        },
    )


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    session: SessionDep,
) -> HTMLResponse:
    redirect = admin_guard(request)
    if redirect:
        return redirect
    customers = (
        await session.scalars(
            select(Customer)
            .options(selectinload(Customer.subscriptions))
            .order_by(desc(Customer.created_at))
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "users.html",
        {"customers": customers, "csrf": ensure_csrf(request), "now": datetime.now(UTC)},
    )


@app.post("/admin/subscriptions/{subscription_id}/disable")
async def admin_disable_subscription(
    request: Request,
    subscription_id: str,
    session: SessionDep,
    csrf: str = Form(...),
) -> RedirectResponse:
    redirect = admin_guard(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    gateway = make_remnawave_gateway(settings)
    try:
        await disable_subscription(
            session,
            gateway,
            subscription_id,
            actor=f"admin:{settings.admin_username}",
        )
    except SubscriptionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Subscription not found") from error
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/subscriptions/{subscription_id}/repair")
async def admin_repair_subscription(
    request: Request,
    subscription_id: str,
    session: SessionDep,
    csrf: str = Form(...),
) -> RedirectResponse:
    redirect = admin_guard(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf)
    subscription = await session.get(Subscription, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    gateway = make_remnawave_gateway(settings)
    await refresh_subscription_access(
        session,
        gateway,
        settings,
        subscription,
        actor=f"admin:{settings.admin_username}",
    )
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/{access_token}/import/{client_name}", response_class=HTMLResponse)
async def short_connect_import_page(
    request: Request,
    access_token: str,
    client_name: str,
    session: SessionDep,
):
    return await connect_import_page(request, access_token, client_name, session)


@app.get("/{access_token}", response_class=HTMLResponse)
async def short_connect_page(
    request: Request,
    access_token: str,
    session: SessionDep,
) -> HTMLResponse:
    return await connect_page(request, access_token, session)
