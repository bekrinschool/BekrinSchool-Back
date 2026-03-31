"""
Verify PDF file storage: print file path, size, and signature for TeacherPDF records.
Use to confirm whether stored files are empty or corrupted (0 of 0 pages).

Usage:
  python manage.py verify_pdf_storage              # first 5 PDFs
  python manage.py verify_pdf_storage 1             # TeacherPDF id=1
  python manage.py verify_pdf_storage --exam 2      # Exam id=2 (pdf_document or pdf_file)
"""
import os
from django.core.management.base import BaseCommand
from tests.models import TeacherPDF, Exam


class Command(BaseCommand):
    help = "Print file path, size, and PDF signature (first 10 bytes) for TeacherPDF or Exam PDF."

    def add_arguments(self, parser):
        parser.add_argument("pdf_id", nargs="?", type=int, help="TeacherPDF primary key")
        parser.add_argument("--exam", type=int, dest="exam_id", help="Exam id (check pdf_document / pdf_file)")

    def handle(self, *args, **options):
        exam_id = options.get("exam_id")
        pdf_id = options.get("pdf_id")

        if exam_id is not None:
            self._check_exam(exam_id)
            return
        if pdf_id is not None:
            self._check_teacher_pdf(pdf_id)
            return
        # No id: show first 5 TeacherPDFs
        qs = TeacherPDF.objects.filter(is_deleted=False).order_by("-id")[:5]
        if not qs.exists():
            self.stdout.write(self.style.WARNING("No TeacherPDF records found."))
            return
        self.stdout.write(f"[FILE_SIZE] [FILE_SIGNATURE] for first {qs.count()} TeacherPDF(s):\n")
        for pdf in qs:
            self._check_teacher_pdf(pdf.id, quiet=False)

    def _check_teacher_pdf(self, pk, quiet=True):
        try:
            obj = TeacherPDF.objects.get(pk=pk)
        except TeacherPDF.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"TeacherPDF id={pk} not found."))
            return
        if not obj.file:
            self.stdout.write(self.style.ERROR(f"TeacherPDF id={pk}: no file field set."))
            return
        self._print_file_info(
            label=f"TeacherPDF id={pk}",
            path_getter=lambda: obj.file.path if hasattr(obj.file, "path") else None,
            storage=obj.file.storage,
            name=obj.file.name,
            size_attr=lambda: getattr(obj.file, "size", None),
            file_open=lambda: obj.file.open("rb"),
            quiet=quiet,
        )

    def _check_exam(self, exam_id):
        try:
            exam = Exam.objects.select_related("pdf_document").get(pk=exam_id)
        except Exam.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Exam id={exam_id} not found."))
            return
        self.stdout.write(f"[FILE_SIZE] [FILE_SIGNATURE] for Exam id={exam_id}:\n")
        if exam.pdf_document and exam.pdf_document.file:
            f = exam.pdf_document.file
            self._print_file_info(
                label=f"Exam pdf_document (TeacherPDF id={exam.pdf_document_id})",
                path_getter=lambda: f.path if hasattr(f, "path") else None,
                storage=f.storage,
                name=f.name,
                size_attr=lambda: getattr(f, "size", None) or getattr(exam.pdf_document, "file_size", None),
                file_open=lambda: f.open("rb"),
                quiet=False,
            )
        elif exam.pdf_file:
            f = exam.pdf_file
            self._print_file_info(
                label="Exam.pdf_file",
                path_getter=lambda: f.path if hasattr(f, "path") else None,
                storage=f.storage,
                name=f.name,
                size_attr=lambda: getattr(f, "size", None),
                file_open=lambda: f.open("rb"),
                quiet=False,
            )
        else:
            self.stdout.write(self.style.WARNING("Exam has no pdf_document and no pdf_file."))

    def _print_file_info(self, label, path_getter, storage, name, size_attr, file_open, quiet=True):
        path = path_getter()
        if path:
            self.stdout.write(f"\n[FILE_PATH] {label}: {path}")
            if os.path.exists(path):
                size = os.path.getsize(path)
                self.stdout.write(f"[FILE_SIZE] {size} bytes ({size / 1024:.2f} KB)")
                if size < 1024:
                    self.stdout.write(self.style.WARNING("  -> Size < 1KB: almost certainly empty or invalid PDF."))
            else:
                self.stdout.write(self.style.ERROR("  -> Path does not exist on disk."))
        else:
            self.stdout.write(f"\n[FILE_PATH] {label}: (no .path; storage name={name})")
            try:
                size = size_attr()
                self.stdout.write(f"[FILE_SIZE] (from storage) {size} bytes" if size is not None else "[FILE_SIZE] unknown")
            except Exception as e:
                self.stdout.write(f"[FILE_SIZE] error: {e}")

        try:
            if not storage.exists(name):
                self.stdout.write(self.style.ERROR("[FILE_SIGNATURE] File not found in storage."))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[FILE_SIGNATURE] storage.exists error: {e}"))
            return

        try:
            with file_open() as fh:
                first = fh.read(10)
                self.stdout.write(f"[FILE_SIGNATURE] first 10 bytes: {first!r}")
                if first.startswith(b"%PDF-"):
                    self.stdout.write("  -> Valid PDF header.")
                else:
                    self.stdout.write(self.style.WARNING("  -> Does NOT start with b'%PDF-' (corrupted or not a PDF)."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[FILE_SIGNATURE] read error: {e}"))
