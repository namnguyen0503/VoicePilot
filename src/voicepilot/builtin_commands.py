"""Editable built-in utterance patterns, separate from user-created commands."""

from __future__ import annotations

import re
import tomllib
from copy import deepcopy
from importlib import resources

import tomli_w


def default_table() -> dict:
    """Read the immutable shipped command definitions."""
    return tomllib.loads(resources.files("voicepilot").joinpath(
        "resources", "commands.toml").read_text(encoding="utf-8"))


def validate_overrides(value: object) -> dict[str, list[str]]:
    """Validate whole edits before applying; retain native actions and parameter contracts."""
    from voicepilot.intent.normalize import normalize
    from voicepilot.intent.parser import _compile_rule, _load_entries

    if not isinstance(value, dict):
        raise ValueError("Prompt mặc định phải là danh sách theo hành động.")
    table = default_table()
    originals = {row["name"]: row for row in table["intent"] if row.get("patterns")}
    result = {}
    for name, patterns in value.items():
        if name not in originals:
            raise ValueError("Hành động mặc định không hợp lệ.")
        if not isinstance(patterns, list) or not 1 <= len(patterns) <= 64:
            raise ValueError("Mỗi lệnh cần từ 1 đến 64 cách gọi.")
        allowed = set(re.findall(r"\{([^{}]+)\}", " ".join(originals[name]["patterns"])))
        clean = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 2048:
                raise ValueError("Cách gọi cần có nội dung, tối đa 2048 ký tự.")
            if not normalize(pattern).text:
                raise ValueError("Cách gọi phải có chữ.")
            if not set(re.findall(r"\{([^{}]+)\}", pattern)) <= allowed:
                raise ValueError("Giữ đúng tên tham số được hướng dẫn cho lệnh này.")
            clean.append(pattern.strip())
        required = set(originals[name].get("params", []))
        provided = set(re.findall(r"\{([^{}]+)\}", " ".join(clean)))
        if "target" in provided:
            provided.add("app_name")
        if "question" in provided:
            provided.add("query")
        if not required <= provided:
            raise ValueError("Cần ít nhất một cách gọi nhận đủ tham số của hành động.")
        result[name] = list(dict.fromkeys(clean))
        originals[name]["patterns"] = result[name]
        originals[name]["example"] = result[name][0]
    entries, _ = _load_entries(tomli_w.dumps(table), "builtin_patterns")
    for entry in entries:
        for pattern in entry.patterns:
            _compile_rule(entry, pattern, False)
    return result


def command_table(overrides: dict | None = None) -> dict:
    """Return a detached effective table for parser and decoder hotwords."""
    table = default_table()
    changes = validate_overrides(overrides or {})
    for row in table["intent"]:
        if row["name"] in changes:
            row["patterns"] = deepcopy(changes[row["name"]])
            row["example"] = row["patterns"][0]
    return table
