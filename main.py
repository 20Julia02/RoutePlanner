"""HTTP entry point for the route planner."""

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from network_preparation import prepare_network
from public_catalog import PublicNetworkCatalog
from prepared_store import PreparedNetworkStore
from security import (
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    SlidingWindowRateLimiter,
)
from web_service import (
    PlanningError,
    plan_prepared_network,
    plan_route,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
logger = logging.getLogger(__name__)


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Ignored invalid integer environment variable %s", name)
        return default


def _nonnegative_env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Ignored invalid integer environment variable %s", name)
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# A local installation is bounded only by the computer's available resources.
# Vercel requests still have to stay within the hosting platform's boundary.
DEFAULT_MAX_REQUEST_BYTES = 4_000_000 if os.getenv("VERCEL") else 0
MAX_REQUEST_BYTES = _nonnegative_env_int(
    "APP_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES
)
MAX_CONCURRENT_PLANS = _positive_env_int("APP_MAX_CONCURRENT_PLANS", 2)
PLAN_REQUESTS_PER_MINUTE = _positive_env_int("APP_PLAN_REQUESTS_PER_MINUTE", 10)
MAX_PUBLIC_NETWORK_BYTES = _positive_env_int("APP_MAX_PUBLIC_NETWORK_BYTES", 200_000_000)
ENABLE_DOCS = os.getenv("APP_ENABLE_DOCS", "").strip().lower() in {"1", "true", "yes"}
ALLOW_LOCAL_ADMIN = _env_flag("APP_ALLOW_LOCAL_ADMIN", default=not bool(os.getenv("VERCEL")))
DEFAULT_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver", "*.vercel.app"]
configured_hosts = [
    value.strip()
    for value in os.getenv("APP_ALLOWED_HOSTS", "").split(",")
    if value.strip()
]
ALLOWED_HOSTS = list(dict.fromkeys([*DEFAULT_ALLOWED_HOSTS, *configured_hosts]))
PUBLIC_DATA_HOSTS = tuple(
    value.strip()
    for value in os.getenv(
        "APP_PUBLIC_DATA_HOSTS", ".public.blob.vercel-storage.com"
    ).split(",")
    if value.strip()
)

network_store = PublicNetworkCatalog(
    BASE_DIR / "data" / "prepared",
    manifest_url=os.getenv("APP_PUBLIC_NETWORK_MANIFEST_URL"),
    manifest_sha256=os.getenv("APP_PUBLIC_NETWORK_MANIFEST_SHA256"),
    allowed_hosts=PUBLIC_DATA_HOSTS,
    maximum_document_bytes=MAX_PUBLIC_NETWORK_BYTES,
)
admin_store = PreparedNetworkStore(BASE_DIR / "data" / "prepared")
plan_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PLANS)
plan_rate_limiter = SlidingWindowRateLimiter(PLAN_REQUESTS_PER_MINUTE, 60)

app = FastAPI(
    title="Planer tras zwiedzania API",
    version="1.0.0",
    description="Planowanie wielodniowych tras pieszych.",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)
if MAX_REQUEST_BYTES:
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(SecurityHeadersMiddleware)


def _calculate_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Plan from raw input or reuse the prepared network selected by the client."""

    network_id = payload.get("network_id")
    if network_id:
        try:
            network = network_store.load(str(network_id))
        except FileNotFoundError as exc:
            raise PlanningError("Nie znaleziono wskazanego publicznego zestawu.") from exc
        except OSError as exc:
            logger.exception("Public network document could not be downloaded")
            raise PlanningError("Publiczny zestaw jest chwilowo niedostępny.") from exc
        except ValueError as exc:
            raise PlanningError("Publiczny zestaw jest uszkodzony lub niedostępny.") from exc
        return plan_prepared_network(network, payload)
    if not ALLOW_LOCAL_ADMIN:
        raise PlanningError("W publicznej aplikacji można korzystać tylko z przygotowanych zestawów.")
    return plan_route(payload)


@app.get("/api/health", tags=["system"])
def health() -> Dict[str, str]:
    """Report that the API process is ready."""

    return {"status": "ok", "engine": "open-source"}


def _local_admin_allowed(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    return ALLOW_LOCAL_ADMIN and client_host in {"127.0.0.1", "::1"}


@app.get("/api/config", tags=["system"])
def frontend_config(request: Request) -> Dict[str, bool]:
    """Expose non-sensitive feature flags needed by the browser UI."""

    return {"local_admin": _local_admin_allowed(request)}


def _require_local_admin(request: Request) -> None:
    if not _local_admin_allowed(request):
        raise HTTPException(status_code=404, detail="Nie znaleziono zasobu.")


@app.post("/api/admin/networks", tags=["local administration"])
def prepare_admin_network(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Prepare and persist a network only for a loopback administrator."""

    _require_local_admin(request)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Endpoint przyjmuje wyłącznie JSON.")
    try:
        network = prepare_network(payload, calculate_cost_matrix=True)
        metadata = admin_store.save(str(payload.get("name") or ""), network)
        return {"metadata": metadata}
    except PlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/plan", tags=["routing"])
def create_plan(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Calculate a bounded, stateless plan without persisting client data."""

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Endpoint przyjmuje wyłącznie JSON.")
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail="Żądania między witrynami są niedozwolone.")
    if not payload.get("network_id") and not _local_admin_allowed(request):
        raise HTTPException(
            status_code=422,
            detail="W publicznej aplikacji można korzystać tylko z przygotowanych zestawów.",
        )
    client_key = request.client.host if request.client else "unknown"
    if not plan_rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=429,
            detail="Przekroczono limit wyznaczania tras. Spróbuj ponownie za minutę.",
            headers={"Retry-After": "60"},
        )
    if not plan_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="Serwer wykonuje już maksymalną liczbę obliczeń. Spróbuj ponownie później.",
            headers={"Retry-After": "10"},
        )
    try:
        return _calculate_plan(payload)
    except PlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        plan_slots.release()


@app.get("/api/networks", tags=["data preparation"])
def list_prepared_networks() -> Dict[str, Any]:
    """List reusable prepared network summaries."""

    try:
        return {"items": network_store.list()}
    except (OSError, ValueError) as exc:
        logger.exception("Public network catalog could not be loaded")
        raise HTTPException(
            status_code=503,
            detail="Publiczne zestawy danych są chwilowo niedostępne.",
        ) from exc


@app.get("/api/networks/{network_id}/attractions", tags=["data preparation"])
def list_public_network_attractions(network_id: str) -> Dict[str, Any]:
    """List public attraction settings; edits are persisted by the browser."""

    try:
        return {"items": network_store.list_attractions(network_id)}
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono wskazanego publicznego zestawu."
        ) from exc
    except (OSError, ValueError) as exc:
        logger.exception("Public network attractions could not be loaded")
        raise HTTPException(
            status_code=503,
            detail="Atrakcje publicznego zestawu są chwilowo niedostępne.",
        ) from exc


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    """Serve the browser application entry point."""

    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
