from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from bot.db import repo
from bot.db.models import OrderStatus
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb
from bot.services.fulfillment import fulfill_order

logger = logging.getLogger(__name__)
router = Router(name="admin-orders")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "adm:orders")
async def pending_orders(call: CallbackQuery, bot: Bot) -> None:
    node_id = getattr(bot, "node_id", 0)
    orders = await repo.list_pending_review_orders(node_id=node_id)
    if not orders:
        await call.message.edit_text(
            "🧾 No pending receipts. ✅", reply_markup=admin_menu_kb(node_id=node_id)
        )
        await call.answer()
        return

    await call.message.edit_text(
        f"🧾 <b>{len(orders)} pending receipt(s)</b> — sending them below:",
        reply_markup=admin_menu_kb(node_id=node_id),
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    for order in orders:
        plan = await repo.get_plan(order.plan_id)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve", callback_data=f"adm:order:approve:{order.id}")
        kb.button(text="❌ Reject", callback_data=f"adm:order:reject:{order.id}")
        kb.adjust(2)
        qty = getattr(order, "quantity", 1) or 1
        qty_line = f"Quantity: {qty}\n" if qty > 1 else ""
        caption = (
            f"🧾 Order #{order.id}\n"
            f"User: <code>{order.user_tg_id}</code>\n"
            f"Plan: {plan.title if plan else '?'}\n"
            f"{qty_line}"
            f"Amount: {int(order.amount):,} {order.currency}"
        )
        if order.receipt_file_id:
            await bot.send_photo(
                call.from_user.id,
                order.receipt_file_id,
                caption=caption,
                reply_markup=kb.as_markup(),
            )
        else:
            await bot.send_message(
                call.from_user.id, caption, reply_markup=kb.as_markup()
            )
    await call.answer()


@router.callback_query(F.data.startswith("adm:order:approve:"))
async def approve_order(call: CallbackQuery, bot: Bot) -> None:
    order_id = int(call.data.rsplit(":", 1)[1])
    order = await repo.get_order(order_id)
    if order is None:
        await call.answer("Order not found.", show_alert=True)
        return
    node_id = getattr(bot, "node_id", 0)
    if order.node_id != node_id:
        await call.answer("Access Denied.", show_alert=True)
        return
    if order.status == OrderStatus.paid:
        await call.answer("Already fulfilled.", show_alert=True)
        return
    await call.answer("Approving…")
    ok = await fulfill_order(bot, order)
    note = "✅ Approved & provisioned." if ok else "⚠️ Provisioning failed (see logs)."
    try:
        await call.message.edit_caption(
            caption=(call.message.caption or "") + f"\n\n{note}"
        )
    except Exception:  # noqa: BLE001
        await call.message.answer(note)


@router.callback_query(F.data.startswith("adm:order:reject:"))
async def reject_order(call: CallbackQuery, bot: Bot) -> None:
    order_id = int(call.data.rsplit(":", 1)[1])
    order = await repo.get_order(order_id)
    if order is None:
        await call.answer("Order not found.", show_alert=True)
        return
    node_id = getattr(bot, "node_id", 0)
    if order.node_id != node_id:
        await call.answer("Access Denied.", show_alert=True)
        return
    if order.status == OrderStatus.rejected:
        await call.answer("Already rejected.", show_alert=True)
        return
    order = await repo.set_order_status(order_id, OrderStatus.rejected)
    if order is None:
        await call.answer("Order not found.", show_alert=True)
        return
    try:
        await bot.send_message(
            order.user_tg_id,
            f"❌ Your payment for order #{order_id} was rejected. "
            "Contact support if you think this is a mistake.",
        )
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Rejected.")
    try:
        await call.message.edit_caption(
            caption=(call.message.caption or "") + "\n\n❌ Rejected."
        )
    except Exception:  # noqa: BLE001
        await call.message.answer("❌ Rejected.")
