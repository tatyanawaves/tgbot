import feedparser
import requests
from bs4 import BeautifulSoup
import re
import logging
from typing import List, Dict, Optional

# Все домены используют только RSS-summary (без скачивания полного текста).
# Это критично для Render free tier где сеть медленная.
SKIP_FULL_TEXT = True


def fetch_rss_news(feed_url: str, limit: Optional[int] = None) -> List[Dict]:
    """Parse RSS feed and return list of news items."""
    try:
        # feedparser.parse(url) НЕ имеет таймаута и может зависнуть навсегда.
        # Поэтому сначала скачиваем через requests (с таймаутом), потом парсим.
        logging.info(f"  Downloading RSS feed...")
        resp = requests.get(feed_url, timeout=8, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; FincashBot/1.0)'
        })
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        logging.info(f"  Got {len(feed.entries)} entries")

        news = []
        entries = feed.entries[:limit] if limit else feed.entries[:10]
        for entry in entries:
            # Берём summary из RSS — быстро и надёжно
            text = ""
            summary = getattr(entry, 'summary', '')
            if summary:
                text = BeautifulSoup(summary, 'html.parser').get_text(separator=' ', strip=True)

            # Если summary пустой и не skip — пробуем полный текст
            if not text and not SKIP_FULL_TEXT:
                text = extract_text_from_url(entry.link)

            source_name = feed_url.split('/')[2].replace('www.', '')

            news.append({
                "id": entry.link,
                "title": getattr(entry, 'title', ''),
                "url": entry.link,
                "text": text,
                "source": source_name
            })
        return news
    except Exception as e:
        logging.error(f"Error parsing RSS {feed_url}: {e}")
        return []


def extract_text_from_url(url: str) -> str:
    """Attempt to extract the main article text from a URL."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'text/html',
        }
        response = requests.get(url, headers=headers, timeout=5)

        if response.status_code in (401, 403):
            return ""

        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'lxml')

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.extract()

        article = soup.find('article') or soup.find('div', class_='article__text')

        if article:
            paragraphs = article.find_all('p')
        else:
            paragraphs = soup.find_all('p')

        text = " ".join([p.get_text(separator=' ', strip=True) for p in paragraphs])
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        logging.warning(f"Extract failed for {url}: {type(e).__name__}")
        return ""


def fetch_telegram_channel(channel_name: str, limit: Optional[int] = None) -> List[Dict]:
    """Scrape recent posts from public telegram channel."""
    url = f"https://t.me/s/{channel_name}"
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        posts = list(reversed(soup.find_all('div', class_='tgme_widget_message')))

        news = []
        for post in posts:
            msg_id = post.get('data-post')
            if not msg_id:
                continue

            text_div = post.find('div', class_='tgme_widget_message_text')
            if text_div:
                text = text_div.get_text(separator='\n', strip=True)
                post_url = f"https://t.me/{msg_id}"
                news.append({
                    "id": post_url,
                    "title": text[:80],
                    "url": post_url,
                    "text": text,
                    "source": f"@{channel_name}"
                })

                if limit is not None and len(news) >= limit:
                    break
                if len(news) >= 10:
                    break
        return news
    except Exception as e:
        logging.error(f"Error fetching @{channel_name}: {type(e).__name__}")
        return []


def get_latest_news(rss_feeds: List[str], telegram_channels: List[str], max_items_per_source: Optional[int] = None) -> List[Dict]:
    """Fetch from all sources with per-source error isolation."""
    all_news = []

    for feed in rss_feeds:
        source = feed.split('/')[2]
        logging.info(f"Fetching RSS: {source}")
        try:
            items = fetch_rss_news(feed, limit=max_items_per_source)
            all_news.extend(items)
            logging.info(f"  OK: {len(items)} items from {source}")
        except Exception as e:
            logging.error(f"  FAIL: {source}: {e}")

    for channel in telegram_channels:
        logging.info(f"Fetching TG: @{channel}")
        try:
            items = fetch_telegram_channel(channel, limit=max_items_per_source)
            all_news.extend(items)
            logging.info(f"  OK: {len(items)} items from @{channel}")
        except Exception as e:
            logging.error(f"  FAIL: @{channel}: {e}")

    logging.info(f"Total fetched: {len(all_news)} items")
    return all_news
