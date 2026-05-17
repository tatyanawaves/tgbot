import feedparser
import requests
from bs4 import BeautifulSoup
import re
import logging
from typing import List, Dict, Optional

# Domains that block direct scraping — use RSS summary only
BLOCKED_DOMAINS = ['rbc.ru', 'vedomosti.ru']

def _is_blocked_domain(url: str) -> bool:
    for domain in BLOCKED_DOMAINS:
        if domain in url:
            return True
    return False

def fetch_rss_news(feed_url: str, limit: Optional[int] = None) -> List[Dict]:
    """Parse RSS feed and return list of news items."""
    try:
        feed = feedparser.parse(feed_url)
        news = []
        entries = feed.entries[:limit] if limit else feed.entries[:15]  # Cap at 15 per source
        for entry in entries:
            # For blocked domains, skip full-text extraction — use RSS summary
            if _is_blocked_domain(entry.link):
                text = ""
            else:
                text = extract_text_from_url(entry.link)

            if not text:
                summary = getattr(entry, 'summary', '')
                if summary:
                    summary = BeautifulSoup(summary, 'html.parser').get_text(separator=' ', strip=True)
                text = summary

            # Clean source name
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'ru-RU,ru;q=0.9',
        }
        response = requests.get(url, headers=headers, timeout=8)

        if response.status_code in (401, 403):
            return ""

        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'lxml')

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.extract()

        article = soup.find('article') or soup.find('div', class_='article__text') or soup.find('div', class_='l-col-main')

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
        response = requests.get(url, headers=headers, timeout=10)
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
                if len(news) >= 10:  # Cap TG posts per channel
                    break
        return news
    except Exception as e:
        logging.error(f"Error fetching channel {channel_name}: {type(e).__name__}")
        return []

def get_latest_news(rss_feeds: List[str], telegram_channels: List[str], max_items_per_source: Optional[int] = None) -> List[Dict]:
    """Fetch from all sources."""
    all_news = []

    for feed in rss_feeds:
        logging.info(f"Fetching RSS: {feed.split('/')[2]}")
        all_news.extend(fetch_rss_news(feed, limit=max_items_per_source))

    for channel in telegram_channels:
        logging.info(f"Fetching TG: @{channel}")
        all_news.extend(fetch_telegram_channel(channel, limit=max_items_per_source))

    logging.info(f"Total fetched: {len(all_news)} items")
    return all_news
