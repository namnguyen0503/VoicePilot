"""Validated user commands and template matching, independent of Qt and Windows."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from voicepilot.contracts import Command, Intent, WindowContext
from voicepilot.intent.normalize import Normalized, fold, normalize

log = logging.getLogger(__name__)
# Field labels are also used by the form editor. These are native backend parameters.
ACTIONS = {
    "BROWSER_SEARCH": ("Tìm Google", {"query": "Nội dung tìm kiếm"}),
    "BROWSER_OPEN_SITE": ("Mở website", {"url": "Địa chỉ HTTP/HTTPS"}),
    "WIN_OPEN_APP": ("Mở ứng dụng", {"app": "Tên exe hoặc đường dẫn ứng dụng"}),
    "DICTATE_TEXT": ("Chèn văn bản", {"text": "Văn bản cần chèn"}),
    "WIN_HOTKEY": ("Nhấn tổ hợp phím", {"keys": "Phím (ví dụ ctrl+shift+s)"}),
    "WIN_SCREENSHOT": ("Chụp màn hình", {}),
    "PPT_SAVE": ("PowerPoint: lưu", {}),
    "PPT_SAVE_AS": ("PowerPoint: lưu tên khác", {"filename": "Tên tệp"}),
    "PPT_NEXT_SLIDE": ("PowerPoint: slide tiếp", {}),
    "PPT_PREV_SLIDE": ("PowerPoint: slide trước", {}),
    "PPT_START_SHOW": ("PowerPoint: bắt đầu trình chiếu", {}),
    "PPT_STOP_SHOW": ("PowerPoint: dừng trình chiếu", {}),
    "BROWSER_NEXT_TAB": ("Trình duyệt: tab tiếp", {}),
    "BROWSER_PREV_TAB": ("Trình duyệt: tab trước", {}),
    "BROWSER_NEW_TAB": ("Trình duyệt: tab mới", {}),
    "BROWSER_CLOSE_TAB": ("Trình duyệt: đóng tab", {}),
}
CONTEXTS = {
    "any": "Mọi ứng dụng",
    "powerpoint": "PowerPoint",
    "browser": "Edge/Chrome",
    "other": "Ứng dụng khác",
    "none": "Không có cửa sổ",
}
SLOTS = re.compile(r"\{([A-Za-z][A-Za-z0-9_]{0,31})\}")
MAX_COMMANDS = 200
MAX_PATTERNS = 20
MAX_TEXT = 2048


def _slots(text: str) -> list[str]:
    residue = SLOTS.sub("", text)
    if "{" in residue or "}" in residue:
        raise ValueError("Tham số phải có dạng {noidung}, dùng chữ không dấu, số hoặc dấu gạch dưới.")
    return SLOTS.findall(text)


def hotkey_keys(spec: str) -> tuple[str, ...]:
    """Validate a single static chord using the same key map as SendInput."""
    from voicepilot.windows.input import vk_for_key

    keys = tuple(part.strip().lower() for part in spec.split("+"))
    modifiers = {"ctrl", "control", "shift", "alt", "win", "windows"}
    codes = [vk_for_key(key) for key in keys]
    if (
        not keys
        or len(keys) > 5
        or any(code is None for code in codes)
        or len(set(codes)) != len(codes)
        or keys[-1] in modifiers
        or any(key not in modifiers for key in keys[:-1])
    ):
        raise ValueError("Tổ hợp phím không hợp lệ; ví dụ ctrl+shift+s, f5, win+e.")
    return keys


def validate_commands(items: object) -> list[dict]:
    """Validate an entire edit/import transaction; return detached canonical records."""
    if not isinstance(items, list) or len(items) > MAX_COMMANDS:
        raise ValueError(f"Danh sách có tối đa {MAX_COMMANDS} lệnh.")
    result = []
    ids = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Mỗi lệnh phải là một mục cấu hình.")
        ident, name = raw.get("id"), raw.get("name")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident) or ident in ids:
            raise ValueError("ID lệnh không hợp lệ hoặc bị trùng.")
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Nhập tên lệnh (tối đa 120 ký tự).")
        enabled = raw.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Trạng thái bật/tắt phải là boolean.")
        intent = raw.get("intent")
        if not isinstance(intent, str) or intent not in ACTIONS:
            raise ValueError("Chọn một hành động được hỗ trợ.")
        patterns = raw.get("patterns")
        if (
            not isinstance(patterns, list)
            or not 1 <= len(patterns) <= MAX_PATTERNS
            or not all(isinstance(p, str) and p.strip() and len(p) <= 300 for p in patterns)
        ):
            raise ValueError(f"Nhập từ 1 đến {MAX_PATTERNS} cách gọi, mỗi cách một dòng (tối đa 300 ký tự).")
        patterns = [p.strip() for p in patterns]
        normalized = [fold(normalize(p)).text for p in patterns]
        if len(set(normalized)) != len(patterns) or any(not p for p in normalized):
            raise ValueError("Các cách gọi bị trùng hoặc không có chữ.")
        available = []
        for pattern in patterns:
            slots = _slots(pattern)
            if re.search(r"[^\s]\{|\}[^\s]", pattern):
                raise ValueError("Trong cách gọi, tham số phải cách các từ bên cạnh bằng khoảng trắng.")
            if len(slots) > 3 or len(set(slots)) != len(slots) or re.search(r"\}\s*\{", pattern):
                raise ValueError("Tối đa 3 tham số khác nhau; phải có từ phân cách giữa các tham số.")
            available.append(set(slots))
        params = raw.get("params", {})
        if not isinstance(params, dict) or set(params) != set(ACTIONS[intent][1]):
            raise ValueError("Điền đúng các trường hành động.")
        for value in params.values():
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
                raise ValueError("Tham số hành động không được rỗng hoặc quá dài.")
            used = set(_slots(value))
            if any(not used <= slots for slots in available):
                raise ValueError("Mọi cách gọi phải cung cấp đủ tham số dùng trong hành động.")
        context = raw.get("context", "any")
        process = raw.get("process", "")
        if not isinstance(context, str) or context not in CONTEXTS:
            raise ValueError("Ngữ cảnh không hợp lệ.")
        if not isinstance(process, str) or (process and not re.fullmatch(r"[\w .()-]+\.exe", process, re.I)):
            raise ValueError("Ứng dụng đích phải là tên exe, ví dụ notepad.exe.")
        if intent == "WIN_HOTKEY":
            if _slots(params["keys"]):
                raise ValueError("Phím phải cố định; không nhận phím từ lời nói.")
            hotkey_keys(params["keys"])
        if intent == "WIN_OPEN_APP" and _slots(params["app"]):
            raise ValueError("Đường dẫn ứng dụng phải cố định.")
        if intent == "BROWSER_OPEN_SITE":
            from urllib.parse import urlsplit

            from voicepilot.windows.browser import validate_url

            template = params["url"]
            if validate_url(SLOTS.sub("sample", template)) is None or _slots(urlsplit(template).netloc):
                raise ValueError(
                    "Website phải là HTTP/HTTPS; tham số chỉ được đặt trong đường dẫn hoặc query."
                )
        ids.add(ident)
        result.append(
            dict(
                id=ident,
                name=name.strip(),
                enabled=enabled,
                patterns=patterns,
                intent=intent,
                params=dict(params),
                context=context,
                process=process.strip().lower(),
            )
        )
    return result


def load_commands(items: object) -> list[dict]:
    """Recover valid entries from a hand-edited file without logging their content."""
    if not isinstance(items, list):
        log.warning("Invalid custom command table")
        return []
    result = []
    for item in items[:MAX_COMMANDS]:
        try:
            result = validate_commands([*result, item])
        except ValueError:
            log.warning("Invalid custom command entry ignored")
    return result


@dataclass(frozen=True)
class _Pattern:
    item: dict
    folded: bool
    regex: re.Pattern
    slots: tuple[str, ...]
    specificity: int


class CustomMatcher:
    """Match user templates with context filters and deterministic conflict handling."""

    def __init__(self, items: list[dict]) -> None:
        self._patterns = []
        for item in validate_commands(items):
            if not item["enabled"]:
                continue
            for pattern in item["patterns"]:
                for folded in (False, True):
                    slots = tuple(_slots(pattern))
                    parts = SLOTS.split(pattern)
                    pieces, literal_len = [], 0
                    for index, part in enumerate(parts):
                        if index % 2:
                            pieces.append("(.+?)")
                        else:
                            norm = normalize(part)
                            literal = fold(norm).text if folded else norm.text
                            if literal:
                                pieces.append(re.escape(literal))
                                literal_len += len(literal)
                    self._patterns.append(
                        _Pattern(item, folded, re.compile("^" + " ".join(pieces) + "$"), slots, literal_len)
                    )

    def match(
        self, text: str, plain: Normalized, folded: Normalized, ctx: WindowContext | None
    ) -> Command | None:
        """None means no custom match; UNKNOWN means an ambiguous custom match."""
        if len(text) > MAX_TEXT:
            return None
        kind = ctx.app_kind.value if ctx else "none"
        hits = []
        for rule in self._patterns:
            item = rule.item
            if item["context"] not in ("any", kind):
                continue
            if item["process"] and (ctx is None or ctx.process_name.casefold() != item["process"].casefold()):
                continue
            view = folded if rule.folded else plain
            match = rule.regex.fullmatch(view.text)
            if match:
                score = (rule.specificity, bool(item["process"]), item["context"] != "any", not rule.folded)
                hits.append((score, rule, match, view))
        if not hits:
            return None
        best = max(h[0] for h in hits)
        top = [h for h in hits if h[0] == best]
        if len({h[1].item["id"] for h in top}) != 1:
            return Command(Intent.UNKNOWN, text, example="Sửa cách gọi bị trùng trong Lệnh tùy chỉnh")
        _, rule, match, view = top[0]
        captures = {slot: view.original(text, *match.span(i)) for i, slot in enumerate(rule.slots, 1)}

        def render(template: str) -> str:
            return SLOTS.sub(
                lambda m: (
                    quote(captures[m[1]], safe="")
                    if rule.item["intent"] == "BROWSER_OPEN_SITE"
                    else captures[m[1]]
                ),
                template,
            )

        params = {key: render(value) for key, value in rule.item["params"].items()}
        return Command(Intent[rule.item["intent"]], text, params=params, custom_id=rule.item["id"])
