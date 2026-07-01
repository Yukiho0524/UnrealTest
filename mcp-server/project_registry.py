from __future__ import annotations

import json
from pathlib import Path


def find_unreal_projects(workspace_root: Path) -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    for project_path in sorted(workspace_root.glob("**/*.uproject")):
        project_data = json.loads(project_path.read_text(encoding="utf-8"))
        engine_info = read_engine_info(project_path.parent)
        projects.append(
            {
                "name": project_path.stem,
                "path": str(project_path),
                "engineAssociation": str(project_data.get("EngineAssociation", "")),
                "editorPath": engine_info.get("windowsEditorPath", ""),
                "installLocation": engine_info.get("installLocation", ""),
            }
        )
    return projects


def read_engine_info(project_dir: Path) -> dict[str, str]:
    engine_info_path = project_dir / "engine.version.json"
    if not engine_info_path.exists():
        return {}
    return json.loads(engine_info_path.read_text(encoding="utf-8"))
