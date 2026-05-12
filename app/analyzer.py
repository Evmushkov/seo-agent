import re
from collections import Counter

# Russian stop-words (expanded)
STOP_WORDS = {
    "и", "в", "на", "с", "по", "к", "у", "за", "из", "от", "до", "не", "что", "как",
    "это", "для", "все", "так", "но", "он", "она", "они", "его", "её", "их", "мы",
    "вы", "ты", "же", "бы", "ли", "да", "нет", "о", "а", "или", "ни", "при", "над",
    "под", "без", "был", "была", "было", "были", "быть", "есть", "будет", "будут",
    "мне", "мной", "нас", "вас", "тебя", "себя", "вам", "нам", "тебе", "ему", "ей",
    "им", "ней", "ним", "том", "того", "тому", "тот", "та", "те", "то", "этот", "эта",
    "эти", "этих", "чтобы", "если", "когда", "где", "кто", "чем", "чего", "только",
    "уже", "ещё", "тоже", "также", "очень", "более", "может", "между", "после",
    "перед", "через", "свой", "своя", "свои", "своих", "свою", "наш", "ваш", "какой",
    "какая", "какие", "этом", "этой", "один", "одна", "одно", "два", "три", "весь",
    "вся", "всё", "всех", "ру", "www", "https", "http", "com", "рус", "ваших",
    "нашей", "нашего", "наших", "ваши", "вашей", "вашего", "вашим", "нашим",
    "которые", "который", "которая", "которое", "которых", "которому", "которой",
    "nbsp", "amp", "quot", "mdash", "laquo", "raquo",
    # Common UI words (technical noise)
    "войти", "вход", "регистрация", "пароль", "email", "телефон", "отправить",
    "согласен", "условиями", "политикой", "конфиденциальности", "оферты",
    "оферта", "политика", "восстановить", "повторить", "код", "смс",
    "имя", "фио", "сообщение", "вопрос", "комментарий", "адрес",
}

# Additional technical/UI words to flag
TECH_MARKERS = {
    "войти", "вход", "регистрация", "пароль", "email", "телефон", "отправить",
    "согласен", "условиями", "политикой", "конфиденциальности", "оферты",
    "корзина", "корзину", "избранное", "сравнение", "подписаться", "подписка",
    "cookie", "cookies", "javascript", "включите", "браузере", "меню",
    "каталог", "фильтр", "сортировка", "показать", "ещё", "загрузить",
    "обратная", "связь", "заказать", "звонок", "доставка", "оплата",
    "возврат", "гарантия", "оферта", "политика", "конфиденциальность",
}


def tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens."""
    text = text.lower()
    text = re.sub(r"[^а-яёa-z0-9\s-]", " ", text)
    words = text.split()
    return [w for w in words if len(w) > 2 and w not in STOP_WORDS]


def word_frequencies(text: str, top_n: int = 30) -> list[tuple[str, int]]:
    """Get top N word frequencies from text."""
    tokens = tokenize(text)
    return Counter(tokens).most_common(top_n)


def analyze_content(page_content: dict, keywords: list[str] = None) -> dict:
    """Full content analysis of a page."""
    body = page_content.get("body_text", "")
    nav = page_content.get("nav_text", "")
    link_text = page_content.get("link_texts", "")

    body_tokens = tokenize(body)
    nav_tokens = tokenize(nav)
    all_tokens = body_tokens  # body already has nav removed in crawler

    total_words = len(all_tokens)
    if total_words == 0:
        return {
            "total_words": 0,
            "top_words": [],
            "tech_ratio": 100,
            "keyword_presence": {},
            "content_score": 0,
        }

    # Word frequencies
    freq = Counter(all_tokens)
    top_words = freq.most_common(30)

    # Count technical/UI words in the full page text
    full_text_tokens = tokenize(body + " " + nav)
    tech_count = sum(1 for t in full_text_tokens if t in TECH_MARKERS)
    nav_token_count = len(tokenize(nav))
    tech_ratio = round(((tech_count + nav_token_count) / max(len(full_text_tokens), 1)) * 100)

    # Keyword analysis
    keyword_presence = {}
    if keywords:
        body_lower = body.lower()
        title_lower = (page_content.get("title", "") + " " + page_content.get("h1", "")).lower()
        meta_lower = (page_content.get("meta_description", "") + " " + page_content.get("meta_keywords", "")).lower()

        for kw in keywords:
            kw_lower = kw.lower()
            kw_tokens = kw_lower.split()

            in_title = kw_lower in title_lower
            in_meta = kw_lower in meta_lower
            in_body = kw_lower in body_lower

            # Count occurrences in body
            count = body_lower.count(kw_lower)
            # Also count individual word matches
            token_matches = sum(1 for t in kw_tokens if t in freq)

            keyword_presence[kw] = {
                "found": in_body or in_title or in_meta,
                "in_title": in_title,
                "in_meta": in_meta,
                "in_body": in_body,
                "body_count": count,
                "token_matches": token_matches,
                "total_tokens": len(kw_tokens),
            }

    # Content score (0-100)
    kw_found = sum(1 for v in keyword_presence.values() if v["found"]) if keyword_presence else 0
    kw_total = len(keyword_presence) if keyword_presence else 1
    kw_ratio = kw_found / kw_total

    has_h1 = bool(page_content.get("h1", "").strip())
    has_meta = bool(page_content.get("meta_description", "").strip())
    content_length_score = min(total_words / 300, 1.0)  # 300+ words = full score

    content_score = round(
        (kw_ratio * 40)
        + ((1 - min(tech_ratio, 100) / 100) * 30)
        + (content_length_score * 15)
        + (has_h1 * 8)
        + (has_meta * 7)
    )

    return {
        "total_words": total_words,
        "top_words": [[w, c] for w, c in top_words],
        "tech_ratio": min(tech_ratio, 100),
        "keyword_presence": keyword_presence,
        "keywords_found": kw_found,
        "keywords_total": kw_total,
        "content_score": content_score,
        "has_h1": has_h1,
        "has_meta_description": has_meta,
        "h1": page_content.get("h1", ""),
        "meta_description": page_content.get("meta_description", ""),
    }
