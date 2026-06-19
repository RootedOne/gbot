from __future__ import annotations

import logging
from typing import Callable, List, Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.db.models import Service
from bot.keyboards.user_kb import delivery_kb, service_actions_kb
from bot.services.provisioning import ProvisionResult
from bot.utils.qr import make_qr_png

logger = logging.getLogger(__name__)


# Telegram caption hard limit.
_CAPTION_LIMIT = 1024


def build_delivery_caption(
    result: ProvisionResult, title: str
) -> tuple[str, Optional[str]]:
    """Build a subscription-only delivery caption and QR payload."""
    lines = [f"✅ <b>{title}</b>", ""]

    if result.sub_url:
        urls = [u.strip() for u in result.sub_url.strip().split("\n") if u.strip()]
        if len(urls) > 1:
            lines.append("🔗 <b>Subscription links</b>:")
            lines.append("")
            for url in urls:
                from urllib.parse import urlparse
                domain = urlparse(url).hostname or url
                lines.append(f"🌐 <b>{domain}</b>:")
                lines.append(f"<code>{url}</code>")
                lines.append("")
        else:
            lines.append("🔗 <b>Subscription link</b>:")
            lines.append(f"<code>{urls[0]}</code>")
            lines.append("")
        lines.append("📷 Scan the QR to import into your VPN app.")
        return "\n".join(lines), urls[0] if urls else None

    lines.append("⚠️ Subscription link is not available yet.")
    lines.append("Use <b>📋 Get config links</b> below for individual configs.")
    return "\n".join(lines), None


async def send_config_links_one_by_one(
    bot: Bot,
    chat_id: int,
    links: List[str],
    *,
    prefix: str = "📋",
) -> int:
    """Send each config link in its own message. Returns count sent."""
    if not links:
        return 0
    total = len(links)
    for index, link in enumerate(links, start=1):
        label = f"{prefix} Config {index}/{total}" if total > 1 else f"{prefix} Config"
        await bot.send_message(
            chat_id,
            f"{label}\n<code>{link}</code>",
            disable_web_page_preview=True,
        )
    return total


async def send_configs(
    bot: Bot,
    chat_id: int,
    result: ProvisionResult,
    title: str = "Your VPN is ready",
    _: Callable[[str], str] = lambda k: k,
) -> None:
    """Deliver subscription QR + link only; config links via button."""
    caption, qr_target = build_delivery_caption(result, title)
    markup = delivery_kb(result.service.id, _)

    if qr_target:
        try:
            png = make_qr_png(qr_target)
            if len(caption) <= _CAPTION_LIMIT:
                await bot.send_photo(
                    chat_id,
                    BufferedInputFile(png.read(), filename="sub.png"),
                    caption=caption,
                    reply_markup=markup,
                )
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("QR send failed: %s", exc)

    await bot.send_message(
        chat_id,
        caption,
        disable_web_page_preview=True,
        reply_markup=markup,
    )


async def send_service_card(
    bot: Bot,
    chat_id: int,
    service: Service,
    links: List[str],
    sub_url: Optional[str],
    usage_line: str,
    _: Callable[[str], str] = lambda k: k,
) -> None:
    text_lines = [
        f"🛡 <b>Service</b> <code>{service.email}</code>",
        usage_line,
        "",
    ]
    if sub_url:
        urls = [u.strip() for u in sub_url.strip().split("\n") if u.strip()]
        if len(urls) > 1:
            text_lines.append("🔗 Subscription links:")
            text_lines.append("")
            for url in urls:
                from urllib.parse import urlparse
                domain = urlparse(url).hostname or url
                text_lines.append(f"🌐 <b>{domain}</b>:")
                text_lines.append(f"<code>{url}</code>")
                text_lines.append("")
        else:
            text_lines.append("🔗 Subscription:")
            text_lines.append(f"<code>{urls[0]}</code>")
    if links:
        text_lines.append("")
        text_lines.append(f"📋 {len(links)} config link(s) — tap <b>Config links</b> below.")
    await bot.send_message(
        chat_id,
        "\n".join(text_lines),
        disable_web_page_preview=True,
        reply_markup=service_actions_kb(service.id, can_migrate=False, _=_),
    )
