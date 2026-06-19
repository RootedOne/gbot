from __future__ import annotations

import base64
import json
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse


class LinkParseError(Exception):
    """Raised when a share link cannot be parsed into an Xray outbound."""


def _b64decode(data: str) -> bytes:
    data = data.strip()
    # Support url-safe and standard base64 with missing padding.
    data = data.replace("-", "+").replace("_", "/")
    padding = (-len(data)) % 4
    return base64.b64decode(data + "=" * padding)


def _first(qs: Dict[str, list], key: str, default: str = "") -> str:
    val = qs.get(key)
    if not val:
        return default
    return val[0]


def _stream_settings(opts: Dict[str, Any]) -> Dict[str, Any]:
    """Build Xray streamSettings from a normalized options dict."""
    network = opts.get("network") or "tcp"
    security = opts.get("security") or "none"

    stream: Dict[str, Any] = {"network": network, "security": security}

    # Security layer
    if security == "tls":
        tls: Dict[str, Any] = {}
        if opts.get("sni"):
            tls["serverName"] = opts["sni"]
        if opts.get("fp"):
            tls["fingerprint"] = opts["fp"]
        if opts.get("alpn"):
            tls["allowInsecure"] = False
            tls["alpn"] = [a for a in str(opts["alpn"]).split(",") if a]
        stream["tlsSettings"] = tls
    elif security == "reality":
        reality: Dict[str, Any] = {}
        if opts.get("sni"):
            reality["serverName"] = opts["sni"]
        if opts.get("fp"):
            reality["fingerprint"] = opts["fp"]
        if opts.get("pbk"):
            reality["publicKey"] = opts["pbk"]
        if opts.get("sid") is not None:
            reality["shortId"] = opts.get("sid", "")
        if opts.get("spx"):
            reality["spiderX"] = opts["spx"]
        stream["realitySettings"] = reality

    # Transport layer
    if network == "ws":
        ws: Dict[str, Any] = {"path": opts.get("path") or "/"}
        if opts.get("host"):
            ws["headers"] = {"Host": opts["host"]}
        stream["wsSettings"] = ws
    elif network == "httpupgrade":
        hu: Dict[str, Any] = {"path": opts.get("path") or "/"}
        if opts.get("host"):
            hu["host"] = opts["host"]
        stream["httpupgradeSettings"] = hu
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": opts.get("serviceName") or opts.get("path") or "",
            "multiMode": opts.get("mode") == "multi",
        }
    elif network in ("http", "h2"):
        stream["network"] = "http"
        http: Dict[str, Any] = {"path": opts.get("path") or "/"}
        if opts.get("host"):
            http["host"] = [h for h in str(opts["host"]).split(",") if h]
        stream["httpSettings"] = http
    elif network == "tcp":
        header_type = opts.get("headerType") or "none"
        if header_type == "http":
            tcp_header: Dict[str, Any] = {"type": "http", "request": {}}
            if opts.get("path"):
                tcp_header["request"]["path"] = [
                    p for p in str(opts["path"]).split(",") if p
                ]
            if opts.get("host"):
                tcp_header["request"]["headers"] = {
                    "Host": [h for h in str(opts["host"]).split(",") if h]
                }
            stream["tcpSettings"] = {"header": tcp_header}

    return stream


def _parse_vless(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    uuid = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    qs = parse_qs(parsed.query)

    opts = {
        "network": _first(qs, "type", "tcp"),
        "security": _first(qs, "security", "none"),
        "sni": _first(qs, "sni") or _first(qs, "peer"),
        "fp": _first(qs, "fp"),
        "alpn": unquote(_first(qs, "alpn")),
        "pbk": _first(qs, "pbk"),
        "sid": _first(qs, "sid"),
        "spx": unquote(_first(qs, "spx")),
        "flow": _first(qs, "flow"),
        "path": unquote(_first(qs, "path")),
        "host": unquote(_first(qs, "host")),
        "serviceName": unquote(_first(qs, "serviceName")),
        "headerType": _first(qs, "headerType"),
        "mode": _first(qs, "mode"),
    }
    user: Dict[str, Any] = {"id": uuid, "encryption": "none"}
    if opts["flow"]:
        user["flow"] = opts["flow"]
    return {
        "protocol": "vless",
        "tag": "proxy",
        "settings": {
            "vnext": [{"address": host, "port": int(port), "users": [user]}]
        },
        "streamSettings": _stream_settings(opts),
    }


def _parse_trojan(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    password = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    qs = parse_qs(parsed.query)
    opts = {
        "network": _first(qs, "type", "tcp"),
        "security": _first(qs, "security", "tls"),
        "sni": _first(qs, "sni") or _first(qs, "peer"),
        "fp": _first(qs, "fp"),
        "alpn": unquote(_first(qs, "alpn")),
        "path": unquote(_first(qs, "path")),
        "host": unquote(_first(qs, "host")),
        "serviceName": unquote(_first(qs, "serviceName")),
        "headerType": _first(qs, "headerType"),
    }
    return {
        "protocol": "trojan",
        "tag": "proxy",
        "settings": {
            "servers": [{"address": host, "port": int(port), "password": password}]
        },
        "streamSettings": _stream_settings(opts),
    }


def _parse_vmess(url: str) -> Dict[str, Any]:
    raw = url[len("vmess://"):]
    try:
        decoded = _b64decode(raw).decode("utf-8")
        cfg = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise LinkParseError(f"Invalid vmess link: {exc}") from exc

    net = str(cfg.get("net", "tcp"))
    tls = str(cfg.get("tls", ""))
    opts = {
        "network": "http" if net == "h2" else net,
        "security": "tls" if tls in ("tls", "reality") else "none",
        "sni": cfg.get("sni") or cfg.get("host", ""),
        "fp": cfg.get("fp", ""),
        "alpn": cfg.get("alpn", ""),
        "path": cfg.get("path", ""),
        "host": cfg.get("host", ""),
        "serviceName": cfg.get("path", "") if net == "grpc" else "",
        "headerType": cfg.get("type", "none"),
    }
    try:
        port = int(cfg.get("port", 443))
        alter_id = int(cfg.get("aid", 0))
    except (TypeError, ValueError):
        port, alter_id = 443, 0
    user = {
        "id": cfg.get("id", ""),
        "alterId": alter_id,
        "security": cfg.get("scy") or "auto",
    }
    return {
        "protocol": "vmess",
        "tag": "proxy",
        "settings": {
            "vnext": [{"address": cfg.get("add", ""), "port": port, "users": [user]}]
        },
        "streamSettings": _stream_settings(opts),
    }


def _parse_shadowsocks(url: str) -> Dict[str, Any]:
    body = url[len("ss://"):]
    if "#" in body:
        body = body.split("#", 1)[0]
    # strip plugin query if any
    if "?" in body:
        body = body.split("?", 1)[0]

    method = password = host = ""
    port = 8388
    if "@" in body:
        userinfo, server = body.rsplit("@", 1)
        try:
            method_pwd = _b64decode(userinfo).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            method_pwd = unquote(userinfo)
        if ":" in method_pwd:
            method, password = method_pwd.split(":", 1)
        host, _, port_str = server.rpartition(":")
        port = int(port_str) if port_str.isdigit() else 8388
    else:
        decoded = _b64decode(body).decode("utf-8")
        creds, server = decoded.rsplit("@", 1)
        method, password = creds.split(":", 1)
        host, _, port_str = server.rpartition(":")
        port = int(port_str) if port_str.isdigit() else 8388

    return {
        "protocol": "shadowsocks",
        "tag": "proxy",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": port,
                    "method": method,
                    "password": password,
                }
            ]
        },
    }


def parse_share_link(url: str) -> Dict[str, Any]:
    """Convert a vless/vmess/trojan/ss share link into an Xray outbound dict."""
    url = url.strip()
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme == "vless":
        return _parse_vless(url)
    if scheme == "trojan":
        return _parse_trojan(url)
    if scheme == "vmess":
        return _parse_vmess(url)
    if scheme in ("ss", "shadowsocks"):
        return _parse_shadowsocks(url)
    raise LinkParseError(
        f"Unsupported link scheme '{scheme}'. "
        "Supported: vless, vmess, trojan, ss (hysteria is not supported by xray-core)."
    )


def build_xray_config(outbound: Dict[str, Any], socks_port: int) -> Dict[str, Any]:
    """Wrap an outbound into a full Xray config with a local SOCKS inbound."""
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            }
        ],
        "outbounds": [
            outbound,
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "blocked"},
        ],
    }
