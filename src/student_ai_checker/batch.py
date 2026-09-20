"""User-facing assignment/profile resolution for batch checking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from student_ai_checker.exceptions import StudentAICheckerError


@dataclass(frozen=True)
class AssignmentProfile:
    key: str
    practice: str
    criteria: Path
    approval: Path


def assignment_key(assignment: Path) -> str:
    """Return a conservative registry key such as ``pr1`` from a filename."""
    name = assignment.stem.casefold().replace("ё", "е").replace("№", " ")
    match = re.search(
        r"(?:практическ\w*(?:\s+работ\w*)?\s*|пр\s*[._-]?\s*|п\s+)(0*([1-9][0-9]*))",
        name,
    )
    if not match:
        raise StudentAICheckerError(
            "Не удалось определить номер задания по имени файла. "
            "Назовите файл, например, ПР1.docx, или добавьте профиль вручную."
        )
    return f"pr{int(match.group(2))}"


def _read_registry(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise StudentAICheckerError(f"Не удалось прочитать реестр профилей: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StudentAICheckerError(f"Реестр профилей содержит некорректный JSON: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("assignments"), dict):
        raise StudentAICheckerError("Реестр профилей должен содержать объект assignments.")
    return data


def resolve_assignment_profile(assignment: Path, profiles_root: Path) -> AssignmentProfile:
    key = assignment_key(assignment)
    registry_path = profiles_root / "assignment_profiles.json"
    entry: dict[str, object] | None = None
    if registry_path.exists():
        registry = _read_registry(registry_path)
        raw = registry["assignments"].get(key)
        if raw is not None and isinstance(raw, dict):
            entry = raw
    if entry is None:
        entry = {
            "practice": key.removeprefix("pr"),
            "criteria": f"{key}.criteria.approved.json",
            "approval": f"{key}.approval.json",
        }

    practice = str(entry.get("practice", key.removeprefix("pr")))
    criteria_name = entry.get("criteria")
    approval_name = entry.get("approval")
    if not isinstance(criteria_name, str) or not isinstance(approval_name, str):
        raise StudentAICheckerError(f"Профиль {key} должен содержать criteria и approval.")
    criteria = (profiles_root / criteria_name).resolve()
    approval = (profiles_root / approval_name).resolve()
    missing = [str(path) for path in (criteria, approval) if not path.exists()]
    if missing:
        raise StudentAICheckerError(
            f"Для задания {assignment.name} нет утверждённого профиля {key}. "
            "Сначала подготовьте criteria.approved.json и approval.json. "
            + " Отсутствуют: " + ", ".join(missing)
        )
    return AssignmentProfile(key=key, practice=practice, criteria=criteria, approval=approval)
