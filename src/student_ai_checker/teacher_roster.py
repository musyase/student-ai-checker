"""Teacher-facing, surname-sorted batch roster helpers."""

from __future__ import annotations

import re
import csv
from collections import defaultdict
from typing import Any
from pathlib import Path


_GROUP_RE = re.compile(r"(?i)(\d{2})\s*ИС\s*ИП\s*[- ]?(\d{1,2})\s*[- ]?(\d{1,2})")
_PRACTICE_PREFIX_RE = re.compile(
    r"(?i)^(?:практическ\w*\s*(?:работ\w*\s*)?(?:№\s*)?1|пр\s*[._№-]?\s*0?1|п\s*1)\s*[-._: ]*"
)
_PRACTICE_SUFFIX_RE = re.compile(
    r"(?i)[\s_.-]*(?:практическ\w*\s*(?:работ\w*\s*)?(?:№\s*)?1|пр\s*[._№-]?\s*0?1|п\s*1)\s*$"
)


def normalize_group(value: str) -> str:
    """Normalize common group filename spellings for sorting and display."""
    match = _GROUP_RE.search(value)
    if match:
        return f"{match.group(1)}ИСИП-{match.group(2)}-{match.group(3)}"
    return re.sub(r"\s+", " ", value.strip())


def student_label(source_name: str) -> str:
    """Extract the student surname/name part from a submission filename."""
    text = source_name
    text = re.sub(r"(?i)\.docx", "", text)
    text = _GROUP_RE.sub("", text, count=1)
    text = re.sub(r"^\s*\d{1,2}\s*[- ]\s*\d{1,2}\s*[_ -]*", "", text)
    text = _PRACTICE_PREFIX_RE.sub("", text)
    text = _PRACTICE_SUFFIX_RE.sub("", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" .,_-")
    return text or "Не удалось определить фамилию"


def sort_surname(label: str) -> str:
    """Return a stable surname key; preserve all names in the visible label."""
    first = re.split(r"\s*(?:,|\band\b|\bи\b)\s*", label, maxsplit=1, flags=re.IGNORECASE)[0]
    return first.strip().casefold()


def build_teacher_roster(manifest: dict[str, Any], manual_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Create one teacher-facing row per submitted DOCX."""
    manual_by_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in manual_payload.get("items", []):
        manual_by_index[int(item["index"])].append(item)

    rows: list[dict[str, Any]] = []
    for case in manifest.get("cases", []):
        index = int(case["index"])
        label = student_label(case["source_name"])
        manual_items = manual_by_index.get(index, [])
        if case.get("status") == "failed":
            status = "Ошибка проверки"
        elif case.get("manual_review_required"):
            status = "Нужна проверка"
        elif case.get("status") == "manual_review":
            status = "Нужна проверка"
        else:
            status = "Готово"
        estimated_grade = case.get("estimated_grade")
        # A zero here can mean that the model did not return a score at all.
        # It must not be converted into a failing grade of 2.
        if (estimated_grade is None and case.get("scored_points") is not None
                and float(case.get("scored_points") or 0) > 0 and case.get("max_score")):
            percent = float(case["scored_points"]) * 100 / float(case["max_score"])
            estimated_grade = 5 if percent >= 70 else 4 if percent >= 50 else 3 if percent >= 30 else 2
        grade = case.get("proposed_grade") if case.get("proposed_grade") is not None else estimated_grade
        scored_points = case.get("scored_points")
        # A manual case with no proposed total and zero scored points has no
        # numeric result. Keep it blank instead of displaying misleading 0.
        if (case.get("proposed_total") is None and case.get("manual_review_required")
                and float(scored_points or 0) == 0):
            scored_points = None
        criterion_ids = sorted({str(item.get("criterion_id")) for item in manual_items if item.get("criterion_id")})
        reasons = []
        for item in manual_items:
            reason = str(item.get("reason") or "").strip()
            if reason and reason not in reasons:
                reasons.append(reason)
        rows.append({
            "Группа": normalize_group(str(case.get("group") or "")),
            "Фамилия / студенты": label,
            "Сортировка": sort_surname(label),
            "Итоговые баллы": case.get("proposed_total"),
            "Известно баллов": scored_points,
            "Макс. баллов": case.get("max_score"),
            "Оценка": grade,
            "Оценка примерная": bool(case.get("grade_is_estimate") or (grade is not None and case.get("proposed_grade") is None)),
            "Статус": status,
            "Критерии для проверки": ", ".join(criterion_ids),
            "Причина": " | ".join(reasons),
            "Файл": case.get("source_relative_path", case.get("source_name", "")),
            "Индекс": index,
        })
    rows.sort(key=lambda row: (row["Группа"].casefold(), row["Сортировка"], row["Фамилия / студенты"].casefold()))
    return rows


def build_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["Группа"]].append(row)
    summary = []
    for group, items in sorted(groups.items(), key=lambda pair: pair[0].casefold()):
        summary.append({
            "Группа": group,
            "Работ": len(items),
            "Готово": sum(item["Статус"] == "Готово" for item in items),
            "Нужна проверка": sum(item["Статус"] != "Готово" for item in items),
            "Оценка 5": sum(item["Оценка"] == 5 for item in items),
            "Оценка 4": sum(item["Оценка"] == 4 for item in items),
            "Оценка 3": sum(item["Оценка"] == 3 for item in items),
            "Оценка 2": sum(item["Оценка"] == 2 for item in items),
        })
    return summary


def write_teacher_roster_csv(output: Path, manifest: dict[str, Any], manual_payload: dict[str, Any]) -> None:
    """Write the sorted roster and compact group summary beside batch reports."""
    roster = build_teacher_roster(manifest, manual_payload)
    roster_columns = [key for key in roster[0] if key not in {"Сортировка", "Индекс"}] if roster else ["Группа"]
    with (output / "teacher-roster.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=roster_columns)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in roster_columns} for row in roster)
    group_rows = build_group_summary(roster)
    group_columns = list(group_rows[0]) if group_rows else ["Группа"]
    with (output / "groups-summary.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=group_columns)
        writer.writeheader()
        writer.writerows(group_rows)
