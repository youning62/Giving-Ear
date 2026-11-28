"""发布用的清单必须互相一致：版本号、skill 名字和目录、MCP 启动命令里锁定的版本。"""

import json
import re
import tomllib
from pathlib import Path

import givingear

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "plugins" / "givingear"


def _frontmatter(path: Path) -> dict:
    head = path.read_text().split("---")[1]
    out, key = {}, None
    for line in head.splitlines():
        if m := re.match(r"^(\w+):\s*(.*)$", line):
            key, val = m.groups()
            out[key] = val.strip(' "')
        elif key and line.startswith("  ") and not line.strip().endswith(":"):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def test_versions_match_everywhere():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert givingear.__version__ == version == plugin["version"] == market["plugins"][0]["version"]
    assert plugin["mcpServers"]["givingear"]["args"] == [f"givingear=={version}", "mcp"]  # 目录要求锁定精确版本
    for skill in (PLUGIN / "skills").glob("*/SKILL.md"):
        assert f'version: "{version}"' in skill.read_text()


def test_skills_follow_the_agent_skills_spec():
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert [s.parent.name for s in skills] == ["speaker-diarization", "speaker-identification"]
    for path in skills:
        fm = _frontmatter(path)
        assert fm["name"] == path.parent.name
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"]) and len(fm["name"]) <= 64
        assert 0 < len(fm["description"]) <= 1024
        assert len(path.read_text().splitlines()) < 500


def test_marketplace_points_at_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = market["plugins"][0]
    assert entry["name"] == "givingear" and (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").exists()
    for f in PLUGIN.rglob("*"):
        if f.is_file():
            assert f.stat().st_size <= 256 * 1024, f  # 官方目录的单文件上限
