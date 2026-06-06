"""Pick local sticker media for conversational replies."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_result


STICKER_PICKER_SCHEMA = {
    "name": "pick_sticker",
    "description": (
        "Pick one local sticker that matches a conversational mood. "
        "Use the returned media_tag verbatim in your final reply. "
        "A reply may be only the media_tag, media_tag before text, or text before media_tag. "
        "For sticker bombing, call this tool repeatedly for different stickers or repeat a returned media_tag. "
        "If it fails, check the mood input and retry with a broader mood."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mood": {
                "type": "string",
                "description": (
                    "Sticker mood or intent, such as 疑惑, 开心, 撒娇, 震惊, 无语, 生气, 贴贴, 晚安. "
                    "Use a short natural label; do not pass file paths."
                ),
            }
        },
        "required": ["mood"],
    },
}

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_RECENT_LIMIT = 20

_MOOD_ALIASES = {
    "疑惑": ["疑惑", "困惑", "迷惑", "问号", "不懂", "啊", "好奇"],
    "困惑": ["疑惑", "困惑", "迷惑", "问号", "不懂", "啊"],
    "开心": ["开心", "高兴", "快乐", "笑", "庆祝", "可爱", "糖果"],
    "高兴": ["开心", "高兴", "快乐", "笑", "庆祝", "可爱"],
    "撒娇": ["撒娇", "可爱", "贴贴", "害羞", "俏皮", "wink"],
    "贴贴": ["贴贴", "抱抱", "撒娇", "温柔", "亲密", "可爱"],
    "害羞": ["害羞", "脸红", "不好意思", "wink", "可爱"],
    "震惊": ["震惊", "惊讶", "吃惊", "啊", "无语"],
    "无语": ["无语", "震惊", "嫌弃", "生气", "沉默"],
    "生气": ["生气", "小生气", "哼", "嫌弃", "不满"],
    "晚安": ["晚安", "睡觉", "休息", "温柔", "贴贴"],
    "认真": ["认真", "观察", "工作", "思考", "好奇"],
    "随机": ["随机"],
}


def _json_error(message: str) -> str:
    return tool_result(success=False, error=message)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)


def _catalog_paths(hermes_home: Path) -> list[Path]:
    media_root = hermes_home / "media"
    if not media_root.exists():
        return []
    return sorted(path for path in media_root.rglob("catalog.json") if path.is_file())


def _extract_assets(catalog: Any) -> list[dict[str, Any]]:
    if isinstance(catalog, list):
        return [item for item in catalog if isinstance(item, dict)]
    if not isinstance(catalog, dict):
        return []
    for key in ("assets", "stickers", "items", "media"):
        items = catalog.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _resolve_asset_path(raw_path: Any, hermes_home: Path, catalog_dir: Path) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path.strip())
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0].lower() == "media":
        return hermes_home / path
    return catalog_dir / path


def _asset_text(asset: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("id", "name", "label", "description", "caption", "text", "source_title"):
        value = asset.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("tags", "moods", "keywords", "emotion", "emotions"):
        value = asset.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return values


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _query_terms(mood: str) -> list[str]:
    normalized = mood.strip()
    if not normalized:
        return ["随机"]
    terms = [normalized]
    for key, aliases in _MOOD_ALIASES.items():
        if normalized == key or normalized in aliases:
            terms.extend(aliases)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        folded = _normalize(term)
        if folded and folded not in seen:
            seen.add(folded)
            result.append(term)
    return result


def _score_asset(asset: dict[str, Any], terms: list[str]) -> int:
    if any(_normalize(term) == "随机" for term in terms):
        return 1
    score = 0
    haystack = [_normalize(text) for text in _asset_text(asset)]
    for term in terms:
        needle = _normalize(term)
        for value in haystack:
            if not value:
                continue
            if needle == value:
                score += 20
            elif needle in value:
                score += 10
            elif value in needle:
                score += 3
    return score


def _load_recent(usage_path: Path) -> list[str]:
    try:
        data = _load_json(usage_path)
    except (OSError, json.JSONDecodeError):
        return []
    recent = data.get("recent") if isinstance(data, dict) else None
    if not isinstance(recent, list):
        return []
    return [item for item in recent if isinstance(item, str)]


def _remember_pick(usage_path: Path, asset_id: str) -> None:
    recent = _load_recent(usage_path)
    recent.append(asset_id)
    data = {
        "recent": recent[-_RECENT_LIMIT:],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        _save_json(usage_path, data)
    except OSError:
        pass


def _suggest_moods(assets: list[dict[str, Any]]) -> str:
    suggestions: list[str] = []
    for asset in assets:
        moods = asset.get("moods")
        if isinstance(moods, list):
            suggestions.extend(item for item in moods if isinstance(item, str))
        tags = asset.get("tags")
        if isinstance(tags, list):
            suggestions.extend(item for item in tags if isinstance(item, str))
    seen: set[str] = set()
    clean: list[str] = []
    for item in suggestions:
        folded = _normalize(item)
        if folded and folded not in seen and folded not in {"sticker", "official"}:
            seen.add(folded)
            clean.append(item)
        if len(clean) >= 5:
            break
    if clean:
        return " Try a broader mood such as " + ", ".join(repr(item) for item in clean) + "."
    return " Check the mood input and retry with a broader mood."


def _collect_stickers(hermes_home: Path) -> tuple[list[dict[str, Any]], list[str]]:
    stickers: list[dict[str, Any]] = []
    errors: list[str] = []
    for catalog_path in _catalog_paths(hermes_home):
        try:
            catalog = _load_json(catalog_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{catalog_path}: {exc}")
            continue
        for asset in _extract_assets(catalog):
            if asset.get("kind") != "sticker":
                continue
            resolved = _resolve_asset_path(asset.get("path") or asset.get("file"), hermes_home, catalog_path.parent)
            if resolved is None:
                continue
            if resolved.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            prepared = dict(asset)
            prepared["_path"] = resolved
            prepared["_usage_path"] = catalog_path.parent / "usage.json"
            if not prepared.get("id"):
                prepared["id"] = resolved.stem
            stickers.append(prepared)
    return stickers, errors


def pick_sticker_tool(args: dict[str, Any], **_kwargs: Any) -> str:
    mood = str(args.get("mood") or "").strip()
    hermes_home = get_hermes_home()
    stickers, errors = _collect_stickers(hermes_home)
    if not stickers:
        detail = f" Catalog errors: {'; '.join(errors)}" if errors else ""
        return _json_error(
            "No sticker catalog was found under HERMES_HOME/media. "
            "Check that media/*/catalog.json exists and retry."
            + detail
        )

    terms = _query_terms(mood)
    scored = [(asset, _score_asset(asset, terms)) for asset in stickers]
    candidates = [asset for asset, score in scored if score > 0]
    if not candidates:
        return _json_error(
            f"No sticker matched mood={mood!r}. Check the mood input and retry with a broader mood."
            + _suggest_moods(stickers)
        )

    recent_by_usage = {str(path): set(_load_recent(path)) for path in {asset["_usage_path"] for asset in candidates}}
    fresh = [
        asset
        for asset in candidates
        if str(asset.get("id")) not in recent_by_usage.get(str(asset["_usage_path"]), set())
    ]
    pool = fresh or candidates
    picked = random.choice(pool)
    media_path = picked["_path"]
    if not media_path.exists():
        return _json_error(
            f"Selected sticker file does not exist: {media_path}. "
            "Check the sticker catalog path and retry with the same or broader mood."
        )

    asset_id = str(picked.get("id") or media_path.stem)
    _remember_pick(picked["_usage_path"], asset_id)
    return tool_result(
        success=True,
        media_tag="MEDIA:" + media_path.as_posix(),
    )


registry.register(
    name="pick_sticker",
    toolset="messaging",
    schema=STICKER_PICKER_SCHEMA,
    handler=pick_sticker_tool,
    emoji="🖼️",
)
