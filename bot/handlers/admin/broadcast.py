from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import repo
from bot.filters import IsAdmin
from bot.keyboards.admin_kb import admin_menu_kb
from bot.states.forms import BroadcastForm

logger = logging.getLogger(__name__)
router = Router(name="admin-broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "adm:broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastForm.message)
    await call.message.answer(
        "📣 Send the message to broadcast to <b>all users</b>.\n"
        "Send /cancel to abort."
    )
    await call.answer()


@router.message(BroadcastForm.message, F.text == "/cancel")
async def broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.", reply_markup=admin_menu_kb())


@router.message(BroadcastForm.message)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    user_ids = await repo.list_user_ids()
    await message.answer(f"📣 Broadcasting to {len(user_ids)} users…")

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
        await asyncio.sleep(0.05)  # stay under Telegram rate limits

    await message.answer(
        f"✅ Broadcast done. Sent: <b>{sent}</b>, failed: <b>{failed}</b>.",
        reply_markup=admin_menu_kb(),
    )
