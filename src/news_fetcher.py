"""
Auto News Fetcher — fetches real breaking news from free RSS feeds,
translates to Russian, and writes jobs/current_job.json automatically.

Sources (all free, no API key):
  - Reuters World News
  - BBC World News
  - Al Jazeera English
  - Associated Press (via RSS)

Translation: deep-translator (wraps Google Translate, free, no key)

Run this before pipeline.py to get a fresh news job every time.
"""

import json
import os
import re
import sys
import time
import hashlib
import datetime
import feedparser
import requests

try:
    from deep_translator import GoogleTranslator
    TRANSLATE_AVAILABLE = True
except ImportError:
    TRANSLATE_AVAILABLE = False
    print("[NEWS] deep-translator not installed — scripts will be in English")

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Reuters — most reliable
    ("Reuters", "https://feeds.reuters.com/reuters/worldNews"),
    ("Reuters Top", "https://feeds.reuters.com/reuters/topNews"),
    # BBC World
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Russia", "https://feeds.bbci.co.uk/russian/rss.xml"),
    # Al Jazeera
    ("AlJazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    # AP News
    ("AP World", "https://rsshub.app/apnews/topics/world-news"),
]

# Keywords that make a story relevant to our channel
PRIORITY_KEYWORDS = [
    "russia", "ukraine", "putin", "zelensky", "kremlin", "moscow",
    "nato", "war", "military", "offensive", "sanctions", "frontline",
    "missile", "attack", "ceasefire", "negotiations", "eurasian",
    "belarus", "moldova", "georgia", "caucasus", "central asia",
    "china", "xi jinping", "nuclear", "energy", "oil", "gas",
]

HEADERS = {"User-Agent": "EurasiaBreaking RSS Reader/1.0"}


# ── Fetch and score articles ──────────────────────────────────────────────────

def _fetch_feed(source: str, url: str) -> list:
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:20]:
            title   = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            # Strip HTML tags
            summary = re.sub(r'<[^>]+>', ' ', summary)
            summary = re.sub(r'\s+', ' ', summary).strip()
            link    = entry.get("link", "")
            pub     = entry.get("published", "")
            if title and len(title) > 10:
                articles.append({
                    "source": source,
                    "title": title,
                    "summary": summary[:800],
                    "link": link,
                    "published": pub,
                })
        print(f"[NEWS] {source}: {len(articles)} articles")
        return articles
    except Exception as e:
        print(f"[NEWS] Failed to fetch {source}: {e}")
        return []


def _score(article: dict) -> int:
    text = (article["title"] + " " + article["summary"]).lower()
    score = 0
    for kw in PRIORITY_KEYWORDS:
        if kw in text:
            score += 10
    # Prefer longer summaries (more content = better script)
    score += min(len(article["summary"]) // 50, 10)
    return score


def fetch_top_story() -> dict:
    """Fetch all feeds, return the highest-scoring relevant article."""
    all_articles = []
    for source, url in RSS_FEEDS:
        articles = _fetch_feed(source, url)
        all_articles.extend(articles)
        if articles:
            time.sleep(0.5)  # polite delay

    if not all_articles:
        raise RuntimeError("No articles fetched from any RSS feed")

    # Score and sort
    scored = sorted(all_articles, key=_score, reverse=True)
    best = scored[0]
    score = _score(best)
    print(f"[NEWS] Top story (score={score}): {best['title'][:80]}")
    return best


# ── Script generation ─────────────────────────────────────────────────────────

def _build_english_script(article: dict) -> str:
    """Build a broadcast-style script from an RSS article."""
    title   = article["title"].rstrip(".!?")
    summary = article["summary"]
    source  = article["source"]
    date    = datetime.datetime.utcnow().strftime("%B %d, %Y")

    # Expand short summaries
    if len(summary) < 100:
        summary = f"{title}. Full details are still emerging as the situation develops rapidly."

    # Clean up summary sentences
    sentences = re.split(r'(?<=[.!?])\s+', summary)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20][:8]
    body = " ".join(sentences)

    script = (
        f"Breaking news. {title}. "
        f"{body} "
        f"This is a developing story reported by {source} on {date}. "
        f"The situation remains fluid and new details are expected shortly. "
        f"Stay with Eurasia Breaking for continuous live coverage of this story "
        f"and all breaking developments across Russia and the wider Eurasian region."
    )
    return script


def _translate_to_russian(text: str) -> str:
    """Translate text to Russian using Google Translate (free, no key)."""
    if not TRANSLATE_AVAILABLE:
        return text

    # Split into chunks (Google Translate limit ~4500 chars per request)
    max_chunk = 4000
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chunk:
            current += (" " if current else "") + s
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)

    translated_parts = []
    translator = GoogleTranslator(source="en", target="ru")
    for i, chunk in enumerate(chunks):
        for attempt in range(3):
            try:
                result = translator.translate(chunk)
                translated_parts.append(result)
                print(f"[NEWS] Translated chunk {i+1}/{len(chunks)}")
                time.sleep(0.3)
                break
            except Exception as e:
                print(f"[NEWS] Translation attempt {attempt+1} failed: {e}")
                time.sleep(2)
        else:
            # Keep English if translation fails
            translated_parts.append(chunk)

    return " ".join(translated_parts)


def _translate_title(title: str) -> str:
    if not TRANSLATE_AVAILABLE:
        return title
    try:
        return GoogleTranslator(source="en", target="ru").translate(title)
    except Exception:
        return title


# ── Tag generation ────────────────────────────────────────────────────────────

def _extract_tags(article: dict) -> list:
    text  = (article["title"] + " " + article["summary"]).lower()
    base  = ["EurasiaBreaking", "Russia", "breaking news", "world news", "новости"]
    extra = []
    keyword_tags = {
        "ukraine": ["Ukraine", "Украина"], "nato": ["NATO", "НАТО"],
        "putin": ["Putin", "Путин"], "zelensky": ["Zelensky"],
        "military": ["military", "война"], "sanctions": ["sanctions", "санкции"],
        "nuclear": ["nuclear", "ядерный"], "energy": ["energy", "энергетика"],
        "china": ["China", "Китай"], "war": ["war", "война"],
        "ceasefire": ["ceasefire", "перемирие"], "attack": ["attack", "удары"],
    }
    for kw, tags in keyword_tags.items():
        if kw in text:
            extra.extend(tags)
    return list(dict.fromkeys(base + extra))[:15]


# ── Main: generate job JSON ───────────────────────────────────────────────────

def generate_job(lang: str = "ru", output_path: str = "jobs/current_job.json") -> dict:
    print(f"\n[NEWS] Fetching latest breaking news...")
    article = fetch_top_story()

    # Build English script first
    en_script = _build_english_script(article)
    en_title  = article["title"]

    # Translate if Russian
    if lang == "ru":
        print("[NEWS] Translating to Russian...")
        script = _translate_to_russian(en_script)
        title  = "🔴 СРОЧНО: " + _translate_title(en_title)[:75]
        desc   = (
            f"Последние новости: {_translate_title(en_title)}. "
            f"Подписывайтесь на Eurasia Breaking для круглосуточных обновлений.\n\n"
            f"⚠️ Контент предназначен для информационных целей.\n"
            f"Источник: {article['source']}\n\n"
            f"#EurasiaBreaking #Россия #СрочноеСообщение #МировыеНовости"
        )
    else:
        script = en_script
        title  = "🔴 BREAKING: " + en_title[:75]
        desc   = (
            f"Breaking: {en_title}. "
            f"Subscribe to Eurasia Breaking for 24/7 updates.\n\n"
            f"⚠️ For informational purposes only.\n"
            f"Source: {article['source']}\n\n"
            f"#EurasiaBreaking #Russia #BreakingNews #WorldNews"
        )

    # Build search query from title for image fetching
    words = re.findall(r'\b[A-Za-z]{4,}\b', en_title)
    search_query = " ".join(w for w in words[:5] if w.lower() not in
                            {"that","this","with","from","have","been","will","were","they"})
    if not search_query:
        search_query = "Russia world news geopolitics"

    job = {
        "title": title,
        "description": desc,
        "script": script,
        "search_query": search_query,
        "lang": lang,
        "format": "long",
        "tags": _extract_tags(article),
        "category_id": "25",
        "_meta": {
            "source": article["source"],
            "original_title": en_title,
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    print(f"[NEWS] ✅ Job saved → {output_path}")
    print(f"[NEWS] Title: {title}")
    return job


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
    generate_job(lang=lang)
