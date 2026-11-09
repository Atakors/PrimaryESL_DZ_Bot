"""
curriculum.py
=============
Structured Algerian Ministry of National Education Primary School ESL
Didactic Guide curriculum tree for **3PS, 4PS, and 5PS**:

    Level  ->  Sequence  ->  Section  ->  Session  (+ Resource Category)

Every Sequence in 3PS, 4PS, and 5PS has its exact two official Sections,
plus an "All Sections / Sequence General" option so teachers can attach
sequence-wide flashcards, exams, or reviews.

Every Section has the official Didactic Guide Sessions:
  - 3PS & 4PS:
      1. Session 1: I Sing & Have Fun / I Listen & Repeat
      2. Session 2: I Read & Discover
      3. Session 3: I Read & Write
      4. Session 4: I Play Roles
      5. All Sessions (Section General)
  - 5PS:
      1. Session 1: I Get Ready (Initial Situation)
      2. Session 2: I Listen & Interact
      3. Session 3: I Listen & Discover
      4. Session 4: I Read & Understand
      5. Session 5: I Read & Discover
      6. Session 6: I Learn to Write
      7. Session 7: I Write
      8. Session 8: I Check My Progress / I Play Roles
      9. All Sessions (Section General)
"""

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# SESSIONS BY LEVEL (Official Algerian Didactic Guide)
# ---------------------------------------------------------------------------
SESSIONS_3PS_4PS: List[str] = [
    "Session 1: I Sing & Have Fun / I Listen & Repeat",
    "Session 2: I Read & Discover",
    "Session 3: I Read & Write",
    "Session 4: I Play Roles",
    "All Sessions (Section General)",
]

SESSIONS_5PS: List[str] = [
    "Session 1: I Get Ready",
    "Session 2: I Listen & Interact",
    "Session 3: I Listen & Discover",
    "Session 4: I Read & Understand",
    "Session 5: I Read & Discover",
    "Session 6: I Learn to Write",
    "Session 7: I Write",
    "Session 8: I Play Roles / Check Progress",
    "All Sessions (Section General)",
]

SESSIONS_GENERAL: List[str] = [
    "All Sessions / General",
    "Term 1",
    "Term 2",
    "Term 3",
]

LEVEL_SESSIONS: Dict[str, List[str]] = {
    "3PS": SESSIONS_3PS_4PS,
    "4PS": SESSIONS_3PS_4PS,
    "5PS": SESSIONS_5PS,
    "General": SESSIONS_GENERAL,
}

# ---------------------------------------------------------------------------
# LEVELS -> SEQUENCES -> SECTIONS
# ---------------------------------------------------------------------------
LEVELS: Dict[str, Dict] = {
    "3PS": {
        "label": "3rd Year Primary",
        "emoji": "🟢",
        "sequences": {
            "Seq 1: Me, My Family and My Friends": [
                "Sec 1: Me and My Family",
                "Sec 2: Me and My Friends",
                "All Sections (Sequence General)",
            ],
            "Seq 2: My School": [
                "Sec 1: My School Objects",
                "Sec 2: My School Subjects & Days",
                "All Sections (Sequence General)",
            ],
            "Seq 3: My Home": [
                "Sec 1: Rooms of the House",
                "Sec 2: Where Is Everyone?",
                "All Sections (Sequence General)",
            ],
            "Seq 4: My Playtime": [
                "Sec 1: My Toys",
                "Sec 2: My Favourite Toy (Colours & Sizes)",
                "All Sections (Sequence General)",
            ],
            "Seq 5: My Pets": [
                "Sec 1: My Pets",
                "Sec 2: My Pet's Body Parts",
                "All Sections (Sequence General)",
            ],
            "Seq 6: My Fancy Birthday": [
                "Sec 1: Birthday Party & Food",
                "Sec 2: Birthday Table & Feelings",
                "All Sections (Sequence General)",
            ],
            "General 3PS": [
                "Diagnostic & Remediation",
                "Term Exams & Assessments",
                "Annual & Sequence Plans",
                "All Sections (3PS General)",
            ],
        },
    },
    "4PS": {
        "label": "4th Year Primary",
        "emoji": "🔵",
        "sequences": {
            "Seq 1: Family & Friends": [
                "Sec 1: Family",
                "Sec 2: Friends",
                "All Sections (Sequence General)",
            ],
            "Seq 2: School": [
                "Sec 1: Facilities",
                "Sec 2: Mapping",
                "All Sections (Sequence General)",
            ],
            "Seq 3: Fun Time & Games": [
                "Sec 1: Fun Time",
                "Sec 2: Games",
                "All Sections (Sequence General)",
            ],
            "Seq 4: Food & Health": [
                "Sec 1: Healthy Food",
                "Sec 2: Health",
                "All Sections (Sequence General)",
            ],
            "Seq 5: Animals": [
                "Sec 1: Farm Animals",
                "Sec 2: Zoo Animals",
                "All Sections (Sequence General)",
            ],
            "General 4PS": [
                "Diagnostic & Pause / Remediation",
                "Term Exams & Assessments",
                "Annual & Sequence Plans",
                "All Sections (4PS General)",
            ],
        },
    },
    "5PS": {
        "label": "5th Year Primary",
        "emoji": "🟣",
        "sequences": {
            "Seq 1: Jobs, Occupations & Hobbies": [
                "Sec 1: Jobs and Occupations",
                "Sec 2: Hobbies",
                "All Sections (Sequence General)",
            ],
            "Seq 2: Hometown & Amenities / Directions": [
                "Sec 1: Hometown / City / Village Amenities",
                "Sec 2: Directions and Signs",
                "All Sections (Sequence General)",
            ],
            "Seq 3: Holidays & Travelling": [
                "Sec 1: Holidays",
                "Sec 2: Travelling",
                "All Sections (Sequence General)",
            ],
            "General 5PS": [
                "Diagnostic & Pause / Remediation",
                "Term Exams & Assessments",
                "Annual & Sequence Plans",
                "All Sections (5PS General)",
            ],
        },
    },
    "General": {
        "label": "All Levels / Administrative / Guides",
        "emoji": "🟠",
        "sequences": {
            "Official Syllabus & Didactic Guides": [
                "3PS Didactic Guide & Syllabus",
                "4PS Didactic Guide & Syllabus",
                "5PS Didactic Guide & Syllabus",
                "All Levels / Inspectorates",
            ],
            "Flashcard & Poster Templates": [
                "Editable Templates",
                "Phonics & Alphabet Posters",
                "Classroom Display & Rules",
            ],
            "Classroom Management & TPR": [
                "Routines & Action Songs",
                "Reward Systems & Badges",
                "Group Work & Games",
            ],
            "Phonics & Handwriting": [
                "Sounds & CVC Words",
                "Cursive & Print Writing",
                "Reading Cards",
            ],
            "Administrative & Assessments": [
                "Daily Logbook & Progressions",
                "Diagnostic & Exam Templates",
                "Inspector Visit Portfolio",
            ],
        },
    },
}

# Backwards-compatible mapping: level -> ordered list of sequence names.
CURRICULUM: Dict[str, List[str]] = {
    lvl: list(data["sequences"].keys()) for lvl, data in LEVELS.items()
}

#Legacy Unit -> New Sequence migration map (so existing DB rows stay aligned).
LEGACY_UNIT_TO_SEQUENCE: Dict[str, str] = {
    "Unit 1: Me, My Family and My Friends": "Seq 1: Me, My Family and My Friends",
    "Unit 2: My School": "Seq 2: My School",
    "Unit 3: My Home": "Seq 3: My Home",
    "Unit 4: My Playtime": "Seq 4: My Playtime",
    "Unit 5: My Pets": "Seq 5: My Pets",
    "Unit 6: My Fancy Birthday": "Seq 6: My Fancy Birthday",
    "Unit 1: Family & Friends": "Seq 1: Family & Friends",
    "Unit 2: School": "Seq 2: School",
    "Unit 3: Fun Time & Games": "Seq 3: Fun Time & Games",
    "Unit 4: Food & Health": "Seq 4: Food & Health",
    "Unit 5: Animals": "Seq 5: Animals",
    "Official Syllabus & Guides": "Official Syllabus & Didactic Guides",
    "Flashcard Templates": "Flashcard & Poster Templates",
    "Classroom Management": "Classroom Management & TPR",
    "Phonics & TPR": "Phonics & Handwriting",
    "Exams & Assessments": "Administrative & Assessments",
}

# ---------------------------------------------------------------------------
# RESOURCE CATEGORIES
# ---------------------------------------------------------------------------
CATEGORIES: List[str] = [
    "📄 Lesson Plan",
    "🖼️ Flashcards / Posters",
    "📝 Worksheet / Exam",
    "🎵 Song / Audio",
    "🎬 Video",
    "🎮 Game / Activity",
]

CATEGORY_SHORT: Dict[str, str] = {
    "📄 Lesson Plan": "📄 Lesson Plan",
    "🖼️ Flashcards / Posters": "🖼️ Flashcards",
    "📝 Worksheet / Exam": "📝 Worksheet",
    "🎵 Song / Audio": "🎵 Audio",
    "🎬 Video": "🎬 Video",
    "🎮 Game / Activity": "🎮 Game",
}

FILE_TYPES: Dict[str, str] = {
    "document": "📎 Document",
    "photo": "🖼️ Photo",
    "video": "🎬 Video",
    "audio": "🎵 Audio",
}

# ---------------------------------------------------------------------------
# SEARCH HELPERS
# ---------------------------------------------------------------------------
SEARCH_SYNONYMS: Dict[str, str] = {
    "3ap": "3ps",
    "4ap": "4ps",
    "5ap": "5ps",
    "3rd": "3ps",
    "4th": "4ps",
    "5th": "5ps",
    "third": "3ps",
    "fourth": "4ps",
    "fifth": "5ps",
    "y3": "3ps",
    "y4": "4ps",
    "y5": "5ps",
    "year3": "3ps",
    "year4": "4ps",
    "year5": "5ps",
    "fiche": "lesson plan",
    "fiches": "lesson plan",
    "lessonplan": "lesson plan",
    "plans": "plan",
    "cards": "card",
    "test": "exam",
    "tests": "exam",
    "quiz": "exam",
    "evaluation": "exam",
    "assessment": "exam",
    "songs": "song",
    "chant": "song",
    "music": "audio",
    "mp3": "audio",
    "mp4": "video",
    "games": "game",
    "activity": "activit",
    "activities": "activit",
    "sequence": "seq",
    "unit": "seq",
    "seq1": "seq 1",
    "seq2": "seq 2",
    "seq3": "seq 3",
    "seq4": "seq 4",
    "seq5": "seq 5",
    "seq6": "seq 6",
    "unit1": "seq 1",
    "unit2": "seq 2",
    "unit3": "seq 3",
    "unit4": "seq 4",
    "unit5": "seq 5",
    "unit6": "seq 6",
    "section": "sec",
    "sec1": "sec 1",
    "sec2": "sec 2",
    "session1": "session 1",
    "session2": "session 2",
    "session3": "session 3",
    "session4": "session 4",
    "session5": "session 5",
    "session6": "session 6",
    "session7": "session 7",
    "session8": "session 8",
}

STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "at",
    "with", "my", "me", "i", "is", "are", "please", "pls", "file", "files",
    "about", "de", "la", "le", "les", "des", "du", "et", "pour",
}


# ---------------------------------------------------------------------------
# LOOKUP UTILITIES (Level -> Sequence -> Section -> Session -> Category)
# ---------------------------------------------------------------------------
def get_level_keys() -> List[str]:
    return list(LEVELS.keys())


def is_valid_level(level: str) -> bool:
    return level in LEVELS


def level_label(level: str) -> str:
    data = LEVELS.get(level)
    return f"{level} — {data['label']}" if data else level


def level_button(level: str) -> str:
    data = LEVELS.get(level)
    return f"{data['emoji']} {level} — {data['label']}" if data else level


def get_sequences(level: str) -> List[str]:
    seqs = LEVELS.get(level, {}).get("sequences", {})
    return list(seqs.keys())


def get_sequence(level: str, index: int) -> Optional[str]:
    seqs = get_sequences(level)
    if 0 <= index < len(seqs):
        return seqs[index]
    return None


def get_sequence_index(level: str, sequence: str) -> int:
    try:
        return get_sequences(level).index(sequence)
    except ValueError:
        return -1


# Backwards-compatible aliases (get_units / get_unit)
get_units = get_sequences
get_unit = get_sequence
get_unit_index = get_sequence_index


def get_sections(level: str, sequence: str) -> List[str]:
    seqs = LEVELS.get(level, {}).get("sequences", {})
    if sequence in seqs:
        return list(seqs[sequence])
    mapped = LEGACY_UNIT_TO_SEQUENCE.get(sequence)
    if mapped and mapped in seqs:
        return list(seqs[mapped])
    return ["All Sections (Sequence General)"]


def get_section(level: str, sequence: str, index: int) -> Optional[str]:
    secs = get_sections(level, sequence)
    if 0 <= index < len(secs):
        return secs[index]
    return None


def get_sessions(level: str) -> List[str]:
    return list(LEVEL_SESSIONS.get(level, SESSIONS_3PS_4PS))


def get_session(level: str, index: int) -> Optional[str]:
    sessions = get_sessions(level)
    if 0 <= index < len(sessions):
        return sessions[index]
    return None


def get_category(index: int) -> Optional[str]:
    if 0 <= index < len(CATEGORIES):
        return CATEGORIES[index]
    return None


def get_category_index(category: str) -> int:
    try:
        return CATEGORIES.index(category)
    except ValueError:
        return -1


def short_category(category: str) -> str:
    return CATEGORY_SHORT.get(category, category)


def file_type_label(file_type: str) -> str:
    return FILE_TYPES.get(file_type, file_type)


# ---------------------------------------------------------------------------
# TRILINGUAL UI STRINGS (EN / AR / FR)
# ---------------------------------------------------------------------------
LANG_NAMES: Dict[str, str] = {
    "en": "🇬🇧 English",
    "ar": "🇩🇿 العربية",
    "fr": "🇫🇷 Français",
}

UI_STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "welcome_header": "👋 Welcome, <b>{name}</b>!",
        "welcome_body": (
            "I'm the <b>Primary ESL DZ Library</b> 🇩🇿 — a shared resource bank for "
            "Algerian primary-school English teachers (<b>3PS • 4PS • 5PS</b>).\n\n"
            "🗂️ <b>Curriculum Tree:</b> Level → Sequence → Section → Session\n"
            "🔎 <b>Search:</b> type keywords (e.g. <code>3ps seq 1 listen</code> or <code>4ps family flashcards</code>)\n"
            "📂 <b>Browse:</b> tap <i>Browse by Level</i>\n"
            "🎒 <b>My Bag:</b> save your favorite files for class prep (/favorites)\n"
            "🏆 <b>Leaderboard:</b> celebrate top teacher contributors (/leaderboard)\n"
            "🔔 <b>Alerts:</b> get notified when new files drop for your level (/alerts)"
        ),
        "choose_level": "📂 <b>Browse the library</b>\n\nStep 1/4 — Choose a <b>Level</b>:",
        "bag_title": "🎒 <b>My Saved Bag</b> — page {page}/{pages}\n\nTap a resource to download it 👇",
        "bag_empty": "🎒 Your saved bag is empty.\nTap <b>⭐ Save to Bag</b> under any downloaded file to bookmark it here!",
        "lang_saved": "✅ Language updated to <b>🇬🇧 English</b>.",
    },
    "ar": {
        "welcome_header": "👋 مرحباً بك أستاذ(ة) <b>{name}</b>!",
        "welcome_body": (
            "أنا <b>مكتبة اللغة الإنجليزية للطور الابتدائي</b> 🇩🇿 (<b>3PS • 4PS • 5PS</b>).\n\n"
            "🗂️ <b>التقسيم الرسمي:</b> المستوى ← المقطع (Sequence) ← الوحدة (Section) ← الحصة (Session)\n"
            "🔎 <b>البحث المباشر:</b> اكتب أي كلمات مفتاحية (مثال: <code>3ps family flashcards</code>)\n"
            "📂 <b>التصفح:</b> اضغط على <i>Browse by Level</i>\n"
            "🎒 <b>حقيبتي:</b> احفظ ملفاتك المفضلة لتحضير الدروس (/favorites)\n"
            "🏆 <b>لوحة الشرف:</b> أفضل الأساتذة المساهمين (/leaderboard)\n"
            "🔔 <b>التنبيهات:</b> اشترك في إشعارات مستواك الدراسي (/alerts)"
        ),
        "choose_level": "📂 <b>تصفح المكتبة</b>\n\nالخطوة 1/4 — اختر <b>المستوى الدراسي</b>:",
        "bag_title": "🎒 <b>حقيبتي المحفوظة</b> — صفحة {page}/{pages}\n\nاضغط على أي ملف لتحميله 👇",
        "bag_empty": "🎒 حقيبتك فارغة حالياً.\nاضغط على <b>⭐ Save to Bag</b> أسفل أي ملف لحفظه هنا!",
        "lang_saved": "✅ تم تغيير اللغة إلى <b>🇩🇿 العربية</b>.",
    },
    "fr": {
        "welcome_header": "👋 Bienvenue, <b>{name}</b> !",
        "welcome_body": (
            "Bienvenue sur la <b>Bibliothèque Primary ESL DZ</b> 🇩🇿 pour les enseignants "
            "d'anglais du primaire en Algérie (<b>3PS • 4PS • 5PS</b>).\n\n"
            "🗂️ <b>Arborescence officielle :</b> Niveau → Sequence → Section → Session\n"
            "🔎 <b>Rechercher :</b> tapez vos mots-clés (ex. <code>3ps family flashcards</code>)\n"
            "📂 <b>Parcourir :</b> appuyez sur <i>Browse by Level</i>\n"
            "🎒 <b>Mon Cartable :</b> gardez vos fichiers favoris (/favorites)\n"
            "🏆 <b>Classement :</b> meilleurs enseignants contributeurs (/leaderboard)\n"
            "🔔 <b>Alertes :</b> recevez une notification pour vos niveaux (/alerts)"
        ),
        "choose_level": "📂 <b>Parcourir la bibliothèque</b>\n\nÉtape 1/4 — Choisissez un <b>Niveau</b> :",
        "bag_title": "🎒 <b>Mon Cartable</b> — page {page}/{pages}\n\nAppuyez sur une ressource pour la télécharger 👇",
        "bag_empty": "🎒 Votre cartable est vide.\nAppuyez sur <b>⭐ Save to Bag</b> sous un fichier pour l'enregistrer !",
        "lang_saved": "✅ Langue définie sur <b>🇫🇷 Français</b>.",
    },
}


def tr(lang: str, key: str, **kwargs) -> str:
    table = UI_STRINGS.get(lang) or UI_STRINGS["en"]
    template = table.get(key) or UI_STRINGS["en"].get(key, key)
    return template.format(**kwargs) if kwargs else template


def contributor_badge(files_count: int) -> str:
    if files_count >= 15:
        return "🥇 Gold Contributor"
    if files_count >= 5:
        return "🥈 Silver Contributor"
    if files_count >= 1:
        return "🥉 Bronze Contributor"
    return "🌱 New Member"
