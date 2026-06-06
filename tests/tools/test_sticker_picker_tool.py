import json
from pathlib import Path

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tools import sticker_picker_tool


def _write_catalog(home: Path, assets):
    media_dir = home / "media" / "pack"
    media_dir.mkdir(parents=True)
    for asset in assets:
        raw_path = asset["path"]
        path = home / raw_path if raw_path.startswith("media/") else media_dir / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not asset.get("_missing"):
            path.write_bytes(b"fake-image")
    (media_dir / "catalog.json").write_text(
        json.dumps({"version": 1, "assets": assets}, ensure_ascii=False),
        encoding="utf-8",
    )
    return media_dir


def _call_with_home(home: Path, mood: str):
    token = set_hermes_home_override(home)
    try:
        return json.loads(sticker_picker_tool.pick_sticker_tool({"mood": mood}))
    finally:
        reset_hermes_home_override(token)


def test_pick_sticker_returns_media_tag(tmp_path):
    _write_catalog(
        tmp_path,
        [
            {
                "id": "happy",
                "kind": "sticker",
                "path": "media/pack/happy.png",
                "moods": ["开心"],
                "tags": ["sticker"],
            }
        ],
    )

    result = _call_with_home(tmp_path, "开心")

    assert result["success"] is True
    assert result["media_tag"].startswith("MEDIA:")
    assert result["media_tag"].endswith("/media/pack/happy.png")


def test_pick_sticker_reports_missing_catalog(tmp_path):
    result = _call_with_home(tmp_path, "疑惑")

    assert result["success"] is False
    assert "catalog" in result["error"]
    assert "retry" in result["error"]


def test_pick_sticker_reports_no_match(tmp_path):
    _write_catalog(
        tmp_path,
        [
            {
                "id": "happy",
                "kind": "sticker",
                "path": "media/pack/happy.png",
                "moods": ["开心"],
            }
        ],
    )

    result = _call_with_home(tmp_path, "晚安")

    assert result["success"] is False
    assert "No sticker matched" in result["error"]
    assert "broader mood" in result["error"]


def test_pick_sticker_reports_missing_selected_file(tmp_path):
    _write_catalog(
        tmp_path,
        [
            {
                "id": "missing",
                "kind": "sticker",
                "path": "media/pack/missing.png",
                "moods": ["开心"],
                "_missing": True,
            }
        ],
    )

    result = _call_with_home(tmp_path, "开心")

    assert result["success"] is False
    assert "does not exist" in result["error"]
    assert "retry" in result["error"]


def test_pick_sticker_avoids_recent_when_possible(tmp_path, monkeypatch):
    media_dir = _write_catalog(
        tmp_path,
        [
            {
                "id": "first",
                "kind": "sticker",
                "path": "media/pack/first.png",
                "moods": ["开心"],
            },
            {
                "id": "second",
                "kind": "sticker",
                "path": "media/pack/second.png",
                "moods": ["开心"],
            },
        ],
    )
    (media_dir / "usage.json").write_text(
        json.dumps({"recent": ["first"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(sticker_picker_tool.random, "choice", lambda items: items[0])

    result = _call_with_home(tmp_path, "开心")

    assert result["success"] is True
    assert result["media_tag"].endswith("/media/pack/second.png")
