"""Preserve the operation and conditions when rendering approved PR1 components.

This compiler never sees a submission or a model reply and cannot award points.
The original components remain the immutable arithmetic profile. This is a
versioned rendering of their parent-rubric context, not another grading scale.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

EVALUATION_CONTEXT_VERSION = 4

# Exact descriptions from the approved PR1 v1. Do not apply the interpretation
# of this task to another task that happens to reuse an ID.
_DESCRIPTION_DIGESTS = {
    "pr1.usability": "d1627005fe8e03448603d3e732d032afbfe2cba2f861d702cedda0769209d8c9",
    "pr1.installation-report": "ae6819d89e5bb92fe657b7a0e76eea232f6054721beaecd86fd227921d8edc56",
    "pr1.add-button": "ff49bd58fbe59cf6b649320f39331d7cb6b0cc664a8b5604214ae904ee4f8a2d",
}
_FRAGMENTS = {
    "pr1.usability": {
        "button": "непонятная надпись «ОК» на кнопке добавления",
        "input": "отсутствие подписи/подсказки у поля",
        "font": "слишком мелкий текст задач",
    },
    "pr1.installation-report": {
        "initial": "описано исходное состояние компьютера",
        "actions": "описаны собственные действия",
        "outcome": "указан достигнутый этап и, при возникновении затруднения, проблема и действия после неё",
    },
    "pr1.add-button": {
        "added": "эта новая задача появляется в списке",
        "count": "задач становится три",
        "input": "предложено понятное поведение текста в поле ввода",
    },
}


class EvaluationContextError(ValueError):
    """The rendering is not applicable to the provided rubric/components."""


def compile_evaluation_checks(
    criterion_id: str,
    description: str,
    parts: Sequence[tuple[str, str, int]],
) -> list[dict[str, Any]]:
    """Render independently meaningful checks, retaining exact rubric fragments.

    Unknown task IDs retain their original requirements and get no PR1 rules.
    For known tasks any changed description or component fails before inference.
    Points are accepted only to verify the existing profile, never sent as an
    expected answer. Returned objects are fresh copies, not mutable shared state.
    """
    if not parts or len({p[0] for p in parts}) != len(parts):
        raise EvaluationContextError("invalid_component_set")
    if any(not key or not requirement.strip() for key, requirement, _ in parts):
        raise EvaluationContextError("empty_component_requirement")
    if criterion_id not in _DESCRIPTION_DIGESTS:
        return [{"check_id": key, "requirement": text} for key, text, _ in parts]

    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
    if digest != _DESCRIPTION_DIGESTS[criterion_id]:
        raise EvaluationContextError("parent_rubric_context_changed")
    expected = _FRAGMENTS[criterion_id]
    if ({key for key, _, _ in parts} != set(expected)
            or any(expected.get(key) != text or isinstance(points, bool) or points != 1
                   for key, text, points in parts)):
        raise EvaluationContextError("component_profile_context_mismatch")

    rendered = []
    for key, text, _ in parts:
        check: dict[str, Any] = {"check_id": key, "source_fragment": text}
        if criterion_id == "pr1.usability":
            check.update(
                operation="evaluate_proposed_fix",
                requirement="Предложено осмысленное исправление проблемы: «" + text + "».",
                rubric_fragments=[
                    "Проверяй собственные ответы в правом столбце «Как исправить».",
                    "По 1 баллу за осмысленное исправление каждой проблемы:",
                    text,
                    "Допустимы смысловые варианты, а не только дословные фразы из образца.",
                ],
                interpretation=(
                    "Проверяется исправление неудобства, НЕ наличие или пересказ неудобства. "
                    "Сохранение или усиление той же проблемы не является исправлением. "
                    "Оцени смысл предложенного решения; не требуй дословного образца, "
                    "перерисовки или программной реализации."
                ),
            )
        elif criterion_id == "pr1.add-button":
            operations = {
                "added": "report_new_task",
                "count": "report_new_count",
                "input": "evaluate_input_policy",
            }
            questions = {
                "added": "В ответе описано, что после нажатия новая задача появляется в списке.",
                "count": "В ответе описано, что после нажатия задач становится три.",
                "input": "В ответе предложено понятное поведение текста в поле ввода после добавления.",
            }
            check.update(
                operation=operations[key], requirement=questions[key],
                rubric_fragments=[
                    "Исходное условие:",
                    text,
                    "Очистка поля — допустимый, но не единственный возможный вариант:",
                    "Не требуй кода.",
                ],
                interpretation=(
                    "Для поведения поля достаточно конкретной понятной политики: очистить или освободить поле, "
                    "оставить текст для редактирования, заменить или иным образом объяснить, что происходит с введённым текстом. "
                    "Фраза о том, чтобы освободить поле от уже введённого текста, является такой политикой и засчитывается. "
                    "Не требуй именно очистку, описание события интерфейса или программную реализацию. "
                    "Если ответ прямо говорит, что изменение списка или количества не описано, этот пункт не выполнен."
                ),
            )
        else:
            operations = {"initial": "report_initial_state", "actions": "report_own_actions",
                          "outcome": "report_reached_stage"}
            questions = {
                "initial": "В ответе описано состояние компьютера до собственных действий студента.",
                "actions": "В ответе описано, что сделал сам студент в ходе работы.",
                "outcome": (
                    "В ответе указан этап, до которого студент дошёл в ходе работы. "
                    "Если описано возникшее затруднение, также указаны проблема и действия после неё."
                ),
            }
            check.update(
                operation=operations[key], requirement=questions[key],
                rubric_fragments=[
                    "Оценивай заполненный фрагмент «Ход работы», а не текст инструкции.",
                    text,
                    "Незавершённая установка сама по себе не снижает балл.",
                    "Неполное описание и техническая невозможность проверки — разные ситуации.",
                ],
                interpretation=(
                    "Различай исходное состояние, собственные действия и достигнутый этап. "
                    "Сообщение о том, что среда или редактор уже были на компьютере до начала работы, "
                    "засчитывается как описание исходного состояния, даже если дальнейшие действия не описаны. "
                    "Одно предложение может раскрывать несколько пунктов, но только если содержит "
                    "их смысл. Не выводи результат действий из одного факта исходного состояния. "
                    "Если достигнутый этап не описан в доступном ответе, это пропуск пункта, "
                    "а не неопределённость о воображаемых событиях. Не требуй действий из других пунктов "
                    "повторно и не делай успешную установку условием зачёта."
                ),
            )
            if key == "outcome":
                check["conditional_requirement"] = {
                    "when": "при возникновении затруднения",
                    "then": "проблема и действия после неё",
                    "interpretation": (
                        "Не предполагай неупомянутое затруднение и не требуй обязательной фразы "
                        "об отсутствии проблем. Конкретное противоречие в самом ответе "
                        "может потребовать uncertain; простое отсутствие описания этапа — not_met."
                    ),
                }
        if any(fragment not in description for fragment in check["rubric_fragments"]):
            raise EvaluationContextError("rubric_fragment_not_found")
        rendered.append(check)
    return rendered
