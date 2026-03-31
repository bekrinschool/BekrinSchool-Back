"""
Demo seed for multi-source exams: 1 quiz (BANK), 1 exam (PDF), 1 quiz (JSON).
Creates teacher, group, student, runs, and one student attempt with answers + canvas.
Usage: python manage.py seed_demo_tests
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal


def get_answer_key_quiz():
    """Valid quiz answer key: 12 mc + 3 open."""
    return {
        "type": "quiz",
        "questions": [
            *[{"number": i + 1, "kind": "mc", "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}, {"key": "C", "text": "C"}, {"key": "D", "text": "D"}], "correct": "A"} for i in range(12)],
            *[{"number": 13 + i, "kind": "open", "open_rule": "ORDERED_MATCH", "open_answer": "1 2 3"} for i in range(3)],
        ],
        "situations": [],
    }


def get_answer_key_exam():
    """Valid exam answer key: 22 mc + 5 open + 3 situation."""
    return {
        "type": "exam",
        "questions": [
            *[{"number": i + 1, "kind": "mc", "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}, {"key": "C", "text": "C"}, {"key": "D", "text": "D"}], "correct": "B"} for i in range(22)],
            *[{"number": 23 + i, "kind": "open", "open_rule": "EXACT_MATCH", "open_answer": "cavab"} for i in range(5)],
            *[{"number": 28 + i, "kind": "situation"} for i in range(3)],
        ],
        "situations": [{"index": 1, "pages": [3]}, {"index": 2, "pages": [4]}, {"index": 3, "pages": [5]}],
    }


class Command(BaseCommand):
    help = "Seed demo: quiz (bank), exam (pdf), quiz (json), runs, one attempt"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Seeding demo tests..."))
        from django.contrib.auth import get_user_model
        from tests.models import (
            QuestionTopic, Question, QuestionOption,
            Exam, ExamRun, ExamQuestion, ExamAssignment, ExamStudentAssignment,
            ExamAttempt, ExamAnswer, ExamAttemptCanvas, TeacherPDF,
        )
        from groups.models import Group, GroupStudent
        from students.models import StudentProfile

        User = get_user_model()
        teacher = User.objects.filter(role="teacher").first()
        if not teacher:
            self.stdout.write(self.style.WARNING("Run seed_dev first. No teacher found."))
            return
        groups = list(Group.objects.all()[:1])
        students = list(StudentProfile.objects.filter(deleted_at__isnull=True).select_related("user")[:1])
        if not groups or not students:
            self.stdout.write(self.style.WARNING("Need at least 1 group and 1 student. Run seed_dev first."))
            return
        group = groups[0]
        student_user = students[0].user
        now = timezone.now()

        # 1) Quiz from bank
        topic, _ = QuestionTopic.objects.get_or_create(
            name="Demo Topic",
            defaults={"order": 0, "is_active": True, "is_archived": False},
        )
        quiz_bank_questions = []
        for i in range(12):
            q = Question.objects.create(
                topic=topic, text=f"Quiz MC {i+1}", type="MULTIPLE_CHOICE",
                correct_answer=None, created_by=teacher, is_active=True, is_archived=False,
            )
            for j, letter in enumerate(["A", "B", "C", "D"]):
                opt = QuestionOption.objects.create(question=q, text=letter, is_correct=(letter == "A"), order=j)
                if letter == "A":
                    q.correct_answer = opt.id
                    q.save(update_fields=["correct_answer"])
            quiz_bank_questions.append(q)
        for i in range(3):
            Question.objects.create(
                topic=topic, text=f"Quiz Open {i+1}", type="OPEN_ORDERED",
                correct_answer="1 2 3", answer_rule_type="ORDERED_MATCH",
                created_by=teacher, is_active=True, is_archived=False,
            )
        open_qs = list(Question.objects.filter(topic=topic, type="OPEN_ORDERED").order_by("-id")[:3])
        quiz_bank_questions.extend(open_qs)

        quiz_bank, _ = Exam.objects.update_or_create(
            title="Demo Quiz (Bank)",
            defaults={
                "type": "quiz", "source_type": "BANK", "status": "draft",
                "start_time": now, "duration_minutes": 30, "max_score": 100,
                "created_by": teacher, "is_archived": False,
            },
        )
        for i, q in enumerate(quiz_bank_questions):
            ExamQuestion.objects.get_or_create(exam=quiz_bank, question=q, defaults={"order": i})
        self.stdout.write(self.style.SUCCESS(f"Quiz (BANK): {quiz_bank.title}"))

        # 2) Exam from PDF (no file, just answer_key)
        exam_pdf, _ = Exam.objects.update_or_create(
            title="Demo Exam (PDF)",
            defaults={
                "type": "exam", "source_type": "PDF", "status": "draft",
                "start_time": now, "duration_minutes": 60, "max_score": 150,
                "answer_key_json": get_answer_key_exam(),
                "created_by": teacher, "is_archived": False,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Exam (PDF): {exam_pdf.title}"))

        # 3) Quiz from JSON
        quiz_json, _ = Exam.objects.update_or_create(
            title="Demo Quiz (JSON)",
            defaults={
                "type": "quiz", "source_type": "JSON", "status": "draft",
                "start_time": now, "duration_minutes": 20, "max_score": 100,
                "answer_key_json": get_answer_key_quiz(),
                "created_by": teacher, "is_archived": False,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Quiz (JSON): {quiz_json.title}"))

        # Create run for quiz_bank and one attempt
        run_start = now
        run_end = now + timedelta(minutes=30)
        run_bank, _ = ExamRun.objects.get_or_create(
            exam=quiz_bank, group=group,
            defaults={
                "start_at": run_start, "end_at": run_end,
                "duration_minutes": 30, "status": "active",
                "created_by": teacher,
            },
        )
        att_bank, created = ExamAttempt.objects.get_or_create(
            exam=quiz_bank, exam_run=run_bank, student=student_user,
            defaults={
                "started_at": now, "expires_at": run_end,
                "duration_minutes": 30, "status": "SUBMITTED",
                "finished_at": now, "auto_score": Decimal("66.67"),
            },
        )
        if created:
            for eq in ExamQuestion.objects.filter(exam=quiz_bank).select_related("question").order_by("order")[:5]:
                q = eq.question
                if q.type == "MULTIPLE_CHOICE":
                    opt = q.options.filter(is_correct=True).first()
                    ExamAnswer.objects.create(
                        attempt=att_bank, question=q,
                        selected_option=opt, auto_score=Decimal("100") / 15, requires_manual_check=False,
                    )
                else:
                    ExamAnswer.objects.create(
                        attempt=att_bank, question=q,
                        text_answer="1 2 3", auto_score=Decimal("100") / 15, requires_manual_check=False,
                    )
            # One canvas for first situation (quiz has no situation; skip or use for exam only)
        self.stdout.write(self.style.SUCCESS("Run + attempt (quiz bank) created."))

        # Run for exam (PDF) and one attempt with situation canvas
        run_pdf, _ = ExamRun.objects.get_or_create(
            exam=exam_pdf, group=group,
            defaults={
                "start_at": run_start, "end_at": run_end,
                "duration_minutes": 60, "status": "active",
                "created_by": teacher,
            },
        )
        att_pdf, created = ExamAttempt.objects.get_or_create(
            exam=exam_pdf, exam_run=run_pdf, student=student_user,
            defaults={
                "started_at": now, "expires_at": run_end,
                "duration_minutes": 60, "status": "SUBMITTED",
                "finished_at": now, "auto_score": Decimal("50"),
            },
        )
        if created:
            ak = get_answer_key_exam()
            for q_def in ak["questions"]:
                num = q_def["number"]
                kind = q_def.get("kind", "mc")
                if kind == "mc":
                    ExamAnswer.objects.create(
                        attempt=att_pdf, question=None, question_number=num,
                        selected_option_key="B", auto_score=Decimal("150") / 33, requires_manual_check=False,
                    )
                elif kind == "open":
                    ExamAnswer.objects.create(
                        attempt=att_pdf, question=None, question_number=num,
                        text_answer="cavab", auto_score=Decimal("150") / 33, requires_manual_check=False,
                    )
                else:
                    ExamAnswer.objects.create(
                        attempt=att_pdf, question=None, question_number=num,
                        requires_manual_check=True,
                    )
            ExamAttemptCanvas.objects.get_or_create(
                attempt=att_pdf, situation_index=1,
                defaults={"strokes_json": []},
            )
        self.stdout.write(self.style.SUCCESS("Run + attempt (exam PDF) with canvas created."))

        self.stdout.write(self.style.SUCCESS("seed_demo_tests completed."))
