import config
import requests
import logging
from typing import Optional, List, Dict

SYSTEM_PROMPT = "You are a professional financial news editor for a Russian Telegram channel."


def _extract_content(result: dict, provider: str) -> Optional[str]:
    """Pull the assistant text out of an OpenAI-compatible response.

    Reasoning models (gpt-oss) may return an empty `content` when the whole
    token budget was spent on `reasoning` — treat that as a failure, not as
    a valid empty post.
    """
    choices = result.get("choices") or []
    if not choices:
        logging.error(f"Unexpected {provider} response: {result}")
        return None

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        finish = choices[0].get("finish_reason")
        logging.error(f"{provider} returned empty content (finish_reason={finish}).")
        return None
    return content


def _call_openai_compatible(url: str, api_key: str, model: str, prompt: str,
                            system: str, provider: str,
                            extra_headers: dict = None) -> Optional[str]:
    """Single chat-completions call. Returns None on any error."""
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 2500,
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=90)
        if response.status_code != 200:
            logging.warning(
                f"{provider} [{model}] returned {response.status_code}: {response.text[:300]}"
            )
            return None
        result = _extract_content(response.json(), f"{provider} [{model}]")
        if result:
            logging.info(f"{provider} [{model}] responded successfully.")
        return result
    except Exception as e:
        logging.error(f"{provider} [{model}] error: {e}")
        return None


def _call_groq(prompt: str, system: str) -> Optional[str]:
    """Primary provider: Groq (gpt-oss is free there)."""
    if not config.GROQ_API_KEY:
        logging.warning("GROQ_API_KEY not set.")
        return None

    for model in config.GROQ_MODELS:
        result = _call_openai_compatible(
            "https://api.groq.com/openai/v1/chat/completions",
            config.GROQ_API_KEY, model, prompt, system, "Groq",
        )
        if result:
            return result
    return None


def _call_openrouter(prompt: str, system: str) -> Optional[str]:
    """Fallback provider: OpenRouter, trying each configured model in turn."""
    if not config.LLM_API_KEY:
        logging.warning("LLM_API_KEY (OpenRouter) not set.")
        return None

    extra = {
        "HTTP-Referer": "https://github.com/tinatru/tgbotparser",
        "X-Title": "Fincash Bot",
    }
    for model in config.OPENROUTER_MODELS:
        result = _call_openai_compatible(
            "https://openrouter.ai/api/v1/chat/completions",
            config.LLM_API_KEY, model, prompt, system, "OpenRouter", extra,
        )
        if result:
            return result
    return None


def _call_llm(prompt: str, system: str = SYSTEM_PROMPT) -> Optional[str]:
    """Try Groq first, fall back to OpenRouter."""
    result = _call_groq(prompt, system)
    if result:
        return result
    return _call_openrouter(prompt, system)


def process_text_via_gemini(text: str) -> Optional[str]:
    """Process a single news article."""
    prompt = config.SINGLE_PROMPT.format(text=text)
    return _call_llm(prompt)


def synthesize_digest(articles: List[Dict]) -> Optional[str]:
    """Synthesize multiple articles into one digest post."""
    if not articles:
        return None

    if len(articles) == 1:
        return process_text_via_gemini(articles[0]['text'])

    # Cap at 15 most recent articles to avoid payload too large
    selected = articles[:15]

    parts = []
    for i, art in enumerate(selected, 1):
        title = art.get('title', 'Без заголовка')
        text = art.get('text', '')
        # Shorter truncation to keep total prompt size manageable
        if len(text) > 800:
            text = text[:800] + "..."
        parts.append(f"--- Новость {i} ---\nЗаголовок: {title}\nТекст: {text}")

    news_block = "\n\n".join(parts)
    prompt = config.DIGEST_PROMPT.format(news_block=news_block)
    return _call_llm(prompt)
