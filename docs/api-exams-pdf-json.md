# PDF/JSON exam flow – API summary

This document summarizes endpoints and contracts for the PDF + JSON exam/quiz flow (source types `PDF`, `JSON`).

## Answer key JSON schema

- **no** (optional): question number.
- **qtype**: `"mc"` | `"open"` | `"situation"`.
- **options**: for MC, array of option strings (e.g. `["A", "B", "C", "D"]`).
- **answer** / **correct**: for MC, correct option as **index** (0-based) or key; normalized to `options: [{ key, text }]` and `correct` as option **key** (e.g. `opt_1`).
- Normalization and validation: `tests/answer_key.py` — `validate_and_normalize_answer_key_json()`.

## Teacher

- **Create exam**: `source_type`: `"bank"` | `"JSON"` | `"PDF"`. For PDF: `pdf_id` + `answer_key_json`. For JSON: `answer_key_json` only. Validation enforces composition (quiz/exam counts); invalid composition blocks activation and Start Now.
- **Start Now**: Creates one `ExamRun` per selected group and one per selected student; does not remove existing runs.
- **Attempt detail** (grading): Response includes `attemptBlueprint` (question order + option order, with `correctOptionId` per item) and `situationScoringSet: 'SET1'`.
- **Grade attempt**: `POST /api/teacher/.../attempts/<id>/grade` — body: `manualScores`, optional `per_situation_scores` (Option Set 1: `[{ index: 1-based, fraction: 0 | 1/3 | 1/2 | 2/3 | 1 }]`), `publish`.

## Student

- **Exams list**: Only runs that are active, in time window, accessible, and **not** already submitted (unless attempt was reset). At most one run per exam (by latest `end_at`).
- **Start run**: `GET/POST` start run by `run_id`. Response: `questions` from blueprint (each: `questionNumber`/`questionId`, `kind`, `options: [{ id, text }]` only — **no** correct answers). `pdfUrl`: **protected** PDF URL.
- **Protected PDF**: `GET /api/student/runs/<run_id>/pdf`. Requires: run accessible, in time window, attempt exists, attempt not submitted, not RESTARTED; else **403**. Same-origin + auth cookie.
- **Submit**: `answers[]` with `questionId` or `questionNumber`; for MC, `selectedOptionId` (and optionally `selectedOptionKey`). Backend grades PDF/JSON using blueprint’s `correctOptionId` when `attempt_blueprint` is present.

## Situation scoring

- Quiz max 100, exam max 150; exam units = 22 + 5 + 3×2 = 33.
- Option Set 1 multipliers (0, 1/3, 1/2, 2/3, 1) apply to **situation max only** (2 units per situation for exam).
- Grading uses `max_situation_points`; `per_situation_scores` sent as `{ index, fraction }` (1-based index).

## Student/parent results

- **No** exposure of `answer_key_json` or correct answers.
- Result detail: score and status only; **no** “View exam” or PDF/questions. “Yoxlanılır / Nəticə yayımda deyil” when not published.

## Reset

- Run-aware or reset latest attempt; **do not** delete history. Old attempt marked RESTARTED; new attempt allowed.
