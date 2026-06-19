from __future__ import annotations

from typing import Any, Callable

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.db import repo
from bot.keyboards.user_kb import language_kb, main_menu_kb

router = Router(name="user-start")


def _welcome_text(settings: Any, _ : Callable[[str], str]) -> str:
    return _("welcome", brand_name=settings.brand_name)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    await state.clear()
    settings = get_settings()
    is_admin = settings.is_admin(message.from_user.id)
    node_id = getattr(message.bot, "node_id", 0)
    await repo.get_or_create_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        is_admin=is_admin,
        node_id=node_id,
    )
    # Ask for language selection
    await message.answer(_("select_language"), reply_markup=language_kb())


@router.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext, _: Callable[[str], str]) -> None:
    await message.answer(_("select_language"), reply_markup=language_kb())


@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(call: CallbackQuery, state: FSMContext, _: Callable[[str], str]) -> None:
    lang = call.data.split(":")[1]
    node_id = getattr(call.bot, "node_id", 0)
    await repo.set_user_language(call.from_user.id, lang, node_id=node_id)
    # Update _ to use the new language for the rest of this request
    from bot.utils.locales import get_text
    from functools import partial
    _new = partial(get_text, lang)
    
    settings = get_settings()
    is_admin = settings.is_admin(call.from_user.id)
    node_id = getattr(call.bot, "node_id", 0)
    db_user = await repo.get_user(call.from_user.id, 0)
    is_reseller = bool(db_user and db_user.is_reseller) if node_id == 0 else False

    await call.message.answer(_new("lang_selected"), reply_markup=main_menu_kb(is_admin, is_reseller, _new))
    await call.message.answer(_welcome_text(settings, _new))
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


@router.message(Command("help"))
async def cmd_help_cmd(message: Message, _: Callable[[str], str]) -> None:
    await message.answer(_("help_text"))


@router.message(F.text.in_(["ℹ️ Help", "ℹ️ راهنما"]))
async def cmd_help_btn(message: Message, _: Callable[[str], str]) -> None:
    await message.answer(_("help_text"))


@router.message(F.text.in_(["💬 Support", "💬 پشتیبانی"]))
async def cmd_support(message: Message, _: Callable[[str], str]) -> None:
    settings = get_settings()
    await message.answer(_("support_text", support_contact=settings.support_contact))
