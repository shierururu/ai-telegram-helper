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
