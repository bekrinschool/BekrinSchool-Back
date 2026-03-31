"""
Adapter: normalized answer_key_json (PDF/JSON schema) -> real Question / QuestionOption / ExamQuestion rows.
After sync, exam.source_type becomes BANK so the standard blueprint, grading, and shuffle paths apply.
"""
from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from tests.answer_key import OPTION_KEYS, validate_and_normalize_answer_key_json
from tests.models import Exam, ExamAttempt, ExamQuestion, Question, QuestionOption, QuestionTopic

logger = logging.getLogger(__name__)


def _get_or_create_import_topic(exam: Exam) -> QuestionTopic:
    meta = exam.meta_json if isinstance(exam.meta_json, dict) else {}
    tid = meta.get("json_import_topic_id")
    if tid:
        t = QuestionTopic.objects.filter(pk=tid).first()
        if t:
            return t
    t = QuestionTopic.objects.create(
        name=f"[JSON import] Exam #{exam.id}",
        order=99999,
        is_active=True,
    )
    meta = dict(meta) if isinstance(exam.meta_json, dict) else {}
    meta["json_import_topic_id"] = t.id
    exam.meta_json = meta
    exam.save(update_fields=["meta_json"])
    return t


def _delete_imported_questions(exam: Exam, teacher) -> None:
    meta = exam.meta_json if isinstance(exam.meta_json, dict) else {}
    old_ids = list(meta.get("imported_question_ids") or [])
    ExamQuestion.objects.filter(exam=exam).delete()
    if old_ids and teacher_id_ok(teacher):
        Question.objects.filter(pk__in=old_ids, created_by=teacher).delete()
    meta = dict(meta) if isinstance(exam.meta_json, dict) else {}
    meta["imported_question_ids"] = []
    exam.meta_json = meta
    exam.save(update_fields=["meta_json"])


def teacher_id_ok(teacher) -> bool:
    return teacher is not None and getattr(teacher, "pk", None) is not None


def _create_mc_from_item(
    item: dict,
    topic: QuestionTopic,
    teacher,
    exam: Exam,
) -> Question:
    num = item.get("number")
    text = (item.get("text") or item.get("prompt") or f"Sual {num}").strip() or f"Sual {num}"
    short_title = f"E{exam.id}-Q{num}"[:255]
    opts = [o for o in (item.get("options") or []) if isinstance(o, dict)]
    q = Question(
        topic=topic,
        short_title=short_title,
        text=text,
        type="MULTIPLE_CHOICE",
        mc_option_display="TEXT",
        answer_rule_type="EXACT_MATCH",
        created_by=teacher,
        is_active=True,
    )
    q.save()
    correct_key = str(item.get("correct") or "").strip().upper()
    for i, od in enumerate(opts):
        key = str(od.get("key") or (OPTION_KEYS[i] if i < len(OPTION_KEYS) else str(i))).strip().upper()
        txt = (od.get("text") or "").strip()
        is_correct = bool(correct_key and key == correct_key)
        QuestionOption.objects.create(question=q, order=i, text=txt, label="", is_correct=is_correct)
    correct_opt = q.options.filter(is_correct=True).first()
    if opts and correct_opt is None:
        raise ValueError(f"Sual {num}: düzgün variant tapılmadı (correct={correct_key!r})")
    if correct_opt:
        q.correct_answer = correct_opt.id
        q.save(update_fields=["correct_answer"])
    return q


def _map_open_type_and_rule(rule: str) -> tuple[str, str]:
    rule = (rule or "EXACT_MATCH").strip().upper()
    if rule == "MATCHING":
        return "OPEN_UNORDERED", "MATCHING"
    if rule in ("ORDERED_MATCH", "STRICT_ORDER", "ORDERED_DIGITS"):
        return "OPEN_ORDERED", rule
    if rule in ("UNORDERED_MATCH", "UNORDERED_DIGITS", "ANY_ORDER"):
        if rule == "ANY_ORDER":
            return "OPEN_PERMUTATION", "ANY_ORDER"
        return "OPEN_UNORDERED", rule
    if rule == "NUMERIC_EQUAL":
        return "OPEN_SINGLE_VALUE", "NUMERIC_EQUAL"
    return "OPEN_SINGLE_VALUE", rule if rule in (
        "EXACT_MATCH", "ORDERED_MATCH", "UNORDERED_MATCH", "NUMERIC_EQUAL",
        "ORDERED_DIGITS", "UNORDERED_DIGITS", "STRICT_ORDER", "ANY_ORDER",
    ) else "EXACT_MATCH"


def _create_open_from_item(item: dict, topic: QuestionTopic, teacher, exam: Exam) -> Question:
    num = item.get("number")
    text = (item.get("text") or item.get("prompt") or f"Sual {num}").strip() or f"Sual {num}"
    short_title = f"E{exam.id}-Q{num}"[:255]
    rule_raw = (item.get("open_rule") or "EXACT_MATCH").strip().upper()
    qtype, answer_rule = _map_open_type_and_rule(rule_raw)
    open_ans = item.get("open_answer")
    if open_ans is None:
        open_ans = item.get("answer")
    ca: Any = open_ans
    if answer_rule == "MATCHING" and isinstance(open_ans, str):
        # keep string; evaluator may parse
        ca = open_ans
    q = Question(
        topic=topic,
        short_title=short_title,
        text=text,
        type=qtype,
        answer_rule_type=answer_rule,
        correct_answer=ca,
        created_by=teacher,
        is_active=True,
    )
    q.save()
    return q


def _create_situation_from_item(item: dict, topic: QuestionTopic, teacher, exam: Exam) -> Question:
    num = item.get("number")
    text = (item.get("prompt") or item.get("text") or f"Situasiya {num}").strip() or f"Situasiya {num}"
    short_title = f"E{exam.id}-Q{num}"[:255]
    q = Question(
        topic=topic,
        short_title=short_title,
        text=text,
        type="SITUATION",
        answer_rule_type="EXACT_MATCH",
        correct_answer={},
        created_by=teacher,
        is_active=True,
    )
    q.save()
    return q


def _sort_questions(items: list[dict]) -> list[dict]:
    kind_order = {"mc": 0, "open": 1, "situation": 2}

    def keyfn(q: dict):
        k = (q.get("kind") or "mc").strip().lower()
        num = q.get("number")
        try:
            n = int(num) if num is not None else 0
        except (TypeError, ValueError):
            n = 0
        return (kind_order.get(k, 99), n)

    return sorted([x for x in items if isinstance(x, dict)], key=keyfn)


def sync_json_import_to_bank_exam(exam: Exam, answer_key: dict[str, Any], teacher) -> None:
    """
    Replace exam JSON payload with BANK-linked Question rows. Sets exam.source_type = BANK.
    Keeps a snapshot in meta_json['json_import_snapshot'] for audit.
    """
    if not isinstance(answer_key, dict):
        return
    is_valid, err, normalized = validate_and_normalize_answer_key_json(answer_key)
    if not is_valid or not normalized:
        raise ValueError(err[0] if err else "Invalid answer key")
    questions_raw = normalized.get("questions") or []
    if not questions_raw:
        raise ValueError("No questions in answer key")

    with transaction.atomic():
        _delete_imported_questions(exam, teacher)
        topic = _get_or_create_import_topic(exam)
        exam.refresh_from_db()
        sorted_items = _sort_questions(questions_raw)
        new_ids: list[int] = []
        for idx, item in enumerate(sorted_items):
            kind = (item.get("kind") or "mc").strip().lower()
            if kind == "mc":
                q = _create_mc_from_item(item, topic, teacher, exam)
            elif kind == "open":
                q = _create_open_from_item(item, topic, teacher, exam)
            elif kind == "situation":
                q = _create_situation_from_item(item, topic, teacher, exam)
            else:
                continue
            new_ids.append(q.id)
            ExamQuestion.objects.create(exam=exam, question=q, order=idx)
        meta = dict(exam.meta_json) if isinstance(exam.meta_json, dict) else {}
        meta["imported_question_ids"] = new_ids
        meta["json_import_exam"] = True
        meta["json_import_snapshot"] = normalized
        exam.meta_json = meta
        exam.source_type = "BANK"
        exam.answer_key_json = None
        exam.save(update_fields=["meta_json", "source_type", "answer_key_json"])


def ensure_json_exam_migrated_to_bank(exam: Exam) -> None:
    """
    Lazy migration: legacy JSON-only exams (no ExamQuestion rows) become BANK when safe.
    Skips if there are already attempts (avoid breaking frozen blueprints).
    """
    if exam.source_type != "JSON":
        return
    if ExamQuestion.objects.filter(exam=exam).exists():
        return
    if not exam.answer_key_json or not isinstance(exam.answer_key_json, dict):
        return
    if ExamAttempt.objects.filter(exam=exam).exists():
        logger.warning(
            "json_import_adapter: exam %s is JSON without bank questions but has attempts; skip lazy migrate",
            exam.id,
        )
        return
    teacher = exam.created_by
    if not teacher_id_ok(teacher):
        return
    try:
        sync_json_import_to_bank_exam(exam, exam.answer_key_json, teacher)
        logger.info("json_import_adapter: lazy-migrated JSON exam %s to BANK", exam.id)
    except Exception:
        logger.exception("json_import_adapter: lazy migrate failed for exam %s", exam.id)
