"""Minimal CLI for the Stage 1 technical core."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime
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
from student_ai_checker.grading.visual_review import VISUAL_PIPELINE_VERSION, VisualReviewChecker
from student_ai_checker.grading.checked_scoring import CheckedScoringChecker
from student_ai_checker.grading.checked_text import CHECKED_SCORING_VERSION
from student_ai_checker.grading.grade_scale import GradeScale
from student_ai_checker.teacher_review import build_teacher_review
from student_ai_checker.batch import resolve_assignment_profile

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
    modes.add_argument("--checked-scoring", action="store_true", help="ПР1 v1: цитаты, расчёт текстовых пунктов и соответствий кодом")
    modes.add_argument("--visual-review", action="store_true", help="Отдельный анализ каждого изображения перед оцениванием критерия")
    modes.add_argument("--per-criterion", action="store_true", help="Экспериментально: проверять по одному критерию, последовательно")
    check.add_argument(
        "--automatic",
        action="store_true",
        help="Зафиксировать полностью определённый результат автоматически без подтверждения преподавателя.",
    )
    check.add_argument("--model", help="Имя локальной модели Ollama")
    check.add_argument("--ollama-url", help="URL локального Ollama")
    batch = subcommands.add_parser("batch", help="Проверить папку работ по одному заданию")
    batch.add_argument("--folder", required=True, type=Path, help="Папка с работами студентов")
    batch.add_argument("--assignment", required=True, type=Path, help="Файл задания/чистый DOCX-бланк")
    batch.add_argument("--profiles-root", type=Path, default=Path("examples"), help="Каталог утверждённых профилей")
    batch.add_argument("--output-root", type=Path, help="Каталог результатов; по умолчанию создаётся автоматически")
    batch.add_argument("--workers", type=int, default=2, help="Количество параллельных проверок")
    batch.add_argument("--resume", action="store_true", help="Продолжить существующий пакет")
    batch.add_argument("--model", help="Имя локальной модели Ollama")
    status = subcommands.add_parser("ollama-status", help="Проверить Ollama и модель")
    status.add_argument("--model", help="Имя локальной модели Ollama")
    status.add_argument("--ollama-url", help="URL локального Ollama")
    review = subcommands.add_parser("review", help="Сохранить решение преподавателя поверх result.json")
    review.add_argument("--result", required=True, type=Path, help="Автоматический result.json")
    review.add_argument("--criteria", required=True, type=Path)
    review.add_argument("--decisions", required=True, type=Path, help="JSON с объектом decisions")
    review.add_argument("--output", required=True, type=Path, help="Новый reviewed-result.json")
    app = subcommands.add_parser("app", help="Запустить локальное приложение преподавателя")
    app.add_argument("--host", default="127.0.0.1")
    app.add_argument("--port", type=int, default=8765)
    app.add_argument("--state-dir", type=Path, help="Каталог состояния приложения")
    app.add_argument("--no-browser", action="store_true", help="Не открывать браузер автоматически")
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


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise StudentAICheckerError(f"Не удалось прочитать {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StudentAICheckerError(f"Файл {label} содержит некорректный JSON.") from exc
    if not isinstance(data, dict):
        raise StudentAICheckerError(f"Файл {label} должен содержать JSON-объект.")
    return data


def _resolved_output(*, document, criteria, response, model: str) -> dict[str, object]:
    referenced_ids = {evidence_id for result in response.results for evidence_id in result.evidence_ids}
    evidence = [item.model_dump(mode="json") for item in document.evidence if item.id in referenced_ids]
    results = []
    for result in response.results:
        data = result.model_dump(mode="json")
        data["status_label_ru"] = result.status.label_ru
        results.append(data)
    return {"schema_version": 1, "document": {"source_name": document.source_name, "source_sha256": document.source_sha256}, "criteria_version": criteria.version, "model": model, "results": results, "resolved_evidence": evidence, "extraction_issues": [issue.model_dump(mode="json") for issue in document.issues]}


def _score_summary(
    criteria: CriteriaSet,
    response: GradingResponse,
    grade_scale=None,
    *,
    automatic: bool = False,
) -> dict[str, object]:
    from decimal import Decimal

    scores = [r.proposed_score for r in response.results if r.proposed_score is not None]
    points = float(sum((Decimal(str(score)) for score in scores), Decimal(0)))
    complete = len(scores) == len(criteria.criteria)
    maximum = float(sum(Decimal(str(c.max_score)) for c in criteria.criteria))
    proposed_total = points if complete else None
    effective_scale = grade_scale or GradeScale.standard()
    proposed_grade = effective_scale.grade_for(points, maximum) if complete else None
    manual_review_required = any(r.manual_review_required for r in response.results)
    automatic_final = automatic and complete and not manual_review_required
    return {
        "max_score": maximum,
        "scored_points": points,
        "proposed_total": proposed_total,
        "proposed_grade": proposed_grade,
        "unresolved_count": len(criteria.criteria) - len(scores),
        "manual_review_required": manual_review_required,
        "teacher_confirmed": False,
        "automatic_final": automatic_final,
        "final_total": proposed_total if automatic_final else None,
        "final_grade": proposed_grade if automatic_final else None,
        "grade": proposed_grade if automatic_final else None,
    }


def _run_check(args: argparse.Namespace, settings: Settings) -> int:
    if args.output.exists():
        raise StudentAICheckerError("Файл результата уже существует. Укажите новое имя --output: предыдущая проверка сохранена.")
    if bool(args.template) != bool(args.approval):
        raise StudentAICheckerError("Для проверки по бланку укажите вместе --template и --approval.")
    if args.checked_scoring and not args.template:
        raise StudentAICheckerError("checked-scoring требует --template и --approval.")
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
    checked_checker = None
    with OllamaClient(base_url=base_url, model=model, timeout_seconds=settings.ollama_timeout_seconds, num_ctx=settings.ollama_num_ctx) as ollama:
        checker = TemplateWorkChecker(ollama, comparison) if comparison else StudentWorkChecker(ollama)
        if args.checked_scoring:
            checked_checker = CheckedScoringChecker(ollama, comparison)
            response = checked_checker.check(document, criteria)
        elif args.visual_review:
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
        "criterion_mode": "checked_scoring" if args.checked_scoring else ("visual_review" if args.visual_review else ("per_criterion" if args.per_criterion else "all_at_once")),
    }
    if visual_checker is not None:
        output["visual_review"] = {
            "pipeline_version": VISUAL_PIPELINE_VERSION, "model_output_is_untrusted": True,
            "criteria": visual_checker.audit,
        }
    if checked_checker is not None:
        output["checked_scoring"] = {
            "version": CHECKED_SCORING_VERSION, "profile_sha256": checked_checker.profile_digest,
            "model_output_is_untrusted": True, "criteria": checked_checker.audit,
        }
    output["criteria_sha256"] = criteria_digest(criteria)
    output["approval"] = approval.model_dump(mode="json") if approval else None
    output["template_comparison"] = comparison.report() if comparison else None
    output["summary"] = _score_summary(
        criteria,
        response,
        approval.grade_scale if approval else None,
        automatic=args.automatic,
    )
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
        if summary["proposed_grade"] is not None:
            print(f"Предварительная оценка: {summary['proposed_grade']}/5.")
        else:
            print("Шкала оценки не задана в утверждении критериев.")
    if output["summary"].get("automatic_final"):
        print("Итоговая оценка выставлена автоматически: подтверждение преподавателя не требуется.")
    else:
        print("Итоговая оценка фиксируется после подтверждения преподавателем.")
    return 0


def _run_batch(args: argparse.Namespace, settings: Settings) -> int:
    folder = args.folder.resolve()
    assignment = args.assignment.resolve()
    profiles_root = args.profiles_root.resolve()
    if not folder.is_dir():
        raise StudentAICheckerError(f"Папка работ не найдена: {folder}")
    if not assignment.is_file():
        raise StudentAICheckerError(f"Файл задания не найден: {assignment}")
    if args.workers < 1:
        raise StudentAICheckerError("--workers должен быть не меньше 1.")
    if args.resume and args.output_root is None:
        raise StudentAICheckerError("Для --resume укажите прежний --output-root.")

    profile = resolve_assignment_profile(assignment, profiles_root)
    model = args.model or settings.ollama_model
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "scripts" / "run_checked_batch.py"
    if not runner.exists():
        raise StudentAICheckerError(f"Не найден batch runner: {runner}")
    output = (args.output_root.resolve() if args.output_root else
              folder.parent / "_student-ai-checker-results" /
              f"{profile.key}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    command = [
        sys.executable, str(runner),
        "--source-root", str(folder),
        "--template", str(assignment),
        "--criteria", str(profile.criteria),
        "--approval", str(profile.approval),
        "--output-root", str(output),
        "--practice", profile.practice,
        "--model", model,
        "--workers", str(args.workers),
    ]
    if args.resume:
        command.append("--resume")
    print(f"Профиль: {profile.key}")
    print(f"Результаты: {output}")
    completed = subprocess.run(command, cwd=repo_root, check=False)
    if completed.returncode != 0:
        raise StudentAICheckerError(
            f"Пакетная проверка завершилась с кодом {completed.returncode}. "
            f"Манифест сохранён в {output / 'batch-manifest.json'}"
        )
    print(f"Проверка завершена. Сводка: {output / 'results.csv'}")
    print(f"Ручная очередь: {output / 'manual-review.json'}")
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


def _run_review(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise StudentAICheckerError("Файл reviewed-result уже существует. Укажите новое имя: предыдущая проверка сохранена.")
    automatic = _load_json_object(args.result, "автоматического результата")
    decisions = _load_json_object(args.decisions, "решений преподавателя")
    criteria = _load_criteria(args.criteria)
    try:
        reviewed = build_teacher_review(automatic, criteria, decisions)
    except ValueError as exc:
        raise StudentAICheckerError(f"Решение преподавателя не прошло проверку: {exc}") from exc
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(reviewed, stream, ensure_ascii=False, indent=2, allow_nan=False)
    except OSError as exc:
        raise StudentAICheckerError(f"Не удалось записать результат преподавателя: {args.output}") from exc
    review = reviewed["teacher_review"]
    print(f"Решение преподавателя сохранено: {args.output}")
    print(f"Статус ручной проверки: {review['status']}")
    return 0


def _run_app(args: argparse.Namespace) -> int:
    if args.port < 0 or args.port > 65535:
        raise StudentAICheckerError("Порт должен быть от 0 до 65535.")
    from student_ai_checker.local_app import serve
    serve(host=args.host, port=args.port, state_dir=args.state_dir, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    try:
        settings = Settings.from_env()
        if args.command == "check":
            return _run_check(args, settings)
        if args.command == "batch":
            return _run_batch(args, settings)
        if args.command == "ollama-status":
            return _run_ollama_status(args, settings)
        if args.command == "review":
            return _run_review(args)
        if args.command == "app":
            return _run_app(args)
        raise AssertionError(f"Unhandled command: {args.command}")
    except StudentAICheckerError as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.warning("Операция отменена пользователем.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
