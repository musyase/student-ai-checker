# Student AI Checker

A local DOCX review app for teachers. It extracts text and images from coursework, asks a local Ollama model to assess approved criteria, and checks the model's scores and evidence references in Python. Teachers can inspect uncertain cases in a browser or desktop app.

## Try the review workflow

The demo creates three fictional submissions and prepared results in a temporary directory. It opens the local interface, including a case that needs a teacher's decision. It does not call Ollama or measure grading accuracy. The interface and sample content are currently in Russian.

Requires Python 3.11 or newer:

```bash
python -m pip install -e .
python scripts/demo.py
```

On Windows, use `py` if `python` is unavailable. Stop the demo server to remove its temporary files. For a real model run, install Ollama and a local multimodal model first, then start the app with `student-ai-checker app`. The [Windows and macOS installers](https://github.com/musyase/student-ai-checker/releases/tag/v0.1.11) bundle the app, but Ollama and the model remain separate local dependencies.

## What the code does

1. The DOCX extractor reads paragraphs, table cells, and embedded images. It records the source file hash and gives each piece of evidence a stable ID.
2. The checker sends extracted content and approved criteria to Ollama on the user's computer. Student text is treated as untrusted input; the model has no tools or file access.
3. The validator checks the response against the criteria, allowed scores, and IDs that actually came from the document. A malformed response gets one bounded retry. Unresolved criteria go to manual review instead of receiving an invented score.
4. The local app presents the results by student and group. Teacher decisions are saved separately from the model's original result, and the roster can be exported as CSV.

Start with the [DOCX extractor](src/student_ai_checker/documents/docx_extractor.py), [grading checker](src/student_ai_checker/grading/checker.py), [validator](src/student_ai_checker/grading/validator.py), [local app](src/student_ai_checker/local_app.py), and [teacher review code](src/student_ai_checker/teacher_review.py). The [validator tests](tests/test_validator.py) and [teacher review tests](tests/test_teacher_review.py) show the expected behavior at those boundaries.

## Run one document

[`examples/criteria.json`](examples/criteria.json) shows the criteria format. With Ollama running locally:

```bash
student-ai-checker check --document student.docx --criteria examples/criteria.json --output result.json
```

The result includes the source DOCX hash, criterion results, model name, existing evidence IDs, and extraction warnings. Embedded image bytes are not duplicated in the JSON file. The [Russian guide](README.ru.md) covers batch processing, approval records, resume behavior, the desktop build, and integration tests.

## Evidence and limits

Run `python -m pytest -m "not integration" -q` for the unit suite. [GitHub Actions](https://github.com/musyase/student-ai-checker/actions/workflows/unit-tests.yml) runs it on Windows and Ubuntu with Python 3.11 and 3.14. Integration tests require a real local Ollama installation and are separate from that workflow.

The synthetic demo proves that the review interface can be explored without a model. It does not establish that automated grades are correct. That needs a study using independently scored, anonymized work; the [evaluation plan](docs/quality-evaluation.md) defines the comparison. PDF input is not supported, and the current interface is in Russian. No student document is sent to a cloud API by this application.
