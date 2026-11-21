import os
import json
import time
import feedparser
from datetime import date
from google import genai
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"].strip()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
gemini = genai.Client(api_key=GEMINI_API_KEY)

# Active 2026 RSS feeds across FT, The Economist, and The New York Times
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
    response = gemini.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    data = json.loads(response.text)
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
    print(f"Starting The Daily Brief ingestion for {today_str}...")
    processed_urls = set()
    saved_count = 0

    for source, feed_url in RSS_FEEDS:
        print(f"Fetching RSS: {source} ({feed_url})")
        feed = feedparser.parse(feed_url)
        print(f"  -> Found {len(feed.entries)} entries in feed")

        # Pull top 5 entries per feed (9 feeds x 5 = up to 45 articles/day)
        for entry in feed.entries[:5]:
            url = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "").strip()
            abstract = getattr(entry, "summary", "").strip()

            if not url or not title or url in processed_urls:
                continue
            processed_urls.add(url)

            existing = supabase.table("articles").select("id").eq("url", url).execute()
            if existing.data:
                print(f"  [Skip - Exists] {title[:55]}")
                continue

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
                saved_count += 1
                print(f"  [Saved #{saved_count}] {source}: {title[:55]}")
                time.sleep(4.5)  # Stays safely under Gemini free-tier RPM limits
            except Exception as err:
                print(f"  [Error] {title[:40]} -> {err}")
                time.sleep(5)

    print(f"Ingestion complete. Total new articles saved today: {saved_count}")

if __name__ == "__main__":
    main()
