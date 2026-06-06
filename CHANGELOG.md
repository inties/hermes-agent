# Hermes Agent Changelog

## 2026-06-06 - Proactive QQ DM chat checks

- Added a QQ DM-only `set_next_chat_check(delay_seconds, reason)` tool.
- Added a gateway proactive chat scheduler with one active timer per session.
- Real user messages cancel the pending proactive timer for the same session.
- Timer fire injects an internal `MessageEvent` through `adapter.handle_message(event)`.
- Extended gateway session context with `chat_type` and `session_id` for tools.
- Added tests for proactive scheduling, injection, cancellation, and session context.

