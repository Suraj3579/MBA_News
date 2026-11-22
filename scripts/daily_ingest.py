import os
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
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Enforce a 20-second (20,000 ms) timeout on every Gemini API call so stalled requests fail over fast
gemini = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"timeout": 20000},
)

# Fast, high-quota Flash models only (avoids slow Pro timeouts)
MODEL_FALLBACK_CHAIN = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

RSS_FEEDS = [
    ("Financial Times", "https://www.ft.com/?format=rss"),
    ("Financial Times", "https://www.ft.com/markets?format=rss"),
    ("Financial Times", "https://www.ft.com/technology?format=rss"),
    ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml"),
    ("The Economist", "https://www.economist.com/business/rss.xml"),
    ("The Economist", "https://www.economist.com/the-world-this-week/rss.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"),
]

ALLOWED_TAGS = [
    "Finance & Markets",
    "Strategy & Macro",
    "Tech & AI",
    "Organisational Behaviour",
    "Sustainability & ESG",
    "Operations & Supply Chain",
]

def fetch_feed_safely(feed_url: str):
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; TheDailyBriefBot/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml_bytes = resp.read()
    return feedparser.parse(xml_bytes)

def generate_with_fallback(prompt: str) -> str:
    last_error = None
    for model_name in MODEL_FALLBACK_CHAIN:
        try:
            response = gemini.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return response.text
        except Exception as err:
            last_error = err
            print(f"    [Fallback] {model_name} failed ({type(err).__name__}), trying next model...", flush=True)
            time.sleep(1)
    raise last_error

def analyze_article(source: str, title: str, abstract: str) -> dict:
    prompt = f"""
    You are an executive briefing editor for MBA students across global business schools.
    Analyze this news story from {source}:
    Headline: {title}
    Abstract: {abstract}

    Respond strictly with valid JSON containing three keys:
    1. "summary": A crisp, objective 2-sentence executive summary of the story.
    2. "mba_takeaway": One sharp sentence linking this event to an MBA framework (e.g., capital allocation, Porter's Five Forces, corporate governance, organizational incentives, supply chain resilience).
    3. "tags": A JSON array of 1 or 2 tags chosen ONLY from this exact list: {json.dumps(ALLOWED_TAGS)}.
    """
    raw_json = generate_with_fallback(prompt)
    data = json.loads(raw_json)
    valid_tags = [t for t in data.get("tags", []) if t in ALLOWED_TAGS]
    if not valid_tags:
        valid_tags = ["Strategy & Macro"]
    return {
        "summary": data.get("summary", abstract or title),
        "mba_takeaway": data.get("mba_takeaway", "Relevant to core MBA strategic and market analysis."),
        "tags": valid_tags[:2],
    }

def main():
    today_str = date.today().isoformat()
    print(f"Starting The Daily Brief ingestion for {today_str}...", flush=True)

    # Load all existing URLs in 1 query instead of 45 separate queries
    existing_rows = supabase.table("articles").select("url").execute().data or []
    existing_urls = {row["url"] for row in existing_rows}
    print(f"Loaded {len(existing_urls)} existing URLs from database.", flush=True)

    processed_urls = set()
    saved_count = 0

    for source, feed_url in RSS_FEEDS:
        print(f"Fetching RSS: {source} ({feed_url})", flush=True)
        try:
            feed = fetch_feed_safely(feed_url)
        except Exception as feed_err:
            print(f"  [Feed Skip] {feed_url} -> {feed_err}", flush=True)
            continue

        print(f"  -> Found {len(feed.entries)} entries", flush=True)

        for entry in feed.entries[:5]:
            url = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "").strip()
            abstract = getattr(entry, "summary", "").strip()

            if not url or not title or url in processed_urls or url in existing_urls:
                continue
            processed_urls.add(url)

            try:
                enriched = analyze_article(source, title, abstract)
                supabase.table("articles").insert({
                    "publish_date": today_str,
                    "source": source,
                    "title": title,
                    "url": url,
                    "summary": enriched["summary"],
                    "mba_takeaway": enriched["mba_takeaway"],
                    "tags": enriched["tags"],
                }).execute()
                existing_urls.add(url)
                saved_count += 1
                print(f"  [Saved #{saved_count}] {source}: {title[:55]}", flush=True)
                time.sleep(2)
            except Exception as err:
                print(f"  [Skip Article] {title[:40]} -> {err}", flush=True)
                time.sleep(2)

    print(f"Ingestion complete. Total new articles saved today: {saved_count}", flush=True)

if __name__ == "__main__":
    main()
