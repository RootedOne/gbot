import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.db import repo
from bot.keyboards.user_kb import trial_panels_kb
from bot.services.delivery import send_configs
from bot.services.provisioning import ProvisioningError, provision_trial_service
from bot.utils.locales import get_text


logger = logging.getLogger(__name__)
router = Router(name="freetrial")

MAX_TRIALS = 2

@router.message(F.text.in_([get_text("en", "btn_free_trial"), get_text("fa", "btn_free_trial")]))
async def handle_free_trial_button(message: Message) -> None:
    node_id = getattr(message.bot, "node_id", 0)
    user = await repo.get_user(message.from_user.id, node_id=node_id)
    if not user:
        return
        
    def _loc(key: str, **kwargs) -> str:
        return get_text(user.lang, key, **kwargs)

    count = await repo.count_user_trials(user.tg_id, node_id=node_id)
    if count >= MAX_TRIALS:
        await message.answer(_loc("trial_limit_reached", max_trials=MAX_TRIALS))
        return

    panels = await repo.list_trial_panels()
    if not panels:
        await message.answer(_loc("no_trial_servers"))
        return

    await message.answer(
        _loc("choose_trial_server"),
        reply_markup=trial_panels_kb(panels, _loc)
    )

@router.callback_query(F.data.startswith("trial:"))
async def handle_trial_panel_selection(call: CallbackQuery) -> None:
    panel_id = int(call.data.split(":")[1])
    node_id = getattr(call.bot, "node_id", 0)
    user = await repo.get_user(call.from_user.id, node_id=node_id)
    if not user:
        await call.answer("User not found.", show_alert=True)
        return

    def _loc(key: str, **kwargs) -> str:
        return get_text(user.lang, key, **kwargs)

    # Double check limit
    count = await repo.count_user_trials(user.tg_id, node_id=node_id)
    if count >= MAX_TRIALS:
        await call.answer(_loc("trial_limit_reached", max_trials=MAX_TRIALS), show_alert=True)
        return

    await call.message.edit_reply_markup(reply_markup=None)
    
    # Try provisioning
    msg = await call.message.answer("⏳ ...")
    try:
        result = await provision_trial_service(user.tg_id, panel_id, node_id=node_id)
        
        # Send configs first
        await send_configs(
            bot=call.bot,
            chat_id=user.tg_id,
            result=result,
            title="🎁 Free Trial Ready",
            _=_loc,
        )
        
        # Send success message
        await msg.answer(
            _loc("trial_success", hours=1, mb=200)
        )
    except ProvisioningError as exc:
        await msg.edit_text(f"❌ Provisioning failed: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error provisioning trial service.")
        await msg.edit_text("❌ An unexpected error occurred.")

    await call.answer()
