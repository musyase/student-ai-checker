# Architecture notes — Stage 1

## Boundaries

`documents` отвечает только за безопасное извлечение данных и evidence. Он ничего не знает об Ollama или оценках.

`ai` — заменяемый инфраструктурный адаптер к локальному inference engine. Сейчас это Ollama. В бизнес-логике нет обращения к облачным API.

`grading` получает только утверждённый `CriteriaSet`, extracted evidence и AI adapter. После ответа модели обычный Python-код повторно проверяет ограничения.

`models` содержит контракт данных между слоями.

CLI — тонкая оболочка. Будущий PySide6 UI должен вызывать те же сервисы, а не переносить бизнес-логику в widgets.

## Evidence IDs

Текущий формат:

```text
DOC1:META:0001
DOC1:P:0001
DOC1:T:0001:R:0001:C:0001
DOC1:IMG:0001
```

ID детерминированы порядком OOXML-элементов в одном неизменном DOCX. Исходный файл дополнительно фиксируется SHA-256.

Изображение содержит собственный SHA-256 и ссылку на родительский paragraph/cell, когда такая связь доступна.

## Technical-failure safety

Непроверенный критерий не получает автоматический нулевой балл. Для `insufficient_evidence` / `unable_to_check` используется `proposed_score = null`.

Если extractor сообщает ошибку по типу доказательства, обязательному для критерия, deterministic safety layer не позволяет отрицательному/частичному AI-выводу стать обычным баллом: результат переводится в `unable_to_check` и требует ручной проверки. Положительное завершение допускается только когда модель ссылается на реально извлечённое доказательство требуемого типа.

## Prompt-injection boundary

Защита не основана только на одной фразе в prompt:

1. student content передаётся как отдельный недоверенный payload;
2. approved criteria находятся в trusted system context;
3. у модели нет tools/actions;
4. AI не записывает в БД и не подтверждает оценку;
5. JSON Schema ограничивает форму ответа;
6. Pydantic запрещает лишние/неверные поля и статусы;
7. `GradingValidator` проверяет criteria IDs, titles, max/allowed scores и evidence IDs;
8. неизвестный evidence ID отклоняет ответ целиком;
9. допускается только одна повторная попытка модели;
10. повторная ошибка заканчивается ручной проверкой, а не выдуманным результатом.

## Next architectural steps

После подтверждения реального Ollama integration логичны SQLite persistence, versioned criteria/submissions/AI runs, persistent queue и только затем PySide6 UI.
