from __future__ import annotations

import logging
import os
import time
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import repo
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb
from bot.services.charts import generate_income_chart

logger = logging.getLogger(__name__)
router = Router(name="admin-income")
router.callback_query.filter(IsAdmin())


def format_currency_dict(d: dict) -> str:
    if not d:
        return "0 IRR"
    parts = []
    # Sort currency by name to keep order consistent
    for cur in sorted(d.keys()):
        val = d[cur]
        if cur == "Stars":
            parts.append(f"{int(val):,} Stars")
        else:
            parts.append(f"{int(val):,} {cur}")
    return " + ".join(parts)


@router.callback_query(F.data == "adm:income")
async def show_income_summary(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("Calculating...")
    node_id = getattr(bot, "node_id", 0)
    
    # 1. Fetch data
    stats = await repo.get_income_stats(node_id)
    
    # 2. Render chart
    import tempfile
    chart_dir = tempfile.gettempdir()
    chart_path = f"{chart_dir}/income_chart_{node_id}_{int(time.time())}.png"
    
    try:
        generate_income_chart(stats, node_id, chart_path)
    except Exception as exc:
        logger.exception("Failed to generate income chart: %s", exc)
        chart_path = None

    # 3. Format text caption
    lines = []
    if node_id == 0:
        lines.append("📊 <b>Global Income Summary</b>\n")
    else:
        lines.append(f"📊 <b>Income Summary (Node #{node_id})</b>\n")
        
    lines.append("📅 <b>Earnings Overview</b>")
    lines.append(f"├ Today: <code>{format_currency_dict(stats['periods']['today'])}</code>")
    lines.append(f"├ Last 7 Days: <code>{format_currency_dict(stats['periods']['7d'])}</code>")
    lines.append(f"├ Last 30 Days: <code>{format_currency_dict(stats['periods']['30d'])}</code>")
    lines.append(f"└ All Time: <code>{format_currency_dict(stats['periods']['all_time'])}</code>")
    
    if node_id == 0:
        lines.append("\n🔌 <b>Revenue Sources (30 Days)</b>")
        lines.append(f"├ Main Bot Retail: <code>{format_currency_dict(stats['sources']['main_retail'])}</code>")
        lines.append(f"├ Reseller Wholesale: <code>{format_currency_dict(stats['sources']['reseller_topup'])}</code>")
        lines.append(f"└ Reseller Node Retail: <code>{format_currency_dict(stats['sources']['reseller_retail'])}</code>")
        
    lines.append("\n⭐ <b>Top Popular Plans</b>")
    plan_lines = []
    for idx, p in enumerate(stats["popular_plans"], start=1):
        plan_lines.append(f"{idx}. <b>{p['title']}</b> — {p['sales_count']} sales ({p['currency']})")
    if not plan_lines:
        plan_lines = ["No sales recorded yet."]
    lines.extend(plan_lines)

    caption_text = "\n".join(lines)

    # Keyboard markup
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Refresh", callback_data="adm:income")
    builder.button(text="⬅️ Back to Menu", callback_data="adm:income:back")
    builder.adjust(2)
    markup = builder.as_markup()

    # 4. Clean up the menu text message
    try:
        await call.message.delete()
    except Exception:
        pass

    # 5. Deliver visual report
    if chart_path and os.path.exists(chart_path):
        try:
            await bot.send_photo(
                chat_id=call.from_user.id,
                photo=FSInputFile(chart_path),
                caption=caption_text,
                reply_markup=markup
            )
            # Remove temp chart image
            try:
                os.remove(chart_path)
            except Exception:
                pass
            return
        except Exception as exc:
            logger.error("Failed to send chart photo, falling back to text: %s", exc)

    # Fallback to pure text message if photo delivery fails or chart wasn't generated
    await bot.send_message(
        chat_id=call.from_user.id,
        text=caption_text,
        reply_markup=markup
    )


@router.callback_query(F.data == "adm:income:back")
async def back_to_admin_menu(call: CallbackQuery, bot: Bot) -> None:
    node_id = getattr(bot, "node_id", 0)
    
    # Delete the photo/text message
    try:
        await call.message.delete()
    except Exception:
        pass
        
    # Send fresh main admin panel menu
    await bot.send_message(
        chat_id=call.from_user.id,
        text="🛠 <b>Admin Panel</b>",
        reply_markup=admin_menu_kb(node_id)
    )
    await call.answer()
