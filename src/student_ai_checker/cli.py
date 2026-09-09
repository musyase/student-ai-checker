"""Minimal CLI for the Stage 1 technical core."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from student_ai_checker.ai import OllamaClient
from student_ai_checker.config import Settings
from student_ai_checker.documents import DocxExtractor
from student_ai_checker.exceptions import StudentAICheckerError
from student_ai_checker.grading import StudentWorkChecker
from student_ai_checker.models import CriteriaSet, GradingResponse
from student_ai_checker.documents.template import compare_template
from student_ai_checker.grading.approval import criteria_digest, verify_approval
from student_ai_checker.grading.template_checker import TemplateWorkChecker
from student_ai_checker.grading.per_criterion import PerCriterionChecker
from student_ai_checker.grading.visual_review import VisualReviewChecker

LOGGER = logging.getLogger("student_ai_checker")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="student-ai-checker", description="Локальная проверка студенческих DOCX по утверждённым критериям.")
    parser.add_argument("--verbose", action="store_true", help="Подробное логирование")
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("check", help="Проверить один DOCX")
    check.add_argument("--document", required=True, type=Path)
    check.add_argument("--criteria", required=True, type=Path)
    check.add_argument("--output", type=Path, default=Path("result.json"))
    check.add_argument("--template", type=Path, help="Чистый исходный DOCX-бланк")
    check.add_argument("--approval", type=Path, help="Запись утверждения критериев и бланка")
    modes = check.add_mutually_exclusive_group()
    modes.add_argument("--visual-review", action="store_true", help="Отдельный анализ каждого изображения перед оцениванием критерия")
    modes.add_argument("--per-criterion", action="store_true", help="Экспериментально: проверять по одному критерию, последовательно")
    check.add_argument("--model", help="Имя локальной модели Ollama")
    check.add_argument("--ollama-url", help="URL локального Ollama")
    status = subcommands.add_parser("ollama-status", help="Проверить Ollama и модель")
    status.add_argument("--model", help="Имя локальной модели Ollama")
    status.add_argument("--ollama-url", help="URL локального Ollama")
    return parser


def _load_criteria(path: Path) -> CriteriaSet:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise StudentAICheckerError(f"Не удалось прочитать критерии: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StudentAICheckerError("Файл критериев содержит некорректный JSON.") from exc
    try:
        return CriteriaSet.model_validate(data)
    except ValidationError as exc:
        raise StudentAICheckerError("Файл критериев не соответствует схеме Student AI Checker.\n" + str(exc)) from exc


def _resolved_output(*, document, criteria, response, model: str) -> dict[str, object]:
    referenced_ids = {evidence_id for result in response.results for evidence_id in result.evidence_ids}
    evidence = [item.model_dump(mode="json") for item in document.evidence if item.id in referenced_ids]
    results = []
    for result in response.results:
        data = result.model_dump(mode="json")
        data["status_label_ru"] = result.status.label_ru
        results.append(data)
    return {"schema_version": 1, "document": {"source_name": document.source_name, "source_sha256": document.source_sha256}, "criteria_version": criteria.version, "model": model, "results": results, "resolved_evidence": evidence, "extraction_issues": [issue.model_dump(mode="json") for issue in document.issues]}


def _score_summary(criteria: CriteriaSet, response: GradingResponse) -> dict[str, object]:
    from decimal import Decimal

    scores = [r.proposed_score for r in response.results if r.proposed_score is not None]
    points = float(sum((Decimal(str(score)) for score in scores), Decimal(0)))
    complete = len(scores) == len(criteria.criteria)
    return {
        "max_score": float(sum(Decimal(str(c.max_score)) for c in criteria.criteria)),
        "scored_points": points,
        "proposed_total": points if complete else None,
        "unresolved_count": len(criteria.criteria) - len(scores),
        "manual_review_required": any(r.manual_review_required for r in response.results),
        "teacher_confirmed": False,
        "grade": None,
    }


def _run_check(args: argparse.Namespace, settings: Settings) -> int:
    if args.output.exists():
        raise StudentAICheckerError("Файл результата уже существует. Укажите новое имя --output: предыдущая проверка сохранена.")
    if bool(args.template) != bool(args.approval):
        raise StudentAICheckerError("Для проверки по бланку укажите вместе --template и --approval.")
    criteria = _load_criteria(args.criteria)
    document = DocxExtractor().extract(args.document)
    comparison = None
    approval = None
    if args.template:
        template = DocxExtractor().extract(args.template)
        approval = verify_approval(args.approval, criteria=criteria, template_sha256=template.source_sha256)
        comparison = compare_template(document, template)
        LOGGER.info("Template comparison: %s", comparison.report()["counts"])
    else:
        LOGGER.warning("Без бланка и записи утверждения: технический режим, результат не подтверждён преподавателем.")
    model = args.model or settings.ollama_model
    base_url = args.ollama_url or settings.ollama_base_url
    LOGGER.info("Extracted %s evidence items (%s images) from %s", len(document.evidence), len(document.images), document.source_name)
    visual_checker = None
    with OllamaClient(base_url=base_url, model=model, timeout_seconds=settings.ollama_timeout_seconds, num_ctx=settings.ollama_num_ctx) as ollama:
        checker = TemplateWorkChecker(ollama, comparison) if comparison else StudentWorkChecker(ollama)
        if args.visual_review:
            visual_checker = VisualReviewChecker(ollama, comparison)
            response = visual_checker.check(document, criteria)
        elif args.per_criterion:
            LOGGER.info("Режим: по одному критерию; все допустимые доказательства сохраняются.")
            response = PerCriterionChecker(checker).check(document, criteria)
        else:
            response = checker.check(document, criteria)
    output = _resolved_output(document=document, criteria=criteria, response=response, model=model)
    output["inference_options"] = {
        "num_ctx": settings.ollama_num_ctx,
        "criterion_mode": "visual_review" if args.visual_review else ("per_criterion" if args.per_criterion else "all_at_once"),
    }
    if visual_checker is not None:
        output["visual_review"] = {
            "pipeline_version": 1, "model_output_is_untrusted": True,
            "criteria": visual_checker.audit,
        }
    output["criteria_sha256"] = criteria_digest(criteria)
    output["approval"] = approval.model_dump(mode="json") if approval else None
    output["template_comparison"] = comparison.report() if comparison else None
    output["summary"] = _score_summary(criteria, response)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects against a file appearing during inference.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
    except OSError as exc:
        raise StudentAICheckerError(f"Проверка выполнена, но не удалось записать результат: {args.output}") from exc
    print(f"Результат сохранён: {args.output}")
    for result in response.results:
        score = "—" if result.proposed_score is None else str(result.proposed_score)
        print(f"[{result.status.label_ru}] {result.title}: {score}/{result.max_score}")
    summary = output["summary"]
    total = summary["proposed_total"]
    if total is None:
        print(f"Итог не завершён. Баллы по проверенным критериям: {summary['scored_points']}; есть критерии без балла.")
    else:
        print(f"Предварительная сумма: {total}/{summary['max_score']}.")
    print("Оценка 2–5 не назначена. Результат требует подтверждения преподавателем.")
    return 0


def _run_ollama_status(args: argparse.Namespace, settings: Settings) -> int:
    model = args.model or settings.ollama_model
    base_url = args.ollama_url or settings.ollama_base_url
    with OllamaClient(base_url=base_url, model=model, timeout_seconds=settings.ollama_timeout_seconds, num_ctx=settings.ollama_num_ctx) as ollama:
        models = ollama.list_models()
        print(f"Ollama доступен: {base_url}")
        if model in models:
            print(f"Модель доступна: {model}")
            return 0
        print(f"Модель не найдена: {model}")
        if models:
            print("Установленные модели: " + ", ".join(models))
        return 3


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    try:
        settings = Settings.from_env()
        if args.command == "check":
            return _run_check(args, settings)
        if args.command == "ollama-status":
            return _run_ollama_status(args, settings)
        raise AssertionError(f"Unhandled command: {args.command}")
    except StudentAICheckerError as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.warning("Операция отменена пользователем.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
