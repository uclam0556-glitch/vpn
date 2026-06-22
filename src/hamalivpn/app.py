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
from .deeplinks import happ_deeplink, hiddify_deeplink, streisand_deeplink, v2raytun_deeplink
from .models import AuditLog, Customer, Subscription, as_utc
from .qr import qr_data_uri
from .remnawave import make_remnawave_gateway
from .services import (
    SubscriptionNotFoundError,
    dashboard_metrics,
    disable_subscription,
    get_subscription_by_token,
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "hamalivpn-control"}


@app.get("/connect/{access_token}", response_class=HTMLResponse)
async def connect_page(
    request: Request,
    access_token: str,
    session: SessionDep,
) -> HTMLResponse:
    subscription = await get_subscription_by_token(session, access_token)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    subscription_url = subscription.subscription_url or ""
    expires_at = as_utc(subscription.expires_at)
    return templates.TemplateResponse(
        request,
        "connect.html",
        {
            "subscription": subscription,
            "subscription_url": subscription_url,
            "qr": qr_data_uri(subscription_url) if subscription_url else None,
            "hiddify_link": hiddify_deeplink(subscription_url, settings.subscription_name),
            "v2raytun_link": v2raytun_deeplink(subscription_url),
            "happ_link": happ_deeplink(subscription_url),
            "streisand_link": streisand_deeplink(subscription_url),
            "expired": expires_at <= datetime.now(UTC),
            "support_username": settings.support_username,
        },
    )


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
