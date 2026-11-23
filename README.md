The Daily Brief

Less noise. More insight.

The Daily Brief is a minimalist, executive-grade daily news briefing platform built for MBA cohorts and business leaders. It curates high-signal stories from Financial Times, The Economist, The New York Times, Harvard Business Review, and Bloomberg, pairing concise 2-sentence executive summaries with sharp MBA/strategic takeaways and a 10-tag executive taxonomy.

✨ Key Features

Open-Access Daily Edition: Anyone can browse today's edition, filter by publication or topic, search across insights, and read full source articles without logging in.

Google OAuth Workspace: Signing in with Google unlocks personal features while keeping user data isolated via PostgreSQL Row Level Security (RLS):

Save / Bookmark (★): Curate a personal reading list of must-read stories.

Autosaving Study Notes: Jot down frameworks, class discussion points, or interview prep notes directly on any article card (onblur autosave).

Personalized "My Feed": Filter the daily briefing down to your preferred executive topics.

10-Tag Executive & MBA Taxonomy:

Macro & Geopolitics

Capital Markets & M&A

Corporate Strategy

AI & Digital Transformation

Leadership & Governance

Operations & Supply Chain

Energy & Climate

Consumer & Retail

Healthcare & Biotech

Entrepreneurship & VC

Resilient Hybrid Ingestion Pipeline: Supports both fully automated GitHub Actions ingestion (with multi-model Gemini $\rightarrow$ OpenAI $\rightarrow$ rule-based keyword fallback) and a zero-API-credit local RSS fetcher (fetch_feed.py / sync_brief.py) for manual SQL curation.

🏗️ System Architecture

┌─────────────────────────────────────────────────────────────────────────┐
│                         DATA INGESTION LAYER                            │
│  22 RSS Feeds (FT, Economist, NYT, HBR, Bloomberg)                      │
│   ├── Automated: GitHub Actions (cron 06:00 UTC) -> daily_ingest.py     │
│   │     └── Fallback Chain: Gemini Flash -> GPT-4o-mini -> Rule Engine  │
│   └── Zero-Credit Manual: fetch_feed.py / sync_brief.py -> SQL Upsert   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ HTTPS / PostgREST (Service Role)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         SUPABASE (PostgreSQL + Auth)                    │
│  • public.articles (Public READ, UNIQUE on url)                         │
│  • public.profiles (Private RLS, preferred_tags array)                  │
│  • public.user_article_interactions (Private RLS: likes & notes)        │
│  • Google OAuth (Implicit flow, zero-maintenance session persistence)   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ HTTPS / Supabase JS v2 (Anon Key)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         STATIC FRONTEND (Vercel)                        │
│  Single-file index.html (Tailwind CSS + Newsreader & Plus Jakarta Sans) │
└─────────────────────────────────────────────────────────────────────────┘


📁 Repository Structure

the-daily-brief/
├── index.html                        # Complete static frontend (UI, OAuth, filters, notes)
├── README.md                         # Project documentation
├── project_overview.html             # Printable executive overview (Save as PDF)
├── requirements.txt                  # Python dependencies for ingestion
├── scripts/
│   ├── daily_ingest.py               # Automated multi-LLM + rule-based fallback ingestion
│   ├── fetch_feed.py                 # Standalone local RSS fetcher (0 API keys needed)
│   └── sync_brief.py                 # Deduplicated local RSS exporter against Supabase
└── .github/
    └── workflows/
        └── daily_briefing.yml        # Scheduled daily GitHub Actions workflow


🚀 Setup & Deployment Guide

1. Supabase Database & Auth Setup

Create a new project in Supabase.

In the SQL Editor, create the core tables and security policies:

articles: id (uuid), publish_date (date), source (text), title (text), url (text UNIQUE), summary (text), mba_takeaway (text), tags (text[]), created_at (timestamptz). Enable public SELECT for anon and authenticated.

profiles: id (uuid references auth.users), email (text), full_name (text), preferred_tags (text[]). Enable RLS (auth.uid() = id).

user_article_interactions: user_id (uuid), article_id (uuid), is_favourite (boolean), personal_note (text), updated_at (timestamptz), primary key (user_id, article_id). Enable RLS (auth.uid() = user_id).

Configure Authentication $\rightarrow$ Providers $\rightarrow$ Google:

Create OAuth 2.0 credentials in Google Cloud Console (set Publishing Status to In Production so there is no 100-user cap).

Add your Supabase callback URL (https://<PROJECT_ID>.supabase.co/auth/v1/callback) to Google Cloud Authorized Redirect URIs.

Add your Vercel production domain to Supabase Authentication $\rightarrow$ URL Configuration $\rightarrow$ Site URL & Redirect URLs.

2. Frontend Deployment (Vercel)

Open index.html and replace the two configuration constants with your Supabase credentials:

const SUPABASE_URL = "https://YOUR_PROJECT_ID.supabase.co";
const SUPABASE_ANON_KEY = "YOUR_SUPABASE_ANON_PUBLIC_KEY";


Push the repository to GitHub and import it into Vercel as a static site (no build step required).

3. Daily Content Ingestion

Option A: Automated Daily Cron (GitHub Actions)

Add the following repository secrets in GitHub $\rightarrow$ Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions:

SUPABASE_URL (required)

SUPABASE_SERVICE_KEY (required — service role key)

GEMINI_API_KEY (optional — free Google AI Studio key)

OPENAI_API_KEY (optional — fallback key)

The workflow in .github/workflows/daily_briefing.yml runs automatically at 06:00 UTC daily (or on-demand via Run workflow). If AI credits are exhausted, it automatically falls back to keyword-based tagging so daily publishing never fails.

Option B: Zero-API-Credit Local Curation

Install Python dependencies locally:

pip install feedparser supabase


Run python scripts/sync_brief.py (or python scripts/fetch_feed.py) to fetch all 22 RSS feeds and filter out URLs already stored in Supabase.

Paste the generated candidate list into your AI assistant to produce the deduplicated INSERT INTO public.articles ... ON CONFLICT (url) DO NOTHING; SQL, and execute it in the Supabase SQL Editor.

📊 Capacity & Cost Profile (Free Tier)

Component

Free Tier Allowance

Estimated Usage

Runway

Supabase Database

500 MB storage

~2.4 MB / month (~40 articles/day)

15+ years

Supabase Auth

50,000 MAUs

~100–500 cohort readers

Ample headroom

Vercel Hosting

100 GB bandwidth/mo

~25 KB static index.html

Hundreds of thousands of visits

Google OAuth

Unlimited users

Production OAuth consent screen

Unlimited
