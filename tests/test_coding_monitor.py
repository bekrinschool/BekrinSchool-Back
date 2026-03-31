"""
Regression tests for GET /api/teacher/coding-monitor endpoint.
- Empty dataset, missing relations, group/topic filters
- sort=last_activity, most_solved, most_attempts
- JSON schema (ranking, submissions)
"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from core.models import Organization
from students.models import StudentProfile, TeacherProfile
from groups.models import Group, GroupStudent
from coding.models import CodingTopic, CodingTask, CodingSubmission


class CodingMonitorTests(TestCase):
    """Tests for coding-monitor endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Test Org", slug="test-org")
        self.teacher = User.objects.create_user(
            email="teacher@monitor.test",
            password="pass123",
            full_name="Teacher",
            role="teacher",
            organization=self.org,
        )
        TeacherProfile.objects.get_or_create(user=self.teacher)
        self.student = User.objects.create_user(
            email="student@monitor.test",
            password="pass123",
            full_name="Student One",
            role="student",
            organization=self.org,
        )
        self.student_profile, _ = StudentProfile.objects.get_or_create(
            user=self.student,
            defaults={"grade": "10", "is_deleted": False},
        )
        self.group = Group.objects.create(
            name="Test Group",
            organization=self.org,
            created_by=self.teacher,
        )

    def _auth_header(self, user):
        token = str(AccessToken.for_user(user))
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _get(self, path="/api/teacher/coding-monitor", **params):
        self.client.credentials(**self._auth_header(self.teacher))
        qs = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
        url = f"{path}?{qs}" if qs else path
        return self.client.get(url)

    def test_empty_dataset_returns_200(self):
        """No submissions: returns 200 with empty ranking and submissions."""
        res = self._get()
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertIn("ranking", data)
        self.assertIn("submissions", data)
        self.assertEqual(data["ranking"], [])
        self.assertEqual(data["submissions"]["count"], 0)
        self.assertEqual(data["submissions"]["results"], [])

    def test_invalid_group_id_returns_400(self):
        """groupId=abc should return 400."""
        res = self._get(groupId="abc")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("integer", res.json().get("detail", "").lower())

    def test_invalid_topic_returns_400(self):
        """topic=xyz should return 400."""
        res = self._get(topic="xyz")
        self.assertEqual(res.status_code, 400, res.content)

    def test_group_not_found_returns_404(self):
        """groupId=999999 (non-existent) should return 404."""
        res = self._get(groupId="999999")
        self.assertEqual(res.status_code, 404, res.content)
        self.assertIn("not found", res.json().get("detail", "").lower())

    def test_valid_group_empty_returns_200(self):
        """groupId of existing group with no submissions returns 200 empty."""
        res = self._get(groupId=str(self.group.id))
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data["ranking"], [])
        self.assertEqual(data["submissions"]["count"], 0)

    def test_sort_last_activity_returns_200(self):
        """sort=last_activity returns 200."""
        res = self._get(sort="last_activity")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn("ranking", res.json())

    def test_sort_most_solved_returns_200(self):
        """sort=most_solved returns 200."""
        res = self._get(sort="most_solved")
        self.assertEqual(res.status_code, 200, res.content)

    def test_sort_most_attempts_returns_200(self):
        """sort=most_attempts returns 200."""
        res = self._get(sort="most_attempts")
        self.assertEqual(res.status_code, 200, res.content)

    def test_with_submission_schema(self):
        """With one submission: JSON schema matches expected structure."""
        topic = CodingTopic.objects.create(
            name="Test Topic",
            organization=self.org,
            is_archived=False,
        )
        task = CodingTask.objects.create(
            title="Hello",
            description="Print hello",
            topic=topic,
            deleted_at=None,
            created_by=self.teacher,
        )
        sub = CodingSubmission.objects.create(
            task=task,
            student=self.student,
            status="passed",
            run_type="SUBMIT",
            submitted_code="print('hi')",
            is_archived=False,
        )
        res = self._get()
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertGreater(len(data["ranking"]), 0)
        row = data["ranking"][0]
        self.assertIn("student", row)
        self.assertIn("student_id", row)
        self.assertIn("full_name", row)
        self.assertIn("group_names", row)
        self.assertIn("groupName", row)
        self.assertIn("totalTasksSolved", row)
        self.assertIn("totalAttempts", row)
        self.assertIn("perTaskAttemptCount", row)
        self.assertIn("per_task_map", row)
        self.assertIn("lastActivity", row)
        self.assertIsInstance(row["group_names"], list)
        self.assertIsInstance(row["per_task_map"], dict)
        self.assertEqual(data["submissions"]["count"], 1)
        sub_res = data["submissions"]["results"][0]
        self.assertIn("id", sub_res)
        self.assertIn("taskTitle", sub_res)
        self.assertIn("studentId", sub_res)
        self.assertIn("createdAt", sub_res)

    def test_group_filter_with_member(self):
        """groupId filter with group member returns that student."""
        GroupStudent.objects.create(
            group=self.group,
            student_profile=self.student_profile,
            active=True,
            left_at=None,
            organization=self.org,
        )
        topic = CodingTopic.objects.create(name="T", organization=self.org, is_archived=False)
        task = CodingTask.objects.create(
            title="T1", description="d", topic=topic, deleted_at=None, created_by=self.teacher
        )
        CodingSubmission.objects.create(
            task=task, student=self.student, status="passed",
            run_type="SUBMIT", submitted_code="x", is_archived=False
        )
        res = self._get(groupId=str(self.group.id))
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertGreater(len(data["ranking"]), 0)
        self.assertEqual(data["ranking"][0]["student_id"], self.student.id)

    def test_topic_filter(self):
        """topic filter restricts to that topic's submissions."""
        topic = CodingTopic.objects.create(name="Topic A", organization=self.org, is_archived=False)
        task = CodingTask.objects.create(
            title="Task A", description="d", topic=topic, deleted_at=None, created_by=self.teacher
        )
        CodingSubmission.objects.create(
            task=task, student=self.student, status="passed",
            run_type="SUBMIT", submitted_code="x", is_archived=False
        )
        res = self._get(topic=str(topic.id))
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertGreater(len(data["ranking"]), 0)
        self.assertGreater(data["submissions"]["count"], 0)
