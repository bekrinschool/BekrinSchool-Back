"""
Open-answer evaluation: EXACT_MATCH, ORDERED_MATCH, UNORDERED_MATCH, NUMERIC_EQUAL,
ORDERED_DIGITS, UNORDERED_DIGITS, MATCHING, STRICT_ORDER, ANY_ORDER.

- ORDERED_DIGITS: exact sequence (e.g. "135" only; "153" wrong).
- UNORDERED_DIGITS: same set, any order ("135", "531", "315" all correct).
- EXACT_MATCH: text equality.
- MATCHING: Uyğunluq (1-a, 2-b, 3-c). correct_answer and student_answer as dict or "1-a,2-b,3-c".
"""
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation


def _clean_for_digits(text: str) -> str:
    """Keep only digits, comma, space, dot, semicolon, hyphen. Strip."""
    if not text:
        return ""
    return re.sub(r"[^\d\s,\.\-;]", "", str(text).strip())


def normalize_digits_sequence(text: str) -> list[str]:
    """
    Extract digits as ordered list. For "1,3,5" or "135" or "1 3 5" or "1;3;5" or "1-3-5" -> ["1","3","5"].
    Keeps order. 1 5 3 -> wrong (order matters). Smart validation for ordered match.
    """
    if not text:
        return []
    cleaned = _clean_for_digits(text)
    # Split by comma, semicolon, hyphen or whitespace
    parts = re.split(r"[\s,;\-]+", cleaned)
    result = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # If part is only digits, add each digit separately (135 -> 1,3,5)
        if p.isdigit():
            result.extend(list(p))
        else:
            # Might be "15.0" or mixed - extract digits only
            digits = re.findall(r"\d", p)
            result.extend(digits)
    return result


def normalize_numeric(text: str) -> Decimal | None:
    """
    Parse as single numeric value. Handles 15, 015, 15.0, 15,00 (EU decimal).
    Returns None if not parseable as one number.
    """
    if not text:
        return None
    cleaned = (text or "").strip()
    cleaned = cleaned.replace(",", ".")
    # Remove spaces
    cleaned = cleaned.replace(" ", "")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, TypeError):
        return None


def normalize_whitespace(s: str) -> str:
    if s is None:
        return ""
    return " ".join(s.strip().split())


def normalize_permutation_chars(s: str) -> str:
    """Remove spaces/commas then sort all remaining characters."""
    compact = re.sub(r"[\s,]+", "", str(s or ""))
    return "".join(sorted(compact))


def tokens_ordered(s: str) -> list:
    """Split by comma or space, strip, filter empty. Legacy: "1,3,5" -> ["1","3","5"], "135" -> ["135"]."""
    if not s:
        return []
    parts = re.split(r"[\s,]+", s.strip())
    return [p.strip() for p in parts if p.strip()]


def tokens_unordered(s: str) -> list:
    """Same as ordered but sort for comparison."""
    return sorted(tokens_ordered(s))


def _normalize_matching_pairs(value) -> set[tuple[str, str]]:
    """Convert dict {"1":"a","2":"b"} or string "1-a,2-b" / "1-a2-b3-c" to set of (k,v) pairs. Keys/values lowercased."""
    if value is None:
        return set()
    if isinstance(value, dict):
        return {(str(k).strip().lower(), str(v).strip().lower()) for k, v in value.items() if k is not None and v is not None}
    s = (value or "").strip()
    if not s:
        return set()
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return {(str(k).strip().lower(), str(v).strip().lower()) for k, v in data.items() if k is not None and v is not None}
    except (json.JSONDecodeError, TypeError):
        pass
    pairs = set()
    # Concatenated pairs without commas: "1-a2-b3-c"
    for m in re.finditer(r"(\d+)\s*[-–—:]\s*([a-zA-Z])", s):
        pairs.add((m.group(1), m.group(2).lower()))
    if pairs:
        return pairs
    for part in re.split(r"[,;]", s):
        part = part.strip()
        mm = re.match(r"^(\d+)\s*[-–—:]\s*([a-zA-Z])$", part)
        if mm:
            pairs.add((mm.group(1), mm.group(2).lower()))
    return pairs


def evaluate_open_single_value(
    student_answer: str,
    correct_answer,
    rule_type: str | None,
) -> bool:
    """
    correct_answer can be str, number, or dict (for MATCHING).
    rule_type: EXACT_MATCH, ORDERED_MATCH, UNORDERED_MATCH, NUMERIC_EQUAL,
               ORDERED_DIGITS, UNORDERED_DIGITS, MATCHING, STRICT_ORDER, ANY_ORDER.
    """
    if correct_answer is None:
        return False
    student = (student_answer or "").strip()
    rule = (rule_type or "EXACT_MATCH").upper()

    # STRICT_ORDER / ORDERED_DIGITS: exact sequence. "135" correct; "153" wrong.
    if rule in ("STRICT_ORDER", "ORDERED_DIGITS"):
        student_digits = normalize_digits_sequence(student)
        correct_digits = normalize_digits_sequence(str(correct_answer))
        return student_digits == correct_digits

    # ANY_ORDER / UNORDERED_DIGITS: same elements, any order.
    if rule in ("ANY_ORDER", "UNORDERED_DIGITS"):
        if rule == "ANY_ORDER":
            return normalize_permutation_chars(student) == normalize_permutation_chars(str(correct_answer))
        student_digits = normalize_digits_sequence(student)
        correct_digits = normalize_digits_sequence(str(correct_answer))
        return Counter(student_digits) == Counter(correct_digits)

    # MATCHING: Uyğunluq. Compare pairs (1-a, 2-b, 3-c).
    if rule == "MATCHING":
        student_pairs = _normalize_matching_pairs(student_answer if isinstance(student_answer, (dict, str)) else str(student_answer))
        correct_pairs = _normalize_matching_pairs(correct_answer if isinstance(correct_answer, dict) else str(correct_answer))
        return student_pairs == correct_pairs and len(student_pairs) > 0

    # NUMERIC_EQUAL: single number comparison. 15, 15.0, 015, 15,00 -> correct; 1,5 (two items) -> wrong
    if rule == "NUMERIC_EQUAL":
        a = normalize_numeric(student)
        b = normalize_numeric(str(correct_answer))
        if a is None or b is None:
            return False
        return a == b

    if rule == "EXACT_MATCH":
        return normalize_whitespace(student).lower() == normalize_whitespace(str(correct_answer)).lower()

    # ORDERED_MATCH: accept "1,3,5", "1 3 5", "135" (digit tokenization); order must match
    if rule == "ORDERED_MATCH":
        student_digits = normalize_digits_sequence(student)
        correct_digits = normalize_digits_sequence(str(correct_answer))
        if student_digits or correct_digits:
            return student_digits == correct_digits
        return tokens_ordered(student) == tokens_ordered(str(correct_answer))

    if rule == "UNORDERED_MATCH":
        return tokens_unordered(student) == tokens_unordered(str(correct_answer))

    return normalize_whitespace(student).lower() == normalize_whitespace(str(correct_answer)).lower()
