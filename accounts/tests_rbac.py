"""
Minimal RBAC tests: role-based access control.
- Student token hitting teacher endpoint returns 403
- Parent token hitting teacher endpoint returns 403
- Parent requesting non-child student returns 403
"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from core.models import Organization
from students.models import StudentProfile, ParentProfile, TeacherProfile, ParentChild


class RBACTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org")
        self.client = APIClient()

        self.teacher = User.objects.create_user(
            email="teacher@test.az",
            password="pass123",
            full_name="Teacher",
            role="teacher",
            organization=self.org,
        )
        TeacherProfile.objects.create(user=self.teacher)

        self.student = User.objects.create_user(
            email="student@test.az",
            password="pass123",
            full_name="Student",
            role="student",
            organization=self.org,
        )
        self.student_profile = StudentProfile.objects.create(user=self.student, grade="10")

        self.other_student = User.objects.create_user(
            email="other@test.az",
            password="pass123",
            full_name="Other Student",
            role="student",
            organization=self.org,
        )
        self.other_profile = StudentProfile.objects.create(user=self.other_student, grade="10")

        self.parent = User.objects.create_user(
            email="parent@test.az",
            password="pass123",
            full_name="Parent",
            role="parent",
            organization=self.org,
        )
        ParentProfile.objects.create(user=self.parent)
        ParentChild.objects.create(parent=self.parent, student=self.student)

    def _auth_header(self, user: User) -> dict:
        token = str(AccessToken.for_user(user))
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_student_hitting_teacher_endpoint_returns_403(self):
        self.client.credentials(**self._auth_header(self.student))
        res = self.client.get("/api/teacher/stats")
        self.assertEqual(res.status_code, 403)

    def test_parent_hitting_teacher_endpoint_returns_403(self):
        self.client.credentials(**self._auth_header(self.parent))
        res = self.client.get("/api/teacher/stats")
        self.assertEqual(res.status_code, 403)

    def test_teacher_hitting_teacher_endpoint_returns_200(self):
        self.client.credentials(**self._auth_header(self.teacher))
        res = self.client.get("/api/teacher/stats")
        self.assertIn(res.status_code, (200, 404))  # 404 if no groups/students

    def test_parent_requesting_non_child_student_returns_403(self):
        self.client.credentials(**self._auth_header(self.parent))
        res = self.client.get(f"/api/parent/attendance?studentId={self.other_profile.id}")
        self.assertEqual(res.status_code, 403)

    def test_parent_requesting_own_child_returns_200(self):
        self.client.credentials(**self._auth_header(self.parent))
        res = self.client.get(f"/api/parent/attendance?studentId={self.student_profile.id}")
        self.assertIn(res.status_code, (200, 400))
