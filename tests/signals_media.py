"""
Delete media files when model rows are removed or image fields are replaced.
Compress teacher question/option images on upload (UploadedFile only).
Uses signal.connect() (no django.dispatch.receiver) so imports cannot be missed.
"""
import logging

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db.models.signals import post_delete, post_save, pre_save

from core.image_compression import compress_image_bytes
from tests.models import Exam, ExamAttemptCanvas, Question, QuestionOption, TeacherPDF

logger = logging.getLogger(__name__)


def _safe_delete_file(name: str | None) -> None:
    if not name:
        return
    try:
        default_storage.delete(name)
    except Exception:
        logger.warning("Could not delete media file (missing or permission): %s", name, exc_info=True)


# --- Track previous file names (replace / clear) ---


def _question_track_old_image(sender, instance, **kwargs):
    instance._old_question_image_name = None
    if not instance.pk:
        return
    try:
        prev = Question.objects.only("question_image").get(pk=instance.pk)
        instance._old_question_image_name = prev.question_image.name if prev.question_image else None
    except Question.DoesNotExist:
        pass


def _option_track_old_image(sender, instance, **kwargs):
    instance._old_option_image_name = None
    if not instance.pk:
        return
    try:
        prev = QuestionOption.objects.only("image").get(pk=instance.pk)
        instance._old_option_image_name = prev.image.name if prev.image else None
    except QuestionOption.DoesNotExist:
        pass


def _exam_track_old_pdf(sender, instance, **kwargs):
    instance._old_pdf_file_name = None
    if not instance.pk:
        return
    try:
        prev = Exam.objects.only("pdf_file").get(pk=instance.pk)
        instance._old_pdf_file_name = prev.pdf_file.name if prev.pdf_file else None
    except Exam.DoesNotExist:
        pass


def _teacher_pdf_track_old_file(sender, instance, **kwargs):
    instance._old_teacher_pdf_name = None
    if not instance.pk:
        return
    try:
        prev = TeacherPDF.objects.only("file").get(pk=instance.pk)
        instance._old_teacher_pdf_name = prev.file.name if prev.file else None
    except TeacherPDF.DoesNotExist:
        pass


def _canvas_track_old_image(sender, instance, **kwargs):
    instance._old_canvas_image_name = None
    if not instance.pk:
        return
    try:
        prev = ExamAttemptCanvas.objects.only("image").get(pk=instance.pk)
        instance._old_canvas_image_name = prev.image.name if prev.image else None
    except ExamAttemptCanvas.DoesNotExist:
        pass


# --- Compress new image uploads (multipart) ---


def _maybe_compress_image_field(instance, attr: str, *, canvas: bool) -> None:
    f = getattr(instance, attr, None)
    if not f:
        return
    if not isinstance(f, UploadedFile):
        return
    try:
        raw = f.read()
        if hasattr(f, "seek"):
            f.seek(0)
    except Exception:
        logger.warning("Could not read upload for compression on %s", attr, exc_info=True)
        return
    if not raw:
        return
    try:
        cf = compress_image_bytes(raw, canvas=canvas)
        base = f.name.rsplit(".", 1)[0] if f.name else "image"
        cf.name = f"{base}.jpg"
        setattr(instance, attr, cf)
    except Exception:
        logger.warning("Compression failed for %s; leaving original upload", attr, exc_info=True)


def _question_compress_image(sender, instance, **kwargs):
    _maybe_compress_image_field(instance, "question_image", canvas=False)


def _option_compress_image(sender, instance, **kwargs):
    _maybe_compress_image_field(instance, "image", canvas=False)


def _canvas_compress_image(sender, instance, **kwargs):
    _maybe_compress_image_field(instance, "image", canvas=True)


# --- After save: remove replaced files ---


def _question_drop_replaced_image(sender, instance, **kwargs):
    old = getattr(instance, "_old_question_image_name", None)
    new = instance.question_image.name if instance.question_image else None
    if old and old != new:
        _safe_delete_file(old)


def _option_drop_replaced_image(sender, instance, **kwargs):
    old = getattr(instance, "_old_option_image_name", None)
    new = instance.image.name if instance.image else None
    if old and old != new:
        _safe_delete_file(old)


def _exam_drop_replaced_pdf(sender, instance, **kwargs):
    old = getattr(instance, "_old_pdf_file_name", None)
    new = instance.pdf_file.name if instance.pdf_file else None
    if old and old != new:
        _safe_delete_file(old)


def _teacher_pdf_drop_replaced_file(sender, instance, **kwargs):
    old = getattr(instance, "_old_teacher_pdf_name", None)
    new = instance.file.name if instance.file else None
    if old and old != new:
        _safe_delete_file(old)


def _canvas_drop_replaced_image(sender, instance, **kwargs):
    old = getattr(instance, "_old_canvas_image_name", None)
    new = instance.image.name if instance.image else None
    if old and old != new:
        _safe_delete_file(old)


# --- On row delete ---


def _question_delete_image_file(sender, instance, **kwargs):
    if instance.question_image:
        try:
            instance.question_image.delete(save=False)
        except Exception:
            logger.warning("post_delete question_image failed pk=%s", instance.pk, exc_info=True)


def _option_delete_image_file(sender, instance, **kwargs):
    if instance.image:
        try:
            instance.image.delete(save=False)
        except Exception:
            logger.warning("post_delete option image failed pk=%s", instance.pk, exc_info=True)


def _canvas_delete_image_file(sender, instance, **kwargs):
    if instance.image:
        try:
            instance.image.delete(save=False)
        except Exception:
            logger.warning("post_delete canvas image failed pk=%s", instance.pk, exc_info=True)


def _exam_delete_pdf_file(sender, instance, **kwargs):
    if instance.pdf_file:
        try:
            instance.pdf_file.delete(save=False)
        except Exception:
            logger.warning("post_delete exam pdf failed pk=%s", instance.pk, exc_info=True)


def _teacher_pdf_delete_file(sender, instance, **kwargs):
    if instance.file:
        try:
            instance.file.delete(save=False)
        except Exception:
            logger.warning("post_delete teacher pdf failed pk=%s", instance.pk, exc_info=True)


def connect_media_signals():
    """Register receivers; dispatch_uid prevents duplicates if module is re-imported."""
    pre_save.connect(_question_track_old_image, sender=Question, dispatch_uid="tests_media_q_track")
    pre_save.connect(_question_compress_image, sender=Question, dispatch_uid="tests_media_q_compress")

    pre_save.connect(_option_track_old_image, sender=QuestionOption, dispatch_uid="tests_media_opt_track")
    pre_save.connect(_option_compress_image, sender=QuestionOption, dispatch_uid="tests_media_opt_compress")

    pre_save.connect(_exam_track_old_pdf, sender=Exam, dispatch_uid="tests_media_exam_pdf_track")
    pre_save.connect(_teacher_pdf_track_old_file, sender=TeacherPDF, dispatch_uid="tests_media_tpdf_track")

    pre_save.connect(_canvas_track_old_image, sender=ExamAttemptCanvas, dispatch_uid="tests_media_cv_track")
    pre_save.connect(_canvas_compress_image, sender=ExamAttemptCanvas, dispatch_uid="tests_media_cv_compress")

    post_save.connect(_question_drop_replaced_image, sender=Question, dispatch_uid="tests_media_q_post")
    post_save.connect(_option_drop_replaced_image, sender=QuestionOption, dispatch_uid="tests_media_opt_post")
    post_save.connect(_exam_drop_replaced_pdf, sender=Exam, dispatch_uid="tests_media_exam_pdf_post")
    post_save.connect(_teacher_pdf_drop_replaced_file, sender=TeacherPDF, dispatch_uid="tests_media_tpdf_post")
    post_save.connect(_canvas_drop_replaced_image, sender=ExamAttemptCanvas, dispatch_uid="tests_media_cv_post")

    post_delete.connect(_question_delete_image_file, sender=Question, dispatch_uid="tests_media_q_del")
    post_delete.connect(_option_delete_image_file, sender=QuestionOption, dispatch_uid="tests_media_opt_del")
    post_delete.connect(_canvas_delete_image_file, sender=ExamAttemptCanvas, dispatch_uid="tests_media_cv_del")
    post_delete.connect(_exam_delete_pdf_file, sender=Exam, dispatch_uid="tests_media_exam_pdf_del")
    post_delete.connect(_teacher_pdf_delete_file, sender=TeacherPDF, dispatch_uid="tests_media_tpdf_del")


connect_media_signals()
