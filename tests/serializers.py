"""
Serializers for tests app (legacy Test + Question Bank & Exam)
"""
import json

from rest_framework import serializers
from .models import (
    Test,
    TestResult,
    QuestionTopic,
    Question,
    QuestionOption,
    Exam,
    ExamRun,
    ExamQuestion,
    ExamAttempt,
    ExamAnswer,
    TeacherPDF,
)


class TestSerializer(serializers.ModelSerializer):
    """Test (quiz/exam) serializer"""
    type = serializers.ChoiceField(choices=[('quiz', 'Quiz'), ('exam', 'Exam')])

    class Meta:
        model = Test
        fields = ['id', 'type', 'title', 'pdf_url', 'is_active', 'config']
        read_only_fields = ['id']


class TestResultSerializer(serializers.ModelSerializer):
    """Test Result serializer"""
    testName = serializers.CharField(source='test_name', read_only=True)
    maxScore = serializers.IntegerField(source='max_score', read_only=True)
    groupName = serializers.CharField(source='group.name', read_only=True, allow_null=True)
    
    class Meta:
        model = TestResult
        fields = ['id', 'testName', 'score', 'maxScore', 'date', 'groupName']
        read_only_fields = ['id']


class TestResultCreateSerializer(serializers.Serializer):
    """TestResult create - for manual grade entry"""
    studentProfileId = serializers.IntegerField()
    groupId = serializers.IntegerField(required=False, allow_null=True)
    testName = serializers.CharField()
    maxScore = serializers.IntegerField()
    score = serializers.IntegerField()
    date = serializers.DateField()

    def create(self, validated_data):
        from students.models import StudentProfile
        from groups.models import Group
        sp_id = validated_data.pop('studentProfileId')
        group_id = validated_data.pop('groupId', None)
        validated_data['student_profile'] = StudentProfile.objects.get(id=sp_id)
        validated_data['group'] = Group.objects.get(id=group_id) if group_id and group_id > 0 else None
        validated_data['test_name'] = validated_data.pop('testName')
        validated_data['max_score'] = validated_data.pop('maxScore')
        instance = TestResult.objects.create(**validated_data)
        return instance


# ----- Question Bank & Exam -----

class QuestionTopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionTopic
        fields = ['id', 'name', 'order', 'is_active']


class QuestionOptionSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = QuestionOption
        fields = ['id', 'text', 'label', 'image_url', 'is_correct', 'order']

    def get_image_url(self, obj):
        if getattr(obj, 'image', None) and obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


class McOptionWritableSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    text = serializers.CharField(required=False, allow_blank=True)
    label = serializers.CharField(required=False, allow_blank=True)
    is_correct = serializers.BooleanField(required=False, default=False)
    order = serializers.IntegerField(required=False)


class QuestionSerializer(serializers.ModelSerializer):
    options = QuestionOptionSerializer(many=True, read_only=True)
    question_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = [
            'id', 'topic', 'short_title', 'text', 'type', 'correct_answer', 'answer_rule_type',
            'created_at', 'is_active', 'options', 'question_image', 'question_image_url',
            'mc_option_display',
        ]
        read_only_fields = ['id', 'created_at']

    def get_question_image_url(self, obj):
        if obj.question_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.question_image.url)
            return obj.question_image.url if obj.question_image else None
        return None


class QuestionCreateSerializer(serializers.ModelSerializer):
    """Question Bank creation: accepts topic or topic_id; optional question_image (multipart)."""
    topic_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    options = McOptionWritableSerializer(many=True, required=False)
    question_image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Question
        fields = [
            'topic', 'topic_id', 'short_title', 'text', 'type', 'correct_answer', 'answer_rule_type',
            'is_active', 'options', 'question_image', 'mc_option_display',
        ]
        extra_kwargs = {'topic': {'required': False}}

    def validate_question_image(self, value):
        if value is None:
            return value
        max_size = 5 * 1024 * 1024  # 5MB
        if value.size > max_size:
            raise serializers.ValidationError('Şəkil 5MB-dan böyük ola bilməz.')
        name = value.name or ''
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        allowed = {'jpg', 'jpeg', 'png', 'webp'}
        if ext not in allowed:
            ct = (getattr(value, 'content_type', '') or '').lower()
            if 'jpeg' in ct or ct == 'image/jpg':
                ext = 'jpg'
            elif ct == 'image/png':
                ext = 'png'
            elif ct == 'image/webp':
                ext = 'webp'
        if ext not in allowed:
            raise serializers.ValidationError('Yalnız jpg, png, webp formatları qəbul olunur.')
        return value

    def _validate_option_image_file(self, f):
        if f is None:
            return
        max_size = 5 * 1024 * 1024
        if f.size > max_size:
            raise serializers.ValidationError('Variant şəkli 5MB-dan böyük ola bilməz.')
        name = f.name or ''
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        allowed = {'jpg', 'jpeg', 'png', 'webp'}
        if ext not in allowed:
            ct = (getattr(f, 'content_type', '') or '').lower()
            if 'jpeg' in ct or ct == 'image/jpg':
                ext = 'jpg'
            elif ct == 'image/png':
                ext = 'png'
            elif ct == 'image/webp':
                ext = 'webp'
        if ext not in allowed:
            raise serializers.ValidationError('Variant üçün yalnız jpg, png, webp formatları qəbul olunur.')

    def validate(self, attrs):
        # Allow topic_id for convenience (align with frontend)
        topic = attrs.get('topic')
        topic_id = attrs.pop('topic_id', None)
        if topic_id is not None and topic is None:
            attrs['topic'] = topic_id
        topic = attrs.get('topic')
        is_update = self.instance is not None
        if 'short_title' in attrs:
            st = (attrs.get('short_title') or '').strip()
            if not st:
                raise serializers.ValidationError({'short_title': 'Qısa ad boş ola bilməz.'})
            attrs['short_title'] = st
        elif not is_update:
            raise serializers.ValidationError({'short_title': 'Qısa ad tələb olunur.'})
        if topic is None and not is_update:
            raise serializers.ValidationError({'topic': 'Mövzu (topic və ya topic_id) tələb olunur.'})

        qtype = attrs.get('type')
        if qtype is None and is_update:
            qtype = getattr(self.instance, 'type', None)
        request = self.context.get('request')
        if qtype == 'MULTIPLE_CHOICE':
            mode = attrs.get('mc_option_display')
            if mode is None and is_update:
                mode = getattr(self.instance, 'mc_option_display', None)
            mode = (str(mode or 'TEXT')).upper()
            if mode not in ('TEXT', 'IMAGE'):
                mode = 'TEXT'
            attrs['mc_option_display'] = mode

            options = attrs.get('options') or []
            if len(options) < 2:
                raise serializers.ValidationError(
                    {'options': 'Qapalı suallar üçün ən azı 2 variant tələb olunur.'}
                )
            correct_count = sum(1 for o in options if o.get('is_correct'))
            if correct_count != 1:
                raise serializers.ValidationError(
                    {'options': 'Düzgün cavab olaraq tam bir variant işarələnməlidir.'}
                )

            if mode == 'TEXT':
                for i, opt in enumerate(options):
                    if not (opt.get('text') or str(opt.get('text', '')).strip()):
                        raise serializers.ValidationError(
                            {'options': f'Variant {i + 1} üçün mətn tələb olunur.'}
                        )
                option_texts = []
                for o in options:
                    t = (o.get('text') or '').strip()
                    if t:
                        option_texts.append(t.lower())
                if len(option_texts) != len(set(option_texts)):
                    raise serializers.ValidationError(
                        {'options': 'Eyni cavab variantı təkrar ola bilməz.'}
                    )
            else:
                old_by_id = {}
                if is_update and self.instance:
                    old_by_id = {o.id: o for o in self.instance.options.all()}
                for i, opt in enumerate(options):
                    has_file = bool(request and request.FILES.get(f'option_image_{i}'))
                    if has_file:
                        self._validate_option_image_file(request.FILES.get(f'option_image_{i}'))
                    oid = opt.get('id')
                    has_existing = False
                    if oid and oid in old_by_id and old_by_id[oid].image:
                        has_existing = True
                    if not has_file and not has_existing:
                        raise serializers.ValidationError(
                            {'options': f'Variant {i + 1} üçün şəkil tələb olunur (bütün variantlar şəkil formatında olmalıdır).'}
                        )
        elif qtype and qtype != 'SITUATION':
            correct = attrs.get('correct_answer')
            if correct is None or (isinstance(correct, (str, dict)) and correct == '') or (isinstance(correct, dict) and not correct):
                raise serializers.ValidationError(
                    {'correct_answer': 'Düzgün cavab tələb olunur.'}
                )
            if qtype in ('OPEN_SINGLE_VALUE', 'OPEN_ORDERED', 'OPEN_UNORDERED', 'OPEN_PERMUTATION'):
                if not attrs.get('answer_rule_type'):
                    if qtype == 'OPEN_ORDERED':
                        attrs['answer_rule_type'] = 'STRICT_ORDER'
                    elif qtype == 'OPEN_PERMUTATION':
                        attrs['answer_rule_type'] = 'ANY_ORDER'
                    elif qtype == 'OPEN_UNORDERED':
                        attrs['answer_rule_type'] = 'MATCHING'
                    else:
                        attrs['answer_rule_type'] = 'EXACT_MATCH'
        return attrs

    def _persist_mc_options(self, question, options_data):
        """Create QuestionOption rows for MULTIPLE_CHOICE; handles TEXT vs IMAGE and file uploads."""
        import os
        from django.core.files.base import ContentFile

        request = self.context.get('request')
        mode = (getattr(question, 'mc_option_display', None) or 'TEXT').upper()
        old_by_id = {o.id: o for o in question.options.all()} if question.pk else {}
        question.options.all().delete()

        correct_option_id = None
        for i, opt in enumerate(options_data):
            text = (opt.get('text') or '').strip()
            label = (opt.get('label') or '').strip()
            ob = QuestionOption(
                question=question,
                order=opt.get('order', i),
                text=text,
                label=label,
                is_correct=bool(opt.get('is_correct', False)),
            )
            if mode == 'IMAGE':
                img_f = request.FILES.get(f'option_image_{i}') if request else None
                oid = opt.get('id')
                if img_f:
                    ob.image.save(img_f.name, img_f, save=False)
                elif oid and oid in old_by_id and old_by_id[oid].image:
                    old = old_by_id[oid]
                    old.image.open('rb')
                    data = old.image.read()
                    name = os.path.basename(old.image.name)
                    ob.image.save(name, ContentFile(data), save=False)
            ob.save()
            if ob.is_correct:
                correct_option_id = ob.id
        if correct_option_id is not None:
            question.correct_answer = correct_option_id
            question.save(update_fields=['correct_answer'])

    def create(self, validated_data):
        options_data = validated_data.pop('options', None) or []
        q = Question.objects.create(**validated_data)
        if q.type == 'MULTIPLE_CHOICE' and options_data:
            self._persist_mc_options(q, options_data)
        return q

    def update(self, instance, validated_data):
        options_data = validated_data.pop('options', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if options_data is not None and instance.type == 'MULTIPLE_CHOICE':
            self._persist_mc_options(instance, options_data)
        return instance


def _is_ghost_exam(exam):
    """Active exam is ghost if missing duration or at least one target (group/student)."""
    if exam.status != 'active':
        return False
    has_duration = (exam.duration_minutes or 0) > 0
    has_group = exam.assignments.filter(is_active=True).exists() if hasattr(exam, 'assignments') else False
    has_student = exam.student_assignments.filter(is_active=True).exists() if hasattr(exam, 'student_assignments') else False
    return not (has_duration and (has_group or has_student))


class ExamSerializer(serializers.ModelSerializer):
    assigned_groups = serializers.SerializerMethodField()
    is_ghost = serializers.SerializerMethodField()
    needs_grading = serializers.SerializerMethodField()
    source_type = serializers.CharField(read_only=True)

    class Meta:
        model = Exam
        fields = [
            'id', 'title', 'type', 'source_type', 'start_time', 'status',
            'duration_minutes', 'max_score', 'pdf_file', 'pdf_document',
            'is_result_published', 'is_archived', 'archived_at', 'is_deleted', 'deleted_at',
            'assigned_groups', 'is_ghost', 'needs_grading', 'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'is_deleted', 'deleted_at']

    def get_assigned_groups(self, obj):
        if hasattr(obj, 'assignments'):
            return [{'id': a.group.id, 'name': a.group.name} for a in obj.assignments.all()]
        return []

    def get_is_ghost(self, obj):
        return _is_ghost_exam(obj)

    def get_needs_grading(self, obj):
        """True if exam has at least one submitted attempt not yet published (show in Yoxlama)."""
        return obj.id in self.context.get('unpublished_exam_ids', set())


class ExamRunSerializer(serializers.ModelSerializer):
    group_name = serializers.SerializerMethodField()
    student_name = serializers.SerializerMethodField()
    attempt_count = serializers.SerializerMethodField()

    class Meta:
        model = ExamRun
        fields = [
            'id', 'exam', 'group', 'student', 'group_name', 'student_name',
            'start_at', 'end_at', 'duration_minutes', 'status',
            'created_by', 'created_at', 'attempt_count',
        ]
        read_only_fields = ['id', 'created_at']

    def get_group_name(self, obj):
        return obj.group.name if obj.group else None

    def get_student_name(self, obj):
        return obj.student.full_name if obj.student else None

    def get_attempt_count(self, obj):
        if hasattr(obj, '_attempt_count'):
            return obj._attempt_count
        return obj.attempts.filter(is_archived=False).count()


_BANK_EXAM_QUESTION_TYPE_ORDER = {
    'MULTIPLE_CHOICE': 0,
    'OPEN_SINGLE_VALUE': 1,
    'OPEN_ORDERED': 1,
    'OPEN_UNORDERED': 1,
    'OPEN_PERMUTATION': 1,
    'SITUATION': 2,
}


def _ordered_exam_question_rows(exam):
    """Same ordering as student BANK blueprint: type band (closed → open → situation), then exam order."""
    eqs = list(exam.exam_questions.select_related('question').prefetch_related('question__options').all())
    if getattr(exam, 'source_type', None) == 'BANK':
        eqs.sort(key=lambda eq: (_BANK_EXAM_QUESTION_TYPE_ORDER.get(eq.question.type, 99), eq.order))
    else:
        eqs.sort(key=lambda eq: (eq.order, eq.id))
    return eqs


def _bank_question_answer_kind(qtype):
    if qtype == 'MULTIPLE_CHOICE':
        return 'mc'
    if qtype == 'SITUATION':
        return 'situation'
    return 'open'


def _format_bank_question_correct_display(q):
    """
    Human-readable correct answer for teacher Cavab vərəqi (BANK), not raw JSON.
    """
    t = q.type
    if t == 'MULTIPLE_CHOICE':
        ca = q.correct_answer
        opt_id = None
        if isinstance(ca, dict):
            opt_id = ca.get('option_id')
        elif ca is not None and ca != '':
            try:
                opt_id = int(ca)
            except (TypeError, ValueError):
                opt_id = None
        opt = q.options.filter(pk=opt_id).first() if opt_id is not None else None
        if opt:
            mode = (q.mc_option_display or 'TEXT').upper()
            if mode == 'IMAGE':
                if (opt.label or '').strip():
                    return (opt.label or '').strip()
                return f"Şəkil variantı ({opt.order + 1})"
            return (opt.text or opt.label or str(opt_id)).strip() or str(opt_id)
        return str(ca) if ca is not None and ca != '' else '—'
    if t == 'SITUATION':
        return '—'
    ca = q.correct_answer
    rule = (q.answer_rule_type or 'EXACT_MATCH').upper()
    if rule == 'MATCHING' and isinstance(ca, dict) and ca:
        pairs = []
        for k in sorted(ca.keys(), key=lambda x: str(x)):
            pairs.append(f'{k}-{ca[k]}')
        return ', '.join(pairs)
    if isinstance(ca, dict):
        return (ca.get('text') or ca.get('value') or json.dumps(ca, ensure_ascii=False)).strip() or '—'
    if isinstance(ca, (list, tuple)):
        if rule in ('ANY_ORDER', 'UNORDERED_DIGITS', 'UNORDERED_MATCH'):
            return ', '.join(str(x) for x in ca)
        if all(isinstance(x, (int, float)) or (isinstance(x, str) and len(str(x)) <= 3) for x in ca):
            return ''.join(str(int(x)) if isinstance(x, float) and x == int(x) else str(x) for x in ca)
        return ', '.join(str(x) for x in ca)
    if ca is None or ca == '':
        return '—'
    return str(ca).strip()


class ExamQuestionSerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source='question.text', read_only=True)
    question_type = serializers.CharField(source='question.type', read_only=True)
    question_short_title = serializers.CharField(source='question.short_title', read_only=True)

    class Meta:
        model = ExamQuestion
        fields = [
            'id', 'exam', 'question', 'question_text', 'question_short_title', 'question_type', 'order',
        ]


class ExamQuestionDetailSerializer(serializers.ModelSerializer):
    """Teacher exam detail: full preview payload per linked question (BANK preview + ordering)."""
    question_text = serializers.CharField(source='question.text', read_only=True)
    question_type = serializers.CharField(source='question.type', read_only=True)
    question_short_title = serializers.CharField(source='question.short_title', read_only=True)
    question_image_url = serializers.SerializerMethodField()
    mc_option_display = serializers.CharField(source='question.mc_option_display', read_only=True)
    options = serializers.SerializerMethodField()

    class Meta:
        model = ExamQuestion
        fields = [
            'id', 'exam', 'question', 'question_text', 'question_short_title', 'question_type', 'order',
            'question_image_url', 'mc_option_display', 'options',
        ]

    def get_question_image_url(self, obj):
        q = obj.question
        if getattr(q, 'question_image', None) and q.question_image:
            request = self.context.get('request')
            url = q.question_image.url
            if request:
                return request.build_absolute_uri(url)
            return url
        return None

    def get_options(self, obj):
        q = obj.question
        if q.type != 'MULTIPLE_CHOICE':
            return []
        request = self.context.get('request')
        rows = []
        for o in q.options.all().order_by('order', 'id'):
            row = {
                'id': o.id,
                'text': o.text or '',
                'label': (o.label or '') or '',
                'order': o.order,
            }
            if o.image:
                url = o.image.url
                row['image_url'] = request.build_absolute_uri(url) if request else url
            else:
                row['image_url'] = None
            rows.append(row)
        return rows


class ExamDetailSerializer(serializers.ModelSerializer):
    questions = serializers.SerializerMethodField()
    assigned_groups = serializers.SerializerMethodField()
    source_type = serializers.CharField(read_only=True)
    pdf_url = serializers.SerializerMethodField()
    has_answer_key = serializers.SerializerMethodField()
    question_counts = serializers.SerializerMethodField()
    answer_key_preview = serializers.SerializerMethodField()
    runs = serializers.SerializerMethodField()

    class Meta:
        model = Exam
        fields = [
            'id', 'title', 'type', 'source_type', 'start_time', 'status',
            'duration_minutes', 'max_score', 'pdf_file', 'pdf_document', 'pdf_url',
            'is_result_published', 'has_answer_key', 'question_counts', 'answer_key_preview',
            'questions', 'assigned_groups', 'runs', 'created_at',
        ]

    def get_assigned_groups(self, obj):
        if hasattr(obj, 'assignments'):
            return [{'id': a.group.id, 'name': a.group.name} for a in obj.assignments.all()]
        return []

    def get_pdf_url(self, obj):
        request = self.context.get('request')
        if obj.pdf_document and obj.pdf_document.file:
            try:
                if not obj.pdf_document.file.storage.exists(obj.pdf_document.file.name):
                    return None
            except Exception:
                return None
            url = obj.pdf_document.file.url
            if request:
                return request.build_absolute_uri(url)
            return url
        if obj.pdf_file:
            try:
                if not obj.pdf_file.storage.exists(obj.pdf_file.name):
                    return None
            except Exception:
                return None
            url = obj.pdf_file.url
            if request:
                return request.build_absolute_uri(url)
            return url
        return None

    def get_has_answer_key(self, obj):
        if obj.answer_key_json and isinstance(obj.answer_key_json, dict):
            return True
        meta = obj.meta_json if isinstance(obj.meta_json, dict) else {}
        return bool(meta.get('json_import_snapshot'))

    def get_question_counts(self, obj):
        if obj.source_type == 'BANK' and hasattr(obj, 'exam_questions'):
            closed = open_c = situation = 0
            for eq in obj.exam_questions.all():
                t = getattr(eq.question, 'type', None)
                if t == 'MULTIPLE_CHOICE':
                    closed += 1
                elif t in ('OPEN_SINGLE_VALUE', 'OPEN_ORDERED', 'OPEN_UNORDERED', 'OPEN_PERMUTATION'):
                    open_c += 1
                elif t == 'SITUATION':
                    situation += 1
            return {'closed': closed, 'open': open_c, 'situation': situation, 'total': closed + open_c + situation}
        if obj.answer_key_json and isinstance(obj.answer_key_json, dict):
            from .answer_key import get_answer_key_question_counts
            return get_answer_key_question_counts(obj.answer_key_json)
        return None

    def get_questions(self, obj):
        eqs = _ordered_exam_question_rows(obj)
        return ExamQuestionDetailSerializer(eqs, many=True, context=self.context).data

    def get_answer_key_preview(self, obj):
        """Teacher-only: list of { number, kind, correct, open_answer } for Cavab vərəqi (PDF/JSON or BANK)."""
        if obj.source_type == 'BANK':
            rows = []
            for i, eq in enumerate(_ordered_exam_question_rows(obj), start=1):
                q = eq.question
                kind = _bank_question_answer_kind(q.type)
                text = _format_bank_question_correct_display(q)
                if kind == 'mc':
                    rows.append({'number': i, 'kind': kind, 'correct': text, 'open_answer': None})
                elif kind == 'situation':
                    rows.append({'number': i, 'kind': kind, 'correct': None, 'open_answer': 'Situasiya (əl ilə qiymətləndirmə)'})
                else:
                    rows.append({'number': i, 'kind': kind, 'correct': None, 'open_answer': text})
            return rows
        if obj.source_type not in ('PDF', 'JSON') or not obj.answer_key_json or not isinstance(obj.answer_key_json, dict):
            return None
        questions = obj.answer_key_json.get('questions') or []
        return [
            {
                'number': q.get('number'),
                'kind': (q.get('kind') or '').strip().lower(),
                'correct': q.get('correct'),
                'open_answer': q.get('open_answer') or q.get('answer'),
            }
            for q in questions if isinstance(q, dict)
        ]

    def get_runs(self, obj):
        if not hasattr(obj, 'runs'):
            return []
        from django.db.models import Count, Q
        runs = obj.runs.all().select_related('group', 'student').annotate(
            _attempt_count=Count('attempts', filter=Q(attempts__is_archived=False))
        )
        return ExamRunSerializer(runs, many=True, context=self.context).data


class ExamActivateSerializer(serializers.Serializer):
    """Activate exam: set start_time and duration_minutes (required)."""
    start_time = serializers.DateTimeField(required=True)
    duration_minutes = serializers.IntegerField(required=True, min_value=1)


# Student-facing: question with options (IDs for submission), no correct_answer
class QuestionOptionPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionOption
        fields = ['id', 'text', 'order']


class QuestionPublicSerializer(serializers.ModelSerializer):
    options = QuestionOptionPublicSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ['id', 'text', 'type', 'options']


class TeacherPDFSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    file_size_mb = serializers.SerializerMethodField()

    class Meta:
        model = TeacherPDF
        fields = [
            'id', 'title', 'file', 'file_url', 'original_filename', 'file_size', 'file_size_mb',
            'page_count', 'tags', 'year', 'source', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'file_size']

    def get_file_url(self, obj):
        if obj.file:
            # Check if file actually exists on disk
            try:
                if not obj.file.storage.exists(obj.file.name):
                    return None
            except Exception:
                return None
            request = self.context.get('request')
            if request:
                # Build absolute URL for media file
                return request.build_absolute_uri(obj.file.url)
            # Fallback: relative URL
            return obj.file.url if obj.file else None
        return None

    def get_file_size_mb(self, obj):
        if obj.file_size:
            return round(obj.file_size / (1024 * 1024), 2)
        return None

    def create(self, validated_data):
        pdf = TeacherPDF.objects.create(**validated_data)
        if pdf.file:
            try:
                size = pdf.file.size
            except Exception:
                size = 0
            if size <= 0:
                pdf.delete()  # Do not keep 0-byte files (would show empty in viewer)
                raise serializers.ValidationError({'file': 'Uploaded file is empty or unreadable.'})
            pdf.file_size = size
            pdf.save(update_fields=['file_size'])
        return pdf
