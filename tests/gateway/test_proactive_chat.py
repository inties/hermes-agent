import json
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.proactive_chat import (
    ProactiveChatPlan,
    ProactiveChatScheduler,
    get_scheduler,
    schedule_from_context,
    set_scheduler,
)
from gateway.session import SessionSource
from gateway.session_context import clear_session_vars, set_session_vars


class FakeAdapter:
    def __init__(self):
        self.events = []
        self._active_sessions = {}

    async def handle_message(self, event):
        self.events.append(event)


class FakeRunner:
    def __init__(self):
        self._background_tasks = set()
        self._running_agents = {}
        self.session_store = SimpleNamespace(_entries={})
        self.adapter = FakeAdapter()
        self.adapters = {Platform.QQBOT: self.adapter}


@pytest.fixture(autouse=True)
def _reset_scheduler():
    yield
    scheduler = get_scheduler()
    if scheduler is not None:
        for task in list(getattr(scheduler, "_tasks", {}).values()):
            task.cancel()
    set_scheduler(None)


@pytest.mark.asyncio
async def test_schedule_from_context_registers_qq_dm_plan():
    runner = FakeRunner()
    scheduler = ProactiveChatScheduler(runner)
    set_scheduler(scheduler)
    tokens = set_session_vars(
        platform="qqbot",
        chat_id="user_openid_1",
        chat_type="dm",
        user_id="user_openid_1",
        session_key="qqbot:dm:user_openid_1",
        session_id="sid-1",
    )
    try:
        result = json.loads(
            schedule_from_context(
                {"delay_seconds": 60, "reason": "Follow up on the interface details."}
            )
        )
    finally:
        clear_session_vars(tokens)

    assert result["ok"] is True
    assert scheduler.is_scheduled("qqbot:dm:user_openid_1")
    assert scheduler._plans["qqbot:dm:user_openid_1"].reason == "Follow up on the interface details."
    await scheduler.shutdown()


def test_schedule_from_context_rejects_non_dm_qq_context():
    runner = FakeRunner()
    set_scheduler(ProactiveChatScheduler(runner))
    tokens = set_session_vars(
        platform="qqbot",
        chat_id="group_openid_1",
        chat_type="group",
        user_id="member_openid_1",
        session_key="qqbot:group:group_openid_1",
        session_id="sid-1",
    )
    try:
        result = json.loads(
            schedule_from_context(
                {"delay_seconds": 60, "reason": "Should not schedule in a group."}
            )
        )
    finally:
        clear_session_vars(tokens)

    assert "error" in result
    assert "QQ DM" in result["error"]


@pytest.mark.asyncio
async def test_fire_injects_internal_event_through_adapter():
    runner = FakeRunner()
    scheduler = ProactiveChatScheduler(runner)
    runner.session_store._entries["qqbot:dm:user_openid_1"] = SimpleNamespace(session_id="sid-1")
    plan = ProactiveChatPlan(
        platform=Platform.QQBOT,
        chat_id="user_openid_1",
        user_id="user_openid_1",
        session_key="qqbot:dm:user_openid_1",
        session_id="sid-1",
        fire_at=0,
        reason="Ask whether to continue the proactive chat design.",
    )

    await scheduler._fire(plan)

    assert len(runner.adapter.events) == 1
    event = runner.adapter.events[0]
    assert event.internal is True
    assert event.message_type == MessageType.TEXT
    assert event.source.platform == Platform.QQBOT
    assert event.source.chat_type == "dm"
    assert "Ask whether to continue" in event.text
    assert event.raw_message["kind"] == "proactive_chat_check"


@pytest.mark.asyncio
async def test_real_user_event_cancels_scheduled_plan():
    runner = FakeRunner()
    scheduler = ProactiveChatScheduler(runner)
    scheduler.schedule(
        platform=Platform.QQBOT,
        chat_id="user_openid_1",
        user_id="user_openid_1",
        session_key="qqbot:dm:user_openid_1",
        session_id="sid-1",
        delay_seconds=60,
        reason="Check later.",
    )
    source = SessionSource(
        platform=Platform.QQBOT,
        chat_id="user_openid_1",
        user_id="user_openid_1",
        chat_type="dm",
    )
    event = MessageEvent(text="I replied", source=source)

    scheduler.cancel_for_event(event, "qqbot:dm:user_openid_1")

    assert not scheduler.is_scheduled("qqbot:dm:user_openid_1")
    await scheduler.shutdown()

