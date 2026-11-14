"""
Auto News Fetcher — fetches real breaking news from free RSS feeds,
selects the hottest trending story, translates to Russian, and writes
jobs/current_job.json with a 750-900 word script (~6 min video).

Sources (all free, no API key):
  - Reuters World News
  - BBC World News / BBC Russia
  - Al Jazeera English
  - Associated Press (via RSS)
  - RFE/RL (Radio Free Europe / Radio Liberty)
  - Meduza (English RSS)

Translation: deep-translator (wraps Google Translate, free, no key)

Trending detection: a story appearing across multiple feeds scores higher.
Script target: 750-900 words → ~5.5-7 min video at 130 wpm Russian TTS.
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
    ("Reuters World", "https://feeds.reuters.com/reuters/worldNews"),
    ("Reuters Top",   "https://feeds.reuters.com/reuters/topNews"),
    # BBC
    ("BBC World",     "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Russia",    "https://feeds.bbci.co.uk/russian/rss.xml"),
    # Al Jazeera
    ("AlJazeera",     "https://www.aljazeera.com/xml/rss/all.xml"),
    # AP News via RSSHub
    ("AP World",      "https://rsshub.app/apnews/topics/world-news"),
    # RFE/RL — Eurasia specialists
    ("RFERL",         "https://www.rferl.org/api/zquynqquzy"),
    # Euronews
    ("Euronews",      "https://feeds.feedburner.com/euronews/en/home/"),
]

# Keywords that make a story relevant to our channel
PRIORITY_KEYWORDS = [
    "russia", "ukraine", "putin", "zelensky", "kremlin", "moscow",
    "nato", "war", "military", "offensive", "sanctions", "frontline",
    "missile", "attack", "ceasefire", "negotiations", "eurasian",
    "belarus", "moldova", "georgia", "caucasus", "central asia",
    "china", "xi jinping", "nuclear", "energy", "oil", "gas",
    "kyiv", "dnipro", "kharkiv", "zaporizhzhia", "donetsk",
    "armed forces", "troops", "casualties", "drone", "strike",
]

# High-weight keywords (story is more urgent)
HOT_KEYWORDS = [
    "breaking", "urgent", "attack", "explosion", "killed", "strike",
    "ceasefire", "missile", "nuclear", "invasion", "offensive",
]

HEADERS = {"User-Agent": "EurasiaBreaking RSS Reader/1.0"}


# ── Fetch and score articles ──────────────────────────────────────────────────

def _fetch_feed(source: str, url: str) -> list:
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:25]:
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
                    "summary": summary[:1500],
                    "link": link,
                    "published": pub,
                })
        print(f"[NEWS] {source}: {len(articles)} articles")
        return articles
    except Exception as e:
        print(f"[NEWS] Failed to fetch {source}: {e}")
        return []


def _score(article: dict, all_articles: list = None) -> float:
    text = (article["title"] + " " + article["summary"]).lower()
    score = 0.0

    # Priority keyword hits
    for kw in PRIORITY_KEYWORDS:
        if kw in text:
            score += 10

    # Hot/breaking keyword bonus
    for kw in HOT_KEYWORDS:
        if kw in text:
            score += 15

    # Longer summaries = more content = better script
    score += min(len(article["summary"]) // 60, 15)

    # Trending bonus: same title words appear in multiple sources
    if all_articles:
        my_title_words = set(re.findall(r'\b[a-z]{5,}\b', article["title"].lower()))
        overlap_count = 0
        for other in all_articles:
            if other is article:
                continue
            other_words = set(re.findall(r'\b[a-z]{5,}\b', other["title"].lower()))
            # If 2+ key words overlap → same story, trending
            common = my_title_words & other_words
            if len(common) >= 2:
                overlap_count += 1
        # Up to +50 for trending across many sources
        score += min(overlap_count * 12, 50)

    return score


def fetch_top_stories(n: int = 5) -> list:
    """Fetch all feeds, return top-N relevant articles with trending detection."""
    all_articles = []
    for source, url in RSS_FEEDS:
        articles = _fetch_feed(source, url)
        all_articles.extend(articles)
        if articles:
            time.sleep(0.4)

    if not all_articles:
        raise RuntimeError("No articles fetched from any RSS feed")

    # Deduplicate by title similarity
    seen_titles = []
    unique = []
    for art in all_articles:
        title_words = set(re.findall(r'\b[a-z]{5,}\b', art["title"].lower()))
        is_dupe = any(len(title_words & seen) >= 3 for seen in seen_titles)
        if not is_dupe:
            seen_titles.append(title_words)
            unique.append(art)

    # Score with trending detection
    scored = sorted(unique, key=lambda a: _score(a, all_articles), reverse=True)

    best = scored[:n]
    for art in best:
        sc = _score(art, all_articles)
        print(f"[NEWS] Score={sc:.0f}: {art['title'][:80]}")

    return best


# ── Deep script building ──────────────────────────────────────────────────────

def _build_long_english_script(articles: list) -> str:
    """
    Build a ~750-900 word broadcast script using multiple related articles.
    Structure: Hook → Context → Details → International Reaction →
               Background → Analysis → Conclusion
    Targets 750+ words for 5-6 minute videos at ~130 wpm Russian TTS.
    """
    primary   = articles[0]
    related   = articles[1:]
    title     = primary["title"].rstrip(".!?")
    summary   = primary["summary"]
    source    = primary["source"]
    date      = datetime.datetime.utcnow().strftime("%B %d, %Y")
    year      = datetime.datetime.utcnow().year

    # Gather sentences from all articles
    def clean_sents(text, max_sents=8):
        text = re.sub(r'\s+', ' ', text).strip()
        sents = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sents if len(s.strip()) > 25][:max_sents]

    primary_sents = clean_sents(summary)
    related_sents = []
    for art in related[:3]:
        related_sents.extend(clean_sents(art["summary"], 4))

    # Determine what keywords appear — build topic-aware content
    all_text = (title + " " + summary).lower()
    is_war      = any(w in all_text for w in ["ukraine", "frontline", "troops", "attack"])
    is_diplo    = any(w in all_text for w in ["negotiation", "ceasefire", "talks", "summit"])
    is_sanction = any(w in all_text for w in ["sanction", "economic", "trade", "energy"])
    is_nuclear  = any(w in all_text for w in ["nuclear", "atomic", "warhead"])

    p = primary_sents
    r = related_sents

    # ── Section 1: Breaking Hook (50-60 words) ────────────────────────────────
    sec1 = (
        f"Breaking news from Eurasia. {title}. "
        f"{p[0] if p else 'Full details are emerging rapidly.'} "
        f"This story is developing rapidly, and Eurasia Breaking is tracking every update "
        f"as it comes in from our sources on the ground."
    )

    # ── Section 2: What Happened (100-130 words) ──────────────────────────────
    details = " ".join(p[1:4]) if len(p) > 1 else f"Reports indicate that {title}."
    sec2 = (
        f"Here is what we know so far. "
        f"{details} "
        f"According to {source} and multiple regional correspondents, "
        f"the events unfolded in the past twenty-four hours amid ongoing tensions "
        f"across the region. Local officials have not yet issued a formal statement, "
        f"though sources close to the situation confirm the core facts of this report."
    )

    # ── Section 3: Regional / International Context (120-150 words) ───────────
    if is_war:
        ctx_body = (
            f"This development comes as military operations along the Eastern European front "
            f"continue into their third year. Both sides have reported significant activity "
            f"across multiple sectors of the front line. Western allies have been closely "
            f"monitoring the situation, with NATO defense ministers holding emergency consultations. "
            f"{r[0] if r else ''} "
            f"The humanitarian situation in affected areas remains a grave concern, "
            f"with international organizations calling for immediate access to civilians."
        )
    elif is_diplo:
        ctx_body = (
            f"The diplomatic landscape in the region has shifted significantly in recent weeks. "
            f"Multiple rounds of talks between key parties have produced mixed results. "
            f"{r[0] if r else ''} "
            f"International mediators remain cautiously optimistic, though analysts warn "
            f"that fundamental disagreements over territorial boundaries and security guarantees "
            f"continue to block a comprehensive agreement."
        )
    elif is_sanction:
        ctx_body = (
            f"The economic dimensions of this conflict continue to reshape global energy and trade flows. "
            f"Since the start of restrictions, European nations have accelerated their diversification "
            f"away from Russian energy supplies. {r[0] if r else ''} "
            f"Financial markets reacted with notable volatility to this latest development, "
            f"with oil futures rising and European currency indices shifting sharply."
        )
    else:
        ctx_body = (
            f"The wider geopolitical situation across Eurasia continues to evolve rapidly. "
            f"{r[0] if r else ''} "
            f"Regional powers have been carefully calibrating their responses, "
            f"aware that any miscalculation could have consequences extending far beyond the immediate area. "
            f"International observers are watching closely for signs of escalation or de-escalation."
        )
    sec3 = ctx_body.strip()

    # ── Section 4: International Reactions (100-120 words) ────────────────────
    sec4 = (
        f"The international community has responded swiftly. "
        f"{r[1] if len(r) > 1 else ''} "
        f"The United States State Department issued a statement urging restraint, "
        f"while European Union foreign policy chief called for an emergency session of the European Council. "
        f"China's foreign ministry said it was monitoring the situation closely and called on all parties "
        f"to exercise maximum restraint. The United Nations Secretary-General expressed deep concern "
        f"and offered to facilitate dialogue between the involved parties."
    )

    # ── Section 5: Background (120-150 words) ─────────────────────────────────
    if is_nuclear:
        bg_body = (
            f"To understand this development in full context, it is important to recall the broader strategic picture. "
            f"Nuclear security in Eurasia has been a persistent concern since the dissolution of the Soviet Union in nineteen ninety-one. "
            f"Russia retains the world's largest nuclear arsenal, and any rhetoric or movement involving "
            f"strategic assets draws immediate international attention. "
            f"The Budapest Memorandum of nineteen ninety-four committed Russia, the United States, and the United Kingdom "
            f"to respect the territorial integrity of Ukraine in exchange for the transfer of nuclear weapons to Russia. "
            f"That agreement has been a central point of contention throughout the current conflict."
        )
    elif is_war:
        bg_body = (
            f"To understand the full context, we must look back at how this conflict began. "
            f"Russia launched its full-scale military operation against Ukraine in February twenty twenty-two, "
            f"following years of tensions over NATO expansion and the status of eastern Ukrainian territories. "
            f"The conflict has resulted in tens of thousands of casualties on both sides and created the largest "
            f"refugee crisis in Europe since the Second World War. "
            f"Ukraine has received substantial military and financial support from Western nations, "
            f"while Russia has faced an unprecedented package of international economic sanctions."
        )
    else:
        bg_body = (
            f"This story must be understood against a backdrop of profound geopolitical transformation. "
            f"The post-Cold War order that defined international relations for three decades is under "
            f"unprecedented pressure. Russia's strategic calculus, shaped by historical grievances and "
            f"a desire for a sphere of influence along its borders, has clashed directly with the "
            f"Euro-Atlantic integration aspirations of its neighbors. "
            f"The result has been a fundamental reshaping of European security, with consequences "
            f"that analysts say will define the continent's future for a generation."
        )
    sec5 = bg_body.strip()

    # ── Section 6: Latest Details / Analysis (100-120 words) ─────────────────
    extra = " ".join(p[4:7]) if len(p) > 4 else ""
    r_extra = " ".join(r[2:5]) if len(r) > 2 else ""
    sec6 = (
        f"The latest information from our correspondents indicates the following. "
        f"{extra} {r_extra} "
        f"Military analysts watching the situation say this represents a potential turning point. "
        f"The coming forty-eight to seventy-two hours will be critical in determining whether "
        f"the situation escalates further or whether diplomatic back-channels can contain it."
    ).strip()

    # ── Section 7: Conclusion / CTA (70-80 words) ─────────────────────────────
    sec7 = (
        f"This is a rapidly developing story, and Eurasia Breaking will continue to bring you "
        f"updates as new information becomes available. We are monitoring all official channels, "
        f"regional media, and on-the-ground sources around the clock. "
        f"If you found this report informative, please subscribe to our channel for continuous "
        f"coverage of breaking news from Russia, Ukraine, and the wider Eurasian region. "
        f"Stay informed. Stay safe."
    )

    full_script = " ".join([sec1, sec2, sec3, sec4, sec5, sec6, sec7])
    # Clean up double spaces
    full_script = re.sub(r'\s{2,}', ' ', full_script).strip()

    word_count = len(full_script.split())
    print(f"[NEWS] Script length: {word_count} words (~{word_count//130:.1f} min)")
    return full_script


def _translate_to_russian(text: str) -> str:
    """Translate text to Russian using Google Translate (free, no key)."""
    if not TRANSLATE_AVAILABLE:
        return text

    # Split into chunks (Google Translate limit ~4500 chars per request)
    max_chunk = 3500
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
                time.sleep(0.5)
                break
            except Exception as e:
                print(f"[NEWS] Translation attempt {attempt+1} failed: {e}")
                time.sleep(2 * (attempt + 1))
        else:
            translated_parts.append(chunk)

    return " ".join(translated_parts)


def _translate_title(title: str) -> str:
    if not TRANSLATE_AVAILABLE:
        return title
    try:
        return GoogleTranslator(source="en", target="ru").translate(title)
    except Exception:
        return title


# ── SEO Tag & Description Generation ─────────────────────────────────────────

def _extract_seo_tags(articles: list) -> list:
    """Generate 15 SEO-optimized tags from article content."""
    all_text = " ".join(
        (a["title"] + " " + a["summary"]).lower() for a in articles
    )

    base_tags = [
        "EurasiaBreaking", "Russia", "breaking news", "world news",
        "новости", "РоссияНовости", "Eurasia",
    ]

    keyword_tags = {
        "ukraine": ["Ukraine", "Украина", "Ukraine war", "RussiaUkraine"],
        "nato":    ["NATO", "НАТО", "NATO news"],
        "putin":   ["Putin", "Путин", "Putin news"],
        "zelensky":["Zelensky", "Зеленский"],
        "military":["military", "armed forces", "война"],
        "sanctions":["sanctions", "санкции", "Russia sanctions"],
        "nuclear": ["nuclear", "ядерный", "nuclear threat"],
        "energy":  ["energy crisis", "энергетика", "oil price"],
        "china":   ["China", "Китай", "China Russia"],
        "ceasefire":["ceasefire", "перемирие", "peace talks"],
        "attack":  ["attack", "strike", "удары"],
        "missile": ["missile", "ATACMS", "ракетный удар"],
        "frontline":["frontline", "линия фронта"],
        "drone":   ["drone", "дрон", "FPV drone"],
        "kyiv":    ["Kyiv", "Киев", "Ukraine capital"],
    }

    extra = []
    for kw, tags in keyword_tags.items():
        if kw in all_text and len(extra) < 20:
            extra.extend(tags[:2])

    combined = list(dict.fromkeys(base_tags + extra))
    return combined[:15]


def _build_seo_description(articles: list, title_en: str,
                            title_local: str, lang: str) -> str:
    """
    Build a fully SEO-optimized YouTube description.
    - First 150 chars keyword-rich (shown in search preview)
    - Timestamps for chapters
    - Strong hashtags
    - CTA + disclaimer
    """
    source = articles[0]["source"]
    date   = datetime.datetime.utcnow().strftime("%B %d, %Y")

    if lang == "ru":
        desc = (
            f"🔴 СРОЧНО | {title_en} | Все последние события на Украине и в России — {date}\n\n"
            f"📌 Содержание видео:\n"
            f"00:00 Экстренное сообщение\n"
            f"00:45 Что произошло — подробности\n"
            f"02:00 Региональный контекст\n"
            f"03:00 Международная реакция\n"
            f"04:00 История вопроса\n"
            f"05:00 Последние обновления\n"
            f"06:00 Выводы и итоги\n\n"
            f"📡 Источник: {source} и другие международные агентства\n"
            f"🌍 Следите за Eurasia Breaking для круглосуточных новостей из России, Украины и Евразии.\n\n"
            f"👉 Подписывайтесь | 🔔 Нажмите на колокольчик | 👍 Ставьте лайк\n\n"
            f"⚠️ ОТКАЗ ОТ ОТВЕТСТВЕННОСТИ: Это новостная агрегация в информационных целях.\n"
            f"Контент основан на открытых источниках и международных СМИ.\n\n"
            f"#EurasiaBreaking #Россия #Украина #СрочноеСообщение #МировыеНовости "
            f"#EurasiaNews #RussiaUkraine #BreakingNews #WorldNews #Путин #Зеленский "
            f"#война #НАТО #NATO #новости"
        )
    else:
        desc = (
            f"🔴 BREAKING | {title_en} | Live updates from Russia & Ukraine — {date}\n\n"
            f"📌 Chapters:\n"
            f"00:00 Breaking Alert\n"
            f"00:45 What Happened — Full Details\n"
            f"02:00 Regional Context\n"
            f"03:00 International Reaction\n"
            f"04:00 Background & History\n"
            f"05:00 Latest Updates\n"
            f"06:00 Analysis & Conclusion\n\n"
            f"📡 Source: {source} and international news agencies\n"
            f"🌍 Follow Eurasia Breaking for 24/7 coverage of Russia, Ukraine & Eurasia.\n\n"
            f"👉 Subscribe | 🔔 Hit the Bell | 👍 Like this video\n\n"
            f"⚠️ DISCLAIMER: This is news aggregation for informational purposes.\n"
            f"Content is sourced from open-access international media outlets.\n\n"
            f"#EurasiaBreaking #Russia #Ukraine #BreakingNews #WorldNews "
            f"#RussiaUkraine #Putin #Zelensky #War #NATO #EurasiaNews "
            f"#RussianNews #UkraineWar #Geopolitics #LiveNews"
        )
    return desc


# ── Main: generate job JSON ───────────────────────────────────────────────────

def generate_job(lang: str = "ru", output_path: str = "jobs/current_job.json") -> dict:
    print(f"\n[NEWS] Fetching latest trending breaking news...")
    articles = fetch_top_stories(n=5)
    primary  = articles[0]

    # Build English long-form script (~750-900 words)
    en_script = _build_long_english_script(articles)
    en_title  = primary["title"]

    # Translate if Russian
    if lang == "ru":
        print("[NEWS] Translating to Russian...")
        script = _translate_to_russian(en_script)
        ru_title = "🔴 СРОЧНО: " + _translate_title(en_title)[:72]
        title  = ru_title
    else:
        script = en_script
        title  = "🔴 BREAKING: " + en_title[:72]

    # SEO description + tags
    description = _build_seo_description(articles, en_title, title, lang)
    tags        = _extract_seo_tags(articles)

    # Build search query from title for image fetching
    words = re.findall(r'\b[A-Za-z]{4,}\b', en_title)
    search_query = " ".join(w for w in words[:5] if w.lower() not in
                            {"that","this","with","from","have","been","will","were","they"})
    if not search_query:
        search_query = "Russia Ukraine war news geopolitics"

    job = {
        "title": title,
        "description": description,
        "script": script,
        "search_query": search_query,
        "lang": lang,
        "format": "long",
        "tags": tags,
        "category_id": "25",
        "_meta": {
            "source": primary["source"],
            "original_title": en_title,
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "script_words": len(en_script.split()),
            "related_sources": [a["source"] for a in articles[1:]],
        }
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    word_count = len(script.split())
    print(f"[NEWS] ✅ Job saved → {output_path}")
    print(f"[NEWS] Title  : {title}")
    print(f"[NEWS] Script : {word_count} words  (~{word_count//130:.1f} min video)")
    print(f"[NEWS] Tags   : {', '.join(tags[:5])}...")
    return job


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
    generate_job(lang=lang)
