"""
Answer key JSON validation for PDF/JSON exam sources.
Exam: exactly 30 questions (22 closed + 5 open + 3 situation).
Quiz: minimum 1 question, no maximum (any count allowed).
Accepts user format: no, qtype (closed|open|situation), options as strings, correct as index, answer.
Normalizes to internal: number, kind (mc|open|situation), options as [{key, text}], correct as key, open_answer.
"""
from typing import Any

# Exam: strict 30 questions (22 closed + 5 open + 3 situation)
EXAM_TOTAL = 30
EXAM_CLOSED = 22
EXAM_OPEN = 5
EXAM_SITUATION = 3
# Quiz: no total limit; minimum 1 question
QUIZ_MIN_QUESTIONS = 1

OPEN_RULES = {
    'EXACT_MATCH',
    'ORDERED_MATCH',
    'UNORDERED_MATCH',
    'NUMERIC_EQUAL',
    'ORDERED_DIGITS',
    'UNORDERED_DIGITS',
    'MATCHING',
    'STRICT_ORDER',
    'ANY_ORDER',
}
QUESTION_KINDS = {'mc', 'open', 'situation'}
QTYPE_TO_KIND = {'closed': 'mc', 'open': 'open', 'situation': 'situation'}
OPTION_KEYS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']


def normalize_answer_key_json(data: Any) -> dict[str, Any] | None:
    """
    Accept user-facing format (no, qtype, options as strings, correct as index, answer)
    and return normalized internal format (number, kind, options as [{key, text}], correct as key, open_answer).
    Returns None if invalid or not in user format (caller can use data as-is if already internal).
    """
    if not isinstance(data, dict):
        return None
    questions = data.get('questions')
    if not isinstance(questions, list):
        return None
    exam_type = data.get('type')
    if exam_type not in ('quiz', 'exam'):
        return None
    out_questions = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            return None
        num = q.get('number') if q.get('number') is not None else q.get('no')
        if num is None:
            return None
        qtype = (q.get('qtype') or '').strip().lower()
        kind = q.get('kind') or (QTYPE_TO_KIND.get(qtype) if qtype else None)
        if not kind:
            kind = 'mc' if qtype == 'closed' else ('open' if qtype == 'open' else 'situation')
        kind = kind.strip().lower()
        if kind not in QUESTION_KINDS:
            return None
        item = {'number': int(num) if isinstance(num, (int, float)) else num, 'kind': kind}
        if kind == 'mc':
            opts = q.get('options')
            if isinstance(opts, list):
                option_list = []
                for j, o in enumerate(opts):
                    if isinstance(o, dict):
                        key = str(o.get('key', OPTION_KEYS[j] if j < len(OPTION_KEYS) else str(j))).strip().upper()
                        text = o.get('text', '')
                    else:
                        key = OPTION_KEYS[j] if j < len(OPTION_KEYS) else str(chr(65 + j))
                        text = str(o) if o is not None else ''
                    option_list.append({'key': key, 'text': text})
                item['options'] = option_list
                correct = q.get('correct')
                if correct is not None:
                    idx = int(correct) if isinstance(correct, (int, float)) else None
                    if idx is not None and 0 <= idx < len(option_list):
                        item['correct'] = option_list[idx]['key']
                    elif isinstance(correct, str) and correct.strip():
                        item['correct'] = correct.strip().upper()
                else:
                    item['correct'] = None
            else:
                item['options'] = []
                item['correct'] = None
        elif kind == 'open':
            item['options'] = []
            item['open_answer'] = q.get('open_answer') if q.get('open_answer') is not None else q.get('answer')
            rule = (q.get('open_rule') or '').strip().upper()
            if rule and rule in OPEN_RULES:
                item['open_rule'] = rule
            else:
                item['open_rule'] = 'EXACT_MATCH'
            if rule == 'MATCHING':
                item['matching_left'] = q.get('matching_left') if isinstance(q.get('matching_left'), list) else ['1', '2', '3']
                item['matching_right'] = q.get('matching_right') if isinstance(q.get('matching_right'), list) else ['a', 'b', 'c', 'd', 'e']
        else:
            item['options'] = []
            if q.get('prompt') is not None:
                item['prompt'] = q.get('prompt')
            if q.get('max_multiplier') is not None:
                item['max_multiplier'] = q.get('max_multiplier')
        out_questions.append(item)
    extra = {k: v for k, v in data.items() if k not in ('questions', 'type')}
    return {'type': exam_type, 'questions': out_questions, **extra}


def validate_and_normalize_answer_key_json(data: Any) -> tuple[bool, list[str], dict[str, Any] | None]:
    """
    Normalize user format (no, qtype, options strings, correct index, answer) if present,
    then validate. Returns (is_valid, errors, normalized_data or None).
    """
    if not isinstance(data, dict):
        return False, ['answer_key must be an object'], None
    normalized = normalize_answer_key_json(data)
    if normalized is not None:
        data = normalized
    is_valid, errors = validate_answer_key_json(data)
    return is_valid, errors, (data if is_valid else None)


def validate_answer_key_json(data: Any) -> tuple[bool, list[str]]:
    """
    Validate answer key JSON (internal format: number, kind, options as [{key, text}], correct as key).
    Returns (is_valid, list of error messages).
    """
    errors = []
    if not isinstance(data, dict):
        return False, ['answer_key must be an object']

    exam_type = data.get('type')
    if exam_type not in ('quiz', 'exam'):
        errors.append('"type" must be "quiz" or "exam"')

    questions = data.get('questions')
    if not isinstance(questions, list):
        errors.append('"questions" must be an array')
        return False, errors

    situations = data.get('situations')
    if situations is not None and not isinstance(situations, list):
        errors.append('"situations" must be an array or omitted')

    closed = 0
    open_count = 0
    situation_count = 0
    seen_numbers = set()
    mc_options_keys = set()

    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            errors.append(f'questions[{i}] must be an object')
            continue
        num = q.get('number')
        if num is None:
            errors.append(f'questions[{i}]: "number" required')
        elif num in seen_numbers:
            errors.append(f'questions[{i}]: duplicate number {num}')
        else:
            seen_numbers.add(num)

        kind = (q.get('kind') or '').strip().lower()
        if kind not in QUESTION_KINDS:
            errors.append(f'questions[{i}]: "kind" must be one of mc, open, situation')
        else:
            if kind == 'mc':
                closed += 1
                opts = q.get('options')
                if not isinstance(opts, list):
                    errors.append(f'questions[{i}]: mc question must have "options" array')
                else:
                    keys = set()
                    option_texts_normalized = []
                    for o in opts:
                        if isinstance(o, dict) and o.get('key'):
                            keys.add(str(o.get('key')).strip().upper())
                        # Collect non-empty option texts (case-insensitive) for duplicate check
                        if isinstance(o, dict):
                            t = (o.get('text') or '').strip()
                        else:
                            t = (str(o) if o is not None else '').strip()
                        if t:
                            option_texts_normalized.append(t.lower())
                    if len(option_texts_normalized) != len(set(option_texts_normalized)):
                        errors.append(
                            f'questions[{i}]: Sual {num} - eyni cavab variantı təkrar ola bilməz.'
                        )
                    correct = q.get('correct')
                    if correct is not None and str(correct).strip().upper() not in keys:
                        errors.append(f'questions[{i}]: "correct" must be one of option keys')
            elif kind == 'open':
                open_count += 1
                rule = (q.get('open_rule') or '').strip().upper()
                if rule and rule not in OPEN_RULES:
                    errors.append(f'questions[{i}]: open_rule must be one of {sorted(OPEN_RULES)}')
                # open_answer optional; used for auto-grading
            elif kind == 'situation':
                situation_count += 1

    if situations:
        for j, s in enumerate(situations):
            if not isinstance(s, dict):
                errors.append(f'situations[{j}] must be an object')
            elif 'index' not in s and 'pages' not in s:
                errors.append(f'situations[{j}]: "index" or "pages" required')

    total = closed + open_count + situation_count
    if total == 0:
        errors.append('At least one question is required')
    # Exam: exactly 30 questions (22 closed + 5 open + 3 situation)
    if exam_type == 'exam':
        if total != EXAM_TOTAL:
            errors.append(f'Exam must have exactly {EXAM_TOTAL} questions total (got {total})')
        if closed != EXAM_CLOSED:
            errors.append(f'Exam must have exactly {EXAM_CLOSED} closed (mc) questions (got {closed})')
        if open_count != EXAM_OPEN:
            errors.append(f'Exam must have exactly {EXAM_OPEN} open questions (got {open_count})')
        if situation_count != EXAM_SITUATION:
            errors.append(f'Exam must have exactly {EXAM_SITUATION} situation questions (got {situation_count})')
    # Quiz: minimum 1 question, no maximum
    elif exam_type == 'quiz':
        if total < QUIZ_MIN_QUESTIONS:
            errors.append(f'Quiz must have at least {QUIZ_MIN_QUESTIONS} question(s) (got {total})')

    return len(errors) == 0, errors


def get_answer_key_question_counts(data: Any) -> dict[str, int] | None:
    """Return {closed, open, situation, total} from validated answer_key_json, or None if invalid."""
    if not isinstance(data, dict):
        return None
    questions = data.get('questions') or []
    closed = open_count = situation_count = 0
    for q in questions:
        if not isinstance(q, dict):
            continue
        kind = (q.get('kind') or '').strip().lower()
        if kind == 'mc':
            closed += 1
        elif kind == 'open':
            open_count += 1
        elif kind == 'situation':
            situation_count += 1
    return {
        'closed': closed,
        'open': open_count,
        'situation': situation_count,
        'total': closed + open_count + situation_count,
    }
