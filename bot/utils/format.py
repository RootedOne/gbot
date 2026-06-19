from __future__ import annotations

import time

GB = 1024 ** 3


def gb_to_bytes(gb: float) -> int:
    return int(gb * GB)


def bytes_to_gb(value: int) -> float:
    return value / GB


def human_bytes(num: int) -> str:
    if not num:
        return "0 B"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0:
            text = f"{value:.2f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        value /= 1024.0
    return f"{value:.2f} PB"


def days_from_now_ms(days: int) -> int:
    """Return an epoch-ms timestamp `days` from now (0 -> 0 = never)."""
    if not days:
        return 0
    return int(time.time() * 1000) + days * 86400 * 1000


def now_ms() -> int:
    return int(time.time() * 1000)


def days_left(expiry_ms: int) -> int:
    if not expiry_ms:
        return -1  # unlimited / never expires
    delta = expiry_ms - now_ms()
    if delta <= 0:
        return 0
    return max(0, int(delta // (86400 * 1000)))


def fmt_expiry(expiry_ms: int) -> str:
    if not expiry_ms:
        return "∞ (never)"
    left = days_left(expiry_ms)
    if left <= 0:
        return "expired"
    return f"{left} days left"


def fmt_quota(used: int, total: int) -> str:
    if not total:
        return f"{human_bytes(used)} / ∞"
    return f"{human_bytes(used)} / {human_bytes(total)}"


def progress_bar(used: int, total: int, width: int = 12) -> str:
    if not total:
        return "[" + "·" * width + "] ∞"
    ratio = min(1.0, max(0.0, used / total))
    filled = int(ratio * width)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {int(ratio * 100)}%"
