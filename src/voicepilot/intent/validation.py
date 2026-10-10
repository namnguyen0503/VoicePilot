"""Rank STT candidates using the command requirements of the captured app.

This does not change parsing or execution. An incompatible candidate may still be
returned as the hybrid's last resort so the executor can explain the prerequisite.
"""

from __future__ import annotations

from collections.abc import Collection

from voicepilot.contracts import AppKind, Command, Intent, WindowContext

_POWERPOINT_INTENTS = frozenset({
    Intent.PPT_SAVE, Intent.PPT_SAVE_AS, Intent.PPT_NEXT_SLIDE, Intent.PPT_PREV_SLIDE,
    Intent.PPT_START_SHOW, Intent.PPT_STOP_SHOW,
})
_TAB_INTENTS = frozenset({
    Intent.BROWSER_NEXT_TAB, Intent.BROWSER_PREV_TAB, Intent.BROWSER_NEW_TAB,
    Intent.BROWSER_CLOSE_TAB,
})


def is_valid_candidate(command: Command, context: WindowContext | None,
                       known_sites: Collection[str]) -> bool:
    """Prefer complete commands that can run in this app over misheard alternatives.

    Window identity/focus is checked before execution, not during STT. Bare domains
    remain weak matches because Google can turn a misheard site name into a domain.
    """
    if not command.is_executable:
        return False
    kind = context.app_kind if context is not None else AppKind.NONE
    if command.intent in _POWERPOINT_INTENTS and kind is not AppKind.POWERPOINT:
        return False
    if command.intent in _TAB_INTENTS and kind is not AppKind.BROWSER:
        return False
    if command.intent is Intent.BROWSER_OPEN_SITE and command.params.get("url") not in known_sites:
        return False
    return True
