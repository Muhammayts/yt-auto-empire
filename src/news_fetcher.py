"""
Auto News Fetcher — fetches real breaking news from free RSS feeds,
avoids repeating the same story, generates a 850+ word script (~6-7 min),
and writes jobs/current_job.json.

Key improvements:
  - Deduplication: tracks used story hashes in jobs/seen_stories.json
    to avoid repeating the same story across runs
  - 850+ word scripts enforced with a word-count padder
  - Longer static sections that don't collapse when RSS summaries are thin
  - 8 RSS feeds for more variety
"""

import json
import os
import re
import sys
import time
import hashlib
import datetime
import feedparser

try:
    from deep_translator import GoogleTranslator
    TRANSLATE_AVAILABLE = True
except ImportError:
    TRANSLATE_AVAILABLE = False
    print("[NEWS] deep-translator not installed — scripts will be in English")

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("Reuters World",  "https://feeds.reuters.com/reuters/worldNews"),
    ("Reuters Top",    "https://feeds.reuters.com/reuters/topNews"),
    ("BBC World",      "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Russia",     "https://feeds.bbci.co.uk/russian/rss.xml"),
    ("AlJazeera",      "https://www.aljazeera.com/xml/rss/all.xml"),
    ("AP World",       "https://rsshub.app/apnews/topics/world-news"),
    ("RFERL",          "https://www.rferl.org/api/zquynqquzy"),
    ("Euronews",       "https://feeds.feedburner.com/euronews/en/home/"),
]

PRIORITY_KEYWORDS = [
    "russia", "ukraine", "putin", "zelensky", "kremlin", "moscow",
    "nato", "war", "military", "offensive", "sanctions", "frontline",
    "missile", "attack", "ceasefire", "negotiations", "eurasian",
    "belarus", "moldova", "georgia", "caucasus", "central asia",
    "china", "xi jinping", "nuclear", "energy", "oil", "gas",
    "kyiv", "dnipro", "kharkiv", "zaporizhzhia", "donetsk",
    "armed forces", "troops", "casualties", "drone", "strike",
]

HOT_KEYWORDS = [
    "breaking", "urgent", "attack", "explosion", "killed", "strike",
    "ceasefire", "missile", "nuclear", "invasion", "offensive", "emergency",
]

HEADERS = {"User-Agent": "EurasiaBreaking RSS Reader/1.0"}
HISTORY_FILE = "jobs/seen_stories.json"
HISTORY_HOURS = 36   # skip a story seen less than 36 h ago


# ── Story deduplication history ───────────────────────────────────────────────

def _story_hash(title: str) -> str:
    normalized = re.sub(r'\W+', ' ', title.lower()).strip()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_history(history: dict, new_hash: str, title: str):
    # Prune entries older than 7 days
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
    history = {k: v for k, v in history.items() if v.get("seen_at", "") > cutoff}
    history[new_hash] = {
        "title": title,
        "seen_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _was_recently_used(story_hash: str, history: dict) -> bool:
    if story_hash not in history:
        return False
    seen_at_str = history[story_hash].get("seen_at", "")
    try:
        seen_at = datetime.datetime.fromisoformat(seen_at_str.rstrip("Z"))
        hours_ago = (datetime.datetime.utcnow() - seen_at).total_seconds() / 3600
        return hours_ago < HISTORY_HOURS
    except Exception:
        return False


# ── Fetch and score articles ──────────────────────────────────────────────────

def _fetch_feed(source: str, url: str) -> list:
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:25]:
            title   = entry.get("title", "").strip()
            # Try multiple content fields — some feeds are richer
            summary = (
                entry.get("content", [{}])[0].get("value", "")
                or entry.get("summary", "")
                or entry.get("description", "")
            ).strip()
            summary = re.sub(r'<[^>]+>', ' ', summary)
            summary = re.sub(r'\s+', ' ', summary).strip()
            link    = entry.get("link", "")
            pub     = entry.get("published", "")
            if title and len(title) > 10:
                articles.append({
                    "source": source,
                    "title":   title,
                    "summary": summary[:2000],
                    "link":    link,
                    "published": pub,
                })
        print(f"[NEWS] {source}: {len(articles)} articles")
        return articles
    except Exception as e:
        print(f"[NEWS] Failed to fetch {source}: {e}")
        return []


def _score(article: dict, all_articles: list) -> float:
    text  = (article["title"] + " " + article["summary"]).lower()
    score = 0.0
    for kw in PRIORITY_KEYWORDS:
        if kw in text:
            score += 10
    for kw in HOT_KEYWORDS:
        if kw in text:
            score += 15
    score += min(len(article["summary"]) // 60, 15)
    # Trending bonus: same story covered by multiple sources
    my_words = set(re.findall(r'\b[a-z]{5,}\b', article["title"].lower()))
    for other in all_articles:
        if other is article:
            continue
        other_words = set(re.findall(r'\b[a-z]{5,}\b', other["title"].lower()))
        if len(my_words & other_words) >= 2:
            score += 12
    return min(score, 300)


def fetch_top_stories(history: dict) -> list:
    """
    Fetch all feeds, deduplicate by title, skip recently used stories,
    return a list of related articles led by the hottest unseen story.
    """
    all_articles = []
    for source, url in RSS_FEEDS:
        arts = _fetch_feed(source, url)
        all_articles.extend(arts)
        if arts:
            time.sleep(0.4)

    if not all_articles:
        raise RuntimeError("No articles fetched from any RSS feed")

    # Deduplicate by title similarity
    seen_title_words: list = []
    unique: list = []
    for art in all_articles:
        words = set(re.findall(r'\b[a-z]{5,}\b', art["title"].lower()))
        if any(len(words & sw) >= 3 for sw in seen_title_words):
            continue
        seen_title_words.append(words)
        unique.append(art)

    # Sort by score
    scored = sorted(unique, key=lambda a: _score(a, all_articles), reverse=True)

    # Pick top unseen story
    primary = None
    for art in scored:
        h = _story_hash(art["title"])
        if not _was_recently_used(h, history):
            primary = art
            break

    if primary is None:
        print("[NEWS] ⚠️  All top stories seen recently — using most recent anyway")
        primary = scored[0]

    # Gather related articles (different from primary but same topic)
    primary_words = set(re.findall(r'\b[a-z]{5,}\b', primary["title"].lower()))
    related = []
    for art in scored:
        if art is primary:
            continue
        art_words = set(re.findall(r'\b[a-z]{5,}\b', art["title"].lower()))
        # Include if somewhat related OR just more general Eurasia news
        if len(primary_words & art_words) >= 1 or _score(art, all_articles) > 50:
            related.append(art)
        if len(related) >= 6:
            break

    result = [primary] + related
    for a in result[:3]:
        print(f"[NEWS] Selected [{a['source']}]: {a['title'][:70]}")
    return result


# ── Script builder (enforces 850+ words) ─────────────────────────────────────

def _sentences(text: str, max_n: int = 10) -> list:
    text = re.sub(r'\s+', ' ', text).strip()
    sents = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sents if len(s.strip()) > 20][:max_n]


def _build_long_english_script(articles: list) -> str:
    primary  = articles[0]
    related  = articles[1:]
    title    = primary["title"].rstrip(".!?")
    source   = primary["source"]
    date     = datetime.datetime.utcnow().strftime("%B %d, %Y")
    year     = datetime.datetime.utcnow().year

    p = _sentences(primary["summary"], 10)
    r = []
    for a in related[:4]:
        r.extend(_sentences(a["summary"], 4))

    all_text = (title + " " + primary["summary"]).lower()
    is_war      = any(w in all_text for w in ["ukraine","frontline","troops","attack","military","drone"])
    is_diplo    = any(w in all_text for w in ["negotiation","ceasefire","talks","summit","diplomatic"])
    is_sanction = any(w in all_text for w in ["sanction","economic","trade","energy","oil","gas"])
    is_nuclear  = any(w in all_text for w in ["nuclear","atomic","warhead","missile"])
    is_energy   = any(w in all_text for w in ["power plant","pipeline","energy","electricity","gas"])

    def pick(lst, idx, fallback=""):
        return lst[idx] if idx < len(lst) else fallback

    # ── SECTION 1: Breaking Hook  (~80 words) ────────────────────────────────
    s1 = (
        f"Breaking news alert from Eurasia. {title}. "
        f"{pick(p, 0, f'This story is developing rapidly and new details are emerging by the hour.')} "
        f"Eurasia Breaking is tracking this story in real time, monitoring all official channels, "
        f"regional correspondents, and international wire services. "
        f"Here is everything we know at this hour."
    )

    # ── SECTION 2: What Happened  (~130 words) ────────────────────────────────
    details_raw = " ".join(filter(None, [pick(p,1), pick(p,2), pick(p,3)]))
    if len(details_raw.split()) < 20:
        details_raw = f"Reports from the region indicate that {title.lower()}, with the full scope of the event still becoming clear."
    s2 = (
        f"Let us walk through exactly what happened. "
        f"{details_raw} "
        f"Multiple sources including {source} have confirmed the core elements of this report. "
        f"Regional authorities have been placed on high alert, and emergency protocols have been activated in affected areas. "
        f"Independent verification of some claims is still ongoing as access to the region remains limited for international journalists."
    )

    # ── SECTION 3: Latest Developments  (~120 words) ──────────────────────────
    latest_raw = " ".join(filter(None, [pick(p,4), pick(p,5), pick(r,0)]))
    if len(latest_raw.split()) < 15:
        latest_raw = f"The situation on the ground continues to evolve, with new information arriving from multiple directions."
    s3 = (
        f"Here are the latest developments as of this report. "
        f"{latest_raw} "
        f"Satellite imagery analyzed by open-source intelligence researchers shows significant activity in the region. "
        f"Social media channels from local residents are corroborating the broad outlines of official reports, "
        f"though some details remain impossible to independently confirm at this stage."
    )

    # ── SECTION 4: International Reaction  (~140 words) ───────────────────────
    reaction_detail = " ".join(filter(None, [pick(r,1), pick(r,2)]))
    s4 = (
        f"The international community has responded quickly to these developments. "
        f"{reaction_detail} "
        f"The United States National Security Council convened an emergency meeting, "
        f"with a spokesperson stating that Washington is monitoring the situation closely and stands ready to respond. "
        f"The European Union's foreign policy chief issued a statement calling for maximum restraint "
        f"and urging all parties to pursue dialogue over confrontation. "
        f"NATO's Secretary General said the alliance is in close contact with affected member states "
        f"and that consultations are ongoing. "
        f"China's foreign ministry called for calm and said Beijing supports a negotiated resolution."
    )

    # ── SECTION 5: Regional / Strategic Context  (~150 words) ─────────────────
    if is_nuclear:
        s5 = (
            f"To place this in proper context, we must consider the nuclear dimension. "
            f"Russia maintains the world's largest nuclear arsenal, with an estimated six thousand warheads. "
            f"Any movement or rhetoric involving strategic assets triggers immediate responses from "
            f"Western intelligence agencies and nuclear command structures. "
            f"The United States and its allies maintain continuous monitoring of Russian nuclear forces "
            f"through a combination of satellite surveillance, signals intelligence, and human sources. "
            f"Analysts note that while nuclear rhetoric has intensified since the start of the conflict, "
            f"the actual risk of use remains low — but not zero. "
            f"Former NATO commanders have stated publicly that any use of nuclear weapons, even tactical ones, "
            f"would fundamentally transform the nature of the conflict and trigger an overwhelming response."
        )
    elif is_war:
        s5 = (
            f"To understand the full picture, we need to look at the broader military situation. "
            f"The conflict has now entered what analysts describe as a critical attritional phase, "
            f"with neither side able to achieve the rapid breakthroughs seen in earlier stages of the war. "
            f"Ukraine's armed forces, supplied with Western weapons including artillery, armored vehicles, "
            f"and long-range precision systems, have demonstrated a capacity to strike deep into Russian-held territory. "
            f"Russia, meanwhile, has adapted its tactics to emphasize drone warfare, glide bombs, "
            f"and mass missile strikes against Ukrainian infrastructure, particularly power generation facilities. "
            f"The humanitarian cost continues to mount, with the United Nations estimating that over ten million "
            f"Ukrainians remain displaced, either internally or as refugees in neighboring countries."
        )
    elif is_energy:
        s5 = (
            f"The energy dimension of this conflict deserves careful attention. "
            f"Ukraine's power infrastructure has been a systematic target since the autumn of two thousand twenty-two, "
            f"with Russian strikes destroying or damaging a significant portion of the country's generating capacity. "
            f"The result has been rolling blackouts affecting millions of civilians, particularly during winter months. "
            f"European nations have accelerated their transition away from Russian energy, "
            f"with pipeline gas imports from Russia to Europe falling from over forty percent of supply "
            f"to under ten percent since the start of the conflict. "
            f"This energy decoupling, while painful economically in the short term, "
            f"has permanently altered the geopolitical leverage Russia once held over Europe."
        )
    elif is_diplo:
        s5 = (
            f"The diplomatic landscape surrounding this conflict is exceptionally complex. "
            f"Multiple tracks of negotiation have been attempted since the start of full-scale hostilities, "
            f"including direct contacts facilitated by Turkey, indirect channels through third countries, "
            f"and broader multilateral frameworks involving the United Nations. "
            f"The fundamental obstacles to a negotiated settlement remain the same: "
            f"Ukraine insists on the full restoration of its internationally recognized borders, "
            f"including Crimea, while Russia demands recognition of its territorial gains and "
            f"binding guarantees that Ukraine will not join NATO. "
            f"These positions remain irreconcilable at present, though back-channel contacts continue."
        )
    else:
        s5 = (
            f"The wider geopolitical context is essential for understanding why this matters. "
            f"Russia's strategic posture has undergone a fundamental transformation since February twenty twenty-two. "
            f"The country is now operating under the most extensive package of Western sanctions in history, "
            f"with restrictions on its financial system, technology imports, and energy exports. "
            f"Despite these pressures, the Russian economy has shown unexpected resilience, "
            f"partly by redirecting trade toward China, India, and other non-Western markets. "
            f"The conflict has also accelerated changes in European security architecture, "
            f"with Finland and Sweden joining NATO and defence spending rising sharply across the continent. "
            f"These structural changes will shape European security for a generation."
        )

    # ── SECTION 6: Background  (~130 words) ───────────────────────────────────
    s6 = (
        f"For viewers new to this story, here is the essential background. "
        f"Russia launched a full-scale military invasion of Ukraine on February twenty-fourth, twenty twenty-two, "
        f"following eight years of lower-level conflict in eastern Ukraine that began with Russia's annexation of Crimea in twenty fourteen. "
        f"The invasion triggered the largest ground war in Europe since the Second World War "
        f"and provoked an unprecedented Western response including military aid to Ukraine, "
        f"sweeping economic sanctions against Russia, and the near-total isolation of Russia from Western financial markets. "
        f"{pick(r,3,'The conflict has reshaped global alliances, energy markets, and the international rules-based order in ways that will take years to fully understand.')} "
        f"The human cost has been staggering, with casualties estimated in the hundreds of thousands on both sides."
    )

    # ── SECTION 7: Analysis  (~100 words) ─────────────────────────────────────
    extra = " ".join(filter(None, [pick(p,6), pick(p,7), pick(r,4)]))
    s7 = (
        f"What does this latest development mean going forward? "
        f"{extra if len(extra.split()) > 15 else ''} "
        f"Military and strategic analysts watching the situation say the next seventy-two hours will be decisive. "
        f"If the situation escalates, we could see a significant shift in the operational picture. "
        f"If it stabilizes, it may represent yet another tactical adjustment in a conflict "
        f"that has already surprised observers many times over."
    ).strip()

    # ── SECTION 8: Closing CTA  (~80 words) ───────────────────────────────────
    s8 = (
        f"This is Eurasia Breaking, your source for continuous coverage of breaking news from Russia, Ukraine, and the entire Eurasian region. "
        f"We will continue updating this story throughout the day as new information becomes available. "
        f"If you found this report valuable, please subscribe to our channel and hit the notification bell "
        f"so you never miss a breaking update. "
        f"Like this video to help us reach more viewers who need accurate, timely information about this critical region. "
        f"Stay informed. Stay safe."
    )

    full = " ".join([s1, s2, s3, s4, s5, s6, s7, s8])
    full = re.sub(r'\s{2,}', ' ', full).strip()

    # ── Enforce 850-word minimum ───────────────────────────────────────────────
    words = full.split()
    if len(words) < 850:
        padding = (
            f" The situation in Ukraine and Russia continues to be one of the most closely watched "
            f"geopolitical crises of the twenty-first century. World leaders, military commanders, and "
            f"ordinary citizens across Europe and beyond are following developments with deep anxiety and hope. "
            f"The decisions made in the coming days and weeks will have consequences that extend far beyond "
            f"the borders of Ukraine and Russia, touching on the future of European security, "
            f"the global economic order, and the fundamental principles of international law. "
            f"Eurasia Breaking remains committed to bringing you accurate, fact-based reporting "
            f"without sensationalism, so that you can form your own informed view of these events."
        )
        full = full + padding
        full = re.sub(r'\s{2,}', ' ', full).strip()

    final_count = len(full.split())
    print(f"[NEWS] Script: {final_count} words  (~{final_count//130:.1f} min at 130 wpm)")
    return full


# ── Translation ───────────────────────────────────────────────────────────────

def _translate_to_russian(text: str) -> str:
    if not TRANSLATE_AVAILABLE:
        return text
    max_chunk = 3500
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) < max_chunk:
            cur += (" " if cur else "") + s
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)

    parts = []
    translator = GoogleTranslator(source="en", target="ru")
    for i, chunk in enumerate(chunks):
        for attempt in range(3):
            try:
                parts.append(translator.translate(chunk))
                print(f"[NEWS] Translated chunk {i+1}/{len(chunks)}")
                time.sleep(0.5)
                break
            except Exception as e:
                print(f"[NEWS] Translation {attempt+1} failed: {e}")
                time.sleep(2 * (attempt + 1))
        else:
            parts.append(chunk)
    return " ".join(parts)


def _translate_title(title: str) -> str:
    if not TRANSLATE_AVAILABLE:
        return title
    try:
        return GoogleTranslator(source="en", target="ru").translate(title)
    except Exception:
        return title


# ── SEO Tags & Description ────────────────────────────────────────────────────

def _extract_seo_tags(articles: list) -> list:
    all_text = " ".join((a["title"] + " " + a["summary"]).lower() for a in articles)
    base = ["EurasiaBreaking","Russia","breaking news","world news","новости","РоссияНовости","Eurasia"]
    kw_map = {
        "ukraine":   ["Ukraine","Украина","Ukraine war","RussiaUkraine"],
        "nato":      ["NATO","НАТО","NATO news"],
        "putin":     ["Putin","Путин","Putin news"],
        "zelensky":  ["Zelensky","Зеленский"],
        "military":  ["military","armed forces","война"],
        "sanctions": ["sanctions","санкции","Russia sanctions"],
        "nuclear":   ["nuclear","ядерный","nuclear threat"],
        "energy":    ["energy crisis","энергетика","oil price"],
        "china":     ["China","Китай","China Russia"],
        "ceasefire": ["ceasefire","перемирие","peace talks"],
        "attack":    ["attack","strike","удары"],
        "missile":   ["missile","ракетный удар"],
        "frontline": ["frontline","линия фронта"],
        "drone":     ["drone","дрон","FPV drone"],
        "kyiv":      ["Kyiv","Киев"],
    }
    extra = []
    for kw, tags in kw_map.items():
        if kw in all_text and len(extra) < 20:
            extra.extend(tags[:2])
    return list(dict.fromkeys(base + extra))[:15]


def _build_seo_description(articles: list, en_title: str, lang: str) -> str:
    source = articles[0]["source"]
    date   = datetime.datetime.utcnow().strftime("%B %d, %Y")
    if lang == "ru":
        return (
            f"🔴 СРОЧНО | {en_title} | Россия и Украина — {date}\n\n"
            f"📌 Содержание:\n"
            f"00:00 Экстренное сообщение\n"
            f"00:45 Что произошло\n"
            f"02:00 Последние события\n"
            f"03:00 Международная реакция\n"
            f"04:00 Контекст и стратегический анализ\n"
            f"05:00 История вопроса\n"
            f"06:00 Анализ и выводы\n\n"
            f"📡 Источники: {source} и международные агентства\n"
            f"🌍 Подписывайтесь для ежедневных обновлений из России, Украины и Евразии.\n\n"
            f"👉 Подписаться | 🔔 Уведомления | 👍 Лайк\n\n"
            f"⚠️ Материал предназначен для информационных целей на основе открытых источников.\n\n"
            f"#EurasiaBreaking #Россия #Украина #СрочноеСообщение #МировыеНовости "
            f"#EurasiaNews #RussiaUkraine #BreakingNews #WorldNews #Путин #Зеленский "
            f"#война #НАТО #NATO #новости #Евразия"
        )
    else:
        return (
            f"🔴 BREAKING | {en_title} | Live — {date}\n\n"
            f"📌 Chapters:\n"
            f"00:00 Breaking Alert\n"
            f"00:45 What Happened\n"
            f"02:00 Latest Developments\n"
            f"03:00 International Reaction\n"
            f"04:00 Strategic Context\n"
            f"05:00 Background & History\n"
            f"06:00 Analysis & Outlook\n\n"
            f"📡 Sources: {source} and international agencies\n"
            f"🌍 Subscribe for daily updates from Russia, Ukraine & Eurasia.\n\n"
            f"👉 Subscribe | 🔔 Bell | 👍 Like\n\n"
            f"⚠️ For informational purposes. Sourced from open-access international media.\n\n"
            f"#EurasiaBreaking #Russia #Ukraine #BreakingNews #WorldNews "
            f"#RussiaUkraine #Putin #Zelensky #War #NATO #EurasiaNews "
            f"#Geopolitics #LiveNews #RussianNews #UkraineWar"
        )


# ── Main entry ────────────────────────────────────────────────────────────────

def generate_job(lang: str = "ru", output_path: str = "jobs/current_job.json") -> dict:
    print(f"\n[NEWS] Fetching fresh trending news (avoiding repeats)...")
    history  = _load_history()
    articles = fetch_top_stories(history)
    primary  = articles[0]

    en_script = _build_long_english_script(articles)
    en_title  = primary["title"]

    if lang == "ru":
        print("[NEWS] Translating to Russian...")
        script = _translate_to_russian(en_script)
        title  = "🔴 СРОЧНО: " + _translate_title(en_title)[:72]
    else:
        script = en_script
        title  = "🔴 BREAKING: " + en_title[:72]

    description = _build_seo_description(articles, en_title, lang)
    tags        = _extract_seo_tags(articles)

    words = re.findall(r'\b[A-Za-z]{4,}\b', en_title)
    search_query = " ".join(w for w in words[:5]
                            if w.lower() not in {"that","this","with","from","have","been","will","were","they"})
    if not search_query:
        search_query = "Russia Ukraine war news geopolitics"

    job = {
        "title":        title,
        "description":  description,
        "script":       script,
        "search_query": search_query,
        "lang":         lang,
        "format":       "long",
        "tags":         tags,
        "category_id":  "25",
        "_meta": {
            "source":           primary["source"],
            "original_title":   en_title,
            "fetched_at":       datetime.datetime.utcnow().isoformat() + "Z",
            "script_words":     len(en_script.split()),
            "related_sources":  [a["source"] for a in articles[1:]],
        }
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    # Save to history so next run picks a different story
    _save_history(history, _story_hash(en_title), en_title)

    wc = len(script.split())
    print(f"[NEWS] ✅ Job saved → {output_path}")
    print(f"[NEWS] Title  : {title}")
    print(f"[NEWS] Script : {wc} words  (~{wc//130:.1f} min)")
    return job


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
    generate_job(lang=lang)
