"""Tests for answer_key JSON validation and composition rules."""
from django.test import TestCase
from tests.answer_key import validate_answer_key_json, get_answer_key_question_counts


class TestAnswerKeyValidation(TestCase):
    def test_quiz_valid_12_mc_3_open(self):
        data = {
            "type": "quiz",
            "questions": [
                *[{"number": i + 1, "kind": "mc", "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}], "correct": "A"} for i in range(12)],
                *[{"number": 13 + i, "kind": "open", "open_rule": "EXACT_MATCH", "open_answer": "x"} for i in range(3)],
            ],
        }
        valid, errors = validate_answer_key_json(data)
        self.assertTrue(valid, errors)
        self.assertEqual(get_answer_key_question_counts(data)["total"], 15)
        self.assertEqual(get_answer_key_question_counts(data)["closed"], 12)
        self.assertEqual(get_answer_key_question_counts(data)["open"], 3)

    def test_quiz_invalid_wrong_counts(self):
        data = {
            "type": "quiz",
            "questions": [
                *[{"number": i + 1, "kind": "mc", "options": [{"key": "A", "text": "A"}], "correct": "A"} for i in range(10)],
                *[{"number": 11 + i, "kind": "open", "open_rule": "EXACT_MATCH"} for i in range(5)],
            ],
        }
        valid, errors = validate_answer_key_json(data)
        self.assertFalse(valid)
        self.assertGreater(len(errors), 0)

    def test_exam_valid_22_5_3(self):
        data = {
            "type": "exam",
            "questions": [
                *[{"number": i + 1, "kind": "mc", "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}], "correct": "B"} for i in range(22)],
                *[{"number": 23 + i, "kind": "open", "open_rule": "EXACT_MATCH"} for i in range(5)],
                *[{"number": 28 + i, "kind": "situation"} for i in range(3)],
            ],
            "situations": [{"index": 1, "pages": [3]}, {"index": 2, "pages": [4]}, {"index": 3, "pages": [5]}],
        }
        valid, errors = validate_answer_key_json(data)
        self.assertTrue(valid, errors)
        counts = get_answer_key_question_counts(data)
        self.assertEqual(counts["total"], 30)
        self.assertEqual(counts["closed"], 22)
        self.assertEqual(counts["open"], 5)
        self.assertEqual(counts["situation"], 3)

    def test_invalid_type(self):
        data = {"type": "invalid", "questions": []}
        valid, errors = validate_answer_key_json(data)
        self.assertFalse(valid)

    def test_questions_not_array(self):
        data = {"type": "quiz", "questions": "not-array"}
        valid, errors = validate_answer_key_json(data)
        self.assertFalse(valid)
