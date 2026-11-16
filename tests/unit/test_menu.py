from alpha_course.telegram.menu import (
    ASK,
    FINMODEL,
    GAMES,
    MENU,
    MENU_TEXTS,
    SETTINGS,
    TAXES,
    TOOLS,
    leaves_flow,
)


def test_main_menu_groups_tools_and_exposes_settings():
    texts = [button.text for row in MENU.keyboard for button in row]
    assert texts[0] == ASK
    assert TOOLS in texts and SETTINGS in texts
    assert not {FINMODEL, GAMES, TAXES} & set(texts)
    assert {TOOLS, SETTINGS, FINMODEL, GAMES, TAXES} <= MENU_TEXTS
    assert all(leaves_flow(text) for text in (TOOLS, SETTINGS, FINMODEL, GAMES, TAXES))


async def test_mini_app_button_uses_current_tunnel_url():
    from unittest.mock import AsyncMock

    from alpha_course.telegram.menu import create_menu_router

    current = None
    router = create_menu_router(lambda: current)
    message, state = AsyncMock(), AsyncMock()
    handler = router.message.handlers[0].callback
    await handler(message, state)
    assert "пока не подключено" in message.answer.await_args.args[0]
    for current in ("https://first.trycloudflare.com", "https://second.trycloudflare.com"):
        await handler(message, state)
        markup = message.answer.await_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].web_app.url == current
