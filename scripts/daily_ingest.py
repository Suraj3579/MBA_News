import os
import json
import time
import feedparser
from datetime import date
from google import genai
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
gemini = genai.Client(api_key=GEMINI_API_KEY)

RSS_FEEDS = [
    ("Financial Times", "https://www.ft.com/rss/home"),
    ("Financial Times", "https://www.ft.com/companies?format=rss"),
    ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml"),
    ("The Economist", "https://www.economist.com/business/rss.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
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
    You are an executive briefing editor for Cambridge Judge Business School (CJBS) MBA students.
    Analyze this news story from {source}:
    Headline: {title}
    Abstract: {abstract}

    Respond strictly with valid JSON containing three keys:
    1. "summary": A crisp, objective 2-sentence executive summary of the story.
    2. "mba_takeaway": One sharp sentence linking this event to an MBA concept (e.g., capital structure, Porter's Five Forces, corporate governance, organizational incentives, supply chain resilience).
    3. "tags": A JSON array of 1 or 2 tags chosen ONLY from this exact list: {json.dumps(ALLOWED_TAGS)}.
    """
    response = gemini.models.generate_content(
        model="gemini-2.5-flash",
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
    print(f"Starting CJBS Daily Briefing ingestion for {today_str}...")
    processed_urls = set()

    for source, feed_url in RSS_FEEDS:
        print(f"Fetching RSS: {source} ({feed_url})")
        feed = feedparser.parse(feed_url)
        # Process top 6 stories per feed (~36 articles total per day)
        for entry in feed.entries[:6]:
            url = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "").strip()
            abstract = getattr(entry, "summary", "").strip()

            if not url or not title or url in processed_urls:
                continue
            processed_urls.add(url)

            # Skip if already stored in Supabase
            existing = supabase.table("articles").select("id").eq("url", url).execute()
            if existing.data:
                print(f"  [Skip - Exists] {title[:60]}")
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
                print(f"  [Saved] {source}: {title[:60]}")
                time.sleep(2)  # Respect free-tier rate limits
            except Exception as err:
                print(f"  [Error] {title[:40]} -> {err}")

    print("Daily ingestion complete.")

if __name__ == "__main__":
    main()
