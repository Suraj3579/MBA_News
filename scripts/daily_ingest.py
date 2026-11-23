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

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

gemini = (
    genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": 20000})
    if GEMINI_API_KEY
    else None
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
    except ImportError:
        pass

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

TAG_KEYWORDS = {
    "Macro & Geopolitics": ["fed", "ecb", "central bank", "inflation", "gdp", "tariff", "trade", "sanction", "election", "treasury", "rates", "economy", "geopolitic", "china", "war"],
    "Capital Markets & M&A": ["merger", "acquisition", "m&a", "ipo", "stock", "bond", "private equity", "hedge fund", "bank", "valuation", "earnings", "investor", "market", "debt", "credit"],
    "Corporate Strategy": ["strategy", "rival", "competitor", "restructuring", "turnaround", "pricing", "growth", "profit", "revenue", "dividend", "expansion"],
    "AI & Digital Transformation": ["ai", "artificial intelligence", "openai", "nvidia", "chip", "semiconductor", "cloud", "software", "cyber", "tech", "data", "algorithm", "robot"],
    "Leadership & Governance": ["ceo", "board", "executive", "shareholder", "governance", "culture", "labor", "union", "workforce", "talent", "layoff", "regulator", "antitrust"],
    "Operations & Supply Chain": ["supply chain", "manufacturing", "factory", "shipping", "logistics", "port", "inventory", "procurement", "auto", "airline"],
    "Energy & Climate": ["oil", "gas", "energy", "renewable", "solar", "wind", "climate", "carbon", "esg", "ev", "battery", "nuclear", "grid", "mining"],
    "Consumer & Retail": ["retail", "consumer", "luxury", "brand", "store", "e-commerce", "amazon", "walmart", "food", "beverage", "apparel"],
    "Healthcare & Biotech": ["pharma", "drug", "biotech", "health", "hospital", "fda", "vaccine", "obesity", "medical", "clinical"],
    "Entrepreneurship & VC": ["startup", "venture capital", "vc", "founder", "seed", "series a", "series b", "unicorn", "pitch"],
}

MAX_ARTICLES_PER_DAY = 40


def clean_html_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if "nytimes.com" in url or "economist.com" in url:
        return url.split("?")[0]
    return url


def fallback_rule_based_enrichment(source: str, title: str, abstract: str) -> dict:
    cleaned_abstract = clean_html_text(abstract) or title
    combined = f"{title} {cleaned_abstract}".lower()

    matched_tags = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", combined) for kw in keywords):
            matched_tags.append(tag)

    if not matched_tags:
        matched_tags = ["Corporate Strategy"]

    return {
        "summary": cleaned_abstract,
        "mba_takeaway": f"Key {matched_tags[0].lower()} development tracked from {source}.",
        "tags": matched_tags[:3],
    }


def fetch_feed_safely(feed_url: str):
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml_bytes = resp.read()
    return feedparser.parse(xml_bytes)


def generate_with_fallback(prompt: str) -> str:
    last_error = RuntimeError("No AI API keys configured")

    if gemini:
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
                err_msg = str(err)
                if "429" in err_msg or "503" in err_msg:
                    print(f"    [Fallback] {model_name} busy/rate-limited, switching...", flush=True)
                    time.sleep(1.5)
                else:
                    print(f"    [Fallback] {model_name} unavailable ({type(err).__name__}), switching...", flush=True)

    if openai_client:
        try:
            print("    [Provider Fallback] Switching to OpenAI gpt-4o-mini...", flush=True)
            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            return completion.choices[0].message.content
        except Exception as openai_err:
            last_error = openai_err

    raise last_error


def analyze_article(source: str, title: str, abstract: str) -> dict:
    prompt = f"""
    You are an executive briefing editor for MBA students and business leaders.
    Analyze this news story from {source}:
    Headline: {title}
    Abstract: {abstract}

    Respond strictly with valid JSON containing three keys:
    1. "summary": A crisp, objective 2-sentence executive summary of the story.
    2. "mba_takeaway": One sharp sentence linking this event to an executive/MBA framework (e.g., capital allocation, Porter's Five Forces, corporate governance, pricing power, supply chain resilience).
    3. "tags": A JSON array of 2 or 3 tags chosen ONLY from this exact list: {json.dumps(ALLOWED_TAGS)}.
    """
    try:
        raw_json = generate_with_fallback(prompt)
        data = json.loads(raw_json)
        valid_tags = [t for t in data.get("tags", []) if t in ALLOWED_TAGS]
        if not valid_tags:
            valid_tags = ["Corporate Strategy"]
        return {
            "summary": data.get("summary", clean_html_text(abstract) or title),
            "mba_takeaway": data.get("mba_takeaway", "Relevant to core strategic and market analysis."),
            "tags": valid_tags[:3],
        }
    except Exception as ai_err:
        print(f"    [Zero-Credit Mode] AI unavailable ({type(ai_err).__name__}), using rule-based tagging.", flush=True)
        return fallback_rule_based_enrichment(source, title, abstract)


def main():
    today_str = date.today().isoformat()
    print(f"Starting The Daily Brief automated ingestion for {today_str}...", flush=True)

    # 1. Load all previously stored URLs & titles from Supabase to block duplicates
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
            url = normalize_url(getattr(entry, "link", ""))
            title = clean_html_text(getattr(entry, "title", ""))
            abstract = getattr(entry, "summary", "").strip()
            title_key = title.lower()

            if not url or not title or url in existing_urls or title_key in existing_titles:
                continue

            existing_urls.add(url)
            existing_titles.add(title_key)
            candidates.append((source, title, url, abstract))

    print(f"Collected {len(candidates)} brand-new candidate articles. Processing up to {MAX_ARTICLES_PER_DAY}...", flush=True)

    rows_to_insert = []
    for source, title, url, abstract in candidates[:MAX_ARTICLES_PER_DAY]:
        enriched = analyze_article(source, title, abstract)
        rows_to_insert.append({
            "publish_date": today_str,
            "source": source,
            "title": title,
            "url": url,
            "summary": enriched["summary"],
            "mba_takeaway": enriched["mba_takeaway"],
            "tags": enriched["tags"],
        })
        print(f"  [Prepared #{len(rows_to_insert)}] {source}: {title[:55]}", flush=True)
        time.sleep(0.5)

    # 2. Bulk upsert into Supabase (equivalent to INSERT ... ON CONFLICT (url) DO NOTHING)
    if rows_to_insert:
        supabase.table("articles").upsert(
            rows_to_insert,
            on_conflict="url",
            ignore_duplicates=True,
        ).execute()
        print(f"Ingestion complete. Saved {len(rows_to_insert)} new articles to Supabase.", flush=True)
    else:
        print("Ingestion complete. No new unique articles to insert today.", flush=True)


if __name__ == "__main__":
    main()
