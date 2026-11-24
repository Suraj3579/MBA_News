import os
import re
import html
import json
import time
import socket
import urllib.request
import feedparser
from datetime import date
from google import genai
from supabase import create_client

# Hard 15-second socket timeout for RSS network calls
socket.setdefaulttimeout(15)

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"].strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing. Aborting without touching Supabase.")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
gemini = genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": 20000})

MODEL_FALLBACK_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-2.5-flash-lite",
]

RSS_FEEDS = [
    # Financial Times
    ("Financial Times", "https://www.ft.com/?format=rss"),
    ("Financial Times", "https://www.ft.com/markets?format=rss"),
    ("Financial Times", "https://www.ft.com/companies?format=rss"),
    ("Financial Times", "https://www.ft.com/technology?format=rss"),
    ("Financial Times", "https://www.ft.com/world?format=rss"),
    ("Financial Times", "https://www.ft.com/climate-capital?format=rss"),
    ("Financial Times", "https://www.ft.com/lex?format=rss"),

    # The Economist
    ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml"),
    ("The Economist", "https://www.economist.com/business/rss.xml"),
    ("The Economist", "https://www.economist.com/science-and-technology/rss.xml"),
    ("The Economist", "https://www.economist.com/leaders/rss.xml"),
    ("The Economist", "https://www.economist.com/international/rss.xml"),
    ("The Economist", "https://www.economist.com/the-world-this-week/rss.xml"),

    # The New York Times
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Dealbook.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/EnergyEnvironment.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml"),

    # Harvard Business Review & Bloomberg
    ("Harvard Business Review", "http://feeds.hbr.org/harvardbusiness"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg", "https://feeds.bloomberg.com/technology/news.rss"),
]

ALLOWED_TAGS = [
    "Macro & Geopolitics",
    "Capital Markets & M&A",
    "Corporate Strategy",
    "AI & Digital Transformation",
    "Leadership & Governance",
    "Operations & Supply Chain",
    "Energy & Climate",
    "Consumer & Retail",
    "Healthcare & Biotech",
    "Entrepreneurship & VC",
]

MAX_ARTICLES_PER_DAY = 40


def clean_html_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if "nytimes.com" in url or "economist.com" in url:
        return url.split("?")[0]
    if "ft.com" in url:
        return url.split("?")[0]
    if url.startswith("/") and "hbr.org" not in url:
        return f"https://hbr.org{url}"
    return url


def fetch_feed_safely(feed_url: str):
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml_bytes = resp.read()
    return feedparser.parse(xml_bytes)


def verify_gemini_connection() -> None:
    """Fail fast before touching Supabase if Gemini API connection or quota fails."""
    last_error = None
    for model_name in MODEL_FALLBACK_CHAIN:
        try:
            resp = gemini.models.generate_content(
                model=model_name,
                contents='Return JSON: {"ok": true}',
                config={"response_mime_type": "application/json"},
            )
            if resp and resp.text:
                print(f"Verified Gemini API connection via {model_name}.", flush=True)
                return
        except Exception as err:
            last_error = err
            print(f"  [Healthcheck] {model_name} failed: {type(err).__name__}: {err}", flush=True)

    raise RuntimeError(f"Gemini API connection failed across all models. Aborting database write. Last error: {last_error}")


def generate_with_gemini(prompt: str) -> str:
    last_error = RuntimeError("All Gemini models failed")
    for model_name in MODEL_FALLBACK_CHAIN:
        try:
            response = gemini.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            if not response or not response.text:
                raise ValueError("Empty response from Gemini")
            return response.text
        except Exception as err:
            last_error = err
            err_msg = str(err)
            if "429" in err_msg or "503" in err_msg:
                print(f"    [Fallback] {model_name} busy/rate-limited, switching...", flush=True)
                time.sleep(1.5)
            else:
                print(f"    [Fallback] {model_name} failed ({type(err).__name__}), switching...", flush=True)

    raise last_error


def analyze_article_strict(source: str, title: str, abstract: str) -> dict | None:
    """
    Processes article strictly through Gemini LLM.
    Returns None if Gemini fails or deems the article low-signal (so it is NOT inserted into Supabase).
    """
    prompt = f"""
    You are an executive briefing editor for MBA students and business leaders.
    Evaluate this news story from {source}:
    Headline: {title}
    Abstract: {abstract}

    First, decide if this is a high-signal business, finance, macroeconomics, policy, or technology story worthy of an executive/MBA daily brief (reject cartoons, newsletter promos, travel deals, sports/entertainment trivia, or empty items).

    Respond strictly with valid JSON containing four keys:
    1. "keep": boolean (true if high-signal business/MBA relevant, false if low-signal noise).
    2. "summary": A crisp, objective 2-sentence executive summary of the story.
    3. "mba_takeaway": One sharp sentence linking this event to an executive/MBA framework (e.g., capital allocation, Porter's Five Forces, corporate governance, pricing power, supply chain resilience).
    4. "tags": A JSON array of 2 or 3 tags chosen ONLY from this exact list: {json.dumps(ALLOWED_TAGS)}.
    """
    try:
        raw_json = generate_with_gemini(prompt)
        data = json.loads(raw_json)

        if not data.get("keep", True):
            print(f"  [Filtered Low-Signal] {source}: {title[:55]}", flush=True)
            return None

        summary = (data.get("summary") or "").strip()
        takeaway = (data.get("mba_takeaway") or "").strip()
        valid_tags = [t for t in data.get("tags", []) if t in ALLOWED_TAGS]

        if not summary or not takeaway or not valid_tags:
            print(f"  [Skipped Incomplete LLM Output] {source}: {title[:55]}", flush=True)
            return None

        return {
            "summary": summary,
            "mba_takeaway": takeaway,
            "tags": valid_tags[:3],
        }
    except Exception as ai_err:
        print(f"  [LLM Failed - Skipping Article] {source}: {title[:55]} -> {type(ai_err).__name__}: {ai_err}", flush=True)
        return None


def main():
    today_str = date.today().isoformat()
    print(f"Starting strict LLM-gated ingestion for {today_str}...", flush=True)

    # 1. Fail immediately if Gemini is unreachable or out of credits (no DB writes will happen)
    verify_gemini_connection()

    # 2. Load previously stored URLs & titles from Supabase to block duplicates
    existing_rows = supabase.table("articles").select("url, title").execute().data or []
    existing_urls = {normalize_url(row.get("url", "")) for row in existing_rows}
    existing_titles = {clean_html_text(row.get("title", "")).lower() for row in existing_rows}
    print(f"Loaded {len(existing_urls)} previously stored articles from Supabase.", flush=True)

    candidates = []
    for source, feed_url in RSS_FEEDS:
        try:
            feed = fetch_feed_safely(feed_url)
        except Exception as feed_err:
            print(f"  [Feed Skip] {source} ({feed_url}) -> {feed_err}", flush=True)
            continue

        for entry in feed.entries[:4]:
            raw_link = getattr(entry, "link", "").strip()
            if source == "Harvard Business Review" and raw_link.startswith("/"):
                raw_link = f"https://hbr.org{raw_link}"
            url = normalize_url(raw_link)
            title = clean_html_text(getattr(entry, "title", ""))
            abstract = clean_html_text(getattr(entry, "summary", ""))
            title_key = title.lower()

            if not url or not title or not abstract or url in existing_urls or title_key in existing_titles:
                continue

            existing_urls.add(url)
            existing_titles.add(title_key)
            candidates.append((source, title, url, abstract))

    print(f"Collected {len(candidates)} brand-new candidate articles. Curating up to {MAX_ARTICLES_PER_DAY} via Gemini...", flush=True)

    rows_to_insert = []
    for source, title, url, abstract in candidates:
        if len(rows_to_insert) >= MAX_ARTICLES_PER_DAY:
            break

        enriched = analyze_article_strict(source, title, abstract)
        if enriched is None:
            # Never insert articles that failed Gemini processing or were marked low-signal
            continue

        rows_to_insert.append({
            "publish_date": today_str,
            "source": source,
            "title": title,
            "url": url,
            "summary": enriched["summary"],
            "mba_takeaway": enriched["mba_takeaway"],
            "tags": enriched["tags"],
        })
        print(f"  [LLM Approved #{len(rows_to_insert)}] {source}: {title[:55]}", flush=True)
        time.sleep(0.5)

    # 3. Only write LLM-approved rows to Supabase
    if rows_to_insert:
        supabase.table("articles").upsert(
            rows_to_insert,
            on_conflict="url",
            ignore_duplicates=True,
        ).execute()
        print(f"Ingestion complete. Saved {len(rows_to_insert)} Gemini-curated articles to Supabase.", flush=True)
    else:
        print("Ingestion complete. 0 articles passed Gemini curation; no changes written to Supabase.", flush=True)


if __name__ == "__main__":
    main()
