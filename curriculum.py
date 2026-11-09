"""
curriculum.py
=============
Structured data for the Algerian Primary School ESL curriculum
(3rd, 4th and 5th Year Primary) plus resource categories, search
helpers (synonyms / stop-words) and small lookup utilities used by the
bot's inline keyboards.

Everything the teacher can click on comes from this file, so editing
the curriculum (e.g. a new textbook edition) only requires changing the
dictionaries below — no other code changes are needed.
"""

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# LEVELS & UNITS
# ---------------------------------------------------------------------------
# Key   -> short code stored in the database and used in callback_data.
# label -> human readable name.
# emoji -> decoration for buttons.
# units -> ordered list of units / sequences (index is used in callback_data
#          to stay well below Telegram's 64-byte callback_data limit).
LEVELS: Dict[str, Dict] = {
    "3PS": {
        "label": "3rd Year Primary",
        "emoji": "🟢",
        "units": [
            "Unit 1: Me, My Family and My Friends",
            "Unit 2: My School",
            "Unit 3: My Home",
            "Unit 4: My Playtime",
            "Unit 5: My Pets",
            "Unit 6: My Fancy Birthday",
            "General 3PS",
        ],
    },
    "4PS": {
        "label": "4th Year Primary",
        "emoji": "🔵",
        "units": [
            "Unit 1: Family & Friends",
            "Unit 2: School",
            "Unit 3: Fun Time & Games",
            "Unit 4: Food & Health",
            "Unit 5: Animals",
            "General 4PS",
        ],
    },
    "5PS": {
        "label": "5th Year Primary",
        "emoji": "🟣",
        "units": [
            "Seq 1: Jobs, Occupations & Hobbies",
            "Seq 2: Hometown & Amenities / Directions",
            "Seq 3: Holidays & Travelling",
            "General 5PS",
        ],
    },
    "General": {
        "label": "All Levels / Administrative / Guides",
        "emoji": "🟠",
        "units": [
            "Official Syllabus & Guides",
            "Flashcard Templates",
            "Classroom Management",
            "Phonics & TPR",
            "Exams & Assessments",
        ],
    },
}

# Convenience view: level -> list of units (matches the specification).
CURRICULUM: Dict[str, List[str]] = {lvl: data["units"] for lvl, data in LEVELS.items()}

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

# Short labels used inside compact result buttons:
# "📥 [3PS] Flashcards My Family (🖼️ Flashcards)"
CATEGORY_SHORT: Dict[str, str] = {
    "📄 Lesson Plan": "📄 Lesson Plan",
    "🖼️ Flashcards / Posters": "🖼️ Flashcards",
    "📝 Worksheet / Exam": "📝 Worksheet",
    "🎵 Song / Audio": "🎵 Audio",
    "🎬 Video": "🎬 Video",
    "🎮 Game / Activity": "🎮 Game",
}

# ---------------------------------------------------------------------------
# FILE TYPES accepted by the upload wizard
# ---------------------------------------------------------------------------
FILE_TYPES: Dict[str, str] = {
    "document": "📎 Document",
    "photo": "🖼️ Photo",
    "video": "🎬 Video",
    "audio": "🎵 Audio",
}

# ---------------------------------------------------------------------------
# SEARCH HELPERS
# ---------------------------------------------------------------------------
# Words teachers commonly type that should be mapped to the canonical
# vocabulary stored in the database. Algerian teachers often use the
# French abbreviations "3AP / 4AP / 5AP" (3ème Année Primaire).
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
    "seq1": "seq 1",
    "seq2": "seq 2",
    "seq3": "seq 3",
    "unit1": "unit 1",
    "unit2": "unit 2",
    "unit3": "unit 3",
    "unit4": "unit 4",
    "unit5": "unit 5",
    "unit6": "unit 6",
}

# Words ignored during keyword search because they appear everywhere and
# would only add noise to the ranking.
STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "at",
    "with", "my", "me", "i", "is", "are", "please", "pls", "file", "files",
    "about", "de", "la", "le", "les", "des", "du", "et", "pour",
}


# ---------------------------------------------------------------------------
# LOOKUP UTILITIES
# ---------------------------------------------------------------------------
def get_level_keys() -> List[str]:
    """Return the ordered list of level codes."""
    return list(LEVELS.keys())


def is_valid_level(level: str) -> bool:
    return level in LEVELS


def level_label(level: str) -> str:
    """'3PS' -> '3PS — 3rd Year Primary'."""
    data = LEVELS.get(level)
    return f"{level} — {data['label']}" if data else level


def level_button(level: str) -> str:
    """'3PS' -> '🟢 3PS — 3rd Year Primary'."""
    data = LEVELS.get(level)
    return f"{data['emoji']} {level} — {data['label']}" if data else level


def get_units(level: str) -> List[str]:
    return LEVELS.get(level, {}).get("units", [])


def get_unit(level: str, index: int) -> Optional[str]:
    units = get_units(level)
    if 0 <= index < len(units):
        return units[index]
    return None


def get_unit_index(level: str, unit: str) -> int:
    try:
        return get_units(level).index(unit)
    except ValueError:
        return -1


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
            "🔎 <b>Search:</b> just type keywords (e.g. <code>3ps family flashcards</code>)\n"
            "📂 <b>Browse:</b> tap <i>Browse by Level</i>\n"
            "🎒 <b>My Bag:</b> save your favorite files for Sunday prep (/favorites)\n"
            "🏆 <b>Leaderboard:</b> celebrate top teacher contributors (/leaderboard)\n"
            "🔔 <b>Alerts:</b> get notified when new files drop for your level (/alerts)"
        ),
        "choose_level": "📂 <b>Browse the library</b>\n\nChoose a level:",
        "bag_title": "🎒 <b>My Saved Bag</b> — page {page}/{pages}\n\nTap a resource to download it 👇",
        "bag_empty": "🎒 Your saved bag is empty.\nTap <b>⭐ Save to Bag</b> under any downloaded file to bookmark it here!",
        "lang_saved": "✅ Language updated to <b>🇬🇧 English</b>.",
    },
    "ar": {
        "welcome_header": "👋 مرحباً بك أستاذ(ة) <b>{name}</b>!",
        "welcome_body": (
            "أنا <b>مكتبة اللغة الإنجليزية للطور الابتدائي</b> 🇩🇿 (<b>3PS • 4PS • 5PS</b>).\n\n"
            "🔎 <b>البحث المباشر:</b> اكتب أي كلمات مفتاحية (مثال: <code>3ps family flashcards</code>)\n"
            "📂 <b>التصفح حسب المستوى:</b> اضغط على <i>Browse by Level</i>\n"
            "🎒 <b>حقيبتي:</b> احفظ ملفاتك المفضلة لتحضير الدروس (/favorites)\n"
            "🏆 <b>لوحة الشرف:</b> أفضل الأساتذة المساهمين (/leaderboard)\n"
            "🔔 <b>التنبيهات:</b> اشترك في إشعارات مستواك الدراسي (/alerts)"
        ),
        "choose_level": "📂 <b>تصفح المكتبة حسب المستوى</b>\n\nاختر المستوى الدراسي:",
        "bag_title": "🎒 <b>حقيبتي المحفوظة</b> — صفحة {page}/{pages}\n\nاضغط على أي ملف لتحميله 👇",
        "bag_empty": "🎒 حقيبتك فارغة حالياً.\nاضغط على <b>⭐ Save to Bag</b> أسفل أي ملف لحفظه هنا!",
        "lang_saved": "✅ تم تغيير اللغة إلى <b>🇩🇿 العربية</b>.",
    },
    "fr": {
        "welcome_header": "👋 Bienvenue, <b>{name}</b> !",
        "welcome_body": (
            "Bienvenue sur la <b>Bibliothèque Primary ESL DZ</b> 🇩🇿 pour les enseignants "
            "d'anglais du primaire en Algérie (<b>3PS • 4PS • 5PS</b>).\n\n"
            "🔎 <b>Rechercher :</b> tapez vos mots-clés (ex. <code>3ps family flashcards</code>)\n"
            "📂 <b>Parcourir :</b> appuyez sur <i>Browse by Level</i>\n"
            "🎒 <b>Mon Cartable :</b> gardez vos fichiers favoris (/favorites)\n"
            "🏆 <b>Classement :</b> meilleurs enseignants contributeurs (/leaderboard)\n"
            "🔔 <b>Alertes :</b> recevez une notification pour vos niveaux (/alerts)"
        ),
        "choose_level": "📂 <b>Parcourir la bibliothèque</b>\n\nChoisissez un niveau :",
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

