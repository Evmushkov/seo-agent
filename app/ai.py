import os
import json
import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

client: AsyncAnthropic | None = None


def init_client():
    global client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    client = AsyncAnthropic(api_key=api_key)


async def suggest_keywords(page_content: dict) -> list[str]:
    """Use Claude to suggest SEO keywords for a page."""
    if not client:
        init_client()

    title = page_content.get("title", "")
    h1 = page_content.get("h1", "")
    meta = page_content.get("meta_description", "")
    meta_kw = page_content.get("meta_keywords", "")
    # Send a trimmed body to save tokens
    body_preview = page_content.get("body_text", "")[:2000]

    prompt = f"""Ты — SEO-специалист для интернет-магазина часов.

Вот данные страницы:
- URL: {page_content.get('url', '')}
- Title: {title}
- H1: {h1}
- Meta description: {meta}
- Meta keywords: {meta_kw}
- Начало текста страницы: {body_preview}

Задача: предложи 8-12 ключевых слов/фраз, по которым эта страница ДОЛЖНА находиться в поисковой выдаче.
Ключи должны быть коммерческими (то, что вводит покупатель в поиске) — например: "купить часы casio", "мужские часы orient", "швейцарские часы цена".

Верни ТОЛЬКО JSON-массив строк, без пояснений:
["ключ 1", "ключ 2", ...]"""

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Parse JSON
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        keywords = json.loads(text.strip())
        if isinstance(keywords, list):
            return [str(k) for k in keywords]
    except Exception as e:
        logger.error(f"Claude keyword suggestion failed: {e}")

    # Fallback: extract from title and meta
    fallback = []
    for src in [title, h1, meta_kw]:
        words = [w.strip() for w in src.replace(",", " ").split() if len(w.strip()) > 3]
        fallback.extend(words[:4])
    return fallback[:8] if fallback else ["часы", "купить часы"]


async def generate_recommendations(page_content: dict, analysis: dict) -> str:
    """Use Claude to generate SEO recommendations for a page."""
    if not client:
        init_client()

    prompt = f"""Ты — SEO-специалист. Вот результаты анализа контента страницы интернет-магазина часов:

URL: {page_content.get('url', '')}
Title: {page_content.get('title', '')}
H1: {page_content.get('h1', '')}
Meta description: {page_content.get('meta_description', '')}

Общее кол-во слов в контенте: {analysis.get('total_words', 0)}
Доля технического текста: {analysis.get('tech_ratio', 0)}%
Оценка контента: {analysis.get('content_score', 0)}/100

ТОП-10 слов на странице: {analysis.get('top_words', [])[:10]}

Ключевые слова и их наличие:
{json.dumps(analysis.get('keyword_presence', {}), ensure_ascii=False, indent=2)}

Дай краткие (3-5 пунктов) конкретные рекомендации по улучшению SEO-контента этой страницы.
Фокусируйся на:
1. Что добавить/исправить в контенте
2. Какие ключевые слова нужно включить в текст
3. Проблемы с title/h1/meta
4. Соотношение полезного контента vs технического мусора

Отвечай кратко, по делу, на русском. Без вступлений."""

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude recommendations failed: {e}")
        return "Не удалось сгенерировать рекомендации. Проверьте API-ключ."
