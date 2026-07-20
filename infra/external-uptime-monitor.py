#!/usr/bin/env python3
"""Dependency-free uptime monitor intended for a host outside HamaliVPN production."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINTS = [
    item.strip()
    for item in os.getenv(
        "HAMALI_UPTIME_ENDPOINTS",
        "https://portal.hamali.ru/api/health,https://app.hamali.ru/api/health",
    ).split(",")
    if item.strip()
]
STATE_FILE = pathlib.Path(os.getenv("HAMALI_UPTIME_STATE", "/var/lib/hamali-uptime/state.json"))
BOT_TOKEN = os.getenv("HAMALI_TECH_BOT_TOKEN", "")
CHAT_ID = os.getenv("HAMALI_TECH_CHAT_ID", "")
TIMEOUT = float(os.getenv("HAMALI_UPTIME_TIMEOUT", "10"))


def check(url: str) -> dict:
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "Hamali-Uptime/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl.create_default_context()) as response:
            body = response.read(4096)
            elapsed = round((time.monotonic() - started) * 1000)
            if response.status != 200:
                return {"ok": False, "error": f"HTTP {response.status}", "ms": elapsed}
            if url.rstrip("/").endswith(("/health", "/api/health")):
                payload = json.loads(body)
                if payload.get("status") != "ok":
                    return {"ok": False, "error": "invalid health payload", "ms": elapsed}
            return {"ok": True, "ms": elapsed}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"ok": False, "error": str(exc)[:160], "ms": None}


def notify(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        return
    payload = urllib.parse.urlencode(
        {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=payload, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT):
            pass
    except OSError:
        pass


def main() -> int:
    results = {url: check(url) for url in ENDPOINTS}
    failures = {url: data for url, data in results.items() if not data["ok"]}
    fingerprint = hashlib.sha256("\n".join(sorted(failures)).encode()).hexdigest()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    try:
        previous = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        pass

    if failures and previous.get("fingerprint") != fingerprint:
        lines = ["🚨 <b>HamaliVPN: внешняя проверка не пройдена</b>"]
        lines.extend(f"• {url}: {data['error']}" for url, data in failures.items())
        notify("\n".join(lines))
    elif not failures and previous.get("failed"):
        latency = " · ".join(f"{url}: {data['ms']} мс" for url, data in results.items())
        notify(f"✅ <b>HamaliVPN восстановлен</b>\n{latency}")

    STATE_FILE.write_text(
        json.dumps({"failed": bool(failures), "fingerprint": fingerprint, "results": results})
    )
    print(json.dumps(results, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
