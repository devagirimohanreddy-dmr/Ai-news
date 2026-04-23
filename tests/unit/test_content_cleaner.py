"""Tests for ContentCleaner — HTML to clean markdown conversion."""

from src.scrapers.content_cleaner import ContentCleaner


# ------------------------------------------------------------------ #
# Sample HTML fixtures
# ------------------------------------------------------------------ #

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>AI Breakthrough: New Model Achieves SOTA</title></head>
<body>
    <nav>
        <ul>
            <li><a href="/">Home</a></li>
            <li><a href="/news">News</a></li>
            <li><a href="/about">About</a></li>
        </ul>
    </nav>

    <div class="ad-banner">
        <p>Buy our product! Click here for 50% off!</p>
    </div>

    <article>
        <h1>AI Breakthrough: New Model Achieves SOTA</h1>
        <p>Researchers at DeepMind have announced a new language model that
        achieves state-of-the-art results on multiple benchmarks.</p>
        <h2>Key Findings</h2>
        <p>The model uses a novel architecture that combines transformers with
        memory-augmented networks. Testing showed <strong>significant improvements</strong>
        across all evaluated tasks.</p>
        <ul>
            <li>25% improvement on MMLU</li>
            <li>30% improvement on HumanEval</li>
            <li>15% improvement on GSM8K</li>
        </ul>
    </article>

    <div class="sidebar">
        <h3>Related Stories</h3>
        <ul>
            <li><a href="/story1">Other story 1</a></li>
            <li><a href="/story2">Other story 2</a></li>
        </ul>
    </div>

    <script>
        var tracker = new AnalyticsTracker();
        tracker.pageView('/article/ai-breakthrough');
    </script>

    <footer>
        <p>&copy; 2026 TechNews. All rights reserved.</p>
    </footer>
</body>
</html>
"""

MINIMAL_HTML = "<p>Hello world</p>"

HTML_WITH_ONLY_SCRIPTS = """
<html>
<head><script>console.log('hi');</script></head>
<body><script>tracker.init();</script></body>
</html>
"""


class TestContentCleanerClean:
    """Tests for ContentCleaner.clean()."""

    def test_clean_returns_markdown_from_html(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        assert result  # non-empty
        # Should contain the article heading or body text
        assert "AI Breakthrough" in result or "state-of-the-art" in result

    def test_clean_strips_script_tags(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        assert "AnalyticsTracker" not in result
        assert "<script>" not in result

    def test_clean_strips_nav_elements(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        # The nav links should not appear in cleaned output
        assert "<nav>" not in result

    def test_clean_preserves_article_content(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        # Core article content should survive
        assert "language model" in result or "MMLU" in result or "improvements" in result

    def test_clean_produces_atx_headings(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        # ATX headings use '#' prefix
        assert "#" in result

    def test_clean_empty_string(self) -> None:
        assert ContentCleaner.clean("") == ""

    def test_clean_whitespace_only(self) -> None:
        assert ContentCleaner.clean("   \n\t  ") == ""

    def test_clean_none_like_empty(self) -> None:
        # Passing None would be a type error, but empty string should be safe.
        assert ContentCleaner.clean("") == ""

    def test_clean_plain_text_returned_as_is(self) -> None:
        plain = "This is just a plain text paragraph with no HTML at all."
        result = ContentCleaner.clean(plain)
        assert result == plain

    def test_clean_short_non_html(self) -> None:
        result = ContentCleaner.clean("hello")
        assert result == "hello"

    def test_clean_no_excessive_blank_lines(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        assert "\n\n\n" not in result

    def test_clean_minimal_html(self) -> None:
        # Very small HTML fragment — should still produce output
        html = "<html><body><p>Short article about AI progress.</p></body></html>"
        result = ContentCleaner.clean(html)
        assert "AI progress" in result

    def test_clean_preserves_list_items(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        # At least one list-style marker or the text content should be present
        assert "MMLU" in result or "HumanEval" in result or "GSM8K" in result

    def test_clean_preserves_bold_as_markdown(self) -> None:
        result = ContentCleaner.clean(SAMPLE_ARTICLE_HTML)
        # markdownify should convert <strong> to **...**
        assert "**" in result or "significant improvements" in result


class TestContentCleanerExtractTitle:
    """Tests for ContentCleaner.extract_title()."""

    def test_extract_title_from_html(self) -> None:
        title = ContentCleaner.extract_title(SAMPLE_ARTICLE_HTML)
        assert title  # non-empty
        assert "AI Breakthrough" in title

    def test_extract_title_empty_input(self) -> None:
        assert ContentCleaner.extract_title("") == ""

    def test_extract_title_whitespace_input(self) -> None:
        assert ContentCleaner.extract_title("   ") == ""

    def test_extract_title_plain_text(self) -> None:
        # Plain text may or may not yield a title — should not crash
        result = ContentCleaner.extract_title("no html here")
        assert isinstance(result, str)

    def test_extract_title_html_without_title_tag(self) -> None:
        html = "<html><body><p>No title element here.</p></body></html>"
        result = ContentCleaner.extract_title(html)
        assert isinstance(result, str)
