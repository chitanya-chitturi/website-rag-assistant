"""
config.py - every setting for the project lives here.

To point the bot at a different public website, change START_URLS and
ALLOWED_PREFIXES, then re-run:  python ingest.py --reset
"""

# ---------------------------------------------------------------------------
# 1. WHICH SITE TO READ
# ---------------------------------------------------------------------------
# Default example: GitLab's public company handbook. It reads like an internal
# Confluence space (time off, benefits, holidays, expenses, onboarding...),
# so it is a realistic stand-in for "company documents".
START_URLS = [
    "https://handbook.gitlab.com/handbook/people-group/",
    "https://handbook.gitlab.com/handbook/total-rewards/",
]

# The crawler only follows links that start with one of these prefixes.
# This keeps it inside the part of the site you care about.
ALLOWED_PREFIXES = [
    "https://handbook.gitlab.com/handbook/people-group/",
    "https://handbook.gitlab.com/handbook/total-rewards/",
]

MAX_PAGES = 150          # safety cap so a first run stays small and quick
REQUEST_DELAY_SEC = 1.0  # pause between requests - be polite to the site
USER_AGENT = "site-rag-learning-bot/1.0 (personal learning project)"

# ---------------------------------------------------------------------------
# 2. CHUNKING
# ---------------------------------------------------------------------------
# Pages are first split at their headings (h1-h3). A section longer than
# CHUNK_SIZE characters is split again, with CHUNK_OVERLAP characters shared
# between neighbouring pieces so a sentence cut at a boundary is not lost.
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
MIN_CHUNK_CHARS = 80     # drop tiny fragments ("Back to top", etc.)

# ---------------------------------------------------------------------------
# 3. VECTOR STORE
# ---------------------------------------------------------------------------
DB_DIR = "rag_store"          # folder Chroma writes its data into
COLLECTION_NAME = "site_docs"

# Embedding model: Chroma's built-in default (all-MiniLM-L6-v2, runs locally
# on your CPU, free, ~80 MB downloaded automatically the first time).

# ---------------------------------------------------------------------------
# 4. RETRIEVAL + ANSWERING
# ---------------------------------------------------------------------------
TOP_K = 5                 # how many chunks to hand to the LLM
MAX_DISTANCE = 0.9        # chunks further away than this are "not relevant"
                          # (cosine distance: 0 = identical, 2 = opposite).
                          # Tune it: run ask.py --show-chunks on good and bad
                          # questions and look at the distances you get.

LLM_MODEL = "claude-haiku-5-5"   # fast and cheap; try "claude-sonnet-5-5" too
LLM_MAX_TOKENS = 800
