"""Tests for full-text extraction via trafilatura."""

from unittest.mock import patch

from remarkable_morning.config import Article
from remarkable_morning.extract import extract_all, extract_text

# A realistic HTML page snippet that trafilatura can extract meaningful text from.
SAMPLE_HTML = """
<html>
<head><title>Test Article</title></head>
<body>
<nav>Home About Contact</nav>
<article>
<h1>Major World Event Unfolds Today</h1>
<p>A significant development occurred today in world affairs, according to
multiple sources familiar with the matter. The event, which had been
anticipated for several weeks, finally came to pass in the early morning
hours.</p>
<p>Officials confirmed that the situation is developing rapidly and that
further statements would be issued throughout the day. Analysts noted
that this represents a turning point in the ongoing situation.</p>
<p>Local residents reported seeing unusual activity beginning overnight,
though the full scope of the event only became clear at dawn. International
observers are expected to arrive within the week.</p>
<p>The economic impact is still being assessed, but early indicators suggest
moderate effects on regional markets. Several companies have already
announced contingency plans.</p>
</article>
<footer>Copyright 2026. All rights reserved.</footer>
</body>
</html>
"""


def test_extract_text_from_raw_html():
    article = Article(
        id="test-1",
        title="Test Article",
        url="http://example.com/article",
        raw_html=SAMPLE_HTML,
    )
    extract_text(article)
    assert article.full_text
    assert "Major World Event" in article.full_text
    # Should NOT contain nav or footer boilerplate
    assert "Home About Contact" not in article.full_text


def test_extract_text_preserves_guardian_articles():
    article = Article(
        id="guardian-1",
        title="Guardian Article",
        url="http://theguardian.com/article",
        raw_html="",
        full_text="Already extracted by Guardian API.",
    )
    extract_text(article)
    assert article.full_text == "Already extracted by Guardian API."


def test_extract_text_empty_raw_html_no_url():
    article = Article(
        id="test-2",
        title="Empty",
        url="",
        raw_html="",
    )
    extract_text(article)
    assert article.full_text == ""


def test_extract_text_fallback_to_fetch_url():
    article = Article(
        id="test-3",
        title="Short Summary",
        url="http://example.com/full-article",
        raw_html="<p>Too short.</p>",  # < 20 words, triggers fallback
    )
    # _extract is called twice: first on raw_html (short), then on fetched HTML (long)
    long_text = " ".join(f"word{i}" for i in range(50))
    with patch(
        "remarkable_morning.extract._extract", side_effect=["Too short.", long_text]
    ), patch(
        "remarkable_morning.extract._fetch_url", return_value="downloaded"
    ):
        extract_text(article)
    assert article.full_text == long_text
    assert len(article.full_text.split()) >= 20


def test_extract_text_fetch_url_failure_is_non_fatal():
    article = Article(
        id="test-4",
        title="Short",
        url="http://example.com/broken",
        raw_html="<p>Short.</p>",
    )
    with patch(
        "remarkable_morning.extract._fetch_url", side_effect=Exception("Network error")
    ):
        extract_text(article)
    # Should not raise; full_text stays empty
    assert article.full_text == ""


def test_extract_all_processes_list():
    articles = [
        Article(id="a1", title="A1", url="http://ex.com/1", raw_html=SAMPLE_HTML),
        Article(id="a2", title="A2", url="", raw_html="", full_text="Already done."),
        Article(id="a3", title="A3", url="", raw_html=""),
    ]
    results = extract_all(articles)
    assert len(results) == 3
    assert results[0].full_text  # extracted
    assert results[1].full_text == "Already done."  # preserved
    assert results[2].full_text == ""  # nothing to extract, stays empty


# ---------------------------------------------------------------------------
# Gibberish detection tests
# ---------------------------------------------------------------------------

def test_gibberish_detector_rejects_scrambled_text():
    """Sifted-style anti-scraping: clean first sentence + scrambled rest."""
    text = (
        "UK digital bank Monzo is reportedly in talks to raise between £300m and £500m. "
        "Tkdnada qepz Ygx Jsfk bg Ocgyyw fxgn EQ mlehrdcval dnwvnlj GA Jqpy. "
        "Pj wix whzpezuoe qewaetfq nj ydzwfod oft nxygw. Cwv gdvml vr rvf acli nav."
    )
    from remarkable_morning.extract import _is_clean_text
    assert _is_clean_text(text) is False


def test_gibberish_detector_accepts_clean_english():
    from remarkable_morning.extract import _is_clean_text
    text = (
        "Ukraine peace talks resume with some flexibility by warring parties. "
        "Diplomats say both sides have shown willingness to compromise on key territorial disputes. "
        "The negotiations, mediated by Turkey, entered their third round on Tuesday."
    )
    assert _is_clean_text(text) is True


def test_gibberish_detector_accepts_clean_german():
    from remarkable_morning.extract import _is_clean_text
    text = (
        "Die Europäische Zentralbank hat die Leitzinsen erneut gesenzt. "
        "Die Entscheidung kam überraschend für viele Beobachter, die eine stabile "
        "Geldpolitik erwartet hatten. Präsidentin Lagarde betonte die Bedeutung."
    )
    assert _is_clean_text(text) is True


def test_gibberish_detector_accepts_clean_french():
    from remarkable_morning.extract import _is_clean_text
    text = (
        "La Banque centrale européenne a décidé de maintenir ses taux d'intérêt. "
        "Cette décision a surpris de nombreux analystes qui s'attendaient à une hausse. "
        "La présidente Lagarde a souligné que l'inflation restait sous contrôle."
    )
    assert _is_clean_text(text) is True


def test_gibberish_detector_skips_short_text():
    from remarkable_morning.extract import _is_clean_text
    assert _is_clean_text("Short text.") is True
    assert _is_clean_text("") is True


# ---------------------------------------------------------------------------
# Non-article detection tests
# ---------------------------------------------------------------------------

# Sample non-article texts that pass the gibberish detector (clean language)
# but should be rejected by _is_article_text because they are not article
# content. Each is >= 20 words so it is not caught by the word-count gate
# alone — the pattern match is what rejects them.
VIDEO_PLAYER_FR = (
    "Une extension de votre navigateur semble bloquer le chargement du "
    "lecteur vidéo. Veuillez désactiver votre bloqueur de publicités pour "
    "regarder cette vidéo."
)
VIDEO_PLAYER_EN = (
    "Your browser does not support the video player. Please enable "
    "JavaScript or install a compatible browser to view this content."
)
VIDEO_PLAYER_DE = (
    "Ihr Browser unterstützt keinen Videoplayer. Bitte aktivieren Sie "
    "JavaScript oder verwenden Sie einen kompatiblen Browser, um dieses "
    "Video auf dieser Seite anzusehen."
)
COOKIE_EN = (
    "We use cookies to improve your experience and analyze our traffic. By "
    "clicking accept all, you consent to our use of cookies on this site."
)
COOKIE_DE = (
    "Diese Website verwendet Cookies, um Ihnen das beste Nutzererlebnis zu "
    "bieten. Durch die weitere Nutzung stimmen Sie der Verwendung zu."
)
COOKIE_FR = (
    "Ce site utilise des cookies pour améliorer votre expérience de "
    "navigation. En continuant, vous acceptez l'utilisation des cookies "
    "sur ce site web."
)
PAYWALL_EN = (
    "Subscribe to continue reading this article. Already a subscriber? "
    "Sign in now to access the full content of this story today."
)
PAYWALL_DE = (
    "Dieser Artikel ist nur für Abonnenten. Abonnieren Sie jetzt, um den "
    "vollständigen Inhalt zu lesen und alle Features zu nutzen."
)
PAYWALL_FR = (
    "Cet article est réservé aux abonnés. Abonnez-vous pour continuer à lire "
    "le contenu complet et accéder à toutes les fonctionnalités."
)

# A legitimate short article — real journalism, >= 20 words, no patterns.
LEGIT_SHORT = (
    "Breaking: The central bank announced an emergency rate cut this morning "
    "following overnight market volatility, with the governor citing "
    "coordinated action among major economies to stabilize currency markets."
)


def test_is_article_text_rejects_video_player_fr():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(VIDEO_PLAYER_FR) is False


def test_is_article_text_rejects_video_player_en():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(VIDEO_PLAYER_EN) is False


def test_is_article_text_rejects_video_player_de():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(VIDEO_PLAYER_DE) is False


def test_is_article_text_rejects_cookie_en():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(COOKIE_EN) is False


def test_is_article_text_rejects_cookie_de():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(COOKIE_DE) is False


def test_is_article_text_rejects_cookie_fr():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(COOKIE_FR) is False


def test_is_article_text_rejects_paywall_en():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(PAYWALL_EN) is False


def test_is_article_text_rejects_paywall_de():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(PAYWALL_DE) is False


def test_is_article_text_rejects_paywall_fr():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(PAYWALL_FR) is False


def test_is_article_text_rejects_short_text():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text("This is too short to be an article.") is False
    assert _is_article_text("") is False


def test_is_article_text_accepts_legitimate_short_article():
    from remarkable_morning.extract import _is_article_text
    assert _is_article_text(LEGIT_SHORT) is True


def test_extract_text_rejects_video_only_article():
    """A video-only article whose extraction yields player text stays empty."""
    article = Article(
        id="fr24-1",
        title="Crise bancaire au Paraguay",
        url="",  # no fetch fallback — rejection leaves full_text empty
        raw_html="<html><body>video page</body></html>",
    )
    with patch("remarkable_morning.extract._extract", return_value=VIDEO_PLAYER_FR):
        extract_text(article)
    assert article.full_text == ""


def test_extract_text_rejects_cookie_notice():
    article = Article(
        id="cookie-1",
        title="Some article",
        url="",
        raw_html="<html><body>cookie page</body></html>",
    )
    with patch("remarkable_morning.extract._extract", return_value=COOKIE_EN):
        extract_text(article)
    assert article.full_text == ""


def test_extract_text_rejects_paywall():
    article = Article(
        id="paywall-1",
        title="Paywalled article",
        url="",
        raw_html="<html><body>paywall page</body></html>",
    )
    with patch("remarkable_morning.extract._extract", return_value=PAYWALL_EN):
        extract_text(article)
    assert article.full_text == ""


def test_extract_text_rejects_short_extraction():
    article = Article(
        id="short-1",
        title="Short",
        url="",  # no fallback — short text stays empty
        raw_html="<html><body>page</body></html>",
    )
    short_text = "This is far too short."  # < 20 words
    with patch("remarkable_morning.extract._extract", return_value=short_text):
        extract_text(article)
    assert article.full_text == ""


def test_extract_text_accepts_legitimate_short_article():
    article = Article(
        id="legit-1",
        title="Breaking news",
        url="",
        raw_html="<html><body>article page</body></html>",
    )
    with patch("remarkable_morning.extract._extract", return_value=LEGIT_SHORT):
        extract_text(article)
    assert article.full_text == LEGIT_SHORT
