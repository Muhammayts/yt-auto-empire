"""
SugoidesJapan Content Fetcher — fetches trending/viral content from Japan
across ALL categories: news, anime, gaming, food, pop culture, tech,
weird/funny, travel, sports, music.

Sources (all free, no API key):
  NEWS:        NHK World, Japan Times, The Mainichi, Reuters Asia
  VIRAL/FUNNY: Sora News 24, RocketNews24
  ANIME:       Anime News Network, Crunchyroll News
  GAMING:      Kotaku, IGN Asia, Nintendo Life
  TECH:        The Verge (Asia), Engadget
  FOOD/TRAVEL: Japan Bullet, Timeout Tokyo
  MUSIC/POP:   Billboard Japan (RSS), JpopAsia

Deduplication: tracks used stories in jobs/seen_stories.json
Script target: 850+ words → ~6-7 min video at ~120 wpm TTS
"""

import json, os, re, sys, time, hashlib, datetime, feedparser

try:
    from deep_translator import GoogleTranslator
    TRANSLATE_AVAILABLE = True
except ImportError:
    TRANSLATE_AVAILABLE = False
    print("[NEWS] deep-translator not installed — scripts will be in English")

# ── RSS Feeds — grouped by category ──────────────────────────────────────────
RSS_FEEDS = [
    # 🗞️ News
    ("NHK World",        "https://www3.nhk.or.jp/rss/news/cat0.xml"),
    ("Japan Times",      "https://www.japantimes.co.jp/feed/"),
    ("The Mainichi",     "https://mainichi.jp/rss/etc/english.rss"),
    ("Reuters Asia",     "https://feeds.reuters.com/reuters/asiaPacificNews"),

    # 😂 Viral / Weird / Funny Japan
    ("Sora News 24",     "https://soranews24.com/feed/"),
    ("RocketNews24",     "https://en.rocketnews24.com/feed/"),

    # 🎌 Anime / Manga
    ("Anime News",       "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=us"),
    ("Crunchyroll News", "https://www.crunchyroll.com/newsrss?lang=enUS"),

    # 🎮 Gaming
    ("Kotaku",           "https://kotaku.com/rss"),
    ("Nintendo Life",    "https://www.nintendolife.com/feeds/news"),
    ("IGN",              "https://feeds.ign.com/ign/all"),

    # 📱 Tech
    ("The Verge",        "https://www.theverge.com/rss/index.xml"),
    ("Engadget",         "https://www.engadget.com/rss.xml"),

    # 🍜 Food / Travel / Lifestyle
    ("Time Out Tokyo",   "https://www.timeout.com/tokyo/feed.xml"),
    ("Japan Bullet",     "https://www.japanbullet.com/feed"),

    # 🎵 Music / Pop Culture
    ("JpopAsia",         "https://www.jpopasia.com/feed/news/"),
    ("Billboard JP",     "https://www.billboard-japan.com/d_news/rss"),
]

# Category tags for each feed — used for content-aware script writing
FEED_CATEGORIES = {
    "NHK World":        "news",
    "Japan Times":      "news",
    "The Mainichi":     "news",
    "Reuters Asia":     "news",
    "Sora News 24":     "viral",
    "RocketNews24":     "viral",
    "Anime News":       "anime",
    "Crunchyroll News": "anime",
    "Kotaku":           "gaming",
    "Nintendo Life":    "gaming",
    "IGN":              "gaming",
    "The Verge":        "tech",
    "Engadget":         "tech",
    "Time Out Tokyo":   "lifestyle",
    "Japan Bullet":     "lifestyle",
    "JpopAsia":         "music",
    "Billboard JP":     "music",
}

# Keywords that make a story relevant to our channel
PRIORITY_KEYWORDS = [
    # Places
    "japan", "japanese", "tokyo", "osaka", "kyoto", "okinawa",
    "akihabara", "harajuku", "shibuya", "shinjuku", "asakusa",
    # Anime / Manga
    "anime", "manga", "otaku", "cosplay", "studio ghibli", "shonen",
    "demon slayer", "one piece", "naruto", "dragon ball", "jujutsu",
    # Gaming
    "nintendo", "sony", "playstation", "switch", "pokemon", "mario",
    "zelda", "final fantasy", "gaming", "esports", "game",
    # Tech / Companies
    "toyota", "honda", "fujitsu", "softbank", "panasonic", "sharp",
    "robot", "ai", "technology", "electric vehicle", "innovation",
    # Food
    "ramen", "sushi", "tempura", "matcha", "sake", "wagyu", "bento",
    "convenience store", "combini", "michelin", "street food",
    # Pop culture / Music
    "idol", "j-pop", "jpop", "k-pop", "kawaii", "samurai", "ninja",
    "vtuber", "hololive", "tiktok", "viral", "trending",
    # Nature / Events
    "cherry blossom", "sakura", "mount fuji", "earthquake", "typhoon",
    "sumo", "baseball", "world cup", "olympics", "festival", "matsuri",
    # Economy
    "yen", "nikkei", "economy", "prime minister", "election",
]

HOT_KEYWORDS = [
    "viral", "trending", "record", "first ever", "shocking", "incredible",
    "amazing", "new release", "announced", "revealed", "just dropped",
    "sells out", "banned", "world record", "unexpected", "surprised",
    "only in japan", "weird", "hilarious", "unbelievable", "must see",
    "everyone is talking", "gone viral", "blew up", "legendary",
]

# Boost score for non-news categories to balance against heavy news feeds
CATEGORY_BOOST = {
    "viral":     25,   # Sora News / RocketNews — almost always Japan-specific
    "anime":     20,
    "gaming":    20,
    "music":     15,
    "lifestyle": 15,
    "tech":      10,
    "news":       0,
}

HEADERS      = {"User-Agent": "SugoidesJapan/1.0 RSS Reader"}
HISTORY_FILE = "jobs/seen_stories.json"
HISTORY_HOURS = 72   # skip any story seen in last 72 hours (3 days)


# ── Deduplication ─────────────────────────────────────────────────────────────

def _story_hash(title: str) -> str:
    return hashlib.md5(re.sub(r'\W+', ' ', title.lower()).strip().encode()).hexdigest()[:12]

def _load_history() -> dict:
    # Try multiple locations — workspace root AND jobs/ subdir
    for path in [HISTORY_FILE, os.path.join(os.getcwd(), HISTORY_FILE)]:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[NEWS] Loaded history: {len(data)} seen stories from {path}")
                return data
            except Exception as e:
                print(f"[NEWS] History load error at {path}: {e}")
    print("[NEWS] No history file found — all stories treated as new")
    return {}

def _save_history(history: dict, new_hash: str, title: str):
    # Keep last 14 days only
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).isoformat()
    history = {k: v for k, v in history.items() if v.get("seen_at","") > cutoff}
    history[new_hash] = {"title": title, "seen_at": datetime.datetime.utcnow().isoformat()+"Z"}
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"[NEWS] History saved: {len(history)} total entries in {HISTORY_FILE}")

def _was_recently_used(h: str, history: dict) -> bool:
    if h not in history:
        return False
    try:
        seen = datetime.datetime.fromisoformat(history[h]["seen_at"].rstrip("Z"))
        hours_ago = (datetime.datetime.utcnow() - seen).total_seconds() / 3600
        if hours_ago < HISTORY_HOURS:
            print(f"[NEWS] Skipping (seen {hours_ago:.1f}h ago): {history[h].get('title','')[:60]}")
            return True
        return False
    except Exception:
        return False


# ── Fetch & Score ─────────────────────────────────────────────────────────────

def _fetch_feed(source: str, url: str) -> list:
    try:
        feed = feedparser.parse(url)
        arts = []
        category = FEED_CATEGORIES.get(source, "news")
        for e in feed.entries[:25]:
            title   = e.get("title","").strip()
            summary = (e.get("content",[{}])[0].get("value","")
                       or e.get("summary","") or e.get("description","")).strip()
            summary = re.sub(r'<[^>]+>', ' ', summary)
            summary = re.sub(r'\s+', ' ', summary).strip()
            if title and len(title) > 8:
                arts.append({
                    "source":   source,
                    "category": category,
                    "title":    title,
                    "summary":  summary[:2000],
                    "link":     e.get("link",""),
                })
        print(f"[NEWS] {source} [{category}]: {len(arts)} articles")
        return arts
    except Exception as e:
        print(f"[NEWS] Failed {source}: {e}")
        return []

def _score(article: dict, all_articles: list) -> float:
    text  = (article["title"] + " " + article["summary"]).lower()
    score = sum(10 for kw in PRIORITY_KEYWORDS if kw in text)
    score += sum(20 for kw in HOT_KEYWORDS if kw in text)
    score += min(len(article["summary"]) // 60, 15)
    # Category boost — non-news viral/anime/gaming content gets bumped up
    score += CATEGORY_BOOST.get(article.get("category","news"), 0)
    # Cross-feed overlap bonus
    my_words = set(re.findall(r'\b[a-z]{5,}\b', article["title"].lower()))
    for other in all_articles:
        if other is not article:
            other_words = set(re.findall(r'\b[a-z]{5,}\b', other["title"].lower()))
            if len(my_words & other_words) >= 2:
                score += 12
    return min(score, 400)

def fetch_top_stories(history: dict) -> list:
    all_articles = []
    for source, url in RSS_FEEDS:
        all_articles.extend(_fetch_feed(source, url))
        time.sleep(0.3)

    if not all_articles:
        raise RuntimeError("No articles fetched")

    # Deduplicate
    seen_words, unique = [], []
    for art in all_articles:
        words = set(re.findall(r'\b[a-z]{5,}\b', art["title"].lower()))
        if any(len(words & sw) >= 3 for sw in seen_words):
            continue
        seen_words.append(words)
        unique.append(art)

    scored = sorted(unique, key=lambda a: _score(a, all_articles), reverse=True)

    # Pick freshest unseen story
    unseen = [a for a in scored if not _was_recently_used(_story_hash(a["title"]), history)]
    if unseen:
        primary = unseen[0]
        print(f"[NEWS] ✅ Fresh story found ({len(unseen)} unseen out of {len(scored)} total)")
    else:
        # All stories seen recently — pick the least-recently-used one
        print(f"[NEWS] ⚠️  All {len(scored)} stories were seen recently — picking oldest")
        def _age(a):
            h = _story_hash(a["title"])
            if h not in history: return 0
            try:
                seen = datetime.datetime.fromisoformat(history[h]["seen_at"].rstrip("Z"))
                return (datetime.datetime.utcnow() - seen).total_seconds()
            except Exception:
                return 0
        primary = max(scored, key=_age)

    # Related articles
    p_words  = set(re.findall(r'\b[a-z]{5,}\b', primary["title"].lower()))
    related  = [a for a in scored if a is not primary
                and (len(p_words & set(re.findall(r'\b[a-z]{5,}\b', a["title"].lower()))) >= 1
                     or _score(a, all_articles) > 40)][:6]

    for a in [primary] + related[:2]:
        print(f"[NEWS] [{a['source']}] {a['title'][:70]}")
    return [primary] + related


# ── Script builder ────────────────────────────────────────────────────────────

def _sents(text: str, n: int = 8) -> list:
    text = re.sub(r'\s+', ' ', text).strip()
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 20][:n]

def _build_long_english_script(articles: list) -> str:
    primary = articles[0]
    related = articles[1:]
    title   = primary["title"].rstrip(".!?")
    source  = primary["source"]
    date    = datetime.datetime.utcnow().strftime("%B %d, %Y")

    p = _sents(primary["summary"], 10)
    r = []
    for a in related[:4]:
        r.extend(_sents(a["summary"], 4))

    def pick(lst, i, fb=""): return lst[i] if i < len(lst) else fb

    all_text = (title + " " + primary["summary"]).lower()
    category  = primary.get("category", "news")

    # Fine-grained topic detection (overrides category if needed)
    is_anime  = category == "anime" or any(w in all_text for w in ["anime","manga","otaku","cosplay","studio ghibli","vtuber"])
    is_gaming = category == "gaming" or any(w in all_text for w in ["nintendo","playstation","switch","pokemon","mario","zelda","gaming","esports"])
    is_tech   = category == "tech" or any(w in all_text for w in ["sony","robot","ai","technology","app","electric","innovation","softbank"])
    is_food   = any(w in all_text for w in ["ramen","sushi","food","restaurant","recipe","eat","matcha","wagyu","bento","combini"])
    is_music  = category == "music" or any(w in all_text for w in ["idol","jpop","j-pop","singer","actor","actress","celebrity","vtuber","hololive"])
    is_viral  = category == "viral" or any(w in all_text for w in ["viral","weird","funny","hilarious","only in japan","unusual","bizarre","unbelievable"])
    is_news   = any(w in all_text for w in ["earthquake","typhoon","election","minister","economy","yen","disaster"])

    # S1: Hook — tone varies by category (~80 w)
    if is_viral:
        s1 = (
            f"Oh my goodness, you are NOT going to believe this one. Welcome back to SugoidesJapan! "
            f"Today's story is absolutely wild, and it comes straight out of Japan. "
            f"{title}. "
            f"{pick(p,0,'This is exactly the kind of only-in-Japan story that makes the internet go crazy.')} "
            f"Stick around because this one gets better and better the more you hear about it."
        )
    elif is_anime:
        s1 = (
            f"Anime fans, this one is for you! Welcome back to SugoidesJapan. "
            f"Today we are talking about something the entire anime community cannot stop discussing. "
            f"{title}. "
            f"{pick(p,0,'This news has sent shockwaves through the anime fandom worldwide.')} "
            f"Let us dive in and break down everything you need to know."
        )
    elif is_gaming:
        s1 = (
            f"Gamers, listen up — this is HUGE! Welcome back to SugoidesJapan. "
            f"Today we are covering some massive gaming news straight from Japan. "
            f"{title}. "
            f"{pick(p,0,'The gaming world is buzzing about this right now and for very good reason.')} "
            f"Let us get into every single detail."
        )
    elif is_music:
        s1 = (
            f"Hey everyone, welcome back to SugoidesJapan! "
            f"Japan's music and entertainment scene just gave us something incredible to talk about. "
            f"{title}. "
            f"{pick(p,0,'This is blowing up across Japanese social media and fan communities worldwide.')} "
            f"Here is everything you need to know."
        )
    elif is_food:
        s1 = (
            f"Food lovers, this one is going to make you hungry! Welcome back to SugoidesJapan. "
            f"Japan's incredible food scene has done it again with something truly special. "
            f"{title}. "
            f"{pick(p,0,'Food lovers around the world are talking about this right now.')} "
            f"Let us take a deep and delicious dive into this story."
        )
    else:
        s1 = (
            f"Hello everyone, and welcome back to SugoidesJapan. "
            f"Today we are covering a story that is trending all across Japan right now. "
            f"{title}. "
            f"{pick(p,0,'This story is spreading rapidly across Japanese social media and news outlets.')} "
            f"Stay with us as we break down everything you need to know."
        )

    # S2: What's happening (~130 w)
    det = " ".join(filter(None,[pick(p,1),pick(p,2),pick(p,3)]))
    if len(det.split()) < 20:
        det = f"Reports from {source} and other Japanese media confirm that {title.lower()}."
    s2 = (
        f"So what exactly is going on? Here are the full details. "
        f"{det} "
        f"This story first came to light when {source} reported on the development, "
        f"and it quickly spread across Japanese social media platforms including Twitter Japan, "
        f"Instagram, and YouTube, where it became one of the top trending topics of the day. "
        f"Reactions from the Japanese public have been strong and mixed."
    )

    # S3: Latest updates (~110 w)
    latest = " ".join(filter(None,[pick(p,4),pick(p,5),pick(r,0)]))
    if len(latest.split()) < 15:
        latest = "The story continues to develop with new information emerging throughout the day."
    s3 = (
        f"Here are the latest updates as of {date}. "
        f"{latest} "
        f"Japanese netizens have been responding in huge numbers online, "
        f"with the story generating hundreds of thousands of views and shares in just a few hours. "
        f"Multiple major Japanese news outlets have now picked up the story, "
        f"confirming its importance and relevance to everyday life in Japan."
    )

    # S4: Why it matters / reaction (~140 w)
    react = " ".join(filter(None,[pick(r,1),pick(r,2)]))
    s4 = (
        f"Why is this story resonating so strongly with people in Japan? "
        f"{react} "
        f"Japanese social media users have been sharing their thoughts widely. "
        f"Many expressed excitement and surprise, with some calling it one of the most "
        f"significant developments in this area in recent memory. "
        f"Others noted the broader implications for Japanese society and culture. "
        f"Industry experts and commentators have also weighed in, "
        f"with most agreeing that this represents a meaningful shift worth paying close attention to."
    )

    # S5: Deep context — topic-aware (~150 w)
    if is_viral:
        s5 = (
            f"You know, Japan has a very special relationship with going viral. "
            f"Japanese internet culture is one of the most unique and creative in the entire world. "
            f"Platforms like Twitter Japan — one of the most active Twitter communities globally — "
            f"can take a single funny or bizarre story and turn it into a national conversation overnight. "
            f"Japan's culture of craftsmanship, attention to detail, and love of the quirky and unexpected "
            f"means that incredible, weird, and wonderful things happen there all the time. "
            f"Japanese people have a word for it — 'sugoi' — which means amazing, incredible, unbelievable. "
            f"And honestly, that is exactly the right word for what we are talking about today."
        )
    elif is_anime:
        s5 = (
            f"To understand why this matters so much, let us look at the anime industry. "
            f"Japan's animation industry generates over two trillion yen annually and employs hundreds of thousands of people. "
            f"Anime is no longer just a Japanese cultural product — it is a global phenomenon, "
            f"with streaming platforms like Netflix, Crunchyroll, and Amazon Prime competing fiercely for rights. "
            f"The global anime market has grown by over fifty percent in the last five years alone. "
            f"What happens in Japan's anime industry often sets trends that ripple across entertainment worldwide. "
            f"This latest development is a perfect example of how the industry continues to evolve and surprise fans."
        )
    elif is_gaming:
        s5 = (
            f"Japan is the birthplace of modern gaming as we know it. "
            f"Nintendo, Sony PlayStation, Sega, Capcom, Square Enix — virtually every iconic gaming brand "
            f"you grew up with traces its roots back to Japan. "
            f"The global video game industry is worth over two hundred billion dollars, "
            f"and Japanese companies are responsible for some of its most beloved franchises: "
            f"Mario, Zelda, Pokemon, Final Fantasy, Monster Hunter, Street Fighter — the list goes on forever. "
            f"When something major happens in Japanese gaming, the entire world's gaming community pays attention. "
            f"Today's story is a perfect example of why Japan still leads the way."
        )
    elif is_tech:
        s5 = (
            f"Japan has always been at the cutting edge of technology, and this story is no exception. "
            f"From the invention of the walkman to the creation of the world's fastest bullet trains, "
            f"Japan has consistently led the world in technological innovation. "
            f"Today, Japanese companies like Sony, Nintendo, Toyota, and SoftBank continue to shape "
            f"how the world interacts with technology. "
            f"Robotics and artificial intelligence are areas where Japan holds a commanding position, "
            f"with the country investing heavily in automation to address its aging population challenge. "
            f"This story shows once again why Japan's tech sector remains one of the most exciting to watch."
        )
    elif is_food:
        s5 = (
            f"Japanese food culture is one of the most admired in the entire world, "
            f"and it continues to evolve in the most exciting ways. "
            f"Japan has more Michelin-starred restaurants than any other country on earth, "
            f"with Tokyo alone holding over two hundred stars — more than Paris and New York combined. "
            f"But Japanese food culture is not just about fine dining. "
            f"It is about the craftsmanship and dedication to quality that goes into every bowl of ramen, "
            f"every piece of sushi, and every cup of matcha tea. "
            f"Food trends that start in Japan regularly spread worldwide, "
            f"making this story especially interesting for food lovers everywhere."
        )
    elif is_music:
        s5 = (
            f"Japanese pop culture and entertainment have an enormous global footprint. "
            f"J-pop and idol culture have millions of dedicated fans not just in Japan "
            f"but across Asia, Europe, and North America. "
            f"VTubers — virtual YouTubers pioneered by Japanese companies like Hololive — "
            f"have taken the streaming world by storm with millions of subscribers globally. "
            f"The idol industry in Japan is a multi-billion yen business built on fan devotion, "
            f"meticulously crafted public images, and an almost unparalleled level of engagement. "
            f"When something happens in this world, millions of fans worldwide pay very close attention."
        )
    else:
        s5 = (
            f"To put this in context, Japan is one of the world's most fascinating and unique societies. "
            f"With a population of about one hundred twenty-five million people, "
            f"Japan is the world's third-largest economy and home to some of humanity's most distinctive culture, "
            f"from ancient samurai traditions to cutting-edge robotics. "
            f"Japanese society balances a deep reverence for tradition with an enthusiastic embrace of innovation. "
            f"This combination makes Japan a constant source of interesting and surprising stories "
            f"that capture global attention and inspire people around the world. "
            f"Understanding what trends in Japan today often tells us something important about where the world is headed tomorrow."
        )

    # S6: Background & history (~120 w)
    s6 = (
        f"Let us also look at some important background information for context. "
        f"{pick(r,3,'Japan continues to be one of the most culturally influential nations in the world.')} "
        f"Japan has undergone remarkable transformations over the past century — "
        f"from the Meiji Restoration that opened the country to the world in the eighteen hundreds, "
        f"to the post-war economic miracle that turned Japan into a global powerhouse in just a few decades. "
        f"Today's Japan is a country navigating major challenges including an aging population, "
        f"economic pressures, and shifting cultural values, while simultaneously exporting its unique culture "
        f"and products to an eager global audience."
    )

    # S7: Extra detail + analysis (~100 w)
    extra = " ".join(filter(None,[pick(p,6),pick(p,7),pick(r,4)]))
    s7 = (
        f"Diving deeper into this story, there is more worth noting. "
        f"{extra if len(extra.split()) > 15 else ''} "
        f"Analysts who follow Japanese trends closely say this kind of development is a sign "
        f"of how rapidly things can change and evolve in Japan. "
        f"What begins as a niche story can quickly become a nationwide conversation, "
        f"reflecting the unique speed at which information travels in Japan's hyper-connected society."
    ).strip()

    # S8: Closing CTA (~80 w)
    s8 = (
        f"That is everything we have on this story for now. "
        f"SugoidesJapan covers the most interesting, trending, and viral stories from Japan every single day. "
        f"If you enjoyed this video, please give it a like — it really helps our channel grow. "
        f"Hit the subscribe button and turn on the notification bell "
        f"so you never miss our daily updates from Japan. "
        f"We will see you in the next video. Arigatou gozaimashita!"
    )

    full = re.sub(r'\s{2,}', ' ', " ".join([s1,s2,s3,s4,s5,s6,s7,s8])).strip()

    # Enforce 850-word minimum
    if len(full.split()) < 850:
        full += (
            " Japan's influence on global pop culture, technology, and lifestyle continues to grow every year. "
            "From the latest anime releases to groundbreaking robotics, from viral food trends to iconic fashion, "
            "Japan never stops surprising and inspiring the rest of the world. "
            "At SugoidesJapan we are dedicated to bringing you the most exciting and relevant stories "
            "from this remarkable country, delivered fresh every single day. "
            "Thank you so much for watching and for being part of our growing community of Japan enthusiasts."
        )
        full = re.sub(r'\s{2,}', ' ', full).strip()

    wc = len(full.split())
    print(f"[NEWS] Script: {wc} words (~{wc//120:.1f} min at 120 wpm)")
    return full


# ── Translation to Japanese ───────────────────────────────────────────────────

def _translate_to_japanese(text: str) -> str:
    if not TRANSLATE_AVAILABLE:
        return text
    max_chunk = 3500
    sents = re.split(r'(?<=[.!?])\s+', text)
    chunks, cur = [], ""
    for s in sents:
        if len(cur) + len(s) < max_chunk:
            cur += (" " if cur else "") + s
        else:
            if cur: chunks.append(cur)
            cur = s
    if cur: chunks.append(cur)

    parts = []
    translator = GoogleTranslator(source="en", target="ja")
    for i, chunk in enumerate(chunks):
        for attempt in range(3):
            try:
                parts.append(translator.translate(chunk))
                print(f"[NEWS] Translated chunk {i+1}/{len(chunks)}")
                time.sleep(0.5)
                break
            except Exception as e:
                print(f"[NEWS] Translation {attempt+1} failed: {e}")
                time.sleep(2*(attempt+1))
        else:
            parts.append(chunk)
    return " ".join(parts)

def _translate_title(title: str, target: str = "ja") -> str:
    if not TRANSLATE_AVAILABLE: return title
    try:
        return GoogleTranslator(source="en", target=target).translate(title)
    except Exception:
        return title


# ── SEO Tags & Description ────────────────────────────────────────────────────

def _extract_seo_tags(articles: list) -> list:
    all_text = " ".join((a["title"]+" "+a["summary"]).lower() for a in articles)
    base = ["SugoidesJapan","Japan","日本","viral","trending","バイラル","トレンド","日本のニュース"]
    kw_map = {
        "anime":     ["anime","アニメ","manga","マンガ"],
        "nintendo":  ["Nintendo","任天堂","gaming","ゲーム"],
        "sony":      ["Sony","ソニー","technology","テクノロジー"],
        "ramen":     ["ramen","ラーメン","Japanese food","日本食"],
        "sushi":     ["sushi","寿司","Japanese cuisine"],
        "idol":      ["jpop","J-POP","idol","アイドル"],
        "tokyo":     ["Tokyo","東京","Japan travel"],
        "earthquake":["earthquake","地震","Japan disaster"],
        "typhoon":   ["typhoon","台風","Japan weather"],
        "robot":     ["robot","ロボット","Japan AI","人工知能"],
        "kawaii":    ["kawaii","かわいい","Japanese culture","日本文化"],
        "game":      ["game","ゲーム","esports","gaming news"],
        "cosplay":   ["cosplay","コスプレ","otaku","オタク"],
        "sumo":      ["sumo","相撲","Japanese sports"],
        "toyota":    ["Toyota","トヨタ","Japan business","日本経済"],
    }
    extra = []
    for kw, tags in kw_map.items():
        if kw in all_text and len(extra) < 20:
            extra.extend(tags[:2])
    return list(dict.fromkeys(base + extra))[:15]

def _build_seo_description(articles: list, en_title: str, lang: str) -> str:
    source = articles[0]["source"]
    date   = datetime.datetime.utcnow().strftime("%Y年%m月%d日") if lang == "ja" \
             else datetime.datetime.utcnow().strftime("%B %d, %Y")
    if lang == "ja":
        return (
            f"🔥 トレンド | {en_title} | 日本のバイラルニュース — {date}\n\n"
            f"📌 目次:\n"
            f"00:00 イントロ・注目のニュース\n"
            f"00:45 詳細と最新情報\n"
            f"02:00 SNSの反応\n"
            f"03:00 なぜ話題になっているのか\n"
            f"04:00 背景と詳しい解説\n"
            f"05:00 専門家の分析\n"
            f"06:00 まとめ・チャンネル登録のお願い\n\n"
            f"📡 情報源: {source} および日本の主要メディア\n"
            f"🌍 毎日日本の最新トレンド・バイラルニュースをお届けします！\n\n"
            f"👉 チャンネル登録 | 🔔 通知オン | 👍 高評価\n\n"
            f"⚠️ 本動画は公開情報をもとにした情報提供を目的としています。\n\n"
            f"#SugoidesJapan #日本 #トレンド #バイラル #日本のニュース "
            f"#アニメ #ゲーム #テクノロジー #日本文化 #東京 "
            f"#Japan #viral #trending #japanews #anime"
        )
    else:
        return (
            f"🔥 TRENDING | {en_title} | SugoidesJapan — {date}\n\n"
            f"📌 Chapters:\n"
            f"00:00 Intro & Breaking Story\n"
            f"00:45 Full Details\n"
            f"02:00 Social Media Reaction\n"
            f"03:00 Why It's Trending\n"
            f"04:00 Background & Context\n"
            f"05:00 Expert Analysis\n"
            f"06:00 Wrap-Up & Subscribe\n\n"
            f"📡 Source: {source} and major Japanese media\n"
            f"🌍 Daily trending & viral news from Japan!\n\n"
            f"👉 Subscribe | 🔔 Bell | 👍 Like\n\n"
            f"#SugoidesJapan #Japan #trending #viral #japannews "
            f"#anime #gaming #technology #japanculture #tokyo"
        )


# ── Main entry ────────────────────────────────────────────────────────────────

def generate_job(lang: str = "ja", output_path: str = "jobs/current_job.json") -> dict:
    print(f"\n[NEWS] Fetching fresh trending Japan content...")
    history  = _load_history()
    articles = fetch_top_stories(history)
    primary  = articles[0]

    en_script = _build_long_english_script(articles)
    en_title  = primary["title"]

    if lang == "ja":
        print("[NEWS] Translating to Japanese...")
        script = _translate_to_japanese(en_script)
        title  = "🔥 話題: " + _translate_title(en_title, "ja")[:72]
    else:
        script = en_script
        title  = "🔥 TRENDING: " + en_title[:72]

    description = _build_seo_description(articles, en_title, lang)
    tags        = _extract_seo_tags(articles)

    words = re.findall(r'\b[A-Za-z]{4,}\b', en_title)
    search_query = " ".join(w for w in words[:5]
                            if w.lower() not in {"that","this","with","from","have","been"})
    if not search_query:
        search_query = "Japan Tokyo viral trending culture"

    job = {
        "title":        title,
        "description":  description,
        "script":       script,
        "search_query": search_query + " Japan",
        "lang":         lang,
        "format":       "long",
        "tags":         tags,
        "category_id":  "24",   # 24 = Entertainment (better fit than News for viral)
        "_meta": {
            "source":          primary["source"],
            "original_title":  en_title,
            "fetched_at":      datetime.datetime.utcnow().isoformat() + "Z",
            "script_words":    len(en_script.split()),
        }
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    _save_history(history, _story_hash(en_title), en_title)

    wc = len(script.split())
    print(f"[NEWS] ✅ Job saved → {output_path}")
    print(f"[NEWS] Title: {title}")
    print(f"[NEWS] Script: {wc} words  (~{wc//120:.1f} min)")
    return job


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ja"
    generate_job(lang=lang)
