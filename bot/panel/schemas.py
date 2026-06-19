from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class InboundOption:
    id: int
    remark: str
    protocol: str
    port: int
    tls_flow_capable: bool = False

    @classmethod
    def from_api(cls, data: dict) -> "InboundOption":
        return cls(
            id=int(data.get("id", 0)),
            remark=str(data.get("remark", "")),
            protocol=str(data.get("protocol", "")),
            port=int(data.get("port", 0)),
            tls_flow_capable=bool(data.get("tlsFlowCapable", False)),
        )


@dataclass
class ClientTraffic:
    email: str
    up: int = 0
    down: int = 0
    total: int = 0
    expiry_time: int = 0
    enable: bool = True

    @property
    def used(self) -> int:
        return int(self.up) + int(self.down)

    @classmethod
    def from_api(cls, data: dict) -> "ClientTraffic":
        return cls(
            email=str(data.get("email", "")),
            up=int(data.get("up", 0) or 0),
            down=int(data.get("down", 0) or 0),
            total=int(data.get("total", 0) or 0),
            expiry_time=int(data.get("expiryTime", 0) or 0),
            enable=bool(data.get("enable", True)),
        )


@dataclass
class ClientInfo:
    email: str
    uuid: Optional[str] = None
    sub_id: Optional[str] = None
    total_gb: int = 0
    expiry_time: int = 0
    enable: bool = True
    tg_id: int = 0
    limit_ip: int = 0
    inbound_ids: List[int] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict) -> "ClientInfo":
        sub_id = data.get("subId") or data.get("sub_id")
        return cls(
            email=str(data.get("email", "")),
            uuid=data.get("uuid") or data.get("id"),
            sub_id=sub_id,
            total_gb=int(data.get("totalGB", 0) or 0),
            expiry_time=int(data.get("expiryTime", 0) or 0),
            enable=bool(data.get("enable", True)),
            tg_id=int(data.get("tgId", 0) or 0),
            limit_ip=int(data.get("limitIp", 0) or 0),
            inbound_ids=list(data.get("inboundIds", []) or []),
            raw=data,
        )
