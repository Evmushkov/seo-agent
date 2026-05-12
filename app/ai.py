import os
import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Available models (can be extended)
MODELS = {
    "claude-sonnet-4": "anthropic/claude-sonnet-4",
    "claude-haiku": "anthropic/claude-3.5-haiku",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gemini-2.5-flash": "google/gemini-2.5-flash-preview",
    "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
}

DEFAULT_MODEL = "claude-sonnet-4"


def get_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY not set")
    return key


def get_model_id(model_name: str = None) -> str:
    name = model_name or os.getenv("AI_MODEL", DEFAULT_MODEL)
    return MODELS.get(name, name)  # allow raw model IDs too


def list_models() -> dict:
    return MODELS.copy()


async def chat(system_prompt: str, user_prompt: str, model: str = None, max_tokens: int = 1000) -> str:
    """Send a chat completion request to OpenRouter."""
    api_key = get_api_key()
    model_id = get_model_id(model)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://seo-agent.app",
                "X-Title": "SEO Content Agent",
            },
            json={
                "model": model_id,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError(f"Empty response from {model_id}")
    return choices[0]["message"]["content"].strip()


def parse_json_response(text: str):
    """Extract JSON from model response (handles markdown fences)."""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


async def suggest_keywords(page_content: dict, model: str = None) -> list[str]:
    """Use AI to suggest SEO keywords for a page."""
    title = page_content.get("title", "")
    h1 = page_content.get("h1", "")
    meta = page_content.get("meta_description", "")
    meta_kw = page_content.get("meta_keywords", "")
    body_preview = page_content.get("body_text", "")[:2000]

    system = "Ты — SEO-специалист. Отвечай строго в формате JSON."

    prompt = f"""Вот данные страницы интернет-магазина часов:
- URL: {page_content.get('url', '')}
- Title: {title}
- H1: {h1}
- Meta description: {meta}
- Meta keywords: {meta_kw}
- Начало текста страницы: {body_preview}

Предложи 8-12 ключевых слов/фраз, по которым эта страница ДОЛЖНА находиться в поиске.
Ключи должны быть коммерческими (то, что вводит покупатель) — например: "купить часы casio", "мужские часы orient".

Верни ТОЛЬКО JSON-массив строк:
["ключ 1", "ключ 2", ...]"""

    try:
        text = await chat(system, prompt, model=model, max_tokens=500)
        result = parse_json_response(text)
        if isinstance(result, list):
            return [str(k) for k in result]
    except Exception as e:
        logger.error(f"Keyword suggestion failed: {e}")

    # Fallback: extract from title and meta
    fallback = []
    for src in [title, h1, meta_kw]:
        words = [w.strip() for w in src.replace(",", " ").split() if len(w.strip()) > 3]
        fallback.extend(words[:4])
    return fallback[:8] if fallback else ["часы", "купить часы"]


async def generate_recommendations(page_content: dict, analysis: dict, model: str = None) -> str:
    """Use AI to generate SEO recommendations for a page."""
    system = "Ты — SEO-специалист. Отвечай кратко, по делу, на русском."

    prompt = f"""Результаты анализа контента страницы интернет-магазина часов:

URL: {page_content.get('url', '')}
Title: {page_content.get('title', '')}
H1: {page_content.get('h1', '')}
Meta description: {page_content.get('meta_description', '')}

Кол-во слов: {analysis.get('total_words', 0)}
Доля технического текста: {analysis.get('tech_ratio', 0)}%
Оценка контента: {analysis.get('content_score', 0)}/100

ТОП-10 слов на странице: {analysis.get('top_words', [])[:10]}

Ключевые слова и их наличие:
{json.dumps(analysis.get('keyword_presence', {}), ensure_ascii=False, indent=2)}

Дай 3-5 конкретных рекомендаций по улучшению SEO-контента:
1. Что добавить/исправить в контенте
2. Какие ключевые слова включить в текст
3. Проблемы с title/h1/meta
4. Соотношение контента vs мусора"""

    try:
        return await chat(system, prompt, model=model, max_tokens=800)
    except Exception as e:
        logger.error(f"Recommendations failed: {e}")
        return "Не удалось сгенерировать рекомендации. Проверьте API-ключ."
