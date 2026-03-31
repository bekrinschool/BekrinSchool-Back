"""Generate validated exam-it-security-30.answer_key.json (22+5+3)."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.answer_key import validate_answer_key_json

TITLE = "İT və şəbəkə təhlükəsizliyi (tam imtahan, 30 sual)"
SUBJECT_NOTE = (
    "Platforma qaydası: type=exam üçün 22 qapalı + 5 açıq + 3 situasiya. "
    "İstənilən 20/6/4 payı üçün type=quiz istifadə edin və ya backend limitini dəyişin."
)

questions = []
keys4 = ["A", "B", "C", "D"]
keys5 = ["A", "B", "C", "D", "E"]

mc_fixed = [
    (1, "Firewall əsasən nə üçün istifadə olunur?", ["Daxili şəbəkəni xarici təhdidlərdən filtrləmək", "Yalnız e-poçt göndərmək", "Verilənlər bazası nüsxələmək", "DNS server qurmaq"], "A", 4),
    (2, "Phishing hücumunda hücumçu əsasən nəyə can atır?", ["İstifadəçini saxta səhifəyə yönləndirmək", "Fiziki server oğurluğu", "Printer kağızını dəyişmək", "Monitor parlaqlığını artırmaq"], "A", 4),
    (3, "Güclü parol üçün hansı tövsiyə daha düzgündür?", ["Uzunluq və müxtəlif simvollar", "Ad və doğum tarixi", "Yalnız rəqəmlər", "Bütün hesablarda eyni parol"], "A", 4),
    (4, "VPN əsas funksiyası hansıdır?", ["Şifrəli tunel ilə trafikin qorunması", "Virus skaneri", "E-poçt spam filtri", "SSD sürətini artırmaq"], "A", 4),
    (5, "Malware terminində trojan xüsusiyyəti:", ["Zərərli kodu legit proqram kimi maskalamaq", "Yalnız şəbəkə kabelini kəsmək", "Monitoru söndürmək", "Klaviatura dili dəyişmək"], "A", 4),
    (6, "2FA (iki faktorlu autentifikasiya) nə əlavə edir?", ["İkinci sübut faktoru (məs. OTP)", "İkinci e-poçt ünvanı", "İki monitor", "İki CPU"], "A", 4),
    (7, "SQL injection hansı təbəqədə risk yaradır?", ["Tətbiq/verilənlər bazası interfeysi", "Fiziki server otağı", "Printer drayveri", "USB siçan"], "A", 4),
    (8, "HTTPS ilə HTTP arasındakı əsas fərq:", ["TLS ilə kanal şifrələməsi", "Rəng fərqi", "Fayl ölçüsü", "Yalnız mobil şəbəkə"], "A", 4),
    (9, "Zero-day zəifliyi nədir?", ["Hələ rəsmi yamaq olmayan məlum boşluq", "Köhnə əməliyyat sistemi", "Boş disk sahəsi", "İstifadəçi adının unudulması"], "A", 4),
    (10, "Ransomware tipik hədəfi:", ["Faylları şifrələyib fidye tələb etmək", "Wi-Fi adını dəyişmək", "Brauzer tarixçəsini silmək", "Ekran qətnaməsini artırmaq"], "A", 4),
    (11, "DDoS hücumu nə edir?", ["Xidməti resurs tükətmə ilə əlçatmaz etmək", "Parolu sıfırlamaq", "Antivirus yeniləmək", "DNS cache təmizləmək"], "A", 4),
    (12, "SOC (Security Operations Center) rolu:", ["Təhlükəsizlik hadisələrini monitorinq və cavab", "Satış hesabatı", "Maaş hesablanması", "Dizayn şablonları"], "A", 4),
    (13, "Least privilege prinsipi:", ["Minimum lazım icazə vermək", "Hər kəsə admin hüququ", "Parolsuz giriş", "Bütün portları açmaq"], "A", 4),
    (14, "SIEM aləti əsasən nə toplayır?", ["Log və təhlükəsizlik hadisələrini korrelyasiya", "Yalnız foto", "Musiqini sıxışdırmaq", "Printer sırası"], "A", 4),
    (15, "XSS (Cross-Site Scripting) əsas riski:", ["Brauzerdə zərərli skript icra mühiti", "Printer kağızı", "Kabel uzunluğu", "CPU soyutması"], "A", 4),
    (16, "CSRF hücumunda istifadə olunan mexanizm:", ["Səlahiyyətli sessiyadan istifadə edən saxta sorğu", "USB formatlama", "Monitor kalibrləmə", "SSD TRIM"], "A", 4),
    (17, "Patch idarəetməsinin məqsədi:", ["Zəiflikləri bağlayan yeniləmələri tətbiq etmək", "İşçi cədvəli", "Logo dizaynı", "Domain almaq"], "A", 4),
    (18, "Public key kriptoqrafiyasında açar cütlüyü:", ["Açıq və gizli açar", "İki eyni parol", "İki monitor", "İki router"], "A", 4),
    (19, "Hash funksiyasının xüsusiyyəti (parol saxlama kontekstində):", ["Təkrarsızlıq və tərsinməzlik gözləntisi", "Geri çevrilən sıxışdırma", "Yalnız şəkil üçün", "DNS əvəzləmə"], "A", 4),
    (20, "Insider threat nədir?", ["Təşkilat daxilindən təhdid", "Yalnız xarici hacker", "İnternet provayder", "Buludun qiyməti"], "A", 4),
    (21, "Incident response planında ilk addım adətən:", ["Aşkarlama və təsnifat", "Marketinq kampaniyası", "Ofis təmiri", "Domain satışı"], "A", 4),
    (
        22,
        "Ən yaxşı təcrübə: parol sızıntısı aşkarlananda ilk addım hansıdır?",
        [
            "Hesabları sıfırlamaq/yeniləmək və təsir analizi",
            "Yalnız monitoru söndürmək",
            "DNS serveri silmək",
            "Bütün e-poçtları silmək",
            "Heç nə etməmək",
        ],
        "A",
        5,
    ),
]

for num, stem, opts, correct, nopt in mc_fixed:
    k = keys5 if nopt == 5 else keys4
    ol = []
    for j, t in enumerate(opts):
        ol.append({"key": k[j], "text": t, "image_url": None})
    questions.append(
        {
            "number": num,
            "kind": "mc",
            "prompt": stem,
            "text": stem,
            "image_url": None,
            "options": ol,
            "correct": correct,
        }
    )

questions.extend(
    [
        {
            "number": 23,
            "kind": "open",
            "open_rule": "EXACT_MATCH",
            "prompt": 'SOC analitiki üçün "CIA triadası"nın üç sütununu vergüllə ayıraraq yazın.',
            "text": 'SOC analitiki üçün "CIA triadası"nın üç sütununu vergüllə ayıraraq yazın.',
            "image_url": None,
            "options": [],
            "open_answer": "məxfilik, bütövlük, əlçatanlıq",
        },
        {
            "number": 24,
            "kind": "open",
            "open_rule": "EXACT_MATCH",
            "prompt": "Brauzerdə saxlanan parolların əsas riskini (bir cümlə) qeyd edin.",
            "text": "Brauzerdə saxlanan parolların əsas riskini (bir cümlə) qeyd edin.",
            "image_url": None,
            "options": [],
            "open_answer": "cihaz və ya brauzer kompromet olduqda parolların oğurlanması riski",
        },
        {
            "number": 25,
            "kind": "open",
            "open_rule": "UNORDERED_MATCH",
            "prompt": "Aşağıdakı üç təhlükəsizlik alətini vergüllə ayırın (sıra önəmli deyil): SIEM, IDS, WAF",
            "text": "Aşağıdakı üç təhlükəsizlik alətini vergüllə ayırın (sıra önəmli deyil): SIEM, IDS, WAF",
            "image_url": None,
            "options": [],
            "open_answer": "SIEM, IDS, WAF",
        },
        {
            "number": 26,
            "kind": "open",
            "open_rule": "UNORDERED_MATCH",
            "prompt": "Üç əməliyyat sistemini yazın (vergüllə, sıra önəmli deyil): Linux, Windows, macOS",
            "text": "Üç əməliyyat sistemini yazın (vergüllə, sıra önəmli deyil): Linux, Windows, macOS",
            "image_url": None,
            "options": [],
            "open_answer": "Linux, Windows, macOS",
        },
        {
            "number": 27,
            "kind": "open",
            "open_rule": "MATCHING",
            "prompt": (
                "Sütun 1-dəki rəqəmləri sütun 2-dəki hərflərlə uyğunlaşdırın. "
                "Cavabınızı 1-a, 2-b, 3-c, 4-d formatında yazın (boşluqsuz da qəbul olunur).\n"
                "1) Firewall  2) VPN  3) IDS  4) SIEM\n"
                "a) Monitorinq və korrelyasiya  b) Şəbəkə trafikinin şifrələnməsi  "
                "c) Siyasətə əsaslanan filtrasiya  d) Şəbəkə hücumlarının aşkarlanması"
            ),
            "text": "Uyğunluq: 1=Firewall, 2=VPN, 3=IDS, 4=SIEM — a,b,c,d ilə.",
            "image_url": None,
            "options": [],
            "matching_left": ["1", "2", "3", "4"],
            "matching_right": ["a", "b", "c", "d", "e"],
            "open_answer": "1-c2-b3-d4-a",
        },
    ]
)

sit = [
    (
        28,
        "Situasiya 1 — Şəbəkə insidenti\n"
        "(1) Müştəri şikayət edir: ofis Wi-Fi parolu divarda yazılıb. Hansı iki tədbiri dərhal təklif edirsiniz?\n"
        "(2) Bu riski \"insider\" və ya \"fiziki təhlükəsizlik\" çərçivəsində qısa təsnif edin.\n"
        "(3) Qaralama ilə sadə şəbəkə sxemi (router, AP, istifadəçi) çəkin.",
    ),
    (
        29,
        "Situasiya 2 — E-poçt təhdidi\n"
        "(A) CEO-dan tələsik köçürmə tələb edən e-poçt gəlib. İlk yoxlama addımlarını yazın.\n"
        "(B) İşçiyə bir cümləlik təlim məsləhəti verin.\n"
        "(C) Qaralama ilə \"report phishing\" axını üçün qutu diaqramı çəkin.",
    ),
    (
        30,
        "Situasiya 3 — Bulud konfiqurasiyası\n"
        "1) S3 bucket ictimai oxuma açıqdır — riski izah edin.\n"
        "2) Minimum düzəliş (policy/ACL) tövsiyəniz.\n"
        "3) Qaralama ilə \"public / private\" bucket fərqini şəkil çəkin.",
    ),
]
for num, stem in sit:
    questions.append(
        {
            "number": num,
            "kind": "situation",
            "prompt": stem,
            "text": stem,
            "image_url": None,
            "options": [],
            "max_multiplier": 1,
        }
    )

stable = {str(i): f"q_30_{i:02d}" for i in range(1, 31)}
answer_key = {
    "format_version": 1,
    "description": "OMR və çap üçün xülasə; qiymətləndirmə answer_key_json.questions ilə sinxron saxlanılmalıdır.",
    "by_number": {},
    "mc_omr_keys": [],
}
for q in questions:
    n = q["number"]
    if q["kind"] == "mc":
        answer_key["by_number"][str(n)] = {"id": stable[str(n)], "kind": "mc", "correct_key": q["correct"]}
        answer_key["mc_omr_keys"].append(f"{n}:{q['correct']}")
    elif q["kind"] == "open":
        answer_key["by_number"][str(n)] = {
            "id": stable[str(n)],
            "kind": "open",
            "open_rule": q["open_rule"],
            "correct": q.get("open_answer"),
        }
    else:
        answer_key["by_number"][str(n)] = {
            "id": stable[str(n)],
            "kind": "situation",
            "correct": None,
            "note": "Əl ilə qiymətləndirmə + canvas",
        }

data = {
    "type": "exam",
    "title": TITLE,
    "subject": "İT və şəbəkə təhlükəsizliyi",
    "_documentation": {
        "student_page": "bekrin-front/app/(student)/student/exams/page.tsx",
        "canonical_keys": "number, kind, prompt, text, options[{key,text}], correct, open_answer, open_rule",
        "aliases_not_preserved_on_normalize": "content, correct_answer (istifadə etməyin — normalize silir)",
        "platform_exam_counts": "22 mc + 5 open + 3 situation",
        "user_requested_alternate_counts": SUBJECT_NOTE,
        "pdf_variants_AB": "answer_key_json-da A/B variant dəstəyi yoxdur — iki ayrı imtahan və ya iki PDF faylı lazımdır.",
        "cavab_vereqi_api": "ExamSerializer.answer_key_preview: [{number, kind, correct, open_answer}]",
    },
    "stable_question_ids": stable,
    "answer_key": answer_key,
    "questions": questions,
}

# Full payload (stems, image_url, OMR summary) — do not write normalize-only output; it strips text/prompt.
ok, err = validate_answer_key_json(data)
print("valid", ok)
if err:
    print("errors", err)
if ok:
    out = _ROOT / "docs" / "exam-it-security-30.answer_key.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)
