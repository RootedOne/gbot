from fastapi import FastAPI, Request, Response, Header, HTTPException
from fastapi.responses import JSONResponse
import httpx
import time
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("vpn-relay")

app = FastAPI(
    title="VPN Bot Middle Server Relay",
    version="1.0.0",
    description="Stateless proxy to relay bot requests to 3X-UI panel servers.",
)

# Start time for uptime metric
START_TIME = time.time()

# Shared auth token (to ensure only authorized bot can use the relay)
RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "default-secure-relay-token")

# In-memory metrics
metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "total_latency_ms": 0.0,
}


@app.get("/health")
async def health_check():
    """Health check endpoint displaying uptime, metrics, and configuration status."""
    uptime = time.time() - START_TIME
    avg_latency = 0.0
    if metrics["total_requests"] > 0:
        avg_latency = metrics["total_latency_ms"] / metrics["total_requests"]

    return {
        "status": "healthy",
        "uptime_seconds": round(uptime, 2),
        "relay_token_configured": RELAY_TOKEN != "default-secure-relay-token",
        "metrics": {
            "total_requests": metrics["total_requests"],
            "successful_requests": metrics["successful_requests"],
            "failed_requests": metrics["failed_requests"],
            "avg_latency_ms": round(avg_latency, 2),
        },
    }


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def relay_proxy(
    request: Request,
    path: str,
    x_relay_token: str = Header(None),
    x_relay_target_url: str = Header(None),
    x_relay_target_token: str = Header(None),
    x_relay_target_verify_tls: str = Header("true"),
):
    """Generic stateless proxy to relay HTTP requests to the target panel."""
    # 1. Authenticate the bot
    if not x_relay_token or x_relay_token != RELAY_TOKEN:
        logger.warning("Unauthorized access attempt. X-Relay-Token mismatch.")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid X-Relay-Token.")

    # 2. Validate target headers
    if not x_relay_target_url:
        raise HTTPException(status_code=400, detail="Missing X-Relay-Target-URL header.")

    target_base = x_relay_target_url.rstrip("/")
    # Construct complete target URL
    target_url = f"{target_base}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    # Verify TLS setting
    verify_tls = x_relay_target_verify_tls.lower() == "true"

    logger.info("Relaying request: %s %s -> %s", request.method, path, target_base)

    # 3. Read body contents
    body = await request.body()

    # 4. Prepare request headers to target
    headers = {
        "Accept": "application/json",
    }
    if x_relay_target_token:
        headers["Authorization"] = f"Bearer {x_relay_target_token}"
    if request.headers.get("content-type"):
        headers["Content-Type"] = request.headers.get("content-type")

    # 5. Execute proxied request
    start_time = time.time()
    metrics["total_requests"] += 1

    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
            )
        
        latency_ms = (time.time() - start_time) * 1000.0
        metrics["total_latency_ms"] += latency_ms

        logger.info(
            "Relayed response: status=%s, latency=%sms",
            resp.status_code,
            round(latency_ms, 2)
        )

        if resp.status_code >= 400:
            metrics["failed_requests"] += 1
        else:
            metrics["successful_requests"] += 1

        # Return target panel's exact body and status
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={
                "Content-Type": resp.headers.get("content-type", "application/json"),
            }
        )

    except httpx.HTTPError as exc:
        latency_ms = (time.time() - start_time) * 1000.0
        metrics["total_latency_ms"] += latency_ms
        metrics["failed_requests"] += 1
        logger.error("Relay target error to %s: %s", target_url, exc)
        return JSONResponse(
            status_code=502,
            content={"success": False, "msg": f"Middle Server failed to reach target panel: {exc}"}
        )
