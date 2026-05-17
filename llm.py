import config
import requests
import logging
from typing import Optional, List, Dict

SYSTEM_PROMPT = "You are a professional financial news editor for a Russian Telegram channel."


def _call_openrouter(prompt: str, system: str) -> Optional[str]:
    """Call OpenRouter API. Returns None on rate limit (429) or error."""
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY.strip()}",
        "HTTP-Referer": "https://github.com/tinatru/tgbotparser",
        "X-Title": "Fincash Bot",
        "Content-Type": "application/json"
    }
    data = {
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1200
    }
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=data, timeout=90
        )
        if response.status_code == 429:
            logging.warning("OpenRouter rate limit hit (429). Switching to Groq...")
            return None
        response.raise_for_status()
        result = response.json()
        if "choices" in result and result["choices"]:
            return result["choices"][0]["message"]["content"]
        logging.error(f"Unexpected OpenRouter response: {result}")
        return None
    except Exception as e:
        logging.error(f"OpenRouter error: {e}")
        return None


def _call_groq(prompt: str, system: str) -> Optional[str]:
    """Call Groq API as fallback."""
    if not config.GROQ_API_KEY:
        logging.warning("GROQ_API_KEY not set.")
        return None

    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "openai/gpt-oss-safeguard-20b",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1200
    }
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=data, timeout=90
        )
        response.raise_for_status()
        result = response.json()
        if "choices" in result and result["choices"]:
            logging.info("Groq responded successfully.")
            return result["choices"][0]["message"]["content"]
        logging.error(f"Unexpected Groq response: {result}")
        return None
    except Exception as e:
        logging.error(f"Groq error: {e}")
        return None


def _call_llm(prompt: str, system: str = SYSTEM_PROMPT) -> Optional[str]:
    """Try OpenRouter first, fall back to Groq on rate limit."""
    result = _call_openrouter(prompt, system)
    if result is not None:
        return result
    return _call_groq(prompt, system)


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
