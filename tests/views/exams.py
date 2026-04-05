"""
Question Bank & Exam API (teacher + student).
Visibility: students see exams when status=active and now in [start_time, start_time + duration_minutes].
Results visible only when is_result_published and manual check done.
"""
import base64
import copy
import hashlib
import io
import logging
import math
import os
import re
from decimal import Decimal
from django.core.files.base import ContentFile
from django.http import FileResponse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse, JsonResponse
from accounts.permissions import IsTeacher, IsStudent, IsStudentOrSignedToken
from datetime import timedelta
from tests.models import (
    QuestionTopic,
    Question,
    QuestionOption,
    Exam,
    ExamRun,
    ExamQuestion,
    ExamAttempt,
    ExamAnswer,
    ExamAttemptCanvas,
    ExamRunStudent,
    PdfScribble,
    TeacherPDF,
    ExamAssignment,
    ExamStudentAssignment,
    GradingAuditLog,
)
from groups.models import Group
from core.image_compression import compress_image_bytes
from tests.serializers import (
    QuestionTopicSerializer,
    QuestionSerializer,
    QuestionCreateSerializer,
    QuestionOptionSerializer,
    ExamSerializer,
    ExamDetailSerializer,
    ExamActivateSerializer,
    ExamQuestionSerializer,
    ExamRunSerializer,
    QuestionOptionPublicSerializer,
    QuestionPublicSerializer,
    TeacherPDFSerializer,
)
from tests.evaluate import evaluate_open_single_value
from tests.answer_key import validate_answer_key_json, validate_and_normalize_answer_key_json
from tests.json_import_adapter import ensure_json_exam_migrated_to_bank


def _now():
    return timezone.now()


def _exam_global_end(exam):
    """Scheduled wall-clock end for exams without a run (start_time + duration)."""
    from datetime import timedelta
    if exam.start_time and exam.duration_minutes is not None:
        return exam.start_time + timedelta(minutes=int(exam.duration_minutes))
    return None


def _saved_answers_payload_for_attempt(attempt):
    """Serialize draft/final ExamAnswer rows for student resume (run or exam start)."""
    out = []
    for ans in ExamAnswer.objects.filter(attempt=attempt):
        if not ans:
            continue
        rec = {}
        if ans.question_id:
            rec['questionId'] = ans.question_id
        if ans.question_number is not None:
            rec['questionNumber'] = ans.question_number
        so_id = getattr(ans, 'selected_option_id', None)
        if so_id is not None:
            rec['selectedOptionId'] = so_id
        if ans.selected_option_key:
            rec['selectedOptionKey'] = ans.selected_option_key
        if ans.text_answer and str(ans.text_answer).strip():
            rec['textAnswer'] = str(ans.text_answer).strip()
        if rec:
            out.append(rec)
    return out


def _coerce_student_answers_payload_to_internal_rows(answers_payload):
    """
    Accept compact PDF payload: [{ "no": 1, "qtype": "closed|open|situation", "answer": "..." }, ...]
    and convert to internal rows for draft/submit: questionNumber, selectedOptionKey/Id, textAnswer.
    If payload is already internal shape (e.g. textAnswer key), return unchanged.
    """
    if not isinstance(answers_payload, list) or len(answers_payload) == 0:
        return answers_payload if isinstance(answers_payload, list) else []
    sample = answers_payload[0]
    if not isinstance(sample, dict):
        return answers_payload
    if 'answer' not in sample:
        return answers_payload
    qtype_raw = sample.get('qtype') or sample.get('qType')
    if qtype_raw is None:
        return answers_payload
    no_present = any(k in sample for k in ('no', 'questionNumber', 'question_number'))
    if not no_present:
        return answers_payload
    out = []
    for a in answers_payload:
        if not isinstance(a, dict):
            continue
        num = a.get('no')
        if num is None:
            num = a.get('questionNumber') or a.get('question_number')
        qtype = (a.get('qtype') or a.get('qType') or '').strip().lower()
        ans = a.get('answer')
        if ans is None:
            ans = ''
        ans = str(ans).strip() if isinstance(ans, str) else str(ans)
        row = {}
        try:
            row['questionNumber'] = int(num) if num is not None and str(num).strip().lstrip('-').isdigit() else num
        except (TypeError, ValueError):
            row['questionNumber'] = num
        qid = a.get('questionId') or a.get('question_id')
        if qid is not None:
            row['questionId'] = qid
        if qtype in ('closed', 'mc', 'multiple_choice'):
            row['selectedOptionKey'] = ans.upper() if ans else None
            row['selectedOptionId'] = a.get('selectedOptionId') or a.get('selected_option_id')
            row['textAnswer'] = ''
        else:
            row['textAnswer'] = ans
            row['selectedOptionKey'] = None
            row['selectedOptionId'] = None
        out.append(row)
    return out


def _persist_student_attempt_draft_answers(attempt, answers_payload):
    """
    Replace in-progress attempt's ExamAnswer rows with draft payload (auto-save / suspend).
    Same shape as submit/suspend: list of { questionId?, questionNumber?, selectedOptionId?, selectedOptionKey?, textAnswer? }.
    """
    if not isinstance(answers_payload, list):
        return
    ExamAnswer.objects.filter(attempt=attempt).delete()
    for a in answers_payload:
        if not isinstance(a, dict):
            continue
        qid = a.get('questionId') or a.get('question_id')
        qnum = a.get('questionNumber') or a.get('question_number')
        text_answer = (a.get('textAnswer') or a.get('text_answer') or '').strip() or None
        selected_option_id = a.get('selectedOptionId') or a.get('selected_option_id')
        selected_option_key = (a.get('selectedOptionKey') or a.get('selected_option_key') or '').strip() or None
        q_obj = None
        if qid is not None:
            try:
                q_obj = Question.objects.get(pk=int(qid))
            except Exception:
                q_obj = None
        try:
            qnum_int = int(qnum) if qnum is not None else None
        except Exception:
            qnum_int = None
        ExamAnswer.objects.create(
            attempt=attempt,
            question=q_obj,
            question_number=qnum_int,
            selected_option_id=int(selected_option_id) if selected_option_id is not None and str(selected_option_id).strip().isdigit() else None,
            selected_option_key=selected_option_key,
            text_answer=text_answer,
            auto_score=Decimal('0'),
            requires_manual_check=False,
        )


def _resume_question_index_from_saved(questions_data, saved_rows):
    """Index (0-based) of last question in questions_data that has a saved answer."""
    key_answered = set()
    for s in saved_rows or []:
        if not isinstance(s, dict):
            continue
        has = bool((s.get('textAnswer') or '').strip()) or s.get('selectedOptionId') is not None or bool(
            (s.get('selectedOptionKey') or '').strip()
        )
        if not has:
            continue
        if s.get('questionId') is not None:
            try:
                key_answered.add(('id', int(s['questionId'])))
            except (TypeError, ValueError):
                key_answered.add(('id', s.get('questionId')))
        if s.get('questionNumber') is not None:
            try:
                key_answered.add(('num', int(s['questionNumber'])))
            except (TypeError, ValueError):
                pass
    last_idx = 0
    for i, q in enumerate(questions_data or []):
        if not isinstance(q, dict):
            continue
        ok = False
        qid = q.get('questionId')
        if qid is not None:
            try:
                qid_cmp = int(qid)
            except (TypeError, ValueError):
                qid_cmp = qid
            if ('id', qid_cmp) in key_answered:
                ok = True
        qnum = q.get('questionNumber')
        if qnum is not None:
            try:
                if ('num', int(qnum)) in key_answered:
                    ok = True
            except (TypeError, ValueError):
                pass
        if ok:
            last_idx = i
    return last_idx


def _auto_finish_exam_if_all_graded(exam):
    """
    Auto-transition exam to 'finished' when ALL submitted attempts are graded and published.
    Also moves exam to 'Köhnə testlər' by setting is_archived=False but status='finished'.
    """
    # Get all submitted (non-restarted) attempts
    attempts = ExamAttempt.objects.filter(
        exam=exam,
        status='SUBMITTED',
    ).exclude(is_archived=True)
    
    if not attempts.exists():
        return  # No submitted attempts yet
    
    # Check if ALL submitted attempts are checked and published
    all_graded = all(a.is_checked and a.is_result_published for a in attempts)
    
    if all_graded:
        exam.status = 'finished'
        exam.is_result_published = True
        exam.save(update_fields=['status', 'is_result_published'])
        
        # Also mark all runs as finished
        ExamRun.objects.filter(exam=exam).exclude(status__in=['finished', 'published']).update(status='finished')


def _expire_attempts_for_finished_run(run, now):
    """
    After a run is marked finished: create or update ExamAttempt so every group member
    (or the single student) has an attempt with status EXPIRED if they never submitted.
    Ensures they appear in group results with score 0 and submitted_at = expiry time.
    """
    from groups.services import get_active_students_for_group
    student_ids = []
    if run.group_id:
        memberships = get_active_students_for_group(run.group)
        student_ids = [m.student_profile.user_id for m in memberships]
    elif run.student_id:
        student_ids = [run.student_id]
    else:
        student_ids = list(run.run_students.values_list('student_id', flat=True))
    if not student_ids:
        return
    expiry_time = run.end_at or now
    zero = Decimal('0')
    for student_id in student_ids:
        try:
            attempt = ExamAttempt.objects.filter(exam_run=run, student_id=student_id).first()
            if attempt is None:
                ExamAttempt.objects.create(
                    exam_id=run.exam_id,
                    exam_run=run,
                    student_id=student_id,
                    status='EXPIRED',
                    finished_at=expiry_time,
                    expires_at=expiry_time,
                    duration_minutes=run.duration_minutes,
                    auto_score=zero,
                    manual_score=zero,
                    total_score=zero,
                    is_checked=True,           # auto-zero finalization for non-submitter
                    is_result_published=False, # stays unpublished until teacher publishes
                    is_visible_to_student=False,
                )
            elif attempt.status == 'IN_PROGRESS' and (attempt.expires_at is None or attempt.expires_at < now):
                attempt.status = 'EXPIRED'
                attempt.finished_at = attempt.expires_at or expiry_time
                if attempt.auto_score is None:
                    attempt.auto_score = zero
                if attempt.manual_score is None:
                    attempt.manual_score = zero
                if attempt.total_score is None:
                    attempt.total_score = zero
                attempt.is_checked = True
                attempt.is_result_published = False
                attempt.is_visible_to_student = False
                attempt.save(update_fields=[
                    'status',
                    'finished_at',
                    'auto_score',
                    'manual_score',
                    'total_score',
                    'is_checked',
                    'is_result_published',
                    'is_visible_to_student',
                ])
        except Exception as e:
            logger.exception(
                "expire_attempts_for_finished_run run_id=%s student_id=%s: %s",
                run.id, student_id, e
            )


def _auto_transition_run_status():
    """
    Background check:
    - scheduled → active when now >= start_at
    - active → finished when end_at has passed
    Also check if exam should move to waiting_for_grading.
    Called periodically or on view access.
    """
    now = _now()
    scheduled_runs = ExamRun.objects.filter(status='scheduled', start_at__lte=now)
    for run in scheduled_runs:
        run.status = 'active'
        run.save(update_fields=['status'])
    expired_runs = ExamRun.objects.filter(status='active', end_at__lt=now)
    for run in expired_runs:
        run.status = 'published'
        run.save(update_fields=['status'])
        try:
            _expire_attempts_for_finished_run(run, now)
        except Exception as e:
            logger.exception("_auto_transition_run_status: expire attempts for run_id=%s: %s", run.id, e)

    # For each exam with all runs finished, check if exam should transition
    exams_with_finished_runs = Exam.objects.filter(
        status='active',
        runs__status='finished'
    ).exclude(runs__status='active').distinct()
    
    for exam in exams_with_finished_runs:
        # If no active runs remain, exam goes to waiting_for_grading (handled by view)
        if not exam.runs.filter(status='active').exists():
            has_ungraded = ExamAttempt.objects.filter(
                exam=exam,
                status='SUBMITTED',
                is_checked=False,
            ).exists()
            if has_ungraded:
                # Still has ungraded attempts - keep as active but conceptually waiting
                pass
            else:
                # All graded - auto-finish
                _auto_finish_exam_if_all_graded(exam)


def _build_canvas_response(canvas, request=None, include_canvas_json=False):
    """Build canvas dict with image_url and optional pageIndex. For teacher: include canvas_json and canvasSnapshot."""
    if not canvas:
        return None
    data = {
        'canvasId': canvas.id,
        'questionId': canvas.question_id,
        'updatedAt': canvas.updated_at.isoformat(),
    }
    if getattr(canvas, 'page_index', 0) is not None:
        data['pageIndex'] = getattr(canvas, 'page_index', 0)
    if canvas.image:
        url = request.build_absolute_uri(canvas.image.url) if request else canvas.image.url
        data['imageUrl'] = url
        if include_canvas_json:
            data['canvasSnapshot'] = url
    else:
        data['imageUrl'] = None
        if include_canvas_json:
            data['canvasSnapshot'] = None
    if include_canvas_json and getattr(canvas, 'canvas_json', None) is not None:
        data['canvasJson'] = canvas.canvas_json
    return data


def _build_published_result_questions_and_canvases(attempt, request=None, content_lockdown=False):
    """
    Build questions (yourAnswer vs correctAnswer, points) and canvases for published attempt.
    Used by student and parent result views. Azerbaijani-friendly keys.
    When content_lockdown=True (exam finished + results published), omit questionText and options
    to protect IP; only questionNumber, yourAnswer, correctAnswer, points, questionType.
    """
    blueprint_by_num = {}
    if attempt.attempt_blueprint:
        bp_list = copy.deepcopy(list(attempt.attempt_blueprint))
        if request and getattr(attempt.exam, 'source_type', None) == 'BANK':
            _enrich_bank_blueprint_mc_options(request, attempt.exam, bp_list)
        for item in bp_list:
            num = item.get('questionNumber')
            if num is not None:
                blueprint_by_num[int(num) if isinstance(num, (int, float)) else num] = item
    ak_questions_by_num = {}
    if attempt.exam.answer_key_json and isinstance(attempt.exam.answer_key_json, dict):
        for q in (attempt.exam.answer_key_json.get('questions') or []):
            num = q.get('number')
            if num is not None:
                ak_questions_by_num[int(num) if isinstance(num, (int, float)) else num] = q

    def correct_display(answer):
        if answer.question_id and answer.question:
            q = answer.question
            if q.type == 'MULTIPLE_CHOICE' and q.correct_answer is not None:
                correct_id = q.correct_answer.get('option_id') if isinstance(q.correct_answer, dict) else q.correct_answer
                opts = list(q.options.all()) if hasattr(q, 'options') else []
                opt = next((o for o in opts if getattr(o, 'id', None) == correct_id), None)
                return getattr(opt, 'key', None) if opt else str(correct_id or q.correct_answer)
            if q.type in ('OPEN_SINGLE_VALUE', 'OPEN_ORDERED', 'OPEN_UNORDERED', 'OPEN_PERMUTATION') and q.correct_answer is not None:
                return str(q.correct_answer) if not isinstance(q.correct_answer, dict) else (q.correct_answer.get('text') or q.correct_answer.get('value') or '')
        num = answer.question_number
        bp = blueprint_by_num.get(num) if num is not None else None
        ak_q = ak_questions_by_num.get(num) if num is not None else None
        if bp:
            if (bp.get('kind') or 'mc').lower() == 'mc':
                for o in (bp.get('options') or []):
                    if str(o.get('id')) == str(bp.get('correctOptionId')) or (o.get('key') or '').upper() == str(bp.get('correct', '')).upper():
                        return o.get('key') or o.get('id')
                return str(bp.get('correctOptionId') or bp.get('correct') or '')
            if (bp.get('kind') or '').lower() == 'open' and ak_q:
                return ak_q.get('open_answer') or ak_q.get('correct') or ''
        return None

    answers_list = _answers_in_blueprint_order(attempt)
    if answers_list is None:
        answers_list = _answers_queryset_fallback_order(attempt)

    questions = []
    for presentation_idx, answer in enumerate(answers_list):
        your = answer.selected_option_key or (str(answer.selected_option_id) if answer.selected_option_id else None) or answer.text_answer or ''
        correct = correct_display(answer)
        pts = float(answer.manual_score if answer.manual_score is not None else (answer.auto_score or 0))
        qtext = answer.question.text if answer.question_id and answer.question else f'Sual {answer.question_number}'
        qtype = answer.question.type if answer.question_id and answer.question else ('situation' if answer.requires_manual_check else 'open')
        row = {
            'questionNumber': answer.question_number,
            'presentationOrder': presentation_idx + 1,
            'questionType': qtype,
            'yourAnswer': your,
            'correctAnswer': correct,
            'points': pts,
        }
        if not content_lockdown:
            row['questionText'] = qtext
            if answer.question_id and answer.question and getattr(answer.question, 'question_image', None) and answer.question.question_image:
                row['questionImageUrl'] = request.build_absolute_uri(answer.question.question_image.url)
        num = answer.question_number
        bp = blueprint_by_num.get(num) if num is not None else None
        ak_q = ak_questions_by_num.get(num) if num is not None else None
        if not content_lockdown and (qtype == 'MULTIPLE_CHOICE' or (isinstance(qtype, str) and qtype.lower() == 'mc')) and (bp or ak_q or (answer.question_id and answer.question)):
            correct_id = str(bp.get('correctOptionId') or '') if bp else None
            your_id = str(answer.selected_option_id) if answer.selected_option_id else None
            your_key = (answer.selected_option_key or '').strip().upper()
            options_for_compare = []
            if answer.question_id and answer.question and hasattr(answer.question, 'options'):
                q = answer.question
                correct_opt_id = None
                if q.correct_answer is not None:
                    correct_opt_id = q.correct_answer.get('option_id') if isinstance(q.correct_answer, dict) else q.correct_answer
                    if correct_opt_id is not None:
                        correct_opt_id = int(correct_opt_id)
                opts = list(q.options.all().order_by('order'))
                for i, opt in enumerate(opts):
                    key = chr(65 + i) if i < 26 else str(i + 1)
                    od = {
                        'key': key,
                        'text': getattr(opt, 'text', ''),
                        'id': opt.id,
                        'isCorrect': (correct_opt_id is not None and opt.id == correct_opt_id),
                        'isYours': (your_id and str(opt.id) == str(your_id)) or (your_key and your_key == key),
                    }
                    if request and getattr(opt, 'image', None) and opt.image:
                        od['imageUrl'] = request.build_absolute_uri(opt.image.url)
                    if getattr(opt, 'label', None):
                        od['label'] = opt.label
                    options_for_compare.append(od)
                if correct_id is None and correct_opt_id is not None:
                    correct_id = str(correct_opt_id)
            elif bp and (bp.get('options') or []):
                opts_bp = list(bp.get('options') or [])
                opts_bp_by_id = {str(o.get('id')): o for o in opts_bp}
                opts_bp_by_text = {(str(o.get('text') or '').strip()): str(o.get('id')) for o in opts_bp}
                if ak_q and (ak_q.get('options') or []):
                    for o in sorted(ak_q.get('options') or [], key=lambda x: (str(x.get('key') or '')).upper()):
                        key = (str(o.get('key') or '')).strip().upper()
                        text = o.get('text', '')
                        oid = opts_bp_by_text.get((text or '').strip()) or (opts_bp_by_id and list(opts_bp_by_id.keys())[0])
                        options_for_compare.append({
                            'key': key,
                            'text': text,
                            'id': oid,
                            'isCorrect': oid == correct_id if correct_id else False,
                            'isYours': your_id == oid if your_id else (your_key == key if your_key else False),
                            'imageUrl': None,
                            'label': None,
                        })
                else:
                    for o in opts_bp:
                        oid = str(o.get('id') or '')
                        text = o.get('text', '')
                        key = (str(o.get('key') or oid)).strip().upper()
                        options_for_compare.append({
                            'key': key,
                            'text': text,
                            'id': oid,
                            'isCorrect': oid == correct_id if correct_id else False,
                            'isYours': your_id == oid if your_id else (your_key == key if your_key else False),
                            'imageUrl': o.get('imageUrl'),
                            'label': o.get('label'),
                        })
            if options_for_compare:
                row['options'] = options_for_compare
        questions.append(row)

    canvases_list = list(ExamAttemptCanvas.objects.filter(attempt=attempt).order_by('situation_index', 'page_index', 'question_id'))
    canvases = []
    for c in canvases_list:
        rec = _build_canvas_response(c, request) or {}
        if c.situation_index is not None:
            rec['situationIndex'] = c.situation_index
        canvases.append(rec)

    return {'questions': questions, 'canvases': canvases}


def _blueprint_by_question_number(attempt):
    blueprint_by_num = {}
    bp_list = attempt.attempt_blueprint or []
    for item in bp_list:
        if not isinstance(item, dict):
            continue
        num = item.get('questionNumber')
        if num is not None:
            try:
                blueprint_by_num[int(num)] = item
            except (TypeError, ValueError):
                blueprint_by_num[num] = item
    return blueprint_by_num


def _shuffled_question_order_from_blueprint(blueprint):
    """Presentation-order snapshot for persistence/API: [{questionId}, ...] or [{questionNumber}, ...]."""
    out = []
    for item in blueprint or []:
        if not isinstance(item, dict):
            continue
        qid = item.get('questionId')
        num = item.get('questionNumber')
        if qid is not None:
            try:
                out.append({'questionId': int(qid)})
            except (TypeError, ValueError):
                out.append({'questionId': qid})
        elif num is not None:
            out.append({'questionNumber': num})
    return out


def _answers_in_blueprint_order(attempt):
    """
    Order ExamAnswer rows like attempt.attempt_blueprint (student presentation order).
    Returns None if blueprint is missing/empty — use _answers_queryset_fallback_order(attempt).
    """
    bp = getattr(attempt, 'attempt_blueprint', None) or []
    if not isinstance(bp, list) or len(bp) == 0:
        return None
    answers = list(attempt.answers.all())
    by_qid = {}
    by_qnum = {}
    for a in answers:
        qid = getattr(a, 'question_id', None)
        if qid:
            by_qid[int(qid)] = a
        qn = a.question_number
        if qn is not None:
            try:
                by_qnum[int(qn)] = a
            except (TypeError, ValueError):
                by_qnum[qn] = a
    ordered = []
    seen = set()
    for item in bp:
        if not isinstance(item, dict):
            continue
        qid = item.get('questionId')
        num = item.get('questionNumber')
        ans = None
        if qid is not None:
            try:
                ans = by_qid.get(int(qid))
            except (TypeError, ValueError):
                ans = None
        if ans is None and num is not None:
            try:
                ans = by_qnum.get(int(num))
            except (TypeError, ValueError):
                ans = by_qnum.get(num)
        if ans is not None and ans.id not in seen:
            ordered.append(ans)
            seen.add(ans.id)
    for a in answers:
        if a.id not in seen:
            ordered.append(a)
            seen.add(a.id)
    return ordered


def _answers_queryset_fallback_order(attempt):
    qs = attempt.answers.all()
    if attempt.exam.source_type == 'BANK':
        return list(qs.order_by('question__exam_questions__order'))
    return list(qs.order_by('question_number'))


def _student_answer_has_raw_input(answer):
    text = (answer.text_answer or '').strip()
    if text:
        return True
    if answer.selected_option_id or answer.selected_option_key:
        return True
    return False


def _student_answer_provided_or_scored(answer):
    auto = float(answer.auto_score or 0)
    manual = float(answer.manual_score) if answer.manual_score is not None else 0.0
    if auto > 0 or manual > 0:
        return True
    return _student_answer_has_raw_input(answer)


def _answer_is_situation_for_summary(answer, blueprint_by_num):
    if answer.question_id and answer.question and getattr(answer.question, 'type', None) == 'SITUATION':
        return True
    num = answer.question_number
    bp = blueprint_by_num.get(num) if num is not None else None
    if isinstance(bp, dict):
        kind = str(bp.get('kind') or bp.get('type') or '').lower()
        if kind in ('situation', 'sit'):
            return True
    return False


def _build_student_score_summary_rows(attempt):
    """
    Student-only score rows: no question stems, LaTeX, images, MC option text, or correct answers.
    Situation questions emit two sub-rows (2× unit clarity).
    """
    exam = attempt.exam
    max_s = float(exam.max_score or (100 if exam.type == 'quiz' else 150))
    blueprint = attempt.attempt_blueprint or []
    _, _, total_units = _get_units_from_blueprint(blueprint)
    x_val = _get_x_value(Decimal(str(max_s)), total_units) if total_units and total_units > 0 else Decimal('0')
    x_float = float(x_val)
    blueprint_by_num = _blueprint_by_question_number(attempt)
    situation_ordered = _ordered_situation_answers_for_grading(attempt)
    sit_index_by_answer_id = {situation_ordered[i].id: i + 1 for i in range(len(situation_ordered))}

    answers_list = _answers_in_blueprint_order(attempt)
    if answers_list is None:
        answers_list = _answers_queryset_fallback_order(attempt)

    rows = []
    for presentation_idx, answer in enumerate(answers_list):
        qn = answer.question_number
        qtype = answer.question.type if answer.question_id and answer.question else None
        is_sit = _answer_is_situation_for_summary(answer, blueprint_by_num)
        max_pts = (2.0 * x_float) if is_sit else x_float

        manual = answer.manual_score
        auto = answer.auto_score
        pending = bool(answer.requires_manual_check and manual is None)
        awarded = None if pending else float(manual if manual is not None else (auto or Decimal('0')))

        raw_your = (
            answer.selected_option_key
            or (str(answer.selected_option_id) if answer.selected_option_id else None)
            or (answer.text_answer or '')
            or ''
        )
        your_display = str(raw_your).strip()

        provided = _student_answer_provided_or_scored(answer)
        is_blank = not pending and not provided and (awarded is None or abs(awarded) < 0.0001)

        if is_blank and not pending:
            your_display = 'Cavab verilməyib'

        if pending:
            status_cat = 'pending'
        elif is_blank:
            status_cat = 'blank'
        elif awarded is not None and max_pts > 0:
            if awarded >= max_pts - 0.001:
                status_cat = 'correct'
            elif awarded <= 0.001:
                status_cat = 'wrong'
            else:
                status_cat = 'partial'
        else:
            status_cat = 'wrong'

        def _label_score(a, m, pend, blank):
            if pend:
                return 'Yoxlanılır...'
            if blank:
                return 'Cavab verilməyib'
            if a is None:
                return '—'
            return f"{a:.2f} / {m:.2f}"

        row = {
            'questionNumber': qn,
            'presentationOrder': presentation_idx + 1,
            'questionType': qtype or ('situation' if is_sit else 'open'),
            'yourAnswer': your_display,
            'pendingReview': pending,
            'isBlank': is_blank,
            'status': status_cat,
            'awarded': awarded,
            'max': max_pts,
            'scoreLabel': _label_score(awarded, max_pts, pending, is_blank),
        }

        if is_sit:
            sidx = sit_index_by_answer_id.get(answer.id)
            if not sidx:
                for i, sa in enumerate(situation_ordered):
                    if sa.id == answer.id:
                        sidx = i + 1
                        break
            sidx = sidx or 1
            half_max = max_pts / 2.0
            half_awarded = None if pending else ((awarded / 2.0) if awarded is not None else None)
            sub_blank = is_blank

            def _sub_label(a, m, pend, blank):
                if pend:
                    return 'Yoxlanılır...'
                if blank:
                    return 'Cavab verilməyib'
                if a is None:
                    return '—'
                return f"{a:.2f} / {m:.2f}"

            row['situationSubScores'] = [
                {
                    'label': f'Situasiya {sidx} - Sual 1',
                    'awarded': half_awarded,
                    'max': half_max,
                    'scoreLabel': _sub_label(half_awarded, half_max, pending, sub_blank),
                },
                {
                    'label': f'Situasiya {sidx} - Sual 2',
                    'awarded': half_awarded,
                    'max': half_max,
                    'scoreLabel': _sub_label(half_awarded, half_max, pending, sub_blank),
                },
            ]

        rows.append(row)

    return rows


# ---------- Teacher: Question topics ----------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_question_topics_view(request):
    if request.method == 'GET':
        topics = QuestionTopic.objects.filter(is_active=True, is_archived=False).order_by('order', 'name')
        return Response(QuestionTopicSerializer(topics, many=True).data)
    if request.method == 'POST':
        s = QuestionTopicSerializer(data=request.data)
        if s.is_valid():
            s.save()
            return Response(s.data, status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_question_topic_delete_view(request, pk):
    """Permanent delete. Cascade deletes all questions (and options) in this topic."""
    try:
        topic = QuestionTopic.objects.get(pk=pk)
    except QuestionTopic.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    topic.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


def _mutable_question_request_data(request):
    """
    Avoid request.data.copy() on multipart: QueryDict.copy() deep-copies file objects and raises
    TypeError: cannot pickle 'BufferedRandom' instances (Django/DRF on Windows).
    """
    rd = request.data
    ct = (request.content_type or '') or ''
    if 'multipart' in ct.lower():
        return {k: rd.get(k) for k in rd.keys()}
    if hasattr(rd, 'copy'):
        return rd.copy()
    return dict(rd)


# ---------- Teacher: Questions ----------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_questions_view(request):
    from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
    # Allow multipart for question_image upload
    if request.method == 'POST' and request.content_type and 'multipart' in request.content_type:
        request.parsers = [MultiPartParser(), FormParser(), JSONParser()]
    if request.method == 'GET':
        from django.db.models import Q
        topic_id = request.query_params.get('topic', '').strip()
        type_filter = request.query_params.get('type', '').strip()
        search_q = request.query_params.get('q', '').strip()
        qs = Question.objects.filter(is_active=True, is_archived=False).select_related('topic').prefetch_related('options')
        qs = qs.filter(topic__is_archived=False)
        if topic_id:
            try:
                qs = qs.filter(topic_id=int(topic_id))
            except ValueError:
                pass
        if type_filter:
            qs = qs.filter(type=type_filter)
        if search_q:
            qs = qs.filter(Q(short_title__icontains=search_q) | Q(text__icontains=search_q))
        qs = qs.order_by('topic', 'id')
        return Response(QuestionSerializer(qs, many=True, context={'request': request}).data)
    if request.method == 'POST':
        data = _mutable_question_request_data(request)
        if data.get('topic_id') is not None and data.get('topic') is None:
            data['topic'] = data.get('topic_id')
        if request.FILES.get('question_image'):
            data['question_image'] = request.FILES.get('question_image')
        # FormData sends options as JSON string
        if isinstance(data.get('options'), str):
            try:
                import json
                data['options'] = json.loads(data['options'])
            except (ValueError, TypeError):
                pass
        if isinstance(data.get('correct_answer'), str):
            raw = (data.get('correct_answer') or '').strip()
            if raw:
                try:
                    import json
                    data['correct_answer'] = json.loads(raw)
                except (ValueError, TypeError):
                    pass
        if data.get('question_image') == '':
            data.pop('question_image', None)
        s = QuestionCreateSerializer(data=data, context={'request': request})
        if s.is_valid():
            q = s.save(created_by=request.user)
            return Response(QuestionSerializer(q, context={'request': request}).data, status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_question_detail_view(request, pk):
    from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
    if request.method == 'PATCH' and request.content_type and 'multipart' in request.content_type:
        request.parsers = [MultiPartParser(), FormParser(), JSONParser()]
    try:
        q = Question.objects.prefetch_related('options').get(pk=pk)
    except Question.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        return Response(QuestionSerializer(q, context={'request': request}).data)
    if request.method == 'PATCH':
        data = _mutable_question_request_data(request)
        if data.get('topic_id') is not None and data.get('topic') is None:
            data['topic'] = data.get('topic_id')
        if request.FILES.get('question_image') is not None:
            data['question_image'] = request.FILES.get('question_image')
        if isinstance(data.get('options'), str):
            try:
                import json
                data['options'] = json.loads(data['options'])
            except (ValueError, TypeError):
                pass
        if isinstance(data.get('correct_answer'), str):
            raw = (data.get('correct_answer') or '').strip()
            if raw:
                try:
                    import json
                    data['correct_answer'] = json.loads(raw)
                except (ValueError, TypeError):
                    pass
        if data.get('question_image') == '':
            data.pop('question_image', None)
        s = QuestionCreateSerializer(q, data=data, partial=True, context={'request': request})
        if s.is_valid():
            s.save()
            q.refresh_from_db()
            return Response(QuestionSerializer(q, context={'request': request}).data)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
    if request.method == 'DELETE':
        q.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_questions_bulk_delete_view(request):
    """Permanent bulk delete. Body: { ids: [1, 2, 3] }. Cascade deletes options."""
    ids = request.data.get('ids', [])
    if not isinstance(ids, list) or not ids:
        return Response({'detail': 'ids massivi tələb olunur'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        id_list = [int(i) for i in ids]
    except (TypeError, ValueError):
        return Response({'detail': 'ids rəqəmlərdən ibarət olmalıdır'}, status=status.HTTP_400_BAD_REQUEST)
    qs = Question.objects.filter(pk__in=id_list)
    deleted_count = qs.count()
    qs.delete()
    return Response({'deleted': deleted_count, 'message': f'{deleted_count} sual silindi'})


# ---------- Teacher: Exams ----------
def _validate_exam_composition(exam):
    """
    Validate question composition rules.
    PDF/JSON: delegated to answer_key validation (dynamic counts).
    BANK: at least one question; no fixed 30/22/5/3.
    Returns (is_valid, error_message).
    """
    # PDF/JSON: validate from answer_key_json
    if exam.source_type in ('PDF', 'JSON') and exam.answer_key_json and isinstance(exam.answer_key_json, dict):
        is_valid, err = _validate_exam_composition_from_answer_key(exam.answer_key_json)
        return is_valid, err
    # BANK: count from exam_questions
    eqs = list(exam.exam_questions.select_related('question').all()) if hasattr(exam, 'exam_questions') else []
    closed = open_c = situation = 0
    for eq in eqs:
        q = eq.question
        if not q:
            continue
        t = (getattr(q, 'type') or '').upper()
        if t == 'MULTIPLE_CHOICE':
            closed += 1
        elif t in ('OPEN_SINGLE_VALUE', 'OPEN_ORDERED', 'OPEN_UNORDERED', 'OPEN_PERMUTATION', 'OPEN'):
            open_c += 1
        elif t == 'SITUATION':
            situation += 1
    total = closed + open_c + situation
    if exam.type == 'exam':
        if total < 1:
            return False, 'İmtahan üçün ən azı 1 sual tələb olunur.'
    elif exam.type == 'quiz':
        if total < 1:
            return False, 'Quiz üçün ən azı 1 sual tələb olunur.'
    return True, None


def _validate_exam_composition_from_answer_key(answer_key):
    """Validate composition from answer_key_json. Returns (is_valid, error_message)."""
    is_valid, errors = validate_answer_key_json(answer_key)
    if not is_valid and errors:
        return False, errors[0] if len(errors) == 1 else '; '.join(errors[:5])
    return True, None


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exams_view(request):
    if request.method == 'GET':
        # Auto-transition expired runs to 'finished' and check exam status
        try:
            _auto_transition_run_status()
        except Exception:
            pass  # Don't fail exam listing if auto-transition has issues
        exams = Exam.objects.filter(
            created_by=request.user, is_archived=False, is_deleted=False,
        ).exclude(status='deleted').select_related(
            'created_by', 'pdf_document'
        ).prefetch_related(
            'exam_questions__question', 'assignments__group', 'student_assignments'
        ).order_by('-start_time')
        unpublished_exam_ids = set(
            ExamAttempt.objects.filter(
                exam__created_by=request.user,
                finished_at__isnull=False,
                is_result_published=False,
                is_archived=False,
            ).exclude(status='RESTARTED').values_list('exam_id', flat=True).distinct()
        )
        return Response(ExamSerializer(
            exams, many=True,
            context={'request': request, 'unpublished_exam_ids': unpublished_exam_ids},
        ).data)
    if request.method == 'POST':
        data = request.data.copy()
        source_type = (data.get('source_type') or 'BANK').upper()
        if source_type not in ('BANK', 'PDF'):
            return Response({'detail': 'source_type must be BANK or PDF'}, status=status.HTTP_400_BAD_REQUEST)

        # PDF: require answer_key (answer_key_json or json_import); normalize no/qtype/options/correct index
        answer_key = data.get('answer_key_json') or data.get('json_import')
        if source_type == 'PDF':
            if not answer_key:
                return Response({'detail': 'answer_key_json or json_import required for PDF source'}, status=status.HTTP_400_BAD_REQUEST)
            is_valid, err, normalized = validate_and_normalize_answer_key_json(answer_key)
            if not is_valid:
                return Response({'detail': err[0] if err else 'Invalid answer key', 'errors': err or []}, status=status.HTTP_400_BAD_REQUEST)
            answer_key = normalized or answer_key
            exam_type = answer_key.get('type') or 'quiz'
            data['type'] = exam_type
            data['answer_key_json'] = answer_key
        if source_type == 'PDF':
            pdf_id = data.get('pdf_id') or data.get('pdfId')
            if pdf_id:
                try:
                    from django.conf import settings
                    qs = TeacherPDF.objects.filter(pk=int(pdf_id), is_archived=False, is_deleted=False)
                    if not getattr(settings, 'SINGLE_TENANT', True):
                        qs = qs.filter(teacher=request.user)
                    pdf = qs.get()
                    # Verify PDF file actually exists on disk
                    if not pdf.file or not pdf.file.storage.exists(pdf.file.name):
                        return Response({'detail': 'PDF file not found on disk'}, status=status.HTTP_400_BAD_REQUEST)
                    data['pdf_document'] = pdf.id
                except (TeacherPDF.DoesNotExist, ValueError, TypeError):
                    return Response({'detail': 'PDF not found or not owned by teacher'}, status=status.HTTP_400_BAD_REQUEST)
        elif source_type == 'BANK':
            data.pop('answer_key_json', None)
            data.pop('json_import', None)
            data.pop('pdf_document_id', None)
            data.pop('pdf_document', None)

        s = ExamSerializer(data=data)
        if s.is_valid():
            exam = s.save(created_by=request.user)
            update_f = ['max_score']
            exam.source_type = source_type
            update_f.append('source_type')
            if not exam.max_score:
                exam.max_score = 100 if exam.type == 'quiz' else 150
            if source_type == 'PDF' and answer_key:
                exam.answer_key_json = answer_key
                update_f.append('answer_key_json')
            exam.save(update_fields=update_f)
            if source_type == 'BANK':
                question_ids = data.get('question_ids') or data.get('questionIds') or []
                if isinstance(question_ids, list) and question_ids:
                    for idx, qid in enumerate(question_ids):
                        try:
                            q = Question.objects.get(pk=int(qid), is_active=True, is_archived=False)
                            ExamQuestion.objects.get_or_create(exam=exam, question=q, defaults={'order': idx})
                        except (Question.DoesNotExist, ValueError, TypeError):
                            pass
            return Response(ExamSerializer(exam).data, status=status.HTTP_201_CREATED)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_activate_view(request, pk):
    """Activate exam: set start_time and duration_minutes. Requires both; sets status to active."""
    try:
        exam = Exam.objects.get(pk=pk, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    data = request.data.copy()
    start_time_str = data.get('start_time') or data.get('startTime')
    duration_minutes = data.get('duration_minutes') or data.get('durationMinutes')
    if start_time_str:
        from django.utils.dateparse import parse_datetime
        parsed = parse_datetime(start_time_str)
        if parsed is not None:
            data['start_time'] = parsed
    s = ExamActivateSerializer(data=data)
    if not s.is_valid():
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
    validated = s.validated_data
    exam.start_time = validated['start_time']
    exam.duration_minutes = validated['duration_minutes']
    exam.status = 'active'
    exam.save(update_fields=['start_time', 'duration_minutes', 'status'])
    return Response(ExamSerializer(exam).data)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_detail_view(request, pk):
    try:
        exam = Exam.objects.prefetch_related(
            'exam_questions__question__options',
            'exam_questions__question',
            'assignments__group',
            'runs__group',
            'runs__student',
        ).select_related('pdf_document').get(pk=pk, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        return Response(ExamDetailSerializer(exam, context={'request': request}).data)
    if request.method == 'PATCH':
        # Status cannot be changed directly - it's controlled by runs
        data = request.data.copy()
        data.pop('status', None)  # Remove status from update data
        s = ExamSerializer(exam, data=data, partial=True)
        if s.is_valid():
            ex = s.save()
            if s.validated_data.get('is_archived') is True and ex.archived_at is None:
                ex.archived_at = _now()
                ex.save(update_fields=['archived_at'])
            return Response(ExamSerializer(ex).data)
        return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
    if request.method == 'DELETE':
        now = _now()
        exam.status = 'deleted'
        exam.is_deleted = True
        exam.deleted_at = now
        exam.is_archived = True
        if exam.archived_at is None:
            exam.archived_at = now
        exam.save(update_fields=['status', 'is_deleted', 'deleted_at', 'is_archived', 'archived_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_add_question_view(request, exam_id):
    try:
        exam = Exam.objects.get(pk=exam_id)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    question_id = request.data.get('question_id') or request.data.get('questionId')
    if not question_id:
        return Response({'detail': 'question_id required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        question = Question.objects.get(pk=question_id, is_active=True, is_archived=False)
    except Question.DoesNotExist:
        return Response({'detail': 'Question not found'}, status=status.HTTP_404_NOT_FOUND)
    order = ExamQuestion.objects.filter(exam=exam).count()
    eq, created = ExamQuestion.objects.get_or_create(exam=exam, question=question, defaults={'order': order})
    if not created:
        return Response(ExamQuestionSerializer(eq).data)
    return Response(ExamQuestionSerializer(eq).data, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_remove_question_view(request, exam_id, question_id):
    try:
        exam = Exam.objects.get(pk=exam_id)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    deleted, _ = ExamQuestion.objects.filter(exam=exam, question_id=question_id).delete()
    return Response(status=status.HTTP_204_NO_CONTENT if deleted else status.HTTP_404_NOT_FOUND)


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_assign_groups_view(request, exam_id):
    """Assign exam to groups. POST: assign groups, DELETE: remove assignment."""
    try:
        exam = Exam.objects.get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    
    if request.method == 'POST':
        group_ids = request.data.get('groupIds') or request.data.get('group_ids') or []
        if not isinstance(group_ids, list):
            return Response({'detail': 'groupIds must be a list'}, status=status.HTTP_400_BAD_REQUEST)
        
        groups = Group.objects.filter(id__in=group_ids, created_by=request.user)
        if groups.count() != len(group_ids):
            return Response({'detail': 'Some groups not found or not owned by teacher'}, status=status.HTTP_400_BAD_REQUEST)
        
        created = []
        for group in groups:
            assignment, _ = ExamAssignment.objects.get_or_create(exam=exam, group=group)
            created.append({'examId': exam.id, 'groupId': group.id, 'groupName': group.name})
        
        return Response({'assignments': created}, status=status.HTTP_201_CREATED)
    
    if request.method == 'DELETE':
        group_id = request.data.get('groupId') or request.data.get('group_id')
        if not group_id:
            return Response({'detail': 'groupId required'}, status=status.HTTP_400_BAD_REQUEST)
        deleted, _ = ExamAssignment.objects.filter(exam=exam, group_id=group_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT if deleted else status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_start_now_view(request, exam_id):
    """
    Activate exam for targets: group_ids and/or student_id and/or student_ids.
    Per-assignment timing: each target gets start_time=now, end_time=now+duration.
    Does NOT remove existing assignments; adds/updates only the specified targets.
    """
    from datetime import timedelta
    from django.conf import settings
    try:
        exam = Exam.objects.get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'error': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    
    is_valid, error_msg = _validate_exam_composition(exam)
    if not is_valid:
        return Response({'error': error_msg}, status=status.HTTP_400_BAD_REQUEST)
    
    group_ids = request.data.get('groupIds') or request.data.get('group_ids') or []
    if not isinstance(group_ids, list):
        group_ids = []
    # Normalize: exclude None from group_ids (e.g. from JSON [null])
    group_ids = [g for g in group_ids if g is not None]
    student_id = request.data.get('studentId') or request.data.get('student_id')
    student_ids = request.data.get('studentIds') or request.data.get('student_ids') or []
    if not isinstance(student_ids, list):
        student_ids = []
    student_ids = [s for s in student_ids if s is not None]
    duration_minutes = (
        request.data.get('durationMinutes') or request.data.get('duration_minutes')
        or exam.duration_minutes or 60
    )
    try:
        duration_minutes = max(1, int(duration_minutes))
    except (TypeError, ValueError):
        duration_minutes = 60
    start_time_str = request.data.get('startTime') or request.data.get('start_time')

    if not group_ids and not student_id and not student_ids:
        return Response({'error': 'At least one target required: groupIds or studentId or studentIds'}, status=status.HTTP_400_BAD_REQUEST)
    
    now = _now()
    # Parse start_time if provided, otherwise use now
    if start_time_str:
        try:
            from django.utils.dateparse import parse_datetime
            start_time = parse_datetime(start_time_str)
            if start_time is None:
                start_time = now
            # Allow start in the past so teachers can pull back dates (e.g. tomorrow → today)
        except Exception:
            start_time = now
    else:
        start_time = now
    end_time = start_time + timedelta(minutes=int(duration_minutes))
    duration_int = int(duration_minutes)

    with transaction.atomic():
        exam.status = 'active'
        exam.save(update_fields=['status'])

        # Per-assignment timing: add/update targets, do NOT delete existing
        if group_ids:
            groups_qs = Group.objects.filter(id__in=group_ids)
            if not getattr(settings, 'SINGLE_TENANT', True):
                groups_qs = groups_qs.filter(created_by=request.user)
            for group in groups_qs:
                ExamAssignment.objects.update_or_create(
                    exam=exam, group=group,
                    defaults={
                        'start_time': start_time,
                        'duration_minutes': duration_int,
                        'is_active': True,
                    }
                )
                # Create one ExamRun per group so students see this exam in their list
                run_status = 'active' if start_time <= now else 'scheduled'
                ExamRun.objects.create(
                    exam=exam,
                    group=group,
                    group_name_snapshot=group.name,
                    student=None,
                    start_at=start_time,
                    end_at=end_time,
                    duration_minutes=duration_int,
                    status=run_status,
                    created_by=request.user,
                )

        if student_id:
            from accounts.models import User
            try:
                student = User.objects.get(pk=int(student_id), role='student')
                org_id = getattr(request.user, 'organization_id', None)
                if org_id and getattr(student, 'organization_id', None) != org_id:
                    pass
                else:
                    ExamStudentAssignment.objects.update_or_create(
                        exam=exam, student=student,
                        defaults={
                            'start_time': start_time,
                            'duration_minutes': duration_int,
                            'is_active': True,
                        }
                    )
                    # Create one ExamRun for this student so they see it in their list
                    run_status = 'active' if start_time <= now else 'scheduled'
                    ExamRun.objects.create(
                        exam=exam,
                        group=None,
                        student=student,
                        student_name_snapshot=student.full_name,
                        start_at=start_time,
                        end_at=end_time,
                        duration_minutes=duration_int,
                        status=run_status,
                        created_by=request.user,
                    )
            except (User.DoesNotExist, ValueError, TypeError):
                pass

        # Multi-student custom session: one run that tracks selected students together.
        if student_ids:
            from accounts.models import User
            valid_students = []
            org_id = getattr(request.user, 'organization_id', None)
            for sid in student_ids:
                try:
                    s = User.objects.get(pk=int(sid), role='student')
                    if org_id and getattr(s, 'organization_id', None) != org_id:
                        continue
                    valid_students.append(s)
                except (User.DoesNotExist, ValueError, TypeError):
                    continue
            if valid_students:
                for s in valid_students:
                    ExamStudentAssignment.objects.update_or_create(
                        exam=exam, student=s,
                        defaults={
                            'start_time': start_time,
                            'duration_minutes': duration_int,
                            'is_active': True,
                        }
                    )
                run_status = 'active' if start_time <= now else 'scheduled'
                multi_run = ExamRun.objects.create(
                    exam=exam,
                    group=None,
                    student=None,
                    start_at=start_time,
                    end_at=end_time,
                    duration_minutes=duration_int,
                    status=run_status,
                    created_by=request.user,
                )
                ExamRunStudent.objects.bulk_create(
                    [ExamRunStudent(run=multi_run, student=s) for s in valid_students],
                    ignore_conflicts=True,
                )

    return Response(ExamSerializer(exam).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_stop_view(request, exam_id):
    """Stop exam: set all active runs to finished, then exam status to finished if all runs finished."""
    try:
        exam = Exam.objects.prefetch_related('runs').get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'error': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    
    # Set all active runs to finished
    active_runs = exam.runs.filter(status='active')
    active_runs.update(status='finished')
    
    # If all runs are finished, set exam status to finished
    remaining_active = exam.runs.filter(status='active').exists()
    if not remaining_active:
        exam.status = 'finished'
        exam.save(update_fields=['status'])
    
    return Response(ExamSerializer(exam).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_run_update_view(request, run_id):
    """Update run: duration and/or start_at (for scheduled). end_at = start_at + duration_minutes."""
    try:
        run = ExamRun.objects.select_related('exam').get(pk=run_id, exam__created_by=request.user)
    except ExamRun.DoesNotExist:
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    if run.status not in ('active', 'scheduled'):
        return Response({'detail': 'Can only update active or scheduled runs'}, status=status.HTTP_400_BAD_REQUEST)

    now = _now()
    update_fields = []
    duration_minutes = request.data.get('duration_minutes') or request.data.get('durationMinutes')
    tentative_start = run.start_at
    tentative_duration = run.duration_minutes
    start_time_str = request.data.get('start_at') or request.data.get('startAt')
    if start_time_str and run.status == 'scheduled':
        try:
            from django.utils.dateparse import parse_datetime
            start_at = parse_datetime(start_time_str)
            if start_at is not None:
                tentative_start = start_at
        except (TypeError, ValueError):
            pass
    if duration_minutes is not None:
        try:
            tentative_duration = int(duration_minutes)
        except (TypeError, ValueError):
            return Response({'detail': 'duration_minutes must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        if tentative_duration < 1:
            return Response({'detail': 'duration_minutes must be at least 1'}, status=status.HTTP_400_BAD_REQUEST)
    # Minimum duration: elapsed since run start + 2 minutes (so end is not before now+2min).
    elapsed_min = int(math.ceil((now - tentative_start).total_seconds() / 60.0))
    min_required = max(1, elapsed_min + 2)
    tentative_end = tentative_start + timedelta(minutes=tentative_duration)
    would_end_in_past = tentative_end <= now
    # +2 min margin unless teacher intentionally shortens so the window already ended (flash-end).
    if duration_minutes is not None and tentative_duration < min_required and not would_end_in_past:
        return Response(
            {
                'detail': 'İmtahanın bitməsinə ən azı 2 dəqiqə qalmalıdır.',
                'min_duration_minutes': min_required,
                'code': 'MIN_DURATION_MARGIN',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if duration_minutes is not None:
        run.duration_minutes = tentative_duration
        update_fields.append('duration_minutes')
    if start_time_str and run.status == 'scheduled':
        try:
            from django.utils.dateparse import parse_datetime
            start_at = parse_datetime(start_time_str)
            if start_at is not None:
                run.start_at = start_at
                update_fields.append('start_at')
        except (TypeError, ValueError):
            pass
    flash_end = False
    bulk_count = 0
    if update_fields:
        run.end_at = run.start_at + timedelta(minutes=run.duration_minutes)
        update_fields.append('end_at')
        run.save(update_fields=update_fields)
        if run.status == 'active' and run.end_at <= now:
            bulk_count = _bulk_auto_submit_attempts_for_past_run_end(run, now)
            flash_end = True
            run.refresh_from_db()
    data = ExamRunSerializer(run).data
    data['flashEndTriggered'] = bool(flash_end)
    data['bulkSubmittedCount'] = bulk_count
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_create_run_view(request, exam_id):
    """
    POST /api/teacher/exams/{id}/create-run
    Body: { groupId?, studentId?, duration_minutes, startTime? }
    Returns: { runId, start_at, end_at }
    Creates run and automatically activates exam.
    """
    try:
        exam = Exam.objects.get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    is_valid, err = _validate_exam_composition(exam)
    if not is_valid:
        return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
    group_id = request.data.get('groupId') or request.data.get('group_id')
    student_id = request.data.get('studentId') or request.data.get('student_id')
    duration_minutes = request.data.get('duration_minutes') or request.data.get('durationMinutes')
    start_time_str = request.data.get('startTime') or request.data.get('start_time')
    if not duration_minutes:
        return Response({'detail': 'duration_minutes required'}, status=status.HTTP_400_BAD_REQUEST)
    duration_minutes = int(duration_minutes)
    if not group_id and not student_id:
        return Response({'detail': 'groupId or studentId required'}, status=status.HTTP_400_BAD_REQUEST)
    now = _now()
    if start_time_str:
        try:
            from django.utils.dateparse import parse_datetime
            start_at = parse_datetime(start_time_str)
            if start_at is None:
                start_at = now
            elif start_at < now:
                return Response({'detail': 'Start time cannot be in the past'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            start_at = now
    else:
        start_at = now
    end_at = start_at + timedelta(minutes=duration_minutes)
    from accounts.models import User
    
    with transaction.atomic():
        # Automatically activate exam when run is created
        if exam.status == 'draft':
            exam.status = 'active'
            exam.save(update_fields=['status'])
        
        run = ExamRun.objects.create(
            exam=exam,
            group_id=int(group_id) if group_id else None,
            student_id=int(student_id) if student_id else None,
            group_name_snapshot=(Group.objects.filter(id=int(group_id)).values_list("name", flat=True).first() if group_id else None),
            student_name_snapshot=(User.objects.filter(id=int(student_id), role='student').values_list("full_name", flat=True).first() if student_id else None),
            start_at=start_at,
            end_at=end_at,
            duration_minutes=duration_minutes,
            status='active' if start_at <= now else 'scheduled',
            created_by=request.user,
        )
    return Response({
        'runId': run.id,
        'start_at': run.start_at.isoformat(),
        'end_at': run.end_at.isoformat(),
        'duration_minutes': run.duration_minutes,
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_runs_list_view(request, exam_id):
    """GET /api/teacher/exams/{id}/runs - List runs for exam."""
    try:
        exam = Exam.objects.get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    from django.db.models import Count, Q
    runs = ExamRun.objects.filter(exam=exam).select_related('group', 'student').annotate(
        _attempt_count=Count('attempts', filter=Q(attempts__is_archived=False))
    ).order_by('-start_at')
    return Response(ExamRunSerializer(runs, many=True, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_active_runs_list_view(request):
    """
    GET /api/teacher/active-runs
    Query params: status (active|scheduled), type (quiz|exam), q (search group/student name)
    Returns runs (scheduled or active) for teacher's exams with exam title, type, run details.
    """
    try:
        _auto_transition_run_status()
    except Exception:
        pass
    from django.db.models import Count, Q
    status_filter = request.query_params.get('status')  # active | scheduled
    type_filter = request.query_params.get('type')  # quiz | exam
    search_q = request.query_params.get('q', '').strip()

    qs = ExamRun.objects.filter(
        exam__created_by=request.user,
        exam__is_archived=False,
        status__in=('scheduled', 'active'),
    ).select_related('exam', 'group', 'student').annotate(
        _attempt_count=Count('attempts', filter=Q(attempts__is_archived=False))
    ).order_by('-start_at')

    if status_filter in ('active', 'scheduled'):
        qs = qs.filter(status=status_filter)
    if type_filter in ('quiz', 'exam'):
        qs = qs.filter(exam__type=type_filter)
    if search_q:
        qs = qs.filter(
            Q(group__name__icontains=search_q) |
            Q(student__first_name__icontains=search_q) |
            Q(student__last_name__icontains=search_q)
        )

    runs = list(qs[:200])  # limit for performance
    out = []
    for run in runs:
        attempt_count = getattr(run, '_attempt_count', run.attempts.filter(is_archived=False).count())
        out.append({
            'runId': run.id,
            'examId': run.exam_id,
            'examTitle': run.exam.title,
            'examType': run.exam.type,
            'group_id': run.group_id,
            'groupName': run.group_name_snapshot or (run.group.name if run.group else None),
            'student_id': run.student_id,
            'studentName': run.student_name_snapshot or (run.student.full_name if run.student else 'Deleted Student'),
            'start_at': run.start_at.isoformat(),
            'end_at': run.end_at.isoformat(),
            'duration_minutes': run.duration_minutes,
            'status': run.status,
            'attempt_count': attempt_count,
        })
    return Response(out)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_finished_runs_list_view(request):
    """
    GET /api/teacher/finished-runs
    Köhnə İmtahanlar: list finished runs with filters.
    Params: group_id, student_id, q (exam title search), page, page_size.
    Status label per run: Yoxlanılır (has unpublished attempts) or Yayımlanıb (all published).
    """
    try:
        _auto_transition_run_status()
    except Exception:
        pass
    from django.db.models import Count, Q
    group_id = request.query_params.get('group_id')
    student_id = request.query_params.get('student_id')
    q_search = (request.query_params.get('q') or '').strip()
    page = max(1, int(request.query_params.get('page', 1)))
    page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))

    qs = ExamRun.objects.filter(
        exam__created_by=request.user,
        published=True,  # move to Köhnə once published by teacher
        is_history_deleted=False,
    ).select_related('exam', 'group', 'student').order_by('-published_at', '-end_at')

    if group_id:
        try:
            qs = qs.filter(group_id=int(group_id))
        except ValueError:
            pass
    if student_id:
        try:
            qs = qs.filter(student_id=int(student_id))
        except ValueError:
            pass
    if q_search:
        qs = qs.filter(exam__title__icontains=q_search)

    total = qs.count()
    start = (page - 1) * page_size
    runs = list(qs[start:start + page_size])

    out = []
    for run in runs:
        submitted = ExamAttempt.objects.filter(
            exam_run=run,
            finished_at__isnull=False,
            is_archived=False,
        ).exclude(status='RESTARTED')
        published_count = submitted.filter(is_result_published=True).count()
        sub_count = submitted.count()
        if sub_count == 0:
            result_status_label = 'Yayımlanıb'
        else:
            result_status_label = 'Yayımlanıb' if published_count >= sub_count else 'Yoxlanılır'
        out.append({
            'runId': run.id,
            'examId': run.exam_id,
            'examTitle': run.exam.title,
            'examType': run.exam.type,
            'group_id': run.group_id,
            'groupName': run.group_name_snapshot or (run.group.name if run.group else None),
            'student_id': run.student_id,
            'studentName': run.student_name_snapshot or (run.student.full_name if run.student else 'Deleted Student'),
            'start_at': run.start_at.isoformat(),
            'end_at': run.end_at.isoformat(),
            'duration_minutes': run.duration_minutes,
            'status': run.status,
            'statusLabel': result_status_label,
            'attempt_count': sub_count,
            'published_count': published_count,
        })
    return Response({
        'items': out,
        'meta': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'has_next': start + len(runs) < total,
        },
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_run_stop_view(request, run_id):
    """POST /api/teacher/runs/{id}/stop - Set run status to finished."""
    try:
        run = ExamRun.objects.select_related('exam').get(pk=run_id, exam__created_by=request.user)
    except ExamRun.DoesNotExist:
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    if run.status not in ('active', 'scheduled'):
        return Response({'detail': 'Run is not active or scheduled'}, status=status.HTTP_400_BAD_REQUEST)
    run.status = 'finished'
    run.save(update_fields=['status'])
    return Response(ExamRunSerializer(run).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_run_history_delete_view(request, run_id):
    """POST /api/teacher/runs/{id}/history-delete - Hide run from Köhnə İmtahanlar."""
    try:
        run = ExamRun.objects.select_related('exam').get(pk=run_id, exam__created_by=request.user)
    except ExamRun.DoesNotExist:
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    if not run.published:
        return Response({'detail': 'Only published (old) runs can be deleted from history'}, status=status.HTTP_400_BAD_REQUEST)
    run.is_history_deleted = True
    run.history_deleted_at = _now()
    run.save(update_fields=['is_history_deleted', 'history_deleted_at'])
    return Response({'ok': True, 'runId': run.id, 'message': 'Köhnə imtahan tarixçədən silindi.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_run_attempts_view(request, run_id):
    """GET /api/teacher/runs/{runId}/attempts - List attempts for a run. Shows all students in group, even if they never started."""
    try:
        run = ExamRun.objects.select_related('exam', 'group', 'student').get(pk=run_id, exam__created_by=request.user)
    except ExamRun.DoesNotExist:
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    
    exam = run.exam
    max_s = float(exam.max_score or (100 if exam.type == 'quiz' else 150))
    
    # Get all attempts for this run
    attempts = ExamAttempt.objects.filter(exam_run=run, is_archived=False).select_related(
        'student', 'student__student_profile'
    ).order_by('-started_at')
    
    # If run is for a group, include all students in group (even if they never started)
    if run.group:
        from groups.services import get_active_students_for_group
        memberships = get_active_students_for_group(run.group)
        student_ids_in_group = {m.student_profile.user_id for m in memberships}
        attempt_by_student = {}
        for a in attempts:
            # attempts is ordered by -started_at; keep first (latest valid) per student
            if a.student_id not in attempt_by_student:
                attempt_by_student[a.student_id] = a
        
        data = []
        for student_id in student_ids_in_group:
            attempt = attempt_by_student.get(student_id)
            if attempt:
                auto = float(attempt.auto_score or 0)
                manual = float(attempt.manual_score or 0) if attempt.manual_score is not None else 0
                final = float(attempt.total_score) if attempt.total_score is not None else (auto + manual)
                release_status = 'PUBLISHED' if attempt.is_result_published else ('GRADED' if attempt.is_checked else 'PENDING')
                data.append({
                    'id': attempt.id,
                    'runId': run.id,
                    'studentId': attempt.student_id,
                    'studentName': attempt.student.full_name,
                    'status': 'SUBMITTED' if attempt.finished_at else ('EXPIRED' if attempt.status == 'EXPIRED' else 'IN_PROGRESS'),
                    'resultReleaseStatus': release_status,
                    'startedAt': attempt.started_at.isoformat(),
                    'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                    'autoScore': auto,
                    'manualScore': float(attempt.manual_score) if attempt.manual_score is not None else None,
                    'finalScore': min(final, max_s),
                    'maxScore': max_s,
                    'isChecked': attempt.is_checked,
                    'isPublished': attempt.is_result_published,
                })
            else:
                # Student never started - show as not started
                from accounts.models import User
                try:
                    student = User.objects.get(id=student_id, role='student')
                    data.append({
                        'id': None,
                        'runId': run.id,
                        'studentId': student.id,
                        'studentName': student.full_name,
                        'status': 'NOT_STARTED',
                        'resultReleaseStatus': 'PENDING',
                        'startedAt': None,
                        'finishedAt': None,
                        'autoScore': 0,
                        'manualScore': None,
                        'finalScore': 0,
                        'maxScore': max_s,
                        'isChecked': False,
                        'isPublished': False,
                    })
                except User.DoesNotExist:
                    pass
        # Group aggregate: sum(all scores including 0s) / total_members (TODO-03)
        from decimal import Decimal
        total_members = len(data)
        sum_score = sum(float(d['finalScore']) for d in data)
        submitted_count = sum(1 for d in data if d.get('finishedAt') and d.get('status') == 'SUBMITTED')
        graded_count = sum(1 for d in data if d.get('isChecked') or (d.get('finishedAt') and d.get('status') == 'SUBMITTED'))
        average_score = (Decimal(sum_score) / Decimal(total_members)).quantize(Decimal('0.01')) if total_members else None
        return Response({
            'attempts': data,
            'summary': {
                'averageScore': float(average_score) if average_score is not None else None,
                'totalStudents': total_members,
                'gradedCount': graded_count,
            },
            'group_aggregate': {
                'total_members': total_members,
                'submitted_count': submitted_count,
                'average_score': float(average_score) if average_score is not None else None,
                'sum_score': float(sum_score),
            },
        })
    elif run.student is None:
        # Multi-student custom run
        student_ids = set(run.run_students.values_list('student_id', flat=True))
        attempt_by_student = {}
        for a in attempts:
            # attempts is ordered by -started_at; keep first (latest valid) per student
            if a.student_id not in attempt_by_student:
                attempt_by_student[a.student_id] = a
        data = []
        for student_id in student_ids:
            attempt = attempt_by_student.get(student_id)
            if attempt:
                auto = float(attempt.auto_score or 0)
                manual = float(attempt.manual_score or 0) if attempt.manual_score is not None else 0
                final = float(attempt.total_score) if attempt.total_score is not None else (auto + manual)
                release_status = 'PUBLISHED' if attempt.is_result_published else ('GRADED' if attempt.is_checked else 'PENDING')
                data.append({
                    'id': attempt.id,
                    'runId': run.id,
                    'studentId': attempt.student_id,
                    'studentName': attempt.student.full_name,
                    'status': 'SUBMITTED' if attempt.finished_at else ('EXPIRED' if attempt.status == 'EXPIRED' else 'IN_PROGRESS'),
                    'resultReleaseStatus': release_status,
                    'startedAt': attempt.started_at.isoformat(),
                    'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                    'autoScore': auto,
                    'manualScore': float(attempt.manual_score) if attempt.manual_score is not None else None,
                    'finalScore': min(final, max_s),
                    'maxScore': max_s,
                    'isChecked': attempt.is_checked,
                    'isPublished': attempt.is_result_published,
                })
            else:
                from accounts.models import User
                student = User.objects.filter(id=student_id, role='student').first()
                if not student:
                    continue
                data.append({
                    'id': None,
                    'runId': run.id,
                    'studentId': student.id,
                    'studentName': student.full_name,
                    'status': 'NOT_STARTED',
                    'resultReleaseStatus': 'PENDING',
                    'startedAt': None,
                    'finishedAt': None,
                    'autoScore': 0,
                    'manualScore': None,
                    'finalScore': 0,
                    'maxScore': max_s,
                    'isChecked': False,
                    'isPublished': False,
                })
        from decimal import Decimal
        total_members = len(data)
        sum_score = sum(float(d['finalScore']) for d in data)
        submitted_count = sum(1 for d in data if d.get('finishedAt') and d.get('status') == 'SUBMITTED')
        graded_count = sum(1 for d in data if d.get('isChecked') or (d.get('finishedAt') and d.get('status') == 'SUBMITTED'))
        average_score = (Decimal(sum_score) / Decimal(total_members)).quantize(Decimal('0.01')) if total_members else None
        return Response({
            'attempts': data,
            'summary': {
                'averageScore': float(average_score) if average_score is not None else None,
                'totalStudents': total_members,
                'gradedCount': graded_count,
            },
            'group_aggregate': {
                'total_members': total_members,
                'submitted_count': submitted_count,
                'average_score': float(average_score) if average_score is not None else None,
                'sum_score': float(sum_score),
            },
        })
    else:
        # Individual student run
        data = []
        for a in attempts:
            auto = float(a.auto_score or 0)
            manual = float(a.manual_score or 0) if a.manual_score is not None else 0
            final = float(a.total_score) if a.total_score is not None else (auto + manual)
            release_status = 'PUBLISHED' if a.is_result_published else ('GRADED' if a.is_checked else 'PENDING')
            data.append({
                'id': a.id,
                'runId': run.id,
                'studentId': a.student_id,
                'studentName': a.student.full_name,
                'status': 'SUBMITTED' if a.finished_at else ('EXPIRED' if a.status == 'EXPIRED' else 'IN_PROGRESS'),
                'resultReleaseStatus': release_status,
                'startedAt': a.started_at.isoformat(),
                'finishedAt': a.finished_at.isoformat() if a.finished_at else None,
                'autoScore': float(a.auto_score or 0),
                'manualScore': float(a.manual_score) if a.manual_score is not None else None,
                'finalScore': final,
                'maxScore': max_s,
                'isChecked': a.is_checked,
                'isPublished': a.is_result_published,
            })
        from decimal import Decimal
        finished_scores = [float(d['finalScore']) for d in data if d.get('finishedAt')]
        total_students = len(data)
        graded_count = sum(1 for d in data if d.get('isChecked') or d.get('finishedAt'))
        average_score = None
        if finished_scores:
            avg = (Decimal(sum(finished_scores)) / Decimal(len(finished_scores))).quantize(Decimal('0.01'))
            average_score = float(avg)
        return Response({
            'attempts': data,
            'summary': {
                'averageScore': average_score,
                'totalStudents': total_students,
                'gradedCount': graded_count,
            },
        })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_run_reset_student_view(request, run_id):
    """POST /api/teacher/runs/{runId}/reset-student - Body: { studentId }. Mark attempt RESTARTED, student can start again."""
    try:
        run = ExamRun.objects.get(pk=run_id, exam__created_by=request.user)
    except ExamRun.DoesNotExist:
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    student_id = request.data.get('studentId') or request.data.get('student_id')
    if not student_id:
        return Response({'detail': 'studentId required'}, status=status.HTTP_400_BAD_REQUEST)
    student_id = int(student_id)
    attempt = ExamAttempt.objects.filter(exam_run=run, student_id=student_id).order_by('-started_at').first()
    if not attempt:
        return Response({'detail': 'No attempt found for this student in this run'}, status=status.HTTP_404_NOT_FOUND)
    now = _now()
    with transaction.atomic():
        attempt.status = 'RESTARTED'
        attempt.save(update_fields=['status'])
        ExamStudentAssignment.objects.update_or_create(
            exam=run.exam,
            student_id=student_id,
            defaults={
                'start_time': now,
                'end_time': now + timedelta(minutes=run.duration_minutes),
                'duration_minutes': run.duration_minutes,
                'is_active': True,
            }
        )
    return Response({
        'message': 'Şagird yenidən başlaya bilər',
        'studentId': student_id,
        'runId': run.id,
    })


# ---------- Student: List active exam RUNS (per-run time window) ----------
@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exams_list_view(request):
    """Return ACTIVE runs available to student (by group membership or direct assignment)."""
    from groups.models import GroupStudent
    from students.models import StudentProfile
    from django.db.models import Q
    now = _now()
    try:
        student_profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        student_profile = None
    if not student_profile:
        return Response([])
    group_ids = list(
        GroupStudent.objects.filter(
            student_profile=student_profile,
            active=True,
            left_at__isnull=True,
        ).values_list('group_id', flat=True)
    )
    runs = ExamRun.objects.filter(
        status__in=['active', 'suspended'],
        exam__is_archived=False,
        exam__is_deleted=False,
    ).exclude(exam__status='deleted').filter(
        Q(group_id__in=group_ids) | Q(student=request.user) | Q(run_students__student=request.user)
    ).filter(
        start_at__lte=now,
        end_at__gte=now,
    ).select_related('exam').order_by('end_at').distinct()

    # Exclude runs where student already submitted (strict post-submission lockdown).
    submitted_run_ids = set(
        ExamAttempt.objects.filter(
            student=request.user,
            exam_run_id__isnull=False,
            finished_at__isnull=False,
        ).exclude(status='RESTARTED').values_list('exam_run_id', flat=True)
    )
    runs = [r for r in runs if r.id not in submitted_run_ids]

    # Prefer at most one run per exam (avoid multiple entries for same exam): keep run with latest end_at per exam_id
    seen_exam_ids = set()
    deduped = []
    for r in sorted(runs, key=lambda x: (x.exam_id, -x.end_at.timestamp())):
        if r.exam_id not in seen_exam_ids:
            seen_exam_ids.add(r.exam_id)
            deduped.append(r)
    runs = deduped

    data = []
    for run in runs:
        remaining_seconds = max(0, int((run.end_at - now).total_seconds()))
        data.append({
            'runId': run.id,
            'examId': run.exam_id,
            'id': run.exam_id,
            'title': run.exam.title,
            'type': run.exam.type,
            'status': run.status,
            'suspendedAt': run.suspended_at.isoformat() if run.suspended_at else None,
            'teacherUnlockedAt': run.teacher_unlocked_at.isoformat() if getattr(run, 'teacher_unlocked_at', None) else None,
            'sourceType': run.exam.source_type,
            'startTime': run.start_at.isoformat(),
            'endTime': run.end_at.isoformat(),
            'durationMinutes': run.duration_minutes,
            'remainingSeconds': remaining_seconds,
        })
    return Response(data)


# ---------- Student: List my exam results (all submitted, published or not) ----------
@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_my_results_view(request):
    """GET /api/student/exams/my-results - List student's submitted exam attempts. Use published_only=1 for archive (only PUBLISHED)."""
    from django.db.models import Q
    exam_type = (request.query_params.get('type') or '').strip().lower()
    published_only = request.query_params.get('published_only', '').strip().lower() in ('1', 'true', 'yes')
    attempts = ExamAttempt.objects.filter(
        student=request.user,
        finished_at__isnull=False,
        is_archived=False,
        exam__is_deleted=False,
        is_result_session_deleted=False,
    ).exclude(status='RESTARTED').exclude(exam__status='deleted').filter(
        Q(exam_run__isnull=True) | Q(exam_run__is_history_deleted=False),
    ).select_related('exam').order_by('-finished_at')
    if published_only:
        attempts = attempts.filter(is_result_published=True, is_checked=True)
    if exam_type in ('quiz', 'exam'):
        attempts = attempts.filter(exam__type=exam_type)
    data = []
    for a in attempts:
        max_score = float(a.exam.max_score or (100 if a.exam.type == 'quiz' else 150))
        is_published = a.is_result_published and a.is_checked
        status_enum = 'PUBLISHED' if is_published else ('WAITING_MANUAL' if a.is_checked else 'SUBMITTED')
        auto_s = float(a.auto_score or 0) if a.auto_score is not None else None
        manual_s = float(a.manual_score) if a.manual_score is not None else None
        total_s = float(a.total_score) if a.total_score is not None else (float(a.auto_score or 0) + float(a.manual_score or 0)) if a.auto_score is not None else None
        data.append({
            'attemptId': a.id,
            'examId': a.exam_id,
            'examTitle': a.exam.title,
            'examType': a.exam.type,
            'title': a.exam.title,
            'status': status_enum,
            'is_result_published': is_published,
            'autoScore': auto_s if is_published else None,
            'manualScore': manual_s if is_published else None,
            'totalScore': total_s if is_published else None,
            'maxScore': max_score,
            'score': total_s if is_published else None,
            'submittedAt': a.finished_at.isoformat() if a.finished_at else None,
            'finishedAt': a.finished_at.isoformat() if a.finished_at else None,
        })
    return Response(data)


def _get_student_assignment_context(exam, student):
    """Get assignment context for student: (start_time, end_time, duration_minutes). Uses assignment-level or exam-level."""
    from groups.models import GroupStudent
    from students.models import StudentProfile
    from django.db.models import Q
    now = timezone.now()
    try:
        sp = student.student_profile
    except Exception:
        sp = None
    group_ids = list(
        GroupStudent.objects.filter(
            student_profile=sp,
            active=True,
            left_at__isnull=True,
        ).values_list('group_id', flat=True)
    ) if sp else []
    # Group assignments
    for ass in ExamAssignment.objects.filter(exam=exam, group_id__in=group_ids, is_active=True):
        st = ass.start_time or exam.start_time
        dur = ass.duration_minutes or exam.duration_minutes
        if st is not None and dur is not None:
            et = st + timedelta(minutes=dur)
            if st <= now <= et:
                return st, et, dur
    # Direct student assignment
    for ass in ExamStudentAssignment.objects.filter(exam=exam, student=student, is_active=True):
        st = ass.start_time or exam.start_time
        dur = ass.duration_minutes or exam.duration_minutes
        if st is not None and dur is not None:
            et = st + timedelta(minutes=dur)
            if st <= now <= et:
                return st, et, dur
    # Exam-level timing (activated exam)
    if exam.start_time is not None and exam.duration_minutes is not None:
        et = exam.start_time + timedelta(minutes=exam.duration_minutes)
        if exam.start_time <= now <= et:
            return exam.start_time, et, exam.duration_minutes
    return None, None, None


def _student_has_run_access(run, user):
    """Check if student has access to this run (group membership or direct)."""
    from groups.models import GroupStudent
    from students.models import StudentProfile
    from tests.models import ExamStudentAssignment
    if run.student_id == user.id:
        return True
    if run.run_students.filter(student_id=user.id).exists():
        return True
    # Backward-compatible fallback: some earlier custom runs may not have run_students rows.
    # If student has an active direct assignment for the same exam, allow run access.
    if run.group_id is None and run.student_id is None:
        if ExamStudentAssignment.objects.filter(
            exam_id=run.exam_id,
            student_id=user.id,
            is_active=True,
        ).exists():
            return True
    try:
        sp = user.student_profile
    except Exception:
        return False
    if run.group_id is None:
        return False
    return GroupStudent.objects.filter(
        group_id=run.group_id,
        student_profile=sp,
        active=True,
        left_at__isnull=True,
    ).exists()


logger = logging.getLogger(__name__)


def _get_run_page_urls(run_id, request):
    """Return list of absolute URLs for cached page images for this run (media/exam_pages/run_{id}/)."""
    from django.conf import settings
    output_dir = os.path.join(settings.MEDIA_ROOT, 'exam_pages', f'run_{run_id}')
    if not os.path.isdir(output_dir):
        return []
    urls = []
    for name in os.listdir(output_dir):
        if name.startswith('page_') and name.lower().endswith(('.jpg', '.jpeg')):
            try:
                num = int(name[5:-4])
            except ValueError:
                continue
            rel = os.path.join('exam_pages', f'run_{run_id}', name).replace('\\', '/')
            url = request.build_absolute_uri(settings.MEDIA_URL.rstrip('/') + '/' + rel)
            urls.append((num, url))
    urls.sort(key=lambda x: x[0])
    return [u for _, u in urls]


def _get_exam_pdf_file(exam):
    """Return the PDF file field (FileField) for the exam, or None."""
    if exam.pdf_document and exam.pdf_document.file:
        return exam.pdf_document.file
    if exam.pdf_file:
        return exam.pdf_file
    return None


def _ensure_run_pdf_images(run, request):
    """
    Generate PNG pages for the run's exam PDF once; cache under media/exams/run_{run_id}/.
    Called when exam starts so GET /pages can serve cached images.
    """
    from django.conf import settings
    from utils.pdf_converter import convert_pdf_to_images, _get_poppler_path
    import tempfile

    exam = run.exam
    pdf_file = _get_exam_pdf_file(exam)
    if not pdf_file or not pdf_file.storage.exists(pdf_file.name):
        logger.warning("_ensure_run_pdf_images run_id=%s no pdf file or file missing in storage", run.id)
        return {"success": False, "error": "PDF conversion failed: PDF file missing", "pages": []}

    output_dir = os.path.join(settings.MEDIA_ROOT, 'exam_pages', f'run_{run.id}')
    os.makedirs(output_dir, exist_ok=True)

    # Cache: if images already exist, do not regenerate
    if os.path.isdir(output_dir) and os.listdir(output_dir):
        existing = [f for f in os.listdir(output_dir) if f.startswith('page_') and f.lower().endswith(('.jpg', '.jpeg'))]
        if existing:
            pages = _get_run_page_urls(run.id, request)
            return {"success": True, "error": "", "pages": pages}

    pdf_path = None
    try:
        pdf_path = getattr(pdf_file, 'path', None)
        if pdf_path and not os.path.isfile(pdf_path):
            pdf_path = None
    except (AttributeError, NotImplementedError):
        pdf_path = None

    if not pdf_path or not os.path.isfile(pdf_path):
        with pdf_file.open('rb') as fh:
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(fh.read())
                pdf_path = tmp.name
        try:
            poppler_path = _get_poppler_path()
            res = convert_pdf_to_images(pdf_path, output_dir, poppler_path=poppler_path)
        finally:
            try:
                os.unlink(pdf_path)
            except OSError:
                pass
        if not res.get("success"):
            logger.error("_ensure_run_pdf_images run_id=%s conversion_failed error=%s", run.id, res.get("error", ""), exc_info=True)
            return {"success": False, "error": res.get("error") or "PDF conversion failed", "pages": []}
        pages = _get_run_page_urls(run.id, request)
        return {"success": True, "error": "", "pages": pages}

    if not os.path.exists(pdf_path):
        logger.error("_ensure_run_pdf_images run_id=%s PDF path does not exist: %s", run.id, pdf_path)
        return {"success": False, "error": f"PDF conversion failed: PDF path does not exist: {pdf_path}", "pages": []}
    try:
        poppler_path = _get_poppler_path()
        res = convert_pdf_to_images(pdf_path, output_dir, poppler_path=poppler_path)
        if not res.get("success"):
            logger.error("_ensure_run_pdf_images run_id=%s conversion_failed error=%s", run.id, res.get("error", ""), exc_info=True)
            return {"success": False, "error": res.get("error") or "PDF conversion failed", "pages": []}
        pages = _get_run_page_urls(run.id, request)
        return {"success": True, "error": "", "pages": pages}
    except ModuleNotFoundError as e:
        logger.error("_ensure_run_pdf_images run_id=%s module_missing", run.id, exc_info=True)
        return {"success": False, "error": f"PDF conversion failed: {e}", "pages": []}
    except FileNotFoundError as e:
        logger.error("_ensure_run_pdf_images run_id=%s file_missing", run.id, exc_info=True)
        return {"success": False, "error": f"PDF conversion failed: {e}", "pages": []}
    except Exception as e:
        logger.error("_ensure_run_pdf_images run_id=%s unexpected_error", run.id, exc_info=True)
        return {"success": False, "error": f"PDF conversion failed: {e}", "pages": []}


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_run_pages_view(request, run_id):
    """
    GET /api/student/runs/{run_id}/pages
    Returns list of image URLs for the exam PDF pages (generated once when exam starts).
    Same access rules as PDF: run active, student has access, attempt exists, not submitted.
    """
    import traceback
    from django.conf import settings

    try:
        now = _now()
        run = ExamRun.objects.select_related('exam', 'exam__pdf_document').filter(pk=run_id).first()
        if not run:
            return Response({'detail': 'Exam run not found', 'error': 'Exam run not found'}, status=status.HTTP_404_NOT_FOUND)
        if getattr(run.exam, 'is_deleted', False) or getattr(run.exam, 'status', None) == 'deleted':
            return Response({'detail': 'Exam run not found', 'error': 'Exam run not found'}, status=status.HTTP_404_NOT_FOUND)
        # Cheating lock should not block other students in a group run.
        # For group runs `run.student_id` is null, so we allow access for everyone.
        if run.is_cheating_detected and run.student_id is not None and run.student_id != request.user.id:
            return Response({'detail': 'Run locked: cheating detected'}, status=status.HTTP_403_FORBIDDEN)

        if run.status != 'active' or run.start_at > now or run.end_at < now:
            return Response({'detail': 'Run is not active or outside time window'}, status=status.HTTP_403_FORBIDDEN)
        if not _student_has_run_access(run, request.user):
            return Response({'detail': 'You do not have access to this run'}, status=status.HTTP_403_FORBIDDEN)
        attempt = ExamAttempt.objects.filter(exam_run=run, student=request.user).order_by('-started_at').first()
        if not attempt:
            return Response({'detail': 'Start the exam first to view pages'}, status=status.HTTP_403_FORBIDDEN)
        if attempt.finished_at is not None:
            return Response({'detail': 'Exam already submitted'}, status=status.HTTP_403_FORBIDDEN)
        if attempt.status == 'RESTARTED':
            return Response({'detail': 'This attempt was reset'}, status=status.HTTP_403_FORBIDDEN)

        exam = run.exam
        if exam.source_type != 'PDF':
            return Response({'detail': 'No PDF for this exam source', 'sourceType': exam.source_type}, status=status.HTTP_404_NOT_FOUND)
        pdf_file = _get_exam_pdf_file(exam)
        if not pdf_file:
            return Response({'detail': 'No PDF for this exam'}, status=status.HTTP_404_NOT_FOUND)
        if not pdf_file.storage.exists(pdf_file.name):
            return Response({'detail': 'PDF file not found in storage', 'error': f'Missing: {pdf_file.name}'}, status=status.HTTP_404_NOT_FOUND)

        try:
            pdf_path = getattr(pdf_file, 'path', None)
            if pdf_path and not os.path.isfile(pdf_path):
                pdf_path = None
        except (AttributeError, NotImplementedError, TypeError, OSError):
            pdf_path = None
        if pdf_path and not os.path.isfile(pdf_path):
            return Response(
                {'error': f'PDF file not found: {pdf_path}', 'detail': 'PDF file not found on disk'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        output_dir = os.path.join(settings.MEDIA_ROOT, 'exam_pages', f'run_{run.id}')
        os.makedirs(output_dir, exist_ok=True)

        # Cache: if images already exist, return them without regenerating
        existing_pages = []
        if os.path.isdir(output_dir):
            for name in os.listdir(output_dir):
                if name.startswith('page_') and name.lower().endswith(('.jpg', '.jpeg')):
                    try:
                        num = int(name[5:-4])
                        rel = os.path.join('exam_pages', f'run_{run.id}', name).replace('\\', '/')
                        url = request.build_absolute_uri(settings.MEDIA_URL.rstrip('/') + '/' + rel)
                        existing_pages.append((num, url))
                    except ValueError:
                        continue
        if existing_pages:
            existing_pages.sort(key=lambda x: x[0])
            return Response({'pages': [u for _, u in existing_pages]})

        # Generate images
        gen = _ensure_run_pdf_images(run, request)
        if isinstance(gen, dict) and gen.get('success') is False:
            return Response(gen, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        pages = _get_run_page_urls(run.id, request)
        if not pages:
            return Response(
                {'detail': 'Pages not ready', 'error': 'PDF conversion produced no images'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'pages': pages})

    except Exception as e:
        logger.error("student_run_pages unexpected_error run_id=%s error=%s", run_id, e, exc_info=True)
        return Response(
            {'success': False, 'error': 'PDF conversion failed', 'pages': []},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@xframe_options_exempt
def student_run_pdf_view(request, run_id):
    """
    Protected PDF: require run accessible + within time + attempt exists + attempt not submitted.
    Returns 403 if any check fails. Streams PDF file.

    Authentication:
    - Normal API access: JWT Bearer token in Authorization header
    - Iframe access: Signed token in ?token= query parameter (iframes cannot send headers)

    NOTE: This is a regular Django view (not DRF @api_view) to prevent DRF renderers
    from corrupting binary PDF data. Authentication/permissions are handled manually.
    """
    # Manual DRF authentication (JWT)
    from rest_framework.request import Request
    from rest_framework.views import APIView
    from rest_framework_simplejwt.authentication import JWTAuthentication

    # Wrap request in DRF Request for authentication/permission checking
    drf_request = Request(request)

    # Try JWT authentication first
    jwt_auth = JWTAuthentication()
    try:
        user, token = jwt_auth.authenticate(drf_request)
        if user:
            request.user = user
    except Exception:
        # JWT auth failed, will check for signed token in permission
        pass

    # Check permission (handles both JWT and signed token)
    permission = IsStudentOrSignedToken()

    # Create a mock view object with kwargs
    class MockView(APIView):
        def __init__(self, run_id):
            self.kwargs = {'run_id': run_id}

    mock_view = MockView(run_id)

    # Check permission (this will also handle signed token auth if JWT failed)
    if not permission.has_permission(drf_request, mock_view):
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)

    # Ensure request.user is set (permission class sets it for token auth)
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return JsonResponse({'detail': 'Authentication credentials were not provided.'}, status=401)

    # Now proceed with business logic
    now = _now()
    try:
        run = ExamRun.objects.select_related('exam', 'exam__pdf_document').get(pk=run_id)
    except ExamRun.DoesNotExist:
        logger.warning("student_run_pdf run_id=%s user_id=%s run_not_found", run_id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'Run not found'}, status=404)
    if getattr(run.exam, 'is_deleted', False) or getattr(run.exam, 'status', None) == 'deleted':
        return JsonResponse({'detail': 'Run not found'}, status=404)
    if run.status != 'active' or run.start_at > now or run.end_at < now:
        logger.warning("student_run_pdf run_id=%s exam_id=%s user_id=%s run_not_active_or_outside_window", run_id, run.exam_id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'Run is not active or outside time window'}, status=403)
    # Cheating lock should not block other students in a group run.
    if run.is_cheating_detected and run.student_id is not None and run.student_id != request.user.id:
        return JsonResponse({'detail': 'Run locked: cheating detected'}, status=403)
    if not _student_has_run_access(run, request.user):
        logger.warning("student_run_pdf run_id=%s exam_id=%s user_id=%s no_access", run_id, run.exam_id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'You do not have access to this run'}, status=403)
    attempt = ExamAttempt.objects.filter(exam_run=run, student=request.user).order_by('-started_at').first()
    if not attempt:
        logger.warning("student_run_pdf run_id=%s exam_id=%s user_id=%s no_attempt", run_id, run.exam_id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'Start the exam first to view the PDF'}, status=403)
    if attempt.finished_at is not None:
        logger.warning("student_run_pdf run_id=%s attempt_id=%s user_id=%s already_submitted", run_id, attempt.id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'Exam already submitted; PDF no longer available'}, status=403)
    if attempt.status == 'RESTARTED':
        logger.warning("student_run_pdf run_id=%s attempt_id=%s user_id=%s attempt_restarted", run_id, attempt.id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'This attempt was reset'}, status=403)
    exam = run.exam
    if exam.source_type != 'PDF':
        return JsonResponse({'detail': 'No PDF for this exam source', 'sourceType': exam.source_type}, status=404)
    pdf_file = None
    pdf_source = None

    # STEP 1: Identify PDF source and get file reference
    if exam.pdf_document and exam.pdf_document.file:
        pdf_file = exam.pdf_document.file
        pdf_source = f"TeacherPDF(id={exam.pdf_document.id})"
        model_size = exam.pdf_document.file_size
    elif exam.pdf_file:
        pdf_file = exam.pdf_file
        pdf_source = f"Exam.pdf_file(exam_id={exam.id})"
        model_size = pdf_file.size if hasattr(pdf_file, 'size') else None
    else:
        logger.warning("student_run_pdf run_id=%s exam_id=%s user_id=%s no_pdf", run_id, run.exam_id, getattr(request.user, 'id', None))
        return JsonResponse({'detail': 'No PDF for this exam'}, status=404)

    # Verify file exists and has size
    if not pdf_file.storage.exists(pdf_file.name):
        logger.error(f"PDF file not found in storage: {pdf_file.name}")
        return JsonResponse({'detail': 'PDF file not found on storage'}, status=404)
    file_size = getattr(pdf_file, 'size', None)
    if file_size is None or file_size == 0:
        logger.error(f"PDF file is empty or size unknown: {pdf_file.name}, size={file_size}")
        return JsonResponse({'detail': 'PDF file is empty'}, status=500)

    try:
        absolute_path = None
        try:
            absolute_path = pdf_file.path
        except (AttributeError, NotImplementedError):
            pass

        # STEP 4 test: force full body response to rule out streaming issues
        if request.GET.get('force_body') == '1':
            if absolute_path and os.path.exists(absolute_path):
                with open(absolute_path, 'rb') as f:
                    data = f.read()
            else:
                with pdf_file.open('rb') as f:
                    data = f.read()
            response = HttpResponse(data, content_type='application/pdf')
            response['Content-Disposition'] = 'inline'
            logger.info(f"PDF served (force_body) run_id={run_id} size={len(data)}")
            return response

        # Minimal response: file handle + content-type + Content-Disposition only.
        # No Content-Length, Accept-Ranges, Cache-Control — let Django handle streaming.
        if absolute_path and os.path.exists(absolute_path):
            file_handle = open(absolute_path, 'rb')
        else:
            file_handle = pdf_file.open('rb')

        response = FileResponse(file_handle, content_type='application/pdf', as_attachment=False)
        response['Content-Disposition'] = 'inline'
        logger.info(f"PDF served run_id={run_id} source={pdf_source} file={pdf_file.name} size={file_size}")
        return response

    except Exception as e:
        logger.exception(
            f"student_run_pdf error run_id={run_id}, exam_id={run.exam_id}, user_id={getattr(request.user, 'id', None)}, error={e}"
        )
        return JsonResponse({'detail': 'Could not serve PDF'}, status=500)


def _rng_for_attempt(seed):
    """Deterministic RNG per attempt so resume never re-shuffles; seed=None uses a fresh Random (legacy fallbacks)."""
    import random
    if seed is None:
        return random.Random()
    return random.Random(int(seed))


def _build_blueprint_bank(exam, seed=None):
    """Build attempt blueprint for BANK exam: stable option ids (option PK), shuffled display order, correctOptionId.

    Question order: MC block, then OPEN, then SITUATION — each block is shuffled independently (grouped shuffle).
    """
    rng = _rng_for_attempt(seed)
    eqs = list(
        ExamQuestion.objects.filter(exam=exam)
        .select_related('question')
        .prefetch_related('question__options')
        .order_by('order')
    )
    type_order = {'MULTIPLE_CHOICE': 0, 'OPEN_SINGLE_VALUE': 1, 'OPEN_ORDERED': 1, 'OPEN_UNORDERED': 1, 'OPEN_PERMUTATION': 1, 'SITUATION': 2}
    eqs.sort(key=lambda eq: (type_order.get(eq.question.type, 99), eq.order))

    def _bucket(eq):
        t = eq.question.type
        if t == 'MULTIPLE_CHOICE':
            return 0
        if t == 'SITUATION':
            return 2
        return 1

    mc_eqs = [eq for eq in eqs if _bucket(eq) == 0]
    open_eqs = [eq for eq in eqs if _bucket(eq) == 1]
    sit_eqs = [eq for eq in eqs if _bucket(eq) == 2]
    rng.shuffle(mc_eqs)
    rng.shuffle(open_eqs)
    rng.shuffle(sit_eqs)
    ordered_eqs = mc_eqs + open_eqs + sit_eqs

    blueprint = []
    for eq in ordered_eqs:
        q = eq.question
        kind = 'mc' if q.type == 'MULTIPLE_CHOICE' else ('open' if q.type != 'SITUATION' else 'situation')
        opts = list(q.options.order_by('order'))
        correct_option_id = None
        if q.type == 'MULTIPLE_CHOICE' and q.correct_answer is not None:
            try:
                correct_option_id = int(q.correct_answer) if not isinstance(q.correct_answer, dict) else q.correct_answer.get('option_id')
            except (TypeError, ValueError, AttributeError):
                pass
        if kind == 'mc' and opts:
            rng.shuffle(opts)
            options_blueprint = [{'id': str(o.id), 'text': o.text} for o in opts]
            correctOptionId = str(correct_option_id) if correct_option_id and any(o.id == correct_option_id for o in opts) else (str(opts[0].id) if opts else None)
            blueprint.append({
                'questionId': q.id,
                'questionNumber': eq.order + 1,
                'kind': kind,
                'options': options_blueprint,
                'correctOptionId': correctOptionId,
            })
        else:
            entry = {
                'questionId': q.id,
                'questionNumber': eq.order + 1,
                'kind': kind,
                'options': [],
                'correctOptionId': None,
            }
            if kind == 'open':
                entry['open_rule'] = (q.answer_rule_type or 'EXACT_MATCH').strip().upper()
                if entry['open_rule'] == 'MATCHING' and isinstance(q.correct_answer, dict) and q.correct_answer:
                    left = list(str(k) for k in q.correct_answer.keys())
                    right = list(set(str(v).lower() for v in q.correct_answer.values()))
                    entry['matching_left'] = left or ['1', '2', '3']
                    entry['matching_right'] = sorted(right) if right else ['a', 'b', 'c', 'd', 'e']
                elif entry['open_rule'] == 'MATCHING':
                    entry['matching_left'] = ['1', '2', '3']
                    entry['matching_right'] = ['a', 'b', 'c', 'd', 'e']
            blueprint.append(entry)
    return blueprint


def _pdf_mc_resolve_correct_key_and_opt_id(q_def):
    """
    PDF/JSON answer-key MC block: `correct` may be option letter ('A'), 0-based index (0 = first option),
    or missing. Returns (correct_key_upper_or_none, stable_option_id 'opt_N' or None).
    Used for blueprint correctOptionId and submission grading (key-only student payloads).
    """
    if not isinstance(q_def, dict):
        return None, None
    opts = [o for o in (q_def.get('options') or []) if isinstance(o, dict)]
    if not opts:
        return None, None
    cor = q_def.get('correct')
    idx = None
    if isinstance(cor, bool):
        cor = None
    if isinstance(cor, (int, float)):
        idx = int(cor)
    elif isinstance(cor, str) and cor.strip() != '':
        s = cor.strip()
        if s.lstrip('-').isdigit():
            idx = int(s)
    if idx is not None:
        if 0 <= idx < len(opts):
            letter = (opts[idx].get('key') or '').strip().upper()
            if not letter:
                letter = chr(ord('A') + idx) if 0 <= idx < 26 else str(idx)
            return letter, f'opt_{idx + 1}'
        return None, None
    if cor is not None:
        cor_u = str(cor).strip().upper()
        for i, o in enumerate(opts):
            if (o.get('key') or '').strip().upper() == cor_u:
                return cor_u, f'opt_{i + 1}'
        return cor_u if cor_u else None, None
    return None, None


def _build_blueprint_pdf_json(answer_key, seed=None, grouped_shuffle=True, shuffle_mc_options=True):
    """Build attempt blueprint for PDF/JSON: stable option ids opt_1..opt_n, optional shuffled display order.

    PDF exams: pass grouped_shuffle=False and shuffle_mc_options=False so questions and A/B/C… order
    match the printed answer key (no shuffling). JSON exams keep grouped shuffle + per-question option shuffle.
    """
    rng = _rng_for_attempt(seed)
    if not isinstance(answer_key, dict):
        return []
    questions_raw = answer_key.get('questions') or []
    if not isinstance(questions_raw, list):
        return []
    # Only process dict items to avoid AttributeError on bad data
    questions_raw = [q for q in questions_raw if isinstance(q, dict)]
    if grouped_shuffle:
        kind_order = {'mc': 0, 'open': 1, 'situation': 2}
        sorted_q = sorted(questions_raw, key=lambda q: (kind_order.get((q.get('kind') or '').lower(), 99), q.get('number', 0)))
        mc_q = [q for q in sorted_q if (q.get('kind') or 'mc').lower() == 'mc']
        open_q = [q for q in sorted_q if (q.get('kind') or '').lower() == 'open']
        sit_q = [q for q in sorted_q if (q.get('kind') or '').lower() == 'situation']
        rng.shuffle(mc_q)
        rng.shuffle(open_q)
        rng.shuffle(sit_q)
        ordered_q = mc_q + open_q + sit_q
    else:
        # PDF path: keep original sequential question order for printed key alignment.
        ordered_q = sorted(
            questions_raw,
            key=lambda q: (
                1 if q.get('number') is None else 0,
                q.get('number') if q.get('number') is not None else 0,
            ),
        )

    blueprint = []
    for q in ordered_q:
        num = q.get('number')
        kind = (q.get('kind') or 'mc').lower()
        if kind == 'mc':
            opts = list(q.get('options') or [])
            opts = [o for o in opts if isinstance(o, dict)]  # only dict options
            if opts:
                correct_key_resolved, correct_stable_id = _pdf_mc_resolve_correct_key_and_opt_id(q)
                # Assign stable id per option (by original order), then shuffle display order
                opts_with_id = [{'id': f'opt_{i+1}', 'key': (o.get('key') or '').strip().upper(), 'text': o.get('text', '')} for i, o in enumerate(opts)]
                key_to_id = {o['key'] or o['id']: o['id'] for o in opts_with_id}
                if shuffle_mc_options:
                    rng.shuffle(opts_with_id)
                options_blueprint = [{'id': o['id'], 'text': o['text']} for o in opts_with_id]
                correctOptionId = None
                if correct_stable_id and any(o['id'] == correct_stable_id for o in opts_with_id):
                    correctOptionId = correct_stable_id
                elif correct_key_resolved:
                    correctOptionId = key_to_id.get(correct_key_resolved)
                blueprint.append({'questionNumber': num, 'kind': kind, 'options': options_blueprint, 'correctOptionId': correctOptionId})
            else:
                blueprint.append({'questionNumber': num, 'kind': kind, 'options': [], 'correctOptionId': None})
        else:
            entry = {'questionNumber': num, 'kind': kind, 'options': [], 'correctOptionId': None}
            if kind == 'open':
                entry['open_rule'] = (q.get('open_rule') or 'EXACT_MATCH').strip().upper()
                if entry['open_rule'] == 'MATCHING':
                    entry['matching_left'] = q.get('matching_left') if isinstance(q.get('matching_left'), list) else ['1', '2', '3']
                    entry['matching_right'] = q.get('matching_right') if isinstance(q.get('matching_right'), list) else ['a', 'b', 'c', 'd', 'e']
            blueprint.append(entry)
    return blueprint


def _get_units_from_blueprint(blueprint):
    """
    Universal dynamic scoring units: regular (closed/open) = 1 unit each, situation = 2 units each.
    Total_Units = (closed + open) + (2 * situation). Unit_Value X = Max_Score / Total_Units.
    Returns (count_standard, count_situation, total_units).
    """
    if not blueprint:
        return 0, 0, Decimal('0')
    count_standard = 0
    count_situation = 0
    for item in blueprint:
        kind = (item.get('kind') or 'mc').strip().lower()
        if kind == 'situation':
            count_situation += 1
        else:
            count_standard += 1
    total_units = count_standard + (count_situation * 2)
    return count_standard, count_situation, Decimal(str(total_units))


def _get_x_value(max_score, total_units):
    """Unit value X = max_score / total_units (Decimal, 4 decimal places). Used for dynamic scoring."""
    if total_units <= 0:
        return Decimal('0')
    return (Decimal(str(max_score)) / total_units).quantize(Decimal('0.0001'))


def _situation_teacher_value_to_points(teacher_value, x_value):
    """
    Teacher assigns from [0, 2/3, 1, 4/3, 2]. Score = teacher_value * X.
    Accepts int/float/str for teacher_value.
    """
    if x_value is None or x_value == 0:
        return Decimal('0')
    val = teacher_value
    if val in (0, '0', None):
        return Decimal('0')
    if val in (2/3, '2/3', 0.667, '0.667'):
        return (Decimal('2') / Decimal('3') * x_value).quantize(Decimal('0.01'))
    if val in (1, '1'):
        return x_value.quantize(Decimal('0.01'))
    if val in (4/3, '4/3', 1.333, '1.333'):
        return (Decimal('4') / Decimal('3') * x_value).quantize(Decimal('0.01'))
    if val in (2, '2'):
        return (Decimal('2') * x_value).quantize(Decimal('0.01'))
    try:
        return (Decimal(str(val)) * x_value).quantize(Decimal('0.01'))
    except Exception:
        return Decimal('0')


def _ordered_situation_answers_for_grading(attempt):
    """
    Manual answers that are SITUATION only, ordered by question_number.
    Indices in per_situation_scores (1-based) must match this list — NOT all requires_manual_check
    rows (open-ended manual answers must be excluded).
    """
    bp_by_num = {}
    for item in (attempt.attempt_blueprint or []):
        num = item.get('questionNumber') or item.get('number')
        if num is not None:
            try:
                bp_by_num[int(num)] = item
            except (TypeError, ValueError):
                bp_by_num[num] = item

    out = []
    qs = (
        attempt.answers.filter(requires_manual_check=True)
        .select_related('question')
        .order_by('question_number')
    )
    for a in qs:
        q = getattr(a, 'question', None)
        if q is not None and getattr(q, 'type', None) == 'SITUATION':
            out.append(a)
            continue
        num = a.question_number
        bp = None
        if num is not None:
            try:
                bp = bp_by_num.get(int(num))
            except (TypeError, ValueError):
                bp = bp_by_num.get(num)
        kind = ''
        if isinstance(bp, dict):
            kind = str(bp.get('kind') or bp.get('type') or bp.get('qtype') or '').lower()
        if kind in ('situation', 'sit'):
            out.append(a)
    return out


def _notify_exam_result_published_for_attempt(attempt, requesting_user):
    """
    Create/update student notification with latest total_score (after publish).
    Parents see the same feed via student profile linkage.
    """
    from notifications.services import notify_exam_result_published

    attempt.refresh_from_db()
    exam = attempt.exam
    max_s = float(exam.max_score or (100 if exam.type == 'quiz' else 150))
    score = float(attempt.total_score) if attempt.total_score is not None else (
        float(attempt.auto_score or 0) + float(attempt.manual_score or 0)
    )
    score = max(0.0, min(score, max_s))
    try:
        sp = attempt.student.student_profile
    except Exception:
        sp = None
    group = None
    if attempt.exam_run_id:
        try:
            er = ExamRun.objects.select_related('group').filter(pk=attempt.exam_run_id).first()
            if er and er.group_id:
                group = er.group
        except Exception:
            group = None
    if sp:
        notify_exam_result_published(attempt, score=score, group=group)


def _enrich_bank_blueprint_mc_options(request, exam, blueprint):
    """Mutates blueprint list: for BANK exams, attach mcOptionDisplay and per-option imageUrl/label from DB."""
    if getattr(exam, 'source_type', None) != 'BANK' or not blueprint:
        return blueprint
    q_ids = []
    for item in blueprint:
        if isinstance(item, dict) and item.get('kind') == 'mc' and item.get('questionId'):
            q_ids.append(item['questionId'])
    if not q_ids:
        return blueprint
    questions = Question.objects.filter(pk__in=q_ids).prefetch_related('options')
    q_map = {q.id: q for q in questions}
    for item in blueprint:
        if not isinstance(item, dict) or item.get('kind') != 'mc':
            continue
        qid = item.get('questionId')
        q = q_map.get(qid)
        if not q:
            continue
        item['mcOptionDisplay'] = (getattr(q, 'mc_option_display', None) or 'TEXT').upper()
        opts_by_id = {str(o.id): o for o in q.options.all()}
        for opt in item.get('options') or []:
            oid = str(opt.get('id') or '')
            o = opts_by_id.get(oid)
            if o is None:
                continue
            if o.image:
                opt['imageUrl'] = request.build_absolute_uri(o.image.url)
            if getattr(o, 'label', None):
                opt['label'] = o.label or ''
    return blueprint


def _questions_data_from_blueprint(blueprint):
    """Student-facing: from blueprint return list with only id and text for options (no correctOptionId). Include open_rule and matching for open/MATCHING."""
    out = []
    for item in blueprint:
        if not isinstance(item, dict):
            continue
        qno = item.get('questionNumber') or item.get('questionId')
        kind = item.get('kind', 'mc')
        kind_l = (kind or 'mc').lower()
        if kind_l == 'mc':
            qtype = 'closed'
        elif kind_l == 'open':
            qtype = 'open'
        elif kind_l == 'situation':
            qtype = 'situation'
        else:
            qtype = kind_l
        opts = item.get('options') or []
        opt_rows = []
        for o in opts:
            if not isinstance(o, dict):
                continue
            opt_rows.append({
                'id': o.get('id'),
                'text': o.get('text', ''),
                'imageUrl': o.get('imageUrl'),
                'label': o.get('label'),
            })
        row = {
            'questionNumber': qno,
            'questionId': item.get('questionId'),
            'number': qno,
            'kind': kind,
            'type': kind,
            'qtype': qtype,
            'mcOptionDisplay': (item.get('mcOptionDisplay') or 'TEXT').upper(),
            'options': opt_rows,
        }
        if kind == 'open':
            row['open_rule'] = item.get('open_rule') or 'EXACT_MATCH'
            if row['open_rule'] == 'MATCHING':
                row['matching_left'] = item.get('matching_left') or ['1', '2', '3']
                row['matching_right'] = item.get('matching_right') or ['a', 'b', 'c', 'd', 'e']
        out.append(row)
    return out


def _build_pdf_json_questions(answer_key, request, shuffle_options=True):
    """Build questions list from answer_key_json: order mc, open, situation. Shuffle MC options. (Legacy helper.)"""
    import random
    questions_raw = answer_key.get('questions') or []
    kind_order = {'mc': 0, 'open': 1, 'situation': 2}
    sorted_q = sorted(questions_raw, key=lambda q: (kind_order.get((q.get('kind') or '').lower(), 99), q.get('number', 0)))
    out = []
    for q in sorted_q:
        num = q.get('number')
        kind = (q.get('kind') or 'mc').lower()
        prompt = q.get('prompt') or ''
        item = {'questionNumber': num, 'number': num, 'kind': kind, 'type': kind, 'prompt': prompt, 'text': prompt}
        if kind == 'mc':
            opts = list(q.get('options') or [])
            if shuffle_options and opts:
                random.shuffle(opts)
            item['options'] = [{'key': o.get('key', ''), 'text': o.get('text', '')} for o in opts]
        elif kind == 'open':
            item['options'] = []
        elif kind == 'situation':
            item['options'] = []
        out.append(item)
    return out


# ---------- Student: Start by RUN (creates attempt linked to run; returns questions per source type) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_run_start_view(request, run_id):
    """
    POST /api/student/runs/{runId}/start
    Creates attempt for this run and returns source-agnostic question payload.
    PDF exams additionally return pdfUrl; JSON/BANK exams are question-only.
    """
    from groups.models import GroupStudent
    from students.models import StudentProfile
    from django.db.models import Q
    import random
    now = _now()
    try:
        run = ExamRun.objects.select_related('exam', 'exam__pdf_document').get(pk=run_id)
    except ExamRun.DoesNotExist:
        logger.warning("student_run_start run_id=%s user_id=%s run_not_found", run_id, getattr(request.user, 'id', None))
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    if getattr(run.exam, 'is_deleted', False) or getattr(run.exam, 'status', None) == 'deleted':
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    # Cheating lock should not block other students in a group run.
    if run.is_cheating_detected and run.student_id is not None and run.student_id != request.user.id:
        logger.warning(
            "student_run_start run_id=%s exam_id=%s user_id=%s cheating_detected_lock",
            run_id, run.exam_id, getattr(request.user, 'id', None)
        )
        return Response({'detail': 'Run locked: cheating detected'}, status=status.HTTP_403_FORBIDDEN)
    if run.status == 'published' or run.published:
        return Response({'detail': 'Run is published and locked'}, status=status.HTTP_403_FORBIDDEN)
    if run.status == 'suspended':
        return Response({'detail': 'Run is suspended. Wait for teacher approval.'}, status=status.HTTP_403_FORBIDDEN)
    if run.status != 'active' or run.start_at > now or run.end_at < now:
        logger.warning("student_run_start run_id=%s exam_id=%s user_id=%s run_not_active", run_id, run.exam_id, getattr(request.user, 'id', None))
        return Response({'detail': 'Run is not active'}, status=status.HTTP_403_FORBIDDEN)
    try:
        sp = request.user.student_profile
    except Exception:
        sp = None
    if not sp:
        logger.warning("student_run_start run_id=%s user_id=%s no_student_profile", run_id, getattr(request.user, 'id', None))
        return Response({'detail': 'Student profile required'}, status=status.HTTP_403_FORBIDDEN)
    if not _student_has_run_access(run, request.user):
        # Self-heal legacy/custom runs: if student has active assignment for this exam, link to run and continue.
        if run.group_id is None and run.student_id is None and ExamStudentAssignment.objects.filter(
            exam_id=run.exam_id,
            student_id=request.user.id,
            is_active=True,
        ).exists():
            try:
                ExamRunStudent.objects.get_or_create(run=run, student=request.user)
            except Exception:
                pass
        if not _student_has_run_access(run, request.user):
            logger.warning("student_run_start run_id=%s exam_id=%s user_id=%s no_access", run_id, run.exam_id, getattr(request.user, 'id', None))
            return Response({'detail': 'You do not have access to this run'}, status=status.HTTP_403_FORBIDDEN)

    exam = run.exam
    try:
        ensure_json_exam_migrated_to_bank(exam)
        exam = Exam.objects.get(pk=exam.pk)
    except Exception:
        logger.exception('ensure_json_exam_migrated_to_bank run_id=%s exam_id=%s', run_id, getattr(run, 'exam_id', None))
    try:
        existing = ExamAttempt.objects.filter(exam_run=run, student=request.user).exclude(status='RESTARTED').order_by('-started_at').first()
        attempt = None
        if existing:
            if existing.finished_at:
                logger.warning("student_run_start run_id=%s attempt_id=%s user_id=%s already_submitted", run_id, existing.id, getattr(request.user, 'id', None))
                return Response({'detail': 'Already submitted', 'attemptId': existing.id, 'status': 'SUBMITTED'}, status=status.HTTP_400_BAD_REQUEST)
            if existing.expires_at and now > existing.expires_at:
                existing.status = 'EXPIRED'
                existing.save(update_fields=['status'])
                return Response({'attemptId': existing.id, 'status': 'EXPIRED', 'questions': [], 'canvases': []})
            attempt = existing
        else:
            expires_at = run.end_at
            attempt = ExamAttempt.objects.create(
                exam=exam,
                exam_run=run,
                student=request.user,
                expires_at=expires_at,
                duration_minutes=run.duration_minutes,
                status='IN_PROGRESS',
            )

        # Ensure attempt has a frozen blueprint (build once per attempt; never expose correctOptionId to student)
        if not attempt.attempt_blueprint:
            if exam.source_type == 'BANK':
                attempt.attempt_blueprint = _build_blueprint_bank(exam, seed=attempt.pk)
            else:
                answer_key = exam.answer_key_json if isinstance(exam.answer_key_json, dict) else {}
                attempt.attempt_blueprint = _build_blueprint_pdf_json(
                    answer_key,
                    seed=attempt.pk,
                    grouped_shuffle=(exam.source_type != 'PDF'),
                    shuffle_mc_options=(exam.source_type != 'PDF'),
                )
            # Save question order and option order for grading accuracy
            question_order = []
            option_order = {}
            for item in (attempt.attempt_blueprint or []):
                if not isinstance(item, dict):
                    continue
                qno = item.get('questionNumber') or item.get('questionId')
                if qno is not None:
                    question_order.append(qno)
                    opts = [o for o in (item.get('options') or []) if isinstance(o, dict)]
                    if opts:
                        option_order[str(qno)] = [o.get('id') for o in opts]
            attempt.question_order = question_order
            attempt.option_order = option_order
            attempt.shuffled_question_order = _shuffled_question_order_from_blueprint(attempt.attempt_blueprint)
            attempt.save(update_fields=['attempt_blueprint', 'question_order', 'option_order', 'shuffled_question_order'])

        # Wall-clock end always matches the run (server truth for countdown).
        if attempt.expires_at != run.end_at:
            attempt.expires_at = run.end_at
            attempt.save(update_fields=['expires_at'])

        # Build response from blueprint: only qno, kind, options (id + text) — never answer_key_json or correct
        bp_for_student = copy.deepcopy(attempt.attempt_blueprint or [])
        _enrich_bank_blueprint_mc_options(request, exam, bp_for_student)
        questions_data = _questions_data_from_blueprint(bp_for_student)
        if exam.answer_key_json and isinstance(exam.answer_key_json, dict):
            ak_list = [q for q in (exam.answer_key_json.get('questions') or []) if isinstance(q, dict)]
            ak_questions = {q.get('number'): q for q in ak_list}
            for item in questions_data:
                num = item.get('questionNumber') or item.get('number')
                ak = ak_questions.get(num) if num is not None else None
                if ak:
                    prompt = ak.get('prompt') or ak.get('text') or ''
                    item['text'] = item.get('text') or prompt
                    item['prompt'] = prompt
        # Add examQuestionId for BANK where needed (for backward compat) — match by questionId, not blueprint index
        if exam.source_type == 'BANK' and attempt.attempt_blueprint:
            eq_list = list(ExamQuestion.objects.filter(exam=exam).select_related('question').prefetch_related('question__options'))
            eq_by_question_id = {eq.question_id: eq for eq in eq_list}
            for item in questions_data:
                qid = item.get('questionId')
                if qid is None:
                    continue
                eq = eq_by_question_id.get(qid)
                if not eq:
                    continue
                item['examQuestionId'] = eq.id
                item['order'] = eq.order
                q = eq.question
                item['text'] = q.text
                if getattr(q, 'question_image', None) and q.question_image:
                    item['questionImageUrl'] = request.build_absolute_uri(q.question_image.url)
                if item.get('kind') == 'open' and q:
                    item['open_rule'] = (q.answer_rule_type or 'EXACT_MATCH').strip().upper()
                    if item['open_rule'] == 'MATCHING' and isinstance(getattr(q, 'correct_answer', None), dict) and q.correct_answer:
                        item['matching_left'] = [str(k) for k in q.correct_answer.keys()]
                        item['matching_right'] = sorted(set(str(v).lower() for v in q.correct_answer.values())) or ['a', 'b', 'c', 'd', 'e']
                    elif item['open_rule'] == 'MATCHING':
                        item['matching_left'] = item.get('matching_left') or ['1', '2', '3']
                        item['matching_right'] = item.get('matching_right') or ['a', 'b', 'c', 'd', 'e']

        canvases_data = []
        for c in ExamAttemptCanvas.objects.filter(attempt=attempt).order_by('situation_index', 'page_index', 'question_id'):
            rec = _build_canvas_response(c, request, include_canvas_json=True) or {
                'canvasId': c.id,
                'questionId': c.question_id,
                'situationIndex': c.situation_index,
                'updatedAt': c.updated_at.isoformat(),
                'imageUrl': request.build_absolute_uri(c.image.url) if c.image and request else (c.image.url if c.image else None),
            }
            if getattr(c, 'page_index', 0) is not None:
                rec['pageIndex'] = getattr(c, 'page_index', 0)
            canvases_data.append(rec)

        # PDF exam: generate image pages once (cached under media/exam_pages/run_{id}/) and expose page list via /pages
        pdf_url = None
        pdf_scribbles = []
        if exam.source_type == 'PDF' and (exam.pdf_document and exam.pdf_document.file or exam.pdf_file):
            gen = _ensure_run_pdf_images(run, request)
            if isinstance(gen, dict) and gen.get('success') is False:
                return Response(gen, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            pdf_url = request.build_absolute_uri(f'/api/student/runs/{run.id}/pages') if request else f'/api/student/runs/{run.id}/pages'
            for s in PdfScribble.objects.filter(attempt=attempt).order_by('page_index'):
                pdf_scribbles.append({'pageIndex': s.page_index, 'drawingData': s.drawing_data or {}})

        saved_rows = _saved_answers_payload_for_attempt(attempt)
        resume_idx = _resume_question_index_from_saved(questions_data, saved_rows)

        return Response({
            'attemptId': attempt.id,
            'examId': exam.id,
            'runId': run.id,
            'title': exam.title,
            'type': exam.type,
            'status': attempt.status,
            'sourceType': exam.source_type,
            'pdfUrl': pdf_url,
            'pdfScribbles': pdf_scribbles if pdf_scribbles else None,
            'startedAt': attempt.started_at.isoformat(),
            'expiresAt': attempt.expires_at.isoformat() if attempt.expires_at else None,
            'endTime': run.end_at.isoformat(),
            'globalEndAt': run.end_at.isoformat(),
            'serverNow': now.isoformat(),
            'sessionRevision': getattr(attempt, 'session_revision', 0),
            'savedAnswers': saved_rows,
            'resumeQuestionIndex': resume_idx,
            'questions': questions_data,
            'canvases': canvases_data,
        })
    except Exception as e:
        import traceback
        logger.exception(
            "student_run_start error run_id=%s exam_id=%s user_id=%s: %s",
            run_id, run.exam_id, getattr(request.user, 'id', None), e
        )
        return Response(
            {
                "detail": "Could not start exam",
                "reason": str(e),
                "exception_type": type(e).__name__,
                "traceback": traceback.format_exc(),
                "runId": run_id,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ---------- Student: Start exam (create attempt, return questions; do NOT send correct_answer) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_start_view(request, exam_id):
    from datetime import timedelta
    try:
        exam = Exam.objects.get(pk=exam_id)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    if getattr(exam, 'is_deleted', False) or exam.status == 'deleted':
        return Response({'detail': 'Exam is not available'}, status=status.HTTP_403_FORBIDDEN)
    try:
        ensure_json_exam_migrated_to_bank(exam)
        exam = Exam.objects.get(pk=exam_id)
    except Exception:
        logger.exception('ensure_json_exam_migrated_to_bank exam_id=%s', exam_id)
    now = _now()
    if exam.status != 'active':
        return Response({'detail': 'Exam is not available'}, status=status.HTTP_403_FORBIDDEN)
    _, _, duration_minutes = _get_student_assignment_context(exam, request.user)
    if duration_minutes is None:
        return Response({'detail': 'Exam is not available for you at this time'}, status=status.HTTP_403_FORBIDDEN)

    ge = _exam_global_end(exam)
    
    existing = ExamAttempt.objects.filter(exam=exam, student=request.user).order_by('-started_at').first()
    attempt = None
    # RESTARTED attempts are ignored - teacher allowed new attempt
    if existing and existing.status == 'RESTARTED':
        existing = None
    
    if existing:
        if existing.finished_at is not None:
            return Response({
                'detail': 'İmtahan yoxlaması söndürülüb',
                'code': 'EXAM_REVIEW_DISABLED',
                'attemptId': existing.id,
                'status': 'SUBMITTED',
            }, status=status.HTTP_400_BAD_REQUEST)
        if existing.expires_at and now > existing.expires_at:
            existing.status = 'EXPIRED'
            existing.save(update_fields=['status'])
            return Response({
                'attemptId': existing.id,
                'examId': exam.id,
                'title': exam.title,
                'status': 'EXPIRED',
                'expiresAt': existing.expires_at.isoformat() if existing.expires_at else None,
                'questions': [],
                'canvases': [],
            })
        if existing.status == 'IN_PROGRESS' and existing.expires_at and now <= existing.expires_at:
            attempt = existing
    else:
        expires_at = ge if (ge and ge > now) else (now + timedelta(minutes=int(duration_minutes)))
        attempt = ExamAttempt.objects.create(
            exam=exam,
            student=request.user,
            expires_at=expires_at,
            duration_minutes=int(duration_minutes),
            status='IN_PROGRESS',
        )

    if attempt is not None and ge and attempt.finished_at is None:
        if attempt.expires_at != ge:
            attempt.expires_at = ge
            attempt.save(update_fields=['expires_at'])
    
    eqs = ExamQuestion.objects.filter(exam=exam).select_related('question').prefetch_related('question__options').order_by('order')
    questions_data = []
    for i, eq in enumerate(eqs):
        q = eq.question
        options = list(q.options.order_by('order').values('id', 'text', 'order'))
        rec = {
            'examQuestionId': eq.id,
            'questionId': q.id,
            'questionNumber': i + 1,
            'order': eq.order,
            'text': q.text,
            'type': q.type,
            'options': options,
        }
        if getattr(q, 'question_image', None) and q.question_image:
            rec['questionImageUrl'] = request.build_absolute_uri(q.question_image.url)
        questions_data.append(rec)
    canvases_data = []
    for c in ExamAttemptCanvas.objects.filter(attempt=attempt).select_related('question'):
        canvases_data.append(_build_canvas_response(c, request, include_canvas_json=True))
    saved_rows = _saved_answers_payload_for_attempt(attempt)
    resume_idx = _resume_question_index_from_saved(questions_data, saved_rows)
    global_end_iso = ge.isoformat() if ge else (attempt.expires_at.isoformat() if attempt.expires_at else None)
    return Response({
        'attemptId': attempt.id,
        'examId': exam.id,
        'title': exam.title,
        'type': exam.type,
        'status': attempt.status,
        'startedAt': attempt.started_at.isoformat(),
        'expiresAt': attempt.expires_at.isoformat() if attempt.expires_at else None,
        'endTime': (attempt.expires_at.isoformat() if attempt.expires_at else
                    (exam.start_time + timedelta(minutes=exam.duration_minutes or 60)).isoformat() if exam.start_time and exam.duration_minutes else None),
        'globalEndAt': global_end_iso,
        'serverNow': now.isoformat(),
        'sessionRevision': getattr(attempt, 'session_revision', 0),
        'savedAnswers': saved_rows,
        'resumeQuestionIndex': resume_idx,
        'questions': questions_data,
        'canvases': canvases_data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_attempt_sync_view(request, attempt_id):
    """Server clock + expiry + session revision for countdown sync and teacher restart detection."""
    now = _now()
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'exam_run').get(pk=int(attempt_id), student=request.user)
    except (ExamAttempt.DoesNotExist, TypeError, ValueError):
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    _ex = attempt.exam
    if getattr(_ex, 'is_deleted', False) or getattr(_ex, 'status', None) == 'deleted':
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    global_end = None
    if attempt.exam_run_id:
        global_end = attempt.exam_run.end_at
    else:
        global_end = _exam_global_end(attempt.exam)
    expires = attempt.expires_at or global_end
    return Response({
        'attemptId': attempt.id,
        'serverNow': now.isoformat(),
        'expiresAt': expires.isoformat() if expires else None,
        'globalEndAt': global_end.isoformat() if global_end else None,
        'sessionRevision': getattr(attempt, 'session_revision', 0),
        'status': attempt.status,
        'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_attempt_state_view(request, attempt_id):
    """
    GET /api/student/exams/attempts/{attemptId}/state
    Current attempt snapshot for PDF exam hydration (resume / refresh) without creating a new attempt.
    """
    now = _now()
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'exam_run').get(pk=int(attempt_id), student=request.user)
    except (ExamAttempt.DoesNotExist, TypeError, ValueError):
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    _ex = attempt.exam
    if getattr(_ex, 'is_deleted', False) or getattr(_ex, 'status', None) == 'deleted':
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.exam_run_id and not _student_has_run_access(attempt.exam_run, request.user):
        return Response({'detail': 'You do not have access to this attempt'}, status=status.HTTP_403_FORBIDDEN)
    if attempt.finished_at is not None or attempt.status == 'SUBMITTED':
        return Response({
            'attemptId': attempt.id,
            'status': attempt.status,
            'submitted': True,
            'savedAnswers': [],
            'scratchpadData': [],
            'scratchpad_data': [],
        })
    global_end = None
    if attempt.exam_run_id:
        global_end = attempt.exam_run.end_at
    else:
        global_end = _exam_global_end(attempt.exam)
    expires = attempt.expires_at or global_end
    _scrib_rows = list(
        PdfScribble.objects.filter(attempt=attempt).order_by('page_index').values('page_index', 'drawing_data')
    )
    _scratchpad_list = [
        {'pageIndex': s['page_index'], 'drawingData': s['drawing_data'] or {}} for s in _scrib_rows
    ]
    return Response({
        'attemptId': attempt.id,
        'examId': attempt.exam_id,
        'runId': attempt.exam_run_id,
        'status': attempt.status,
        'submitted': False,
        'serverNow': now.isoformat(),
        'expiresAt': expires.isoformat() if expires else None,
        'sessionRevision': getattr(attempt, 'session_revision', 0),
        'savedAnswers': _saved_answers_payload_for_attempt(attempt),
        'scratchpadData': _scratchpad_list,
        'scratchpad_data': _scratchpad_list,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_attempt_draft_answers_view(request, attempt_id):
    """
    POST /api/student/exams/attempts/{attemptId}/draft-answers
    Body: { answers: [...] } — optimistic auto-save for PDF bubble sheet (does not suspend run).
    """
    try:
        attempt = ExamAttempt.objects.select_related('exam_run').get(pk=int(attempt_id), student=request.user)
    except (ExamAttempt.DoesNotExist, TypeError, ValueError):
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.exam_run_id and not _student_has_run_access(attempt.exam_run, request.user):
        return Response({'detail': 'You do not have access to this attempt'}, status=status.HTTP_403_FORBIDDEN)
    if attempt.finished_at is not None:
        return Response({'detail': 'Already submitted'}, status=status.HTTP_400_BAD_REQUEST)
    answers_payload = _coerce_student_answers_payload_to_internal_rows(
        request.data.get('answers') or request.data.get('answers_list') or []
    )
    try:
        with transaction.atomic():
            _persist_student_attempt_draft_answers(attempt, answers_payload)
    except Exception as e:
        logger.exception('student_exam_attempt_draft_answers attempt_id=%s user_id=%s: %s', attempt_id, getattr(request.user, 'id', None), e)
        return Response({'detail': 'Could not save draft'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({'ok': True, 'attemptId': attempt.id})


def _finalize_exam_attempt_submission(
    attempt,
    exam,
    answers_payload,
    now,
    *,
    cheating_detected: bool = False,
    close_run: bool = True,
):
    """
    Grade answers_payload into ExamAnswer rows; mark attempt SUBMITTED with auto_score.
    close_run: if False, do not set ExamRun to finished (e.g. teacher bulk auto-submit for group run).
    """
    answers_by_question_id = {}
    answers_by_question_number = {}
    for a in answers_payload or []:
        if not a:
            continue
        qid = a.get('questionId') or a.get('question_id')
        qnum = a.get('questionNumber') or a.get('question_number')
        if qid is not None:
            try:
                answers_by_question_id[int(qid)] = a
            except (TypeError, ValueError):
                pass
        if qnum is not None:
            try:
                answers_by_question_number[int(qnum)] = a
            except (TypeError, ValueError):
                pass

    is_quiz = exam.type == 'quiz'
    max_score = Decimal(str(exam.max_score or (100 if is_quiz else 150)))
    blueprint = attempt.attempt_blueprint or []
    if exam.source_type != 'BANK' and not blueprint and exam.answer_key_json and isinstance(exam.answer_key_json, dict):
        blueprint = _build_blueprint_pdf_json(
            exam.answer_key_json,
            seed=attempt.pk,
            grouped_shuffle=(exam.source_type != 'PDF'),
            shuffle_mc_options=(exam.source_type != 'PDF'),
        )
    elif exam.source_type == 'BANK' and not blueprint:
        blueprint = _build_blueprint_bank(exam, seed=attempt.pk)
    count_standard, count_situation, total_units = _get_units_from_blueprint(blueprint)
    x_value = _get_x_value(max_score, total_units) if total_units > 0 else Decimal('0')
    pts_per_auto = x_value
    pts_mcq_wrong = Decimal('0') if is_quiz else (-x_value * Decimal('0.25'))

    total_score = Decimal('0')
    mcq_sum = Decimal('0')

    with transaction.atomic():
        ExamAnswer.objects.filter(attempt=attempt).delete()
        if not blueprint and exam.source_type != 'BANK' and (exam.answer_key_json and isinstance(exam.answer_key_json, dict)):
            blueprint = _build_blueprint_pdf_json(
                exam.answer_key_json,
                seed=attempt.pk,
                grouped_shuffle=(exam.source_type != 'PDF'),
                shuffle_mc_options=(exam.source_type != 'PDF'),
            )
        if not blueprint and exam.source_type == 'BANK':
            blueprint = _build_blueprint_bank(exam, seed=attempt.pk)
        if exam.source_type != 'BANK' and (blueprint or (exam.answer_key_json and isinstance(exam.answer_key_json, dict))):
            if blueprint:
                for item in blueprint:
                    num = item.get('questionNumber')
                    kind = (item.get('kind') or 'mc').lower()
                    ans = answers_by_question_number.get(num) or answers_by_question_number.get(int(num) if num is not None else None) or {}
                    selected_id = (ans.get('selectedOptionId') or ans.get('selected_option_id'))
                    if selected_id is not None:
                        selected_id = str(selected_id).strip()
                    selected_key = (ans.get('selectedOptionKey') or ans.get('selected_option_key') or '').strip().upper()
                    if not selected_key and selected_id and kind == 'mc':
                        ak_q = next((x for x in ((exam.answer_key_json or {}).get('questions') or []) if x.get('number') == num), None)
                        if ak_q:
                            for i, o in enumerate(ak_q.get('options') or []):
                                oid = o.get('id') or f'opt_{i + 1}'
                                if str(oid) == str(selected_id):
                                    selected_key = (str(o.get('key') or '')).strip().upper()
                                    break
                    text_answer = (ans.get('textAnswer') or ans.get('text_answer') or '').strip()
                    requires_manual = False
                    auto_score = Decimal('0')
                    if kind == 'mc':
                        correct_option_id = item.get('correctOptionId')
                        match_mcq = False
                        if correct_option_id and selected_id and str(selected_id).strip() == str(correct_option_id).strip():
                            match_mcq = True
                        if not match_mcq and selected_key:
                            ak_q = None
                            if exam.answer_key_json and isinstance(exam.answer_key_json, dict):
                                for qx in (exam.answer_key_json.get('questions') or []):
                                    if not isinstance(qx, dict):
                                        continue
                                    try:
                                        if int(qx.get('number')) == int(num):
                                            ak_q = qx
                                            break
                                    except (TypeError, ValueError):
                                        if qx.get('number') == num:
                                            ak_q = qx
                                            break
                            ck_res, _oid = _pdf_mc_resolve_correct_key_and_opt_id(ak_q or {})
                            if ck_res and selected_key == ck_res:
                                match_mcq = True
                        if match_mcq:
                            auto_score = pts_per_auto
                        elif not is_quiz and (selected_id or selected_key):
                            auto_score = max(pts_mcq_wrong, -total_score)
                    elif kind == 'open':
                        ak = exam.answer_key_json or {}
                        q_def = next((x for x in (ak.get('questions') or []) if x.get('number') == num), {})
                        rule = (q_def.get('open_rule') or 'EXACT_MATCH').strip().upper()
                        open_ans = q_def.get('open_answer')
                        if open_ans is not None and rule and evaluate_open_single_value(text_answer, open_ans, rule):
                            auto_score = pts_per_auto
                    else:
                        requires_manual = True
                    total_score += auto_score
                    if kind == 'mc':
                        mcq_sum += auto_score
                    ExamAnswer.objects.create(
                        attempt=attempt,
                        question=None,
                        question_number=num,
                        selected_option_key=selected_key or None,
                        text_answer=text_answer or None,
                        auto_score=auto_score,
                        requires_manual_check=requires_manual,
                    )
            else:
                questions_list = exam.answer_key_json.get('questions') or []
                kind_order = {'mc': 0, 'open': 1, 'situation': 2}
                sorted_q = sorted(questions_list, key=lambda q: (kind_order.get((q.get('kind') or '').lower(), 99), q.get('number', 0)))
                for q_def in sorted_q:
                    num = q_def.get('number')
                    kind = (q_def.get('kind') or 'mc').lower()
                    ans = answers_by_question_number.get(num) or answers_by_question_number.get(int(num) if num is not None else None) or {}
                    selected_id = ans.get('selectedOptionId') or ans.get('selected_option_id')
                    if selected_id is not None:
                        selected_id = str(selected_id).strip()
                    selected_key = (ans.get('selectedOptionKey') or ans.get('selected_option_key') or '').strip().upper()
                    if not selected_key and selected_id and kind == 'mc':
                        opts = q_def.get('options') or []
                        for i, o in enumerate(opts):
                            oid = o.get('id') or f'opt_{i + 1}'
                            if str(oid) == str(selected_id):
                                selected_key = (str(o.get('key') or '')).strip().upper()
                                break
                    text_answer = (ans.get('textAnswer') or ans.get('text_answer') or '').strip()
                    requires_manual = False
                    auto_score = Decimal('0')
                    if kind == 'mc':
                        correct_key = (str(q_def.get('correct') or '').strip().upper())
                        if correct_key and selected_key and selected_key == correct_key:
                            auto_score = pts_per_auto
                        elif not is_quiz and selected_key:
                            auto_score = max(pts_mcq_wrong, -total_score)
                    elif kind == 'open':
                        rule = (q_def.get('open_rule') or 'EXACT_MATCH').strip().upper()
                        open_ans = q_def.get('open_answer')
                        if open_ans is not None and rule:
                            if evaluate_open_single_value(text_answer, open_ans, rule):
                                auto_score = pts_per_auto
                    else:
                        requires_manual = True
                    total_score += auto_score
                    if kind == 'mc':
                        mcq_sum += auto_score
                    ExamAnswer.objects.create(
                        attempt=attempt,
                        question=None,
                        question_number=num,
                        selected_option_key=selected_key or None,
                        text_answer=text_answer or None,
                        auto_score=auto_score,
                        requires_manual_check=requires_manual,
                    )
        else:
            eqs = list(ExamQuestion.objects.filter(exam=exam).select_related('question').prefetch_related('question__options').order_by('order'))
            type_order = {'MULTIPLE_CHOICE': 0, 'OPEN_SINGLE_VALUE': 1, 'OPEN_ORDERED': 1, 'OPEN_UNORDERED': 1, 'OPEN_PERMUTATION': 1, 'SITUATION': 2}
            eqs.sort(key=lambda eq: (type_order.get(eq.question.type, 99), eq.order))
            blueprint_by_qid = {}
            if blueprint:
                for b in blueprint:
                    qid = b.get('questionId')
                    if qid is not None:
                        blueprint_by_qid[qid] = b
            for eq in eqs:
                q = eq.question
                ans = answers_by_question_id.get(q.id) or {}
                selected_option_id = ans.get('selectedOptionId') or ans.get('selected_option_id')
                text_answer = (ans.get('textAnswer') or ans.get('text_answer') or '').strip()
                requires_manual = False
                auto_score = Decimal('0')
                if q.type == 'MULTIPLE_CHOICE':
                    correct_id = None
                    bp = blueprint_by_qid.get(q.id)
                    if bp and bp.get('correctOptionId') is not None:
                        correct_id = bp.get('correctOptionId')
                        try:
                            correct_id = int(correct_id)
                        except (TypeError, ValueError):
                            pass
                    if correct_id is None and q.correct_answer is not None:
                        if isinstance(q.correct_answer, dict) and 'option_id' in q.correct_answer:
                            correct_id = q.correct_answer.get('option_id')
                        elif isinstance(q.correct_answer, (int, float)):
                            correct_id = int(q.correct_answer)
                    if correct_id is not None and selected_option_id is not None:
                        if str(selected_option_id).strip() == str(correct_id).strip():
                            auto_score = pts_per_auto
                        elif not is_quiz:
                            auto_score = max(pts_mcq_wrong, -total_score)
                    elif not is_quiz and selected_option_id is not None:
                        auto_score = max(pts_mcq_wrong, -total_score)
                elif q.type in ('OPEN_SINGLE_VALUE', 'OPEN_ORDERED', 'OPEN_UNORDERED', 'OPEN_PERMUTATION'):
                    rule = q.answer_rule_type
                    if not rule and q.type == 'OPEN_ORDERED':
                        rule = 'STRICT_ORDER'
                    elif not rule and q.type == 'OPEN_PERMUTATION':
                        rule = 'ANY_ORDER'
                    elif not rule and q.type == 'OPEN_UNORDERED':
                        rule = 'MATCHING'
                    if rule and q.correct_answer is not None:
                        if evaluate_open_single_value(text_answer, q.correct_answer, rule):
                            auto_score = pts_per_auto
                elif q.type == 'SITUATION':
                    requires_manual = True
                total_score += auto_score
                if q.type == 'MULTIPLE_CHOICE':
                    mcq_sum += auto_score
                ExamAnswer.objects.create(
                    attempt=attempt,
                    question=q,
                    selected_option_id=int(selected_option_id) if selected_option_id is not None else None,
                    text_answer=text_answer or None,
                    auto_score=auto_score,
                    requires_manual_check=requires_manual,
                )
        if mcq_sum < 0:
            total_score -= mcq_sum
        total_score = max(Decimal('0'), total_score)
        attempt.finished_at = now
        attempt.auto_score = total_score
        attempt.total_score = total_score
        attempt.status = 'SUBMITTED'
        attempt.is_visible_to_student = False
        cheating_update_fields = []
        if cheating_detected:
            attempt.is_cheating_detected = True
            attempt.cheating_detected_at = now
            cheating_update_fields = ['is_cheating_detected', 'cheating_detected_at']
        attempt.save(update_fields=['finished_at', 'auto_score', 'total_score', 'status', 'is_visible_to_student', *cheating_update_fields])
        if attempt.exam_run_id:
            run = ExamRun.objects.filter(pk=attempt.exam_run_id).only('id', 'group_id', 'student_id').first()
            run_updates = {}
            # Per-student cheating lives on ExamAttempt. Only set run-level flags for dedicated
            # single-student runs so group exams are never "locked" for everyone.
            if cheating_detected and run and run.student_id is not None and run.student_id == attempt.student_id:
                run_updates['is_cheating_detected'] = True
                run_updates['cheating_detected_at'] = now
            # IMPORTANT: finishing one student's attempt must not finish the whole group run.
            # Only direct single-student runs can be auto-finished on submit.
            if close_run and run and run.group_id is None and (run.student_id is None or run.student_id == attempt.student_id):
                run_updates['status'] = 'finished'
            if run_updates:
                ExamRun.objects.filter(pk=attempt.exam_run_id).update(**run_updates)

    return {
        'attemptId': attempt.id,
        'autoScore': float(total_score),
        'maxScore': float(max_score),
        'finishedAt': attempt.finished_at.isoformat(),
        'cheatingDetected': cheating_detected,
    }


def _bulk_auto_submit_attempts_for_past_run_end(run, now):
    """
    Submit all in-progress attempts with saved draft answers; set run to published; expire non-starters.
    Used when teacher shortens duration so end_at is already past (flash-end).
    """
    exam = run.exam
    qs = ExamAttempt.objects.filter(
        exam_run=run,
        finished_at__isnull=True,
        status='IN_PROGRESS',
    ).select_related('exam', 'student')
    submitted = 0
    for att in qs:
        payload = _saved_answers_payload_for_attempt(att)
        try:
            _finalize_exam_attempt_submission(
                att, exam, payload, now,
                cheating_detected=False,
                close_run=False,
            )
            submitted += 1
        except Exception as e:
            logger.exception(
                '_bulk_auto_submit_attempts_for_past_run_end run_id=%s attempt_id=%s: %s',
                run.id, att.id, e,
            )
    run.status = 'published'
    run.save(update_fields=['status'])
    try:
        _expire_attempts_for_finished_run(run, now)
    except Exception as e:
        logger.exception('_bulk_auto_submit_attempts_for_past_run_end expire run_id=%s: %s', run.id, e)
    return submitted


# ---------- Student: Submit exam (evaluate by option ID for MC; open rules for OPEN_*) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_submit_view(request, exam_id):
    try:
        exam = Exam.objects.get(pk=exam_id)
    except Exam.DoesNotExist:
        logger.warning("student_exam_submit exam_id=%s user_id=%s exam_not_found", exam_id, getattr(request.user, 'id', None))
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    if getattr(exam, 'is_deleted', False) or exam.status == 'deleted':
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    attempt_id = request.data.get('attemptId') or request.data.get('attempt_id')
    answers_payload = _coerce_student_answers_payload_to_internal_rows(
        request.data.get('answers') or request.data.get('answers_list') or []
    )
    if not attempt_id:
        return Response({'detail': 'attemptId required'}, status=status.HTTP_400_BAD_REQUEST)
    client_type = request.data.get('type')
    if client_type is not None and str(client_type).strip() != '':
        if str(client_type).lower() not in ('quiz', 'exam'):
            return Response({'detail': 'type must be "quiz" or "exam"'}, status=status.HTTP_400_BAD_REQUEST)
        if str(client_type).lower() != str(exam.type).lower():
            return Response({'detail': 'Payload type does not match this test'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        attempt = ExamAttempt.objects.select_related('exam').prefetch_related('exam__exam_questions__question__options').get(
            pk=attempt_id, exam=exam, student=request.user
        )
    except ExamAttempt.DoesNotExist:
        logger.warning("student_exam_submit exam_id=%s attempt_id=%s user_id=%s attempt_not_found", exam_id, attempt_id, getattr(request.user, 'id', None))
        return Response({'detail': 'Attempt not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.finished_at is not None:
        logger.warning("student_exam_submit exam_id=%s attempt_id=%s user_id=%s already_submitted", exam_id, attempt.id, getattr(request.user, 'id', None))
        return Response({'detail': 'Already submitted'}, status=status.HTTP_400_BAD_REQUEST)
    now = _now()
    if attempt.expires_at and now > attempt.expires_at:
        attempt.status = 'EXPIRED'
        attempt.save(update_fields=['status'])
        logger.warning("student_exam_submit exam_id=%s attempt_id=%s user_id=%s time_expired", exam_id, attempt.id, getattr(request.user, 'id', None))
        return Response({'detail': 'Time has expired'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        cheating_detected = bool(request.data.get('cheatingDetected') or request.data.get('cheating_detected'))
        result = _finalize_exam_attempt_submission(
            attempt, exam, answers_payload, now,
            cheating_detected=cheating_detected,
            close_run=True,
        )
        return Response(result, status=status.HTTP_200_OK)
    except Exception as e:
        logger.exception(
            "student_exam_submit error exam_id=%s attempt_id=%s user_id=%s: %s",
            exam_id, attempt.id, getattr(request.user, 'id', None), e
        )
        return Response({'detail': 'Could not submit'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Student: Suspend exam run (visibility/tab-out) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_suspend_view(request):
    """
    POST /api/student/exams/suspend
    Body: { runId, attemptId, answers?, canvases? }
    - Best-effort final save of answers and canvas_json
    - ExamRun.status -> suspended
    - ExamRun.suspended_at timestamp recorded
    """
    run_id = request.data.get('runId') or request.data.get('run_id')
    attempt_id = request.data.get('attemptId') or request.data.get('attempt_id')
    if not run_id or not attempt_id:
        return Response({'detail': 'runId and attemptId required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        run = ExamRun.objects.select_related('exam').get(pk=int(run_id))
    except (ExamRun.DoesNotExist, TypeError, ValueError):
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)
    if not _student_has_run_access(run, request.user):
        return Response({'detail': 'You do not have access to this run'}, status=status.HTTP_403_FORBIDDEN)
    try:
        attempt = ExamAttempt.objects.get(pk=int(attempt_id), exam_run=run, student=request.user)
    except (ExamAttempt.DoesNotExist, TypeError, ValueError):
        return Response({'detail': 'Attempt not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.finished_at is not None:
        return Response({'detail': 'Already submitted'}, status=status.HTTP_400_BAD_REQUEST)

    answers_payload = _coerce_student_answers_payload_to_internal_rows(
        request.data.get('answers') or request.data.get('answers_list') or []
    )
    canvases_payload = request.data.get('canvases') or []
    now = _now()
    with transaction.atomic():
        _persist_student_attempt_draft_answers(attempt, answers_payload)

        # Final-save canvases (including canvas_json)
        if isinstance(canvases_payload, list):
            for c in canvases_payload:
                if not isinstance(c, dict):
                    continue
                qid = c.get('questionId') or c.get('question_id')
                sidx = c.get('situationIndex') or c.get('situation_index')
                canvas_json = c.get('canvas_json') or c.get('canvasJson')
                snapshot_b64 = c.get('canvas_snapshot_base64') or c.get('canvasSnapshotBase64') or c.get('imageBase64') or c.get('image_base64')
                if qid is None and sidx is None:
                    continue
                q_obj = None
                if qid is not None:
                    try:
                        q_obj = Question.objects.get(pk=int(qid))
                    except Exception:
                        q_obj = None
                try:
                    sidx_int = int(sidx) if sidx is not None else None
                except Exception:
                    sidx_int = None
                canvas, _ = ExamAttemptCanvas.objects.get_or_create(
                    attempt=attempt,
                    question=q_obj,
                    situation_index=sidx_int,
                    defaults={'page_index': 0},
                )
                if canvas_json is not None:
                    canvas.canvas_json = canvas_json
                if snapshot_b64:
                    m = re.match(r'^data:image/(\w+);base64,(.+)$', str(snapshot_b64))
                    if m:
                        fmt, b64 = m.group(1), m.group(2)
                    else:
                        b64 = str(snapshot_b64)
                        fmt = 'png'
                    try:
                        raw = base64.b64decode(b64)
                        cf = compress_image_bytes(raw, canvas=True)
                        cf.name = f'suspend_q{qid or sidx}_{attempt.id}.jpg'
                        canvas.image.save(cf.name, cf, save=False)
                    except Exception:
                        pass
                canvas.save()

        run.status = 'suspended'
        run.suspended_at = now
        run.is_cheating_detected = True
        run.cheating_detected_at = now
        run.save(update_fields=['status', 'suspended_at', 'is_cheating_detected', 'cheating_detected_at'])

        # Teacher red alert notification for cheating/exit.
        try:
            from notifications.services import notify_exam_suspended
            notify_exam_suspended(run=run, student_user=request.user, happened_at=now)
        except Exception:
            logger.exception("suspend notification create failed run_id=%s", run.id)

    return Response({
        'ok': True,
        'runId': run.id,
        'status': run.status,
        'suspendedAt': run.suspended_at.isoformat() if run.suspended_at else None,
    })


# ---------- Student: Save canvas (SITUATION question drawing) ----------
@api_view(['POST', 'PUT'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_canvas_save_view(request, attempt_id):
    """
    POST/PUT /api/student/exams/attempts/<attempt_id>/canvas
    Body: { questionId?, question_id?, situationIndex?, situation_index?, imageBase64?, strokes? }
    For BANK: questionId required. For PDF/JSON: situationIndex required.
    """
    try:
        attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id, student=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.finished_at is not None:
        return Response({'detail': 'Exam already submitted'}, status=status.HTTP_400_BAD_REQUEST)
    question_id = request.data.get('questionId') or request.data.get('question_id')
    situation_index = request.data.get('situationIndex') or request.data.get('situation_index')
    if situation_index is not None:
        try:
            situation_index = int(situation_index)
        except (TypeError, ValueError):
            situation_index = None
    page_index = request.data.get('pageIndex') or request.data.get('page_index')
    if page_index is not None:
        try:
            page_index = int(page_index)
        except (TypeError, ValueError):
            page_index = 0
    else:
        page_index = 0
    if not question_id and situation_index is None:
        return Response({'detail': 'questionId or situationIndex required'}, status=status.HTTP_400_BAD_REQUEST)
    question = None
    if question_id:
        try:
            question = Question.objects.get(pk=question_id)
        except Question.DoesNotExist:
            return Response({'detail': 'Question not found'}, status=status.HTTP_404_NOT_FOUND)
        if question.type != 'SITUATION':
            return Response({'detail': 'Only SITUATION questions support canvas'}, status=status.HTTP_400_BAD_REQUEST)
        eq = ExamQuestion.objects.filter(exam=attempt.exam, question=question).first()
        if not eq:
            return Response({'detail': 'Question not in this exam'}, status=status.HTTP_400_BAD_REQUEST)
    image_base64 = request.data.get('imageBase64') or request.data.get('image_base64')
    canvas_snapshot_base64 = request.data.get('canvas_snapshot_base64') or request.data.get('canvasSnapshotBase64')
    canvas_json = request.data.get('canvas_json') or request.data.get('canvasJson')
    strokes = request.data.get('strokes')
    # Accept Fabric.js payload (canvas_json + canvas_snapshot_base64) or legacy (imageBase64 or strokes)
    has_snapshot = bool(image_base64 or canvas_snapshot_base64)
    if not has_snapshot and not strokes and not canvas_json:
        return Response({'detail': 'imageBase64, canvas_snapshot_base64, strokes, or canvas_json required'}, status=status.HTTP_400_BAD_REQUEST)
    # One canvas per question (BANK) or per situation (PDF/JSON). No multiple pages per situation.
    if question is not None:
        canvas, created = ExamAttemptCanvas.objects.get_or_create(
            attempt=attempt, question=question,
            defaults={'strokes_json': strokes, 'canvas_json': canvas_json}
        )
    else:
        canvas, created = ExamAttemptCanvas.objects.get_or_create(
            attempt=attempt, situation_index=situation_index,
            defaults={'page_index': 0, 'strokes_json': strokes, 'canvas_json': canvas_json}
        )
    snapshot_b64 = canvas_snapshot_base64 or image_base64
    if snapshot_b64:
        m = re.match(r'^data:image/(\w+);base64,(.+)$', snapshot_b64)
        if m:
            fmt, b64 = m.group(1), m.group(2)
        else:
            b64 = snapshot_b64
            fmt = 'png'
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return Response({'detail': 'Invalid base64'}, status=status.HTTP_400_BAD_REQUEST)
        if len(raw) > 12 * 1024 * 1024:
            return Response({'detail': 'Image too large (max 12MB)'}, status=status.HTTP_400_BAD_REQUEST)
        cf = compress_image_bytes(raw, canvas=True)
        cf.name = f'q{question_id or situation_index}_{attempt_id}.jpg'
        canvas.image.save(cf.name, cf, save=False)
    if strokes is not None:
        canvas.strokes_json = strokes
    if canvas_json is not None:
        canvas.canvas_json = canvas_json
    canvas.save()
    return Response(_build_canvas_response(canvas, request, include_canvas_json=True), status=status.HTTP_200_OK)


def _scratchpad_require_exam_id(request, attempt):
    """Scratchpad writes must include exam_id matching the attempt (isolates data to exam + student via attempt)."""
    data = getattr(request, 'data', None) or {}
    raw = data.get('exam_id') if isinstance(data, dict) else None
    if raw is None and isinstance(data, dict):
        raw = data.get('examId')
    if raw is None:
        return Response(
            {'detail': 'exam_id tələb olunur (qaralama yalnız imtahan və şagird cəhdinə bağlıdır)'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        if int(raw) != int(attempt.exam_id):
            return Response({'detail': 'exam_id bu cəhd ilə uyğun gəlmir'}, status=status.HTTP_400_BAD_REQUEST)
    except (TypeError, ValueError):
        return Response({'detail': 'exam_id düzgün rəqəm deyil'}, status=status.HTTP_400_BAD_REQUEST)
    return None


# ---------- Student: PDF page scribbles (drawing overlay per page) ----------
@api_view(['GET', 'POST', 'PUT'])
@permission_classes([IsAuthenticated, IsStudent])
def student_pdf_scribbles_view(request, attempt_id):
    """
    GET: list of { pageIndex, drawingData } for the attempt.
    POST/PUT: Body must include exam_id (or examId) matching the attempt.
    Single page: { exam_id, pageIndex, drawingData } or bulk { exam_id, scribbles: [...] }.
    """
    try:
        attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id, student=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        scribbles = list(
            PdfScribble.objects.filter(attempt=attempt).order_by('page_index').values('page_index', 'drawing_data')
        )
        return Response({
            'scribbles': [{'pageIndex': s['page_index'], 'drawingData': s['drawing_data'] or {}} for s in scribbles]
        })
    if attempt.finished_at is not None:
        return Response({'detail': 'Exam already submitted'}, status=status.HTTP_400_BAD_REQUEST)
    scope_err = _scratchpad_require_exam_id(request, attempt)
    if scope_err is not None:
        return scope_err
    bulk = request.data.get('scribbles')
    if isinstance(bulk, list):
        updated = []
        for item in bulk:
            pi = item.get('pageIndex') if item.get('pageIndex') is not None else item.get('page_index')
            dd = item.get('drawingData') if item.get('drawingData') is not None else item.get('drawing_data')
            if pi is None:
                continue
            try:
                page_index = int(pi)
            except (TypeError, ValueError):
                continue
            if page_index < 0:
                continue
            drawing_data = dd if isinstance(dd, dict) else {}
            obj, _ = PdfScribble.objects.update_or_create(
                attempt=attempt, page_index=page_index,
                defaults={'drawing_data': drawing_data}
            )
            updated.append({'pageIndex': page_index, 'updatedAt': obj.updated_at.isoformat()})
        return Response({'saved': updated})
    page_index = request.data.get('pageIndex') or request.data.get('page_index')
    if page_index is not None:
        try:
            page_index = int(page_index)
        except (TypeError, ValueError):
            page_index = 0
    else:
        page_index = 0
    drawing_data = request.data.get('drawingData') or request.data.get('drawing_data') or {}
    if not isinstance(drawing_data, dict):
        drawing_data = {}
    obj, _ = PdfScribble.objects.update_or_create(
        attempt=attempt, page_index=page_index,
        defaults={'drawing_data': drawing_data}
    )
    return Response({
        'pageIndex': page_index,
        'updatedAt': obj.updated_at.isoformat(),
    })


# ---------- Student: Get attempt result (questions/canvases when published) ----------
@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def student_exam_result_view(request, exam_id, attempt_id):
    mode = (request.query_params.get('mode') or '').strip().lower()
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'exam_run').prefetch_related(
            'answers__question', 'answers__question__options'
        ).get(pk=attempt_id, exam_id=exam_id, student=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.is_result_session_deleted:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.exam_run_id and getattr(attempt.exam_run, 'is_history_deleted', False):
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    ex = attempt.exam
    if getattr(ex, 'is_deleted', False) or getattr(ex, 'status', None) == 'deleted':
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.finished_at is None:
        return Response({'detail': 'Attempt not submitted yet'}, status=status.HTTP_400_BAD_REQUEST)
    if not attempt.exam.is_result_published and not attempt.is_result_published:
        return Response({
            'attemptId': attempt.id,
            'examId': attempt.exam_id,
            'title': attempt.exam.title,
            'status': 'pending_manual',
            'message': 'Nəticələr hələ elan olunmayıb.',
            'autoScore': None,
            'manualScore': None,
            'totalScore': None,
            'score': None,
            'maxScore': float(attempt.exam.max_score or 150),
            'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
            'questions': [],
            'canvases': [],
        }, status=status.HTTP_200_OK)
    manual = attempt.manual_score
    auto = attempt.auto_score
    total = float(attempt.total_score) if attempt.total_score is not None else (float(auto or 0) + float(manual or 0))
    max_s = float(attempt.exam.max_score or 150)
    final_score = max(0.0, min(total, max_s))
    if mode in ('score_summary', 'scores', 'summary'):
        summary_rows = _build_student_score_summary_rows(attempt)
        return Response({
            'attemptId': attempt.id,
            'examId': attempt.exam_id,
            'title': attempt.exam.title,
            'status': 'published',
            'score': final_score,
            'maxScore': max_s,
            'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
            'scoreSummaryMode': True,
            'questions': summary_rows,
            'canvases': [],
        })
    # Content lockdown: when results are published, do not return question_text or options (IP protection)
    content_lockdown = bool(attempt.exam.is_result_published or attempt.is_result_published)
    breakdown = _build_published_result_questions_and_canvases(attempt, request, content_lockdown=content_lockdown)
    # Score breakdown for display: Düzgün cavablar üçün bal, Səhv cavablar üçün cərimə, Situasiya balı, Yekun Bal
    points_from_correct = Decimal('0')
    penalty_from_wrong = Decimal('0')
    situation_score = Decimal('0')
    for a in attempt.answers.all():
        if a.requires_manual_check and a.manual_score is not None:
            situation_score += a.manual_score
        else:
            asc = a.auto_score or Decimal('0')
            if asc > 0:
                points_from_correct += asc
            elif asc < 0:
                penalty_from_wrong += asc
    resp = {
        'attemptId': attempt.id,
        'examId': attempt.exam_id,
        'title': attempt.exam.title,
        'status': 'published',
        'autoScore': float(attempt.auto_score or 0),
        'manualScore': float(manual) if manual is not None else None,
        'totalScore': final_score,
        'score': final_score,
        'maxScore': max_s,
        'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
        'questions': breakdown['questions'],
        'canvases': breakdown['canvases'],
        'scoreBreakdown': {
            'pointsFromCorrect': round(float(points_from_correct), 2),
            'penaltyFromWrong': round(float(penalty_from_wrong), 2),
            'situationScore': round(float(situation_score), 2),
            'total': round(final_score, 2),
        },
    }
    if content_lockdown:
        resp['contentLocked'] = True
        # Do not include pdf_url; question text and options already omitted in breakdown
    return Response(resp)


# ---------- Teacher: Grading ----------
def _mark_run_published_if_done(run):
    """
    If all submitted (non-archived) attempts in this run are published, set run.published=True
    and run.teacher_graded=True so the run drops out of the Yoxlama queue.
    """
    if not run:
        return
    unpublished = ExamAttempt.objects.filter(
        exam_run=run,
        finished_at__isnull=False,
        is_result_published=False,
        is_archived=False,
    ).exclude(status='RESTARTED').exists()
    if not unpublished:
        run.published = True
        run.teacher_graded = True
        run.status = 'published'
        if not run.published_at:
            run.published_at = _now()
        run.save(update_fields=['published', 'teacher_graded', 'status', 'published_at'])


def _get_exam_attempts_payload(request, exam, grading_queue_only=False):
    """
    Build response payload for one exam: either {'runs': [...]} or {'attempts': [...]}.
    Uses request.query_params: GroupId/group_id, status, showArchived.
    When grading_queue_only=True: only include runs that need grading (teacher_graded=False, published=False)
    and that have at least one submitted attempt not yet published (so runs already in Köhnə İmtahanlar are excluded).
    """
    from django.conf import settings
    from django.db.models import Count, Q, Exists, OuterRef

    group_id = request.query_params.get('groupId') or request.query_params.get('group_id')
    status_filter = request.query_params.get('status', '').strip()
    show_archived = request.query_params.get('showArchived', 'false').lower() == 'true'

    runs = ExamRun.objects.filter(exam=exam).select_related('group', 'student').annotate(
        attempt_count=Count('attempts', filter=Q(attempts__is_archived=False))
    ).order_by('-start_at')
    if grading_queue_only:
        # Keep run in Yoxlama until every submitted attempt is published.
        runs = runs.filter(published=False)
        # Include runs that still have at least one submitted unpublished attempt.
        has_unpublished_attempt = ExamAttempt.objects.filter(
            exam_run=OuterRef('pk'),
            finished_at__isnull=False,
            is_result_published=False,
            is_archived=False,
        ).exclude(status='RESTARTED')
        runs = runs.filter(Exists(has_unpublished_attempt))

    group_obj = None
    if group_id:
        try:
            gs = Group.objects.filter(pk=int(group_id))
            if not getattr(settings, 'SINGLE_TENANT', True):
                gs = gs.filter(created_by=request.user)
            group_obj = gs.get()
            runs = runs.filter(group=group_obj)
        except (Group.DoesNotExist, ValueError):
            pass

    # Group attempts by run; for group runs include ALL members (LEFT JOIN style)
    from groups.services import get_active_students_for_group
    from accounts.models import User

    runs_data = []
    max_s = float(exam.max_score or (100 if exam.type == 'quiz' else 150))
    for run in runs:
        qs_attempts = ExamAttempt.objects.filter(exam_run=run).select_related('student', 'student__student_profile')
        if not show_archived:
            qs_attempts = qs_attempts.filter(is_archived=False)
        if status_filter == 'submitted':
            qs_attempts = qs_attempts.filter(finished_at__isnull=False)
        elif status_filter == 'waiting_manual':
            qs_attempts = qs_attempts.filter(finished_at__isnull=False).filter(
                answers__requires_manual_check=True
            ).distinct()
        elif status_filter == 'graded':
            qs_attempts = qs_attempts.filter(manual_score__isnull=False, is_checked=True)
        elif status_filter == 'published':
            qs_attempts = qs_attempts.filter(is_checked=True).filter(exam__is_result_published=True)

        if run.group_id:
            # LEFT JOIN: include every group member; no attempt = NOT_STARTED, score 0
            memberships = get_active_students_for_group(run.group)
            student_ids_in_group = list(m.student_profile.user_id for m in memberships)
            attempt_by_student = {a.student_id: a for a in qs_attempts.order_by('-started_at')}
        elif run.student_id is None:
            # Multi-student run: include selected students in this run
            student_ids_in_group = list(run.run_students.values_list('student_id', flat=True))
            attempt_by_student = {a.student_id: a for a in qs_attempts.order_by('-started_at')}
        else:
            student_ids_in_group = []
            attempt_by_student = {}

        attempts_data = []
        for student_id in student_ids_in_group:
            attempt = attempt_by_student.get(student_id)
            if attempt:
                manual_pending = ExamAnswer.objects.filter(attempt=attempt, requires_manual_check=True).count()
                auto_s = float(attempt.auto_score or 0)
                manual_s = float(attempt.manual_score or 0) if attempt.manual_score is not None else 0
                final_score = float(attempt.total_score) if attempt.total_score is not None else (auto_s + manual_s)
                display_score = max(0.0, min(final_score, max_s))
                release_status = 'PUBLISHED' if attempt.is_result_published else ('GRADED' if attempt.is_checked else 'PENDING')
                attempts_data.append({
                    'id': attempt.id,
                    'runId': run.id,
                    'runEndAt': run.end_at.isoformat(),
                    'runStatus': run.status,
                    'suspendedAt': run.suspended_at.isoformat() if run.suspended_at else None,
                    'isCheatingDetected': bool(run.is_cheating_detected) or bool(getattr(attempt, 'is_cheating_detected', False)),
                    'studentId': attempt.student.id,
                    'studentName': attempt.student.full_name,
                    'status': 'SUBMITTED' if attempt.finished_at else (getattr(attempt, 'status', None) or 'IN_PROGRESS'),
                    'resultReleaseStatus': release_status,
                    'startedAt': attempt.started_at.isoformat(),
                    'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                    'submittedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                    'autoScore': auto_s,
                    'manualScore': float(attempt.manual_score) if attempt.manual_score is not None else None,
                    'finalScore': display_score,
                    'maxScore': max_s,
                    'manualPendingCount': manual_pending,
                    'isChecked': attempt.is_checked,
                    'isPublished': attempt.is_result_published,
                    'isArchived': attempt.is_archived,
                })
            else:
                # Never started - include so they appear in group results
                try:
                    student = User.objects.get(id=student_id, role='student')
                    attempts_data.append({
                        'id': None,
                        'runId': run.id,
                        'runEndAt': run.end_at.isoformat(),
                        'runStatus': run.status,
                        'suspendedAt': run.suspended_at.isoformat() if run.suspended_at else None,
                        'isCheatingDetected': bool(run.is_cheating_detected),
                        'studentId': student.id,
                        'studentName': student.full_name,
                        'status': 'NOT_STARTED',
                        'resultReleaseStatus': 'PENDING',
                        'startedAt': None,
                        'finishedAt': None,
                        'submittedAt': None,
                        'autoScore': 0,
                        'manualScore': None,
                        'finalScore': 0,
                        'maxScore': max_s,
                        'manualPendingCount': 0,
                        'isChecked': False,
                        'isPublished': False,
                        'isArchived': False,
                    })
                except User.DoesNotExist:
                    pass

        if not run.group_id and run.student_id is not None:
            # Individual run: only list existing attempts (no NOT_STARTED placeholder)
            for attempt in qs_attempts.order_by('-started_at'):
                manual_pending = ExamAnswer.objects.filter(attempt=attempt, requires_manual_check=True).count()
                auto_s = float(attempt.auto_score or 0)
                manual_s = float(attempt.manual_score or 0) if attempt.manual_score is not None else 0
                final_score = float(attempt.total_score) if attempt.total_score is not None else (auto_s + manual_s)
                display_score = max(0.0, min(final_score, max_s))
                release_status = 'PUBLISHED' if attempt.is_result_published else ('GRADED' if attempt.is_checked else 'PENDING')
                attempts_data.append({
                    'id': attempt.id,
                    'runId': run.id,
                    'runEndAt': run.end_at.isoformat(),
                    'runStatus': run.status,
                    'suspendedAt': run.suspended_at.isoformat() if run.suspended_at else None,
                    'isCheatingDetected': bool(run.is_cheating_detected) or bool(getattr(attempt, 'is_cheating_detected', False)),
                    'studentId': attempt.student.id,
                    'studentName': attempt.student.full_name,
                    'status': 'SUBMITTED' if attempt.finished_at else (getattr(attempt, 'status', None) or 'IN_PROGRESS'),
                    'resultReleaseStatus': release_status,
                    'startedAt': attempt.started_at.isoformat(),
                    'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                    'submittedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                    'autoScore': auto_s,
                    'manualScore': float(attempt.manual_score) if attempt.manual_score is not None else None,
                    'finalScore': display_score,
                    'maxScore': max_s,
                    'manualPendingCount': manual_pending,
                    'isChecked': attempt.is_checked,
                    'isPublished': attempt.is_result_published,
                    'isArchived': attempt.is_archived,
                })

        # Group aggregate: sum(all scores including 0s) / total_member_count (TODO-03)
        total_members_run = len(attempts_data)
        sum_score_run = sum(a['finalScore'] for a in attempts_data)
        submitted_count_run = sum(1 for a in attempts_data if a.get('finishedAt') and a.get('status') == 'SUBMITTED')
        average_score_run = (Decimal(sum_score_run) / Decimal(total_members_run)).quantize(Decimal('0.01')) if total_members_run else None
        graded_count_run = sum(1 for a in attempts_data if a.get('isChecked') or (a.get('finishedAt') and a.get('status') == 'SUBMITTED'))

        runs_data.append({
            'runId': run.id,
            'examId': exam.id,
            'groupId': run.group_id if run.group_id else None,
            'examTitle': exam.title,
            'groupName': run.group_name_snapshot or (run.group.name if run.group else None),
            'studentName': run.student_name_snapshot or (run.student.full_name if run.student else 'Deleted Student'),
            'startAt': run.start_at.isoformat(),
            'endAt': run.end_at.isoformat(),
            'durationMinutes': run.duration_minutes,
            'status': run.status,
            'suspendedAt': run.suspended_at.isoformat() if run.suspended_at else None,
            'isCheatingDetected': bool(run.is_cheating_detected) or any(
                bool(a.get('isCheatingDetected')) for a in attempts_data
            ),
            'attemptCount': run.attempt_count,
            'attempts': attempts_data,
            'summary': {
                'averageScore': float(average_score_run) if average_score_run is not None else None,
                'totalStudents': total_members_run,
                'gradedCount': graded_count_run,
            },
            'group_aggregate': {
                'total_members': total_members_run,
                'submitted_count': submitted_count_run,
                'average_score': float(average_score_run) if average_score_run is not None else None,
                'sum_score': float(sum_score_run),
            },
        })
    
    # If no runs exist, fallback to old behavior (attempts without runs)
    if not runs_data:
        qs = ExamAttempt.objects.filter(exam=exam, exam_run__isnull=True).select_related('student', 'student__student_profile').order_by('-started_at')
        if not show_archived:
            qs = qs.filter(is_archived=False)
        if group_obj:
            student_ids = list(group_obj.group_students.filter(active=True, left_at__isnull=True).values_list('student_profile__user_id', flat=True))
            qs = qs.filter(student_id__in=student_ids)
        if status_filter == 'submitted':
            qs = qs.filter(finished_at__isnull=False)
        elif status_filter == 'waiting_manual':
            qs = qs.filter(finished_at__isnull=False).filter(
                answers__requires_manual_check=True
            ).distinct()
        elif status_filter == 'graded':
            qs = qs.filter(manual_score__isnull=False, is_checked=True)
        elif status_filter == 'published':
            qs = qs.filter(is_checked=True).filter(exam__is_result_published=True)
        
        attempts_data = []
        ge_flat = _exam_global_end(exam)
        ge_iso = ge_flat.isoformat() if ge_flat else None
        for attempt in qs:
            manual_pending = ExamAnswer.objects.filter(attempt=attempt, requires_manual_check=True).count()
            auto_s = float(attempt.auto_score or 0)
            manual_s = float(attempt.manual_score or 0) if attempt.manual_score is not None else 0
            final_score = float(attempt.total_score) if attempt.total_score is not None else (auto_s + manual_s)
            max_s = float(exam.max_score or (100 if exam.type == 'quiz' else 150))
            attempts_data.append({
                'id': attempt.id,
                'studentId': attempt.student.id,
                'studentName': attempt.student.full_name,
                'groupId': group_obj.id if group_obj else None,
                'groupName': group_obj.name if group_obj else None,
                'examGlobalEndAt': ge_iso,
                'status': 'SUBMITTED' if attempt.finished_at else (getattr(attempt, 'status', None) or 'IN_PROGRESS'),
                'startedAt': attempt.started_at.isoformat(),
                'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                'submittedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
                'autoScore': auto_s,
                'manualScore': float(attempt.manual_score) if attempt.manual_score is not None else None,
                'finalScore': min(final_score, max_s),
                'maxScore': max_s,
                'manualPendingCount': manual_pending,
                'isChecked': attempt.is_checked,
                'isPublished': attempt.is_result_published,
                'isArchived': attempt.is_archived,
            })
        return {'attempts': attempts_data}

    return {'runs': runs_data}


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_attempts_view(request, exam_id):
    """List attempts for an exam, grouped by runs if assigned to groups.
    When gradingQueueOnly=1 (or queue=1): only return runs that need grading (teacher_graded=False, published=False).
    """
    from django.conf import settings
    try:
        qs = Exam.objects.filter(pk=exam_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(created_by=request.user)
        exam = qs.get()
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    grading_queue_only = request.query_params.get('gradingQueueOnly', '').strip() in ('1', 'true') or request.query_params.get('queue', '').strip() in ('1', 'true')
    payload = _get_exam_attempts_payload(request, exam, grading_queue_only=grading_queue_only)
    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_grading_attempts_view(request):
    """
    List attempts for grading across all (or selected) exams. Same filters as exam-attempts.
    Query params: groupId, status, showArchived, optional examId (single or multiple via getlist).
    If no examId: returns data for all exams that have unpublished attempts (needs_grading).
    """
    from django.conf import settings
    from django.db.models import Q

    group_id = request.query_params.get('groupId') or request.query_params.get('group_id')
    status_filter = request.query_params.get('status', '').strip()
    show_archived = request.query_params.get('showArchived', 'false').lower() == 'true'
    exam_ids_param = request.query_params.getlist('examId') or request.query_params.getlist('exam_id')
    if not exam_ids_param and request.query_params.get('examId'):
        exam_ids_param = [request.query_params.get('examId')]
    if not exam_ids_param and request.query_params.get('exam_id'):
        exam_ids_param = [request.query_params.get('exam_id')]

    if exam_ids_param:
        try:
            exam_ids = [int(x) for x in exam_ids_param if x]
        except (TypeError, ValueError):
            exam_ids = []
    else:
        exam_ids = list(
            ExamAttempt.objects.filter(
                exam__created_by=request.user,
                finished_at__isnull=False,
                is_result_published=False,
                is_archived=False,
            ).exclude(status='RESTARTED').values_list('exam_id', flat=True).distinct()
        )

    qs = Exam.objects.filter(pk__in=exam_ids) if exam_ids else Exam.objects.none()
    if not getattr(settings, 'SINGLE_TENANT', True):
        qs = qs.filter(created_by=request.user)
    exams = list(qs)

    all_runs = []
    all_attempts = []
    for exam in exams:
        payload = _get_exam_attempts_payload(request, exam, grading_queue_only=True)
        all_runs.extend(payload.get('runs', []))
        all_attempts.extend(payload.get('attempts', []))

    return Response({'runs': all_runs, 'attempts': all_attempts})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_attempts_cleanup_view(request, exam_id):
    """
    Archive exam attempts (teacher cleanup). Does NOT delete data - sets is_archived=True.
    scope: exam | group | student
    group_id, student_id: optional, for scope
    only_unpublished: true (default) - only archive unpublished attempts
    """
    try:
        exam = Exam.objects.get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    
    scope = request.data.get('scope', 'exam')
    group_id = request.data.get('group_id') or request.data.get('groupId')
    student_id = request.data.get('student_id') or request.data.get('studentId')
    only_unpublished = request.data.get('only_unpublished', True)
    
    qs = ExamAttempt.objects.filter(exam=exam)
    if only_unpublished:
        qs = qs.filter(exam__is_result_published=False)
    if scope == 'group' and group_id:
        from groups.models import GroupStudent
        student_ids = list(GroupStudent.objects.filter(
            group_id=int(group_id),
            active=True,
            left_at__isnull=True,
        ).values_list('student_profile__user_id', flat=True))
        student_ids = [x for x in student_ids if x]
        qs = qs.filter(student_id__in=student_ids)
    elif scope == 'student' and student_id:
        qs = qs.filter(student_id=int(student_id))
    
    updated = qs.update(is_archived=True)
    return Response({'archived': updated, 'message': f'{updated} attempt(s) archived'})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_detail_view(request, attempt_id):
    """Get attempt detail with all answers for grading (BANK and PDF/JSON)."""
    from django.conf import settings
    try:
        qs = ExamAttempt.objects.select_related('exam', 'student', 'exam_run').prefetch_related(
            'answers__question', 'answers__question__options', 'answers__selected_option'
        ).filter(pk=attempt_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(exam__created_by=request.user)
        attempt = qs.get()
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    answers_list = _answers_in_blueprint_order(attempt)
    if answers_list is None:
        answers_list = _answers_queryset_fallback_order(attempt)

    # Build correct-answer lookup for comparison view (PDF/JSON: blueprint + answer_key; BANK: question)
    blueprint_by_num = {}
    if attempt.attempt_blueprint:
        for item in attempt.attempt_blueprint:
            num = item.get('questionNumber')
            if num is not None:
                blueprint_by_num[int(num) if isinstance(num, (int, float)) else num] = item
    ak_questions_by_num = {}
    if attempt.exam.answer_key_json and isinstance(attempt.exam.answer_key_json, dict):
        for q in (attempt.exam.answer_key_json.get('questions') or []):
            num = q.get('number')
            if num is not None:
                ak_questions_by_num[int(num) if isinstance(num, (int, float)) else num] = q

    def _correct_for_answer(answer):
        out = {'correctOptionId': None, 'correctOptionKey': None, 'correctTextAnswer': None}
        if answer.question_id and answer.question:
            q = answer.question
            if q.type == 'MULTIPLE_CHOICE' and q.correct_answer is not None:
                if isinstance(q.correct_answer, dict) and 'option_id' in q.correct_answer:
                    out['correctOptionId'] = q.correct_answer.get('option_id')
                elif isinstance(q.correct_answer, (int, float)):
                    out['correctOptionId'] = int(q.correct_answer)
                opt = next((o for o in (q.options.all() if hasattr(q, 'options') else []) if getattr(o, 'id', None) == out['correctOptionId']), None)
                if opt and getattr(opt, 'key', None):
                    out['correctOptionKey'] = opt.key
            elif q.type in ('OPEN_SINGLE_VALUE', 'OPEN_ORDERED', 'OPEN_UNORDERED', 'OPEN_PERMUTATION') and q.correct_answer is not None:
                out['correctTextAnswer'] = str(q.correct_answer) if not isinstance(q.correct_answer, dict) else (q.correct_answer.get('text') or q.correct_answer.get('value'))
        else:
            num = answer.question_number
            bp = blueprint_by_num.get(num) if num is not None else None
            ak_q = ak_questions_by_num.get(num) if num is not None else None
            if bp:
                kind = (bp.get('kind') or 'mc').lower()
                if kind == 'mc':
                    out['correctOptionId'] = bp.get('correctOptionId')
                    opts = bp.get('options') or []
                    correct_id = bp.get('correctOptionId')
                    for o in opts:
                        if str(o.get('id')) == str(correct_id) or o.get('key', '').upper() == str(bp.get('correct', '')).upper():
                            out['correctOptionKey'] = (o.get('key') or o.get('id'))
                            break
                    if not out['correctOptionKey'] and bp.get('correct'):
                        out['correctOptionKey'] = str(bp.get('correct')).strip().upper()
                elif kind == 'open' and ak_q:
                    out['correctTextAnswer'] = ak_q.get('open_answer') or ak_q.get('correct')
        return out

    answers_data = []
    for presentation_idx, answer in enumerate(answers_list):
        correct_info = _correct_for_answer(answer)
        if answer.question_id:
            q_img_url = None
            if getattr(answer.question, 'question_image', None) and answer.question.question_image:
                q_img_url = request.build_absolute_uri(answer.question.question_image.url)
            answers_data.append({
                'id': answer.id,
                'questionId': answer.question.id,
                'questionNumber': answer.question_number,
                'presentationOrder': presentation_idx + 1,
                'questionText': answer.question.text,
                'questionType': answer.question.type,
                'questionImageUrl': q_img_url,
                'selectedOptionId': answer.selected_option_id,
                'selectedOptionKey': answer.selected_option_key,
                'textAnswer': answer.text_answer,
                'autoScore': float(answer.auto_score or 0),
                'requiresManualCheck': answer.requires_manual_check,
                'manualScore': float(answer.manual_score) if answer.manual_score is not None else None,
                'correctOptionId': correct_info['correctOptionId'],
                'correctOptionKey': correct_info['correctOptionKey'],
                'correctTextAnswer': correct_info['correctTextAnswer'],
            })
        else:
            # PDF/JSON: no Question row — derive type from blueprint (do not treat all manual as SITUATION).
            num = answer.question_number
            bp = blueprint_by_num.get(int(num)) if num is not None else blueprint_by_num.get(num)
            kind = str((bp or {}).get('kind') or (bp or {}).get('type') or '').lower()
            if kind == 'situation':
                qtype = 'SITUATION'
            elif kind == 'open' or kind.startswith('open'):
                qtype = 'OPEN_SINGLE_VALUE'
            elif kind in ('mc', 'multiple_choice'):
                qtype = 'MULTIPLE_CHOICE'
            else:
                qtype = 'SITUATION' if answer.requires_manual_check else 'open'
            answers_data.append({
                'id': answer.id,
                'questionId': None,
                'questionNumber': answer.question_number,
                'presentationOrder': presentation_idx + 1,
                'questionText': f'Sual {answer.question_number}',
                'questionType': qtype,
                'selectedOptionId': None,
                'selectedOptionKey': answer.selected_option_key,
                'textAnswer': answer.text_answer,
                'autoScore': float(answer.auto_score or 0),
                'requiresManualCheck': answer.requires_manual_check,
                'manualScore': float(answer.manual_score) if answer.manual_score is not None else None,
                'correctOptionId': correct_info['correctOptionId'],
                'correctOptionKey': correct_info['correctOptionKey'],
                'correctTextAnswer': correct_info['correctTextAnswer'],
            })

    canvases_list = list(ExamAttemptCanvas.objects.filter(attempt=attempt).order_by('situation_index', 'page_index', 'question_id'))
    canvases_data = []
    for c in canvases_list:
        rec = _build_canvas_response(c, request, include_canvas_json=True) or {}
        if c.situation_index is not None:
            rec['situationIndex'] = c.situation_index
        canvases_data.append(rec)

    # Get PDF/page URLs if exam is PDF/JSON and attempt has a run; include pdfScribbles for composite overlay
    pdf_url = None
    pdf_scribbles = []
    page_urls = []
    if attempt.exam_run and attempt.exam.source_type in ('PDF', 'JSON'):
        page_urls = _get_run_page_urls(attempt.exam_run.id, request)
        pdf_url = request.build_absolute_uri(f'/api/student/runs/{attempt.exam_run.id}/pages') if page_urls else None
        for s in PdfScribble.objects.filter(attempt=attempt).order_by('page_index'):
            pdf_scribbles.append({'pageIndex': s.page_index, 'drawingData': s.drawing_data or {}})

    blueprint = attempt.attempt_blueprint or []
    count_standard, count_situation, total_units = _get_units_from_blueprint(blueprint)
    max_s = float(attempt.exam.max_score or (100 if attempt.exam.type == 'quiz' else 150))
    unit_value_x = float(_get_x_value(Decimal(str(max_s)), total_units)) if total_units > 0 else 0.0

    blueprint_for_response = copy.deepcopy(blueprint)
    _enrich_bank_blueprint_mc_options(request, attempt.exam, blueprint_for_response)

    shuffled_order = getattr(attempt, 'shuffled_question_order', None) or _shuffled_question_order_from_blueprint(blueprint)
    return Response({
        'attemptId': attempt.id,
        'examId': attempt.exam.id,
        'examTitle': attempt.exam.title,
        'sourceType': attempt.exam.source_type,
        'studentId': attempt.student.id,
        'studentName': attempt.student.full_name,
        'runId': attempt.exam_run.id if attempt.exam_run else None,
        'runStatus': attempt.exam_run.status if attempt.exam_run else None,
        'isCheatingDetected': (
            bool(getattr(attempt, 'is_cheating_detected', False))
            or (bool(attempt.exam_run.is_cheating_detected) if attempt.exam_run else False)
        ),
        'pdfUrl': pdf_url,
        'pdfScribbles': pdf_scribbles,
        'pages': page_urls,
        'startedAt': attempt.started_at.isoformat(),
        'finishedAt': attempt.finished_at.isoformat() if attempt.finished_at else None,
        'autoScore': float(attempt.auto_score or 0),
        'manualScore': float(attempt.manual_score) if attempt.manual_score is not None else None,
        'totalScore': float(attempt.total_score or 0),
        'maxScore': max_s,
        'totalUnits': int(total_units),
        'unitValue': round(unit_value_x, 4),
        'countStandard': count_standard,
        'countSituation': count_situation,
        'attemptBlueprint': blueprint_for_response,
        'shuffledQuestionOrder': shuffled_order,
        'answers': answers_data,
        'canvases': canvases_data,
        'situationScoringSet': 'SET2',
    })


# Situation grading: teacher assigns value from [0, 2/3, 1, 4/3, 2]. Score = teacher_value * X.
SITUATION_MULTIPLIERS_SET2 = (0, 2/3, 1, 4/3, 2)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_grade_view(request, attempt_id):
    """Grade manual answers (manualScores by answer id, or per_situation_scores by situation index) and optionally publish."""
    try:
        attempt = ExamAttempt.objects.select_related('exam').prefetch_related('answers').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        logger.warning("teacher_attempt_grade attempt_id=%s user_id=%s not_found", attempt_id, getattr(request.user, 'id', None))
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    exam = attempt.exam
    is_quiz = exam.type == 'quiz'
    max_score = float(exam.max_score or (100 if is_quiz else 150))
    manual_scores = request.data.get('manualScores') or request.data.get('manual_scores') or {}
    per_situation_scores = request.data.get('per_situation_scores') or request.data.get('perSituationScores') or []
    publish = request.data.get('publish', False)
    notes = request.data.get('notes', '')
    student_answer_id = request.data.get('student_answer_id') or request.data.get('studentAnswerId')
    single_score = request.data.get('score')
    teacher_notes = request.data.get('teacher_notes') or request.data.get('teacherNotes') or notes

    try:
        old_total_score = (attempt.auto_score or Decimal('0')) + (attempt.manual_score or Decimal('0'))
        blueprint = attempt.attempt_blueprint or []
        _, _, total_units = _get_units_from_blueprint(blueprint)
        x_value = _get_x_value(max_score, total_units) if total_units > 0 else Decimal('0')

        with transaction.atomic():
            # Source-agnostic explicit single-answer save path (JSON/MANUAL/PDF alike).
            if student_answer_id is not None and single_score is not None:
                try:
                    answer_id = int(student_answer_id)
                    score = Decimal(str(single_score))
                    answer = ExamAnswer.objects.get(pk=answer_id, attempt=attempt)
                    old_answer_score = answer.manual_score or Decimal('0')
                    answer.manual_score = score
                    answer.save(update_fields=['manual_score'])
                    if old_answer_score != score:
                        GradingAuditLog.objects.create(
                            attempt=attempt,
                            teacher=request.user,
                            answer=answer,
                            old_score=old_answer_score,
                            new_score=score,
                            notes=teacher_notes or None,
                        )
                    manual_scores[str(answer_id)] = float(score)
                except (ValueError, TypeError):
                    return Response({'detail': 'Invalid student_answer_id or score'}, status=status.HTTP_400_BAD_REQUEST)
                except ExamAnswer.DoesNotExist:
                    return Response({'detail': 'Answer ID not found'}, status=status.HTTP_404_NOT_FOUND)

            for answer_id_str, score_value in manual_scores.items():
                try:
                    answer_id = int(answer_id_str)
                    score = Decimal(str(score_value))
                    answer = ExamAnswer.objects.get(pk=answer_id, attempt=attempt)
                    old_answer_score = answer.manual_score or Decimal('0')
                    answer.manual_score = score
                    answer.save(update_fields=['manual_score'])
                    if old_answer_score != score:
                        GradingAuditLog.objects.create(
                            attempt=attempt,
                            teacher=request.user,
                            answer=answer,
                            old_score=old_answer_score,
                            new_score=score,
                            notes=teacher_notes or None,
                        )
                except (ValueError, ExamAnswer.DoesNotExist):
                    continue

            # per_situation_scores: [{index: 1, fraction: 0|2/3|1|4/3|2}, ...] or optional manual_score (raw points).
            # Store teacher value as-is when manual_score/points provided; otherwise fraction * X. No auto-adjustment.
            situation_answers = _ordered_situation_answers_for_grading(attempt)
            situation_max_per_q = (Decimal('2') * x_value).quantize(Decimal('0.01')) if x_value else Decimal('0')
            for item in per_situation_scores:
                if not isinstance(item, dict):
                    continue
                idx = item.get('index') or item.get('situationIndex')
                if idx is None:
                    continue
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    continue
                if idx < 1 or idx > len(situation_answers):
                    continue
                raw_score = item.get('manual_score') if item.get('manual_score') is not None else item.get('points')
                if raw_score is not None:
                    try:
                        pts = Decimal(str(raw_score)).quantize(Decimal('0.01'))
                        if pts < 0:
                            pts = Decimal('0')
                        if situation_max_per_q and pts > situation_max_per_q:
                            pts = situation_max_per_q
                    except Exception:
                        pts = _situation_teacher_value_to_points(item.get('fraction') or item.get('score'), x_value)
                else:
                    fraction = item.get('fraction') or item.get('score')
                    pts = _situation_teacher_value_to_points(fraction, x_value)
                # Fresh DB read to avoid prefetch cache (TODO-06 A)
                ans = ExamAnswer.objects.get(pk=situation_answers[idx - 1].id, attempt=attempt)
                old_answer_score = ans.manual_score or Decimal('0')
                ans.manual_score = pts
                ans.save(update_fields=['manual_score'])
                if old_answer_score != pts:
                    GradingAuditLog.objects.create(
                        attempt=attempt,
                        teacher=request.user,
                        answer=ans,
                        old_score=old_answer_score,
                        new_score=pts,
                        notes=teacher_notes or None,
                    )

            # Total manual = sum of all answers' manual_score (after updates). Use a fresh queryset
            # so we include both open-answer manual_scores (updated via get()) and situation
            # manual_scores (updated above); prefetched attempt.answers can be stale for the former.
            answers_for_total = list(ExamAnswer.objects.filter(attempt=attempt))
            total_manual = sum(
                (a.manual_score for a in answers_for_total if a.manual_score is not None),
                Decimal('0'),
            )
            effective_total = Decimal('0')
            for a in answers_for_total:
                if a.manual_score is not None:
                    effective_total += a.manual_score
                else:
                    effective_total += (a.auto_score or Decimal('0'))
            attempt.manual_score = total_manual
            attempt.auto_score = effective_total - total_manual  # display split: auto part of total
            # Mark attempt checked only when all manual-check answers have a manual score.
            requires_manual_qs = ExamAnswer.objects.filter(attempt=attempt, requires_manual_check=True)
            all_manual_answered = not requires_manual_qs.filter(manual_score__isnull=True).exists()
            attempt.is_checked = all_manual_answered
            # FinalScore = max(0, calculatedScore), capped at max_score
            new_total_score = max(Decimal('0'), min(effective_total, Decimal(str(max_score))))
            attempt.total_score = new_total_score
            attempt.save(update_fields=['manual_score', 'auto_score', 'total_score', 'is_checked'])
            # Remove run from Yoxlama queue only when ALL attempts in the run are graded
            if attempt.exam_run_id:
                ungraded = ExamAttempt.objects.filter(
                    exam_run_id=attempt.exam_run_id,
                    finished_at__isnull=False,
                    is_archived=False,
                ).exclude(status='RESTARTED').filter(is_checked=False).exists()
                if not ungraded:
                    ExamRun.objects.filter(pk=attempt.exam_run_id).update(teacher_graded=True)
            # Log total score change if different
            if old_total_score != new_total_score:
                GradingAuditLog.objects.create(
                    attempt=attempt,
                    teacher=request.user,
                    answer=None,
                    old_total_score=old_total_score,
                    new_total_score=new_total_score,
                    notes=teacher_notes or None,
                )

            if publish:
                # Lock scores permanently: set is_result_published on ATTEMPT level
                attempt.is_result_published = True
                attempt.save(update_fields=['is_result_published'])
                # Also set on exam level
                attempt.exam.is_result_published = True
                attempt.exam.save(update_fields=['is_result_published'])
                # Auto-finish exam if all attempts are graded and published
                _auto_finish_exam_if_all_graded(attempt.exam)
                # If all attempts in this run are now published, remove run from Yoxlama queue
                if attempt.exam_run_id:
                    run = ExamRun.objects.filter(pk=attempt.exam_run_id).first()
                    _mark_run_published_if_done(run)

        if publish:
            try:
                attempt.refresh_from_db()
                _notify_exam_result_published_for_attempt(attempt, request.user)
            except Exception:
                logger.exception(
                    "notify after grade publish attempt_id=%s",
                    attempt_id,
                )

        final = float(attempt.total_score or 0)
        if final > max_score:
            final = max_score
        return Response({
            'attemptId': attempt.id,
            'manualScore': float(attempt.manual_score or 0),
            'autoScore': float(attempt.auto_score or 0),
            'totalScore': final,
            'finalScore': final,
            'isPublished': attempt.is_result_published,
        })
    except Exception as e:
        logger.exception(
            "teacher_attempt_grade error attempt_id=%s exam_id=%s user_id=%s: %s",
            attempt_id, attempt.exam_id, getattr(request.user, 'id', None), e
        )
        return Response({'detail': 'Could not grade'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_publish_view(request, attempt_id):
    """Publish/unpublish attempt result. Locks scores permanently on publish."""
    try:
        attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    
    publish = request.data.get('publish', True)
    
    with transaction.atomic():
        # Set per-attempt publish flag (locks scores)
        attempt.is_result_published = publish
        attempt.save(update_fields=['is_result_published'])
        # Also set on exam level
        attempt.exam.is_result_published = publish
        attempt.exam.save(update_fields=['is_result_published'])
        if publish:
            _auto_finish_exam_if_all_graded(attempt.exam)
            # If all attempts in this run are now published, remove run from Yoxlama queue
            if attempt.exam_run_id:
                run = ExamRun.objects.filter(pk=attempt.exam_run_id).first()
                _mark_run_published_if_done(run)

    if publish:
        try:
            attempt.refresh_from_db()
            _notify_exam_result_published_for_attempt(attempt, request.user)
        except Exception:
            logger.exception("notify after attempt publish attempt_id=%s", attempt_id)

    return Response({
        'isPublished': publish,
        'totalScore': float(attempt.total_score or 0),
        'autoScore': float(attempt.auto_score or 0),
        'manualScore': float(attempt.manual_score or 0),
    })


# ---------- Teacher: Publish one run (all graded attempts in run) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_run_publish_view(request, run_id):
    """
    POST /api/teacher/runs/<run_id>/publish/
    Publish all graded attempts for this run. Sets run.published=True, run.published_at=now().
    """
    try:
        run = ExamRun.objects.select_related('exam').get(pk=run_id, exam__created_by=request.user)
    except ExamRun.DoesNotExist:
        return Response({'detail': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)

    to_publish = list(
        ExamAttempt.objects.filter(
            exam_run=run,
            finished_at__isnull=False,
            is_checked=True,
            is_result_published=False,
            is_archived=False,
        )
    )
    now = _now()
    with transaction.atomic():
        if to_publish:
            ExamAttempt.objects.filter(pk__in=[a.id for a in to_publish]).update(is_result_published=True)
        run.published = True
        run.teacher_graded = True
        run.status = 'finished'
        run.published_at = now
        run.save(update_fields=['published', 'teacher_graded', 'status', 'published_at'])
        _auto_finish_exam_if_all_graded(run.exam)

    for att in to_publish:
        try:
            att.refresh_from_db()
            _notify_exam_result_published_for_attempt(att, request.user)
        except Exception:
            logger.exception("notify after run publish attempt_id=%s", att.id)

    return Response({
        'publishedCount': len(to_publish),
        'runPublished': True,
        'publishedAt': now.isoformat(),
        'message': f'{len(to_publish)} nəticə yayımlandı.' if to_publish else 'Run artıq yayımlandı.',
    })


# ---------- Teacher: Publish all runs for an exam (bulk) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_publish_all_view(request, exam_id):
    """
    POST /api/teacher/exams/<exam_id>/publish-all/
    Publish all graded attempts for all runs of this exam. Sets run.published on each run.
    """
    from django.conf import settings
    try:
        qs = Exam.objects.filter(pk=exam_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(created_by=request.user)
        exam = qs.get()
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)

    runs = ExamRun.objects.filter(exam=exam)
    now = _now()
    total_published = 0
    published_attempt_ids = []
    with transaction.atomic():
        for run in runs:
            to_publish = list(
                ExamAttempt.objects.filter(
                    exam_run=run,
                    finished_at__isnull=False,
                    is_checked=True,
                    is_result_published=False,
                    is_archived=False,
                ).values_list('id', flat=True)
            )
            if to_publish:
                ExamAttempt.objects.filter(pk__in=to_publish).update(is_result_published=True)
                total_published += len(to_publish)
                published_attempt_ids.extend(to_publish)
            run.published = True
            run.teacher_graded = True
            run.status = 'published'
            run.published_at = now
            run.save(update_fields=['published', 'teacher_graded', 'status', 'published_at'])
        _auto_finish_exam_if_all_graded(exam)

    if published_attempt_ids:
        for att in ExamAttempt.objects.filter(pk__in=published_attempt_ids).select_related('exam', 'student', 'student__student_profile'):
            try:
                _notify_exam_result_published_for_attempt(att, request.user)
            except Exception:
                logger.exception("notify after exam publish-all attempt_id=%s", att.id)

    return Response({
        'publishedCount': total_published,
        'runsUpdated': runs.filter(published=True).count(),
        'message': f'{total_published} nəticə yayımlandı.',
    })


# ---------- Teacher: Bulk publish results for exam + group ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_group_publish_view(request, exam_id, group_id):
    """
    POST /api/teacher/exams/<exam_id>/groups/<group_id>/publish/
    Publish all graded (is_checked) attempts for this exam and group. Uses bulk_update.
    Creates EXAM_RESULT_PUBLISHED notification for each student (and parents see via child).
    """
    from django.conf import settings

    try:
        qs = Exam.objects.filter(pk=exam_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(created_by=request.user)
        exam = qs.get()
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        gs = Group.objects.filter(pk=group_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            gs = gs.filter(created_by=request.user)
        group = gs.get()
    except (Group.DoesNotExist, ValueError):
        return Response({'detail': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)

    run = (
        ExamRun.objects.filter(exam=exam, group=group, status__in=['finished', 'published'])
        .order_by('-end_at', '-id')
        .first()
    )
    if not run:
        return Response({'detail': 'No run found for this exam and group'}, status=status.HTTP_404_NOT_FOUND)

    to_publish = list(
        ExamAttempt.objects.filter(
            exam_run=run,
            finished_at__isnull=False,
            is_checked=True,
            is_result_published=False,
            is_archived=False,
        ).select_related('student', 'exam')
    )
    if not to_publish:
        return Response({
            'publishedCount': 0,
            'message': 'Yayımlanacaq yoxlanılmış nəticə yoxdur.',
        })

    # Strict guard: do not bulk publish until all submitted attempts are graded.
    has_unchecked = ExamAttempt.objects.filter(
        exam_run=run,
        finished_at__isnull=False,
        is_archived=False,
        is_checked=False,
    ).exclude(status='RESTARTED').exists()
    if has_unchecked:
        return Response(
            {'detail': 'Bütün şagirdlərin yoxlanması bitmədən yayımlamaq olmaz'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    now = _now()
    with transaction.atomic():
        ExamAttempt.objects.filter(pk__in=[a.id for a in to_publish]).update(is_result_published=True)
        run.published = True
        run.published_at = now
        run.save(update_fields=['published', 'published_at'])
        _auto_finish_exam_if_all_graded(exam)

    for att in to_publish:
        try:
            att.refresh_from_db()
            _notify_exam_result_published_for_attempt(att, request.user)
        except Exception:
            logger.exception("notify after group publish attempt_id=%s", att.id)

    return Response({
        'publishedCount': len(to_publish),
        'message': f'{len(to_publish)} nəticə yayımlandı.',
    })


# ---------- Teacher: Unpublish results for exam + group (revert to GRADED) ----------
@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_group_unpublish_view(request, exam_id, group_id):
    """
    POST /api/teacher/exams/<exam_id>/groups/<group_id>/unpublish/
    Revert published attempts for this exam+group to unpublished (is_result_published=False).
    """
    from django.conf import settings
    try:
        qs = Exam.objects.filter(pk=exam_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(created_by=request.user)
        exam = qs.get()
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        gs = Group.objects.filter(pk=group_id)
        if not getattr(settings, 'SINGLE_TENANT', True):
            gs = gs.filter(created_by=request.user)
        group = gs.get()
    except (Group.DoesNotExist, ValueError):
        return Response({'detail': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
    run = (
        ExamRun.objects.filter(exam=exam, group=group, status__in=['finished', 'published'])
        .order_by('-end_at', '-id')
        .first()
    )
    if not run:
        return Response({'detail': 'No run found for this exam and group'}, status=status.HTTP_404_NOT_FOUND)
    updated = ExamAttempt.objects.filter(exam_run=run, is_result_published=True).update(is_result_published=False)
    return Response({'unpublishedCount': updated, 'message': f'{updated} nəticə yayımdan geri alındı.'})


# Removed duplicate teacher_attempt_reopen_view - the canonical one is defined later (line ~2141)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_reset_student_view(request, exam_id):
    """POST /api/teacher/exams/{examId}/reset-student - Reset latest attempt for a student. Body: { studentId }."""
    from datetime import timedelta
    try:
        exam = Exam.objects.get(pk=exam_id, created_by=request.user)
    except Exam.DoesNotExist:
        return Response({'detail': 'Exam not found'}, status=status.HTTP_404_NOT_FOUND)
    student_id = request.data.get('studentId') or request.data.get('student_id')
    if not student_id:
        return Response({'detail': 'studentId required'}, status=status.HTTP_400_BAD_REQUEST)
    attempt = ExamAttempt.objects.filter(exam=exam, student_id=student_id).order_by('-started_at').first()
    if not attempt:
        return Response({'detail': 'No attempt found for this student'}, status=status.HTTP_404_NOT_FOUND)
    duration_minutes = request.data.get('durationMinutes') or request.data.get('duration_minutes') or exam.duration_minutes or 60
    now = _now()
    end_time = now + timedelta(minutes=int(duration_minutes))
    with transaction.atomic():
        attempt.status = 'RESTARTED'
        attempt.save(update_fields=['status'])
        ExamStudentAssignment.objects.update_or_create(
            exam=exam,
            student_id=student_id,
            defaults={
                'start_time': now,
                'duration_minutes': int(duration_minutes),
                'is_active': True,
            }
        )
    return Response({
        'message': 'Şagird yenidən başlaya bilər',
        'studentId': int(student_id),
        'durationMinutes': int(duration_minutes),
        'endTime': end_time.isoformat(),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_restart_view(request, attempt_id):
    """Full reset for one student's attempt (blank sheet). Timer ends at global run/exam wall end, not personal start."""
    from datetime import timedelta
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'student', 'exam_run').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    now = _now()
    run = attempt.exam_run
    if run:
        global_end = run.end_at
        if now >= global_end:
            return Response(
                {'detail': 'İmtahanın ümumi vaxtı bitib; yenidən başlatmaq mümkün deyil.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        global_end = _exam_global_end(attempt.exam)
        if global_end is None or now >= global_end:
            return Response(
                {'detail': 'İmtahanın ümumi vaxtı bitib və ya təyin edilməyib; yenidən başlatmaq mümkün deyil.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    wall_remaining = max(0, int((global_end - now).total_seconds()))
    duration_minutes = max(1, (wall_remaining + 59) // 60)
    end_time = global_end
    new_revision = int(getattr(attempt, 'session_revision', 0) or 0) + 1
    with transaction.atomic():
        # Full wipe of student work for this attempt.
        ExamAnswer.objects.filter(attempt=attempt).delete()
        ExamAttemptCanvas.objects.filter(attempt=attempt).delete()
        PdfScribble.objects.filter(attempt=attempt).delete()

        attempt.started_at = now
        attempt.expires_at = end_time
        attempt.duration_minutes = int(duration_minutes)
        attempt.finished_at = None
        attempt.status = 'IN_PROGRESS'
        attempt.auto_score = None
        attempt.manual_score = None
        attempt.total_score = None
        attempt.is_checked = False
        attempt.is_result_published = False
        attempt.is_visible_to_student = True
        attempt.session_revision = new_revision
        attempt.is_cheating_detected = False
        attempt.cheating_detected_at = None
        attempt.save(update_fields=[
            'started_at', 'expires_at', 'duration_minutes', 'finished_at', 'status',
            'auto_score', 'manual_score', 'total_score', 'is_checked', 'is_result_published', 'is_visible_to_student',
            'session_revision', 'is_cheating_detected', 'cheating_detected_at',
        ])

        if attempt.exam_run_id:
            run_updates = {'status': 'active', 'suspended_at': None}
            er_restart = ExamRun.objects.filter(pk=attempt.exam_run_id).only('student_id').first()
            if er_restart and er_restart.student_id is not None:
                run_updates['is_cheating_detected'] = False
                run_updates['cheating_detected_at'] = None
            ExamRun.objects.filter(pk=attempt.exam_run_id).update(**run_updates)

        ExamStudentAssignment.objects.update_or_create(
            exam=attempt.exam,
            student=attempt.student,
            defaults={
                'start_time': now,
                'duration_minutes': int(duration_minutes),
                'is_active': True,
            }
        )
    return Response({
        'message': 'Attempt fully reset. Student starts from blank sheet.',
        'studentId': attempt.student.id,
        'durationMinutes': int(duration_minutes),
        'endTime': end_time.isoformat(),
        'sessionRevision': new_revision,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_continue_view(request, attempt_id):
    """Continue a suspended/submitted attempt: reopen progress, clear cheating flags, sync expiry to global wall end."""
    from datetime import timedelta
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'exam_run').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.is_checked:
        return Response({'detail': 'Attempt already graded; continue disabled.'}, status=status.HTTP_400_BAD_REQUEST)

    now = _now()
    duration_minutes = attempt.duration_minutes or attempt.exam.duration_minutes or 60
    with transaction.atomic():
        er = None
        if attempt.exam_run_id:
            er = ExamRun.objects.select_for_update().filter(pk=attempt.exam_run_id).first()

        if attempt.finished_at is not None:
            attempt.finished_at = None
            attempt.status = 'IN_PROGRESS'
        elif attempt.status in ('EXPIRED', 'RESTARTED'):
            attempt.status = 'IN_PROGRESS'

        attempt.is_visible_to_student = True
        attempt.is_cheating_detected = False
        attempt.cheating_detected_at = None

        if er:
            attempt.expires_at = er.end_at
        else:
            ge = _exam_global_end(attempt.exam)
            if ge and ge > now:
                attempt.expires_at = ge
            elif not attempt.expires_at or attempt.expires_at < now:
                attempt.expires_at = now + timedelta(minutes=int(duration_minutes))

        attempt.save(update_fields=[
            'finished_at', 'expires_at', 'status', 'is_visible_to_student',
            'is_cheating_detected', 'cheating_detected_at',
        ])

        if attempt.exam_run_id:
            run_updates = {
                'status': 'active',
                'suspended_at': None,
                'teacher_unlocked_at': now,
            }
            # Clear run-level cheating only for dedicated single-student runs (never wipe group run flags blindly).
            er_row = ExamRun.objects.filter(pk=attempt.exam_run_id).only('student_id', 'group_id').first()
            if er_row and er_row.student_id is not None:
                run_updates['is_cheating_detected'] = False
                run_updates['cheating_detected_at'] = None
            ExamRun.objects.filter(pk=attempt.exam_run_id).update(**run_updates)

    return Response({'message': 'Attempt continued', 'attemptId': attempt.id})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_cancel_view(request, attempt_id):
    """Cancel student's attempt immediately and remove it from active workflow."""
    try:
        attempt = ExamAttempt.objects.select_related('exam_run', 'exam').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    run = attempt.exam_run
    student_id = attempt.student_id
    with transaction.atomic():
        ExamAnswer.objects.filter(attempt=attempt).delete()
        ExamAttemptCanvas.objects.filter(attempt=attempt).delete()
        PdfScribble.objects.filter(attempt=attempt).delete()
        attempt.delete()
        # If this is a direct single-student run, finish it so the student cannot re-enter.
        if run and run.group_id is None and run.student_id == student_id:
            run.status = 'finished'
            run.save(update_fields=['status'])

    return Response({'message': 'Attempt cancelled and removed'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_reopen_view(request, attempt_id):
    """Reopen attempt for re-grading."""
    try:
        attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    
    attempt.is_checked = False
    attempt.exam.is_result_published = False
    attempt.save(update_fields=['is_checked'])
    attempt.exam.save(update_fields=['is_result_published'])
    
    return Response({'message': 'Attempt reopened for re-grading'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_attempt_result_session_delete_view(request, attempt_id):
    """POST /api/teacher/attempts/{id}/result-session-delete — Hide this student's result from Köhnə/Nəticələr (does not delete Exam or ExamRun)."""
    try:
        attempt = ExamAttempt.objects.select_related('exam', 'exam_run').get(pk=attempt_id, exam__created_by=request.user)
    except ExamAttempt.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if attempt.finished_at is None:
        return Response({'detail': 'Only submitted attempts can be removed from student results'}, status=status.HTTP_400_BAD_REQUEST)
    if attempt.is_result_session_deleted:
        return Response({'ok': True, 'attemptId': attempt.id, 'message': 'Already hidden from student results.'})
    attempt.is_result_session_deleted = True
    attempt.result_session_deleted_at = _now()
    attempt.save(update_fields=['is_result_session_deleted', 'result_session_deleted_at'])
    return Response({'ok': True, 'attemptId': attempt.id, 'message': 'Şagird üçün nəticə gizlədildi.'})


# ---------- Teacher: PDF Library ----------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_pdfs_view(request):
    """GET: List PDFs (filtered by search, year, tags). POST: Upload new PDF."""
    if request.method == 'GET':
        from django.conf import settings
        qs = TeacherPDF.objects.filter(is_deleted=False, is_archived=False).order_by('-created_at')
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(teacher=request.user)
        search = request.query_params.get('q', '').strip()
        year = request.query_params.get('year', '').strip()
        tag = request.query_params.get('tag', '').strip()
        if search:
            qs = qs.filter(title__icontains=search)
        if year:
            try:
                qs = qs.filter(year=int(year))
            except ValueError:
                pass
        if tag:
            qs = qs.filter(tags__contains=[tag])
        # Filter out PDFs where file doesn't exist on disk
        valid_pdfs = []
        for pdf in qs:
            try:
                if pdf.file and pdf.file.storage.exists(pdf.file.name):
                    valid_pdfs.append(pdf)
            except Exception:
                # Skip PDFs with storage errors
                continue
        serializer = TeacherPDFSerializer(valid_pdfs, many=True, context={'request': request})
        return Response(serializer.data)
    if request.method == 'POST':
        data = _mutable_question_request_data(request)
        data['teacher'] = request.user.id
        if 'file' not in request.FILES and 'file' not in (request.data or {}):
            return Response({'error': 'file is required'}, status=status.HTTP_400_BAD_REQUEST)
        file_obj = request.FILES.get('file') or (request.data.get('file') if hasattr(request.data, 'get') else None)
        if not file_obj:
            return Response({'error': 'file is required'}, status=status.HTTP_400_BAD_REQUEST)
        # Reset file pointer so storage saves full content (avoid 0-byte / 0-page PDFs if anything read the file earlier)
        if hasattr(file_obj, 'seek') and callable(file_obj.seek):
            file_obj.seek(0)
        data['file'] = file_obj
        if not data.get('title'):
            data['title'] = getattr(file_obj, 'name', '') or 'PDF'
        if not data.get('original_filename'):
            data['original_filename'] = getattr(file_obj, 'name', '') or ''
        org_id = getattr(request.user, 'organization_id', None)
        serializer = TeacherPDFSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            pdf = serializer.save(teacher=request.user, organization_id=org_id)
            return Response(TeacherPDFSerializer(pdf, context={'request': request}).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_pdf_detail_view(request, pk):
    """GET: Get PDF. PATCH: Update metadata (title, tags, year, source). DELETE: Soft delete."""
    from django.conf import settings
    try:
        qs = TeacherPDF.objects.filter(pk=pk, is_deleted=False, is_archived=False)
        if not getattr(settings, 'SINGLE_TENANT', True):
            qs = qs.filter(teacher=request.user)
        pdf = qs.get()
    except TeacherPDF.DoesNotExist:
        return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        return Response(TeacherPDFSerializer(pdf, context={'request': request}).data)
    if request.method == 'PATCH':
        serializer = TeacherPDFSerializer(pdf, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    if request.method == 'DELETE':
        now = _now()
        pdf.is_archived = True
        pdf.archived_at = now
        pdf.save(update_fields=['is_archived', 'archived_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
