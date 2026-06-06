"""Gateway-side proactive chat scheduling.

The scheduler owns one timer per gateway session.  Tool calls schedule the
next internal check; real user messages cancel it.  When a timer fires, the
gateway injects a synthetic MessageEvent through the normal adapter pipeline.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.platforms.base import MessageEvent, MessageType

logger = logging.getLogger(__name__)

_scheduler: "ProactiveChatScheduler | None" = None


def set_scheduler(scheduler: "ProactiveChatScheduler | None") -> None:
    """Register the live gateway scheduler for tool handlers."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> "ProactiveChatScheduler | None":
    """Return the live gateway scheduler, if the gateway is running."""
    return _scheduler


@dataclass
class ProactiveChatPlan:
    platform: Platform
    chat_id: str
    user_id: str
    session_key: str
    session_id: str
    fire_at: float
    reason: str
    chat_type: str = "dm"
    user_name: str = ""
    chat_name: str = ""


class ProactiveChatScheduler:
    """Async one-shot scheduler for proactive session checks."""

    def __init__(self, runner: Any):
        self.runner = runner
        self._plans: dict[str, ProactiveChatPlan] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule(
        self,
        *,
        platform: Platform,
        chat_id: str,
        user_id: str,
        session_key: str,
        session_id: str,
        delay_seconds: int,
        reason: str,
        chat_type: str = "dm",
        user_name: str = "",
        chat_name: str = "",
    ) -> ProactiveChatPlan:
        kwargs = {
            "platform": platform,
            "chat_id": chat_id,
            "user_id": user_id,
            "session_key": session_key,
            "session_id": session_id,
            "delay_seconds": delay_seconds,
            "reason": reason,
            "chat_type": chat_type,
            "user_name": user_name,
            "chat_name": chat_name,
        }
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        target_loop = getattr(self.runner, "_gateway_loop", None) or running_loop
        if target_loop is None or not target_loop.is_running():
            raise RuntimeError("gateway event loop is not running")
        if running_loop is target_loop:
            return self._schedule_on_loop(**kwargs)

        future = asyncio.run_coroutine_threadsafe(
            self._schedule_on_loop_async(**kwargs),
            target_loop,
        )
        try:
            return future.result(timeout=5)
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError("timed out scheduling proactive chat check") from exc

    async def _schedule_on_loop_async(self, **kwargs: Any) -> ProactiveChatPlan:
        return self._schedule_on_loop(**kwargs)

    def _schedule_on_loop(
        self,
        *,
        platform: Platform,
        chat_id: str,
        user_id: str,
        session_key: str,
        session_id: str,
        delay_seconds: int,
        reason: str,
        chat_type: str = "dm",
        user_name: str = "",
        chat_name: str = "",
    ) -> ProactiveChatPlan:
        delay = self._clamp_delay(delay_seconds)
        plan = ProactiveChatPlan(
            platform=platform,
            chat_id=str(chat_id),
            user_id=str(user_id or chat_id),
            session_key=str(session_key),
            session_id=str(session_id),
            fire_at=time.time() + delay,
            reason=str(reason).strip(),
            chat_type=str(chat_type or "dm"),
            user_name=str(user_name or ""),
            chat_name=str(chat_name or ""),
        )
        self.cancel(plan.session_key)
        self._plans[plan.session_key] = plan
        task = asyncio.create_task(self._wait_and_fire(plan))
        self._tasks[plan.session_key] = task
        try:
            self.runner._background_tasks.add(task)
            task.add_done_callback(self.runner._background_tasks.discard)
        except Exception:
            pass
        logger.info(
            "Scheduled proactive chat check: platform=%s chat=%s session=%s delay=%ss",
            plan.platform.value,
            plan.chat_id,
            plan.session_id,
            delay,
        )
        return plan

    def cancel(self, session_key: str, *, reason: str = "") -> bool:
        if not session_key:
            return False
        plan_existed = self._plans.pop(session_key, None) is not None
        task = self._tasks.pop(session_key, None)
        if task is not None and not task.done():
            task.cancel()
        if plan_existed or task is not None:
            logger.debug(
                "Cancelled proactive chat check for %s%s",
                session_key,
                f" ({reason})" if reason else "",
            )
            return True
        return False

    def cancel_for_event(self, event: MessageEvent, session_key: str) -> None:
        if getattr(event, "internal", False):
            return
        self.cancel(session_key, reason="user message")

    def is_scheduled(self, session_key: str) -> bool:
        return session_key in self._plans

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            if task is not None and not task.done():
                task.cancel()
        self._tasks.clear()
        self._plans.clear()

    @staticmethod
    def _clamp_delay(delay_seconds: int) -> int:
        try:
            delay = int(delay_seconds)
        except (TypeError, ValueError):
            delay = 600
        min_delay = 30
        max_delay = 7 * 24 * 3600
        return max(min_delay, min(delay, max_delay))

    async def _wait_and_fire(self, plan: ProactiveChatPlan) -> None:
        try:
            while True:
                remaining = plan.fire_at - time.time()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                current_task = asyncio.current_task()
                if self._tasks.get(plan.session_key) is not current_task:
                    return
                if self._plans.get(plan.session_key) is not plan:
                    return
                if self._session_is_busy(plan.session_key):
                    await asyncio.sleep(15)
                    continue
                await self._fire(plan)
                return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(
                "Proactive chat check failed for %s: %s",
                plan.session_key,
                exc,
                exc_info=True,
            )
        finally:
            current_task = asyncio.current_task()
            if self._tasks.get(plan.session_key) is current_task:
                self._tasks.pop(plan.session_key, None)
            if self._plans.get(plan.session_key) is plan:
                self._plans.pop(plan.session_key, None)

    def _session_is_busy(self, session_key: str) -> bool:
        if not session_key:
            return False
        if session_key in getattr(self.runner, "_running_agents", {}):
            return True
        for adapter in getattr(self.runner, "adapters", {}).values():
            active = getattr(adapter, "_active_sessions", {})
            if session_key in active:
                return True
        return False

    async def _fire(self, plan: ProactiveChatPlan) -> None:
        current_entry = self.runner.session_store._entries.get(plan.session_key)
        if current_entry is not None and current_entry.session_id != plan.session_id:
            logger.info(
                "Dropping proactive chat check for %s; session changed %s -> %s",
                plan.session_key,
                plan.session_id,
                current_entry.session_id,
            )
            return

        adapter = self.runner.adapters.get(plan.platform)
        if adapter is None:
            logger.warning("No adapter for proactive chat platform %s", plan.platform.value)
            return

        source = SessionSource(
            platform=plan.platform,
            chat_id=plan.chat_id,
            chat_name=plan.chat_name or None,
            chat_type=plan.chat_type or "dm",
            user_id=plan.user_id or plan.chat_id,
            user_name=plan.user_name or None,
        )
        text = build_proactive_check_prompt(plan.reason)
        raw_message = {
            "_hermes_internal": True,
            "kind": "proactive_chat_check",
            "session_id": plan.session_id,
        }
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=raw_message,
            internal=True,
        )
        logger.info(
            "Injecting proactive chat check: platform=%s chat=%s session=%s",
            plan.platform.value,
            plan.chat_id,
            plan.session_id,
        )
        await adapter.handle_message(event)


def build_proactive_check_prompt(reason: str) -> str:
    reason_text = str(reason or "").strip() or "没有留下具体锚点。"
    return (
        "这是一次主动聊天检查，不是用户消息。\n\n"
        "上次留下的内部锚点：\n"
        f"{reason_text}\n\n"
        "用户在你上次发送消息后没有回复。\n\n"
        "请在当前聊天窗口里发一句简短、自然的话继续对话。不要解释这条内部提示。"
        "发完这句话后，必须调用 set_next_chat_check 设置下一次检查时间。"
        "如果用户已经连续多次没有回应，下次间隔应该更长。"
    )


def schedule_from_context(args: dict) -> str:
    """Tool handler entrypoint used by tools/proactive_chat_tool.py."""
    scheduler = get_scheduler()
    if scheduler is None:
        return json.dumps(
            {"error": "set_next_chat_check is only available while the gateway is running."},
            ensure_ascii=False,
        )

    from gateway.session_context import get_session_env

    platform_name = get_session_env("HERMES_SESSION_PLATFORM", "")
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
    user_id = get_session_env("HERMES_SESSION_USER_ID", "") or chat_id
    user_name = get_session_env("HERMES_SESSION_USER_NAME", "")
    session_key = get_session_env("HERMES_SESSION_KEY", "")
    session_id = get_session_env("HERMES_SESSION_ID", "")
    chat_name = get_session_env("HERMES_SESSION_CHAT_NAME", "")
    chat_type = get_session_env("HERMES_SESSION_CHAT_TYPE", "")
    thread_id = get_session_env("HERMES_SESSION_THREAD_ID", "")

    if platform_name != "qqbot":
        return json.dumps(
            {"error": "set_next_chat_check currently supports QQ bot DM sessions only."},
            ensure_ascii=False,
        )
    if not chat_id or not session_key or not session_id:
        return json.dumps(
            {"error": "Missing gateway session context for proactive chat scheduling."},
            ensure_ascii=False,
        )
    if thread_id or chat_type != "dm":
        return json.dumps(
            {"error": "set_next_chat_check currently supports QQ DM sessions only, not threaded/group contexts."},
            ensure_ascii=False,
        )

    reason = str(args.get("reason") or "").strip()
    if not reason:
        return json.dumps({"error": "reason is required."}, ensure_ascii=False)

    delay_seconds = args.get("delay_seconds", 600)
    plan = scheduler.schedule(
        platform=Platform.QQBOT,
        chat_id=chat_id,
        user_id=user_id,
        user_name=user_name,
        chat_name=chat_name,
        chat_type="dm",
        session_key=session_key,
        session_id=session_id,
        delay_seconds=delay_seconds,
        reason=reason,
    )
    return json.dumps(
        {
            "ok": True,
            "scheduled": True,
            "fire_at": int(plan.fire_at),
            "delay_seconds": max(0, int(plan.fire_at - time.time())),
            "reason": plan.reason,
        },
        ensure_ascii=False,
    )
