"""seed default sources

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-15

Inserts the 22 default news sources that the system scrapes. Uses
``ON CONFLICT DO NOTHING`` keyed on ``id`` so the migration is idempotent
— safe to apply on fresh installs (seeds everything) and on existing
installs (skips rows that already exist).
"""
from alembic import op
from sqlalchemy import text

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


_SOURCES = [
    # (id, name, url, scraper_type, schedule_cron, priority, enabled, config_json, notify_to_teams)
    (1,  'TechCrunch',               'https://techcrunch.com/feed/',                    'rss',       '*/30 * * * *', 2, True,  'null',                                                                                                                                                  True),
    (2,  'The Verge',                'https://www.theverge.com/rss/index.xml',          'rss',       '*/30 * * * *', 2, True,  'null',                                                                                                                                                  True),
    (3,  'Ars Technica',             'https://feeds.arstechnica.com/arstechnica/index', 'rss',       '0 * * * *',    1, True,  'null',                                                                                                                                                  True),
    (4,  'Wired',                    'https://www.wired.com/feed/rss',                  'rss',       '0 * * * *',    1, True,  'null',                                                                                                                                                  True),
    (5,  'MIT Technology Review',    'https://www.technologyreview.com/feed/',          'rss',       '0 */2 * * *',  2, True,  'null',                                                                                                                                                  True),
    (6,  'VentureBeat',              'https://venturebeat.com/feed/',                   'rss',       '*/30 * * * *', 2, True,  'null',                                                                                                                                                  True),
    (7,  'GitHub Trending AI',       'https://github.com/trending',                     'api',       '0 6 * * *',    2, True,  '{"type": "github", "trending_language": "python"}',                                                                                                     False),
    (8,  'Reddit r/MachineLearning', 'https://reddit.com/r/MachineLearning',            'api',       '0 */3 * * *',  2, True,  '{"type": "reddit", "subreddits": ["MachineLearning"]}',                                                                                                 False),
    (9,  'Reddit r/artificial',      'https://reddit.com/r/artificial',                 'api',       '0 */3 * * *',  1, True,  '{"type": "reddit", "subreddits": ["artificial"]}',                                                                                                      False),
    (10, 'Reddit r/technology',      'https://reddit.com/r/technology',                 'api',       '0 */2 * * *',  1, True,  '{"type": "reddit", "subreddits": ["technology"]}',                                                                                                      False),
    (11, 'Hacker News',              'https://news.ycombinator.com',                    'api',       '*/15 * * * *', 2, True,  '{"type": "hn", "limit": 30, "story_type": "top"}',                                                                                                      True),
    (12, 'arXiv AI/ML',              'https://arxiv.org',                               'api',       '0 */12 * * *', 2, True,  '{"type": "arxiv", "categories": ["cs.AI", "cs.LG", "cs.CL"]}',                                                                                          False),
    (13, 'OpenAI Blog',              'https://openai.com/blog',                         'firecrawl', '0 8 * * *',    3, False, '{"urls": ["https://openai.com/blog"]}',                                                                                                                 False),
    (14, 'Anthropic Blog',           'https://www.anthropic.com/news',                  'firecrawl', '0 8 * * *',    3, False, '{"urls": ["https://www.anthropic.com/news"]}',                                                                                                          False),
    (15, 'Google DeepMind Blog',     'https://deepmind.google/discover/blog/',          'firecrawl', '0 8 * * *',    3, False, '{"urls": ["https://deepmind.google/discover/blog/"]}',                                                                                                  False),
    (16, 'Meta AI Blog',             'https://ai.meta.com/blog/',                       'firecrawl', '0 8 * * *',    3, False, '{"urls": ["https://ai.meta.com/blog/"]}',                                                                                                               False),
    (17, 'Twitter AI News',          'https://twitter.com/search?q=AI',                 'twitter',   '0 * * * *',    2, True,  '{"search_queries": ["AI breakthrough", "new AI model", "LLM release", "GPT", "Claude AI", "open source AI"]}',                                         False),
    (18, 'YouTube AI Channels',      'https://youtube.com',                             'youtube',   '0 6 * * *',    1, True,  '{"channel_ids": ["UCbfYPyITQ-7l4upoX8nvctg", "UCZHmQk67mSJgfCCTn7xBfew", "UCNJ1Ymd5yFuUPtn21xtRbbw"]}',                                                False),
    (19, 'NewsAPI AI/Tech',          'https://newsapi.org',                             'newsapi',   '0 */2 * * *',  2, True,  '{"queries": ["artificial intelligence", "machine learning", "AI startup"]}',                                                                            False),
    (20, 'Telegram AI Channels',     'https://t.me',                                    'telegram',  '0 */4 * * *',  1, True,  '{"channels": ["ai_newz", "DeepLearning_daily"]}',                                                                                                       False),
    (21, 'LinkedIn AI Companies',    'https://linkedin.com',                            'linkedin',  '0 6 * * *',    2, True,  '{"company_urls": ["https://www.linkedin.com/company/openai", "https://www.linkedin.com/company/anthropic"]}',                                           False),
    (24, 'open AI blog',             'https://openai.com/news/rss.xml',                 'rss',       '0 8 * * *',    1, True,  'null',                                                                                                                                                  False),
]


def upgrade() -> None:
    """Seed the 22 default sources. Idempotent — re-running is safe."""
    bind = op.get_bind()
    stmt = text(
        "INSERT INTO sources (id, name, url, scraper_type, schedule_cron, "
        "priority, enabled, error_count, config_json, notify_to_teams, "
        "created_at, updated_at) "
        "VALUES (:id, :name, :url, :stype, :cron, :priority, :enabled, 0, "
        "CAST(:cfg AS jsonb), :notify, NOW(), NOW()) "
        "ON CONFLICT (id) DO NOTHING"
    )
    for (sid, name, url, stype, cron, priority, enabled, cfg, notify) in _SOURCES:
        bind.execute(stmt, {
            "id": sid, "name": name, "url": url, "stype": stype,
            "cron": cron, "priority": priority, "enabled": enabled,
            "cfg": cfg, "notify": notify,
        })
    # Make sure the sequence is past our highest seeded ID so future inserts
    # don't collide.
    bind.execute(text(
        "SELECT setval('sources_id_seq', "
        "GREATEST((SELECT COALESCE(MAX(id), 0) FROM sources), 24))"
    ))


def downgrade() -> None:
    """Remove the seeded sources by ID range."""
    op.execute(
        "DELETE FROM sources WHERE id IN "
        "(1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,24)"
    )
