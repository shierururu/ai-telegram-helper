"""«Миф или правда»: a stable daily set, a complete deck and catalogue-only links."""

from __future__ import annotations

from datetime import date, timedelta

from alpha_course.webapp import myths
from alpha_course.webapp.wordle import EPOCH
from tests.unit.test_webapp import Rig


def test_deck_is_well_formed():
    ids = [item.id for item in myths.DECK]
    assert len(ids) == len(set(ids))
    assert len(myths.DECK) % myths.PER_DAY == 0
    assert any(item.truth for item in myths.DECK) and any(not item.truth for item in myths.DECK)
    for item in myths.DECK:
        assert item.statement.endswith(".") and item.why and item.topic


def test_daily_set_is_stable_unique_and_covers_the_deck():
    first = myths.pick(EPOCH)
    assert [item.id for item in first] == [item.id for item in myths.pick(EPOCH)]
    assert len({item.id for item in first}) == myths.PER_DAY
    cycle = len(myths.DECK) // myths.PER_DAY
    seen = {item.id for step in range(cycle) for item in myths.pick(EPOCH + timedelta(days=step))}
    assert seen == {item.id for item in myths.DECK}
    assert [item.id for item in myths.pick(EPOCH + timedelta(days=cycle))] == [
        item.id for item in first
    ]
    assert myths.pick(date(2030, 1, 1))  # any date works


async def test_endpoint_returns_five_cards_with_free_catalogue_links_only():
    async with Rig() as rig:
        status, body = await rig.call("GET", "/api/myths")
        assert status == 200
        assert len(body["cards"]) == myths.PER_DAY
        catalogue = rig.services.knowledge.materials
        for card in body["cards"]:
            assert isinstance(card["truth"], bool) and card["statement"] and card["why"]
            material = card["material"]
            if material is not None:
                assert not material["is_paid"]
                assert material["url"] == catalogue[material["id"]].url
        assert "myths_opened" in rig.events.kinds


async def test_endpoint_requires_auth():
    async with Rig() as rig:
        status, _ = await rig.call("GET", "/api/myths", headers={})
        assert status == 401
