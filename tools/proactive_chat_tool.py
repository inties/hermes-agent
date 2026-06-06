"""Proactive chat scheduling tool."""

from tools.registry import registry


SET_NEXT_CHAT_CHECK_SCHEMA = {
    "name": "set_next_chat_check",
    "description": (
        "Schedule the next proactive chat check for the current QQ DM session. "
        "Use after sending a message when it would be natural to come back later. "
        "This does not send a fixed delayed message; when the time arrives, the "
        "gateway will inject an internal check into this same session so you can "
        "think again from the current history. Always write a concrete reason "
        "that captures what you want to follow up on next time. If the user has "
        "not replied to earlier proactive messages, choose a longer delay."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "delay_seconds": {
                "type": "integer",
                "description": (
                    "Seconds until the next proactive check. Use short delays only "
                    "for active conversations; use much longer delays if the user "
                    "has not replied."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Internal anchor for the next proactive turn: what topic, mood, "
                    "promise, or unfinished thought should be continued. This is "
                    "injected into the next internal check and is not shown directly "
                    "to the user."
                ),
            },
        },
        "required": ["delay_seconds", "reason"],
    },
}


def set_next_chat_check_tool(args, **kw):
    from gateway.proactive_chat import schedule_from_context

    return schedule_from_context(args)


registry.register(
    name="set_next_chat_check",
    toolset="proactive_chat",
    schema=SET_NEXT_CHAT_CHECK_SCHEMA,
    handler=set_next_chat_check_tool,
    emoji="",
)
