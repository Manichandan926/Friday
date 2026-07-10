"""Tests for the web layer (app/core/web.py) and its tools.

No real network is ever touched: `web._run` (the curl wrapper) is monkeypatched
to a spy that records argv and returns canned output, and `web._which` is
patched to control whether curl "exists". `socket.getaddrinfo` is stubbed so
the SSRF guard can be exercised against loopback/private/public hosts without
DNS. The parsing/stripping helpers are pure functions, tested directly.

Adversarial focus: prove fetch_url REFUSES what it should (non-http schemes,
localhost, private/metadata IPs) before any network call, and that external
content comes back visibly fenced as untrusted.
"""
import socket

import pytest

from app.core import tiers, toolkit, web
from app.core.tiers import Tier


def _resolve_to(monkeypatch, ip: str):
    """Make every getaddrinfo() resolve to a single fixed IP."""
    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port or 0))]
    monkeypatch.setattr(web.socket, "getaddrinfo", fake)


@pytest.fixture
def spy_run(monkeypatch):
    """Record argv passed to _run; return a canned (ok, output)."""
    calls = []

    def fake_run(argv, timeout=web._TIMEOUT):
        calls.append(list(argv))
        return fake_run.result

    fake_run.result = (True, "")
    fake_run.calls = calls
    monkeypatch.setattr(web, "_run", fake_run)
    return fake_run


def _curl(monkeypatch, present=True):
    monkeypatch.setattr(web, "_which", lambda n: "/usr/bin/curl" if present else None)


# ── SSRF guard / URL validation ───────────────────────────

class TestValidateUrl:
    def test_public_host_is_allowed(self, monkeypatch):
        _resolve_to(monkeypatch, "93.184.216.34")  # example.com, public
        clean, reason = web._validate_url("http://example.com/page")
        assert reason is None
        assert clean == "http://example.com/page"

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "javascript:alert(1)",
    ])
    def test_non_http_schemes_refused(self, url):
        clean, reason = web._validate_url(url)
        assert clean is None
        assert "http/https" in reason

    @pytest.mark.parametrize("ip", [
        "127.0.0.1",       # loopback
        "10.0.0.5",        # private
        "192.168.1.1",     # private
        "169.254.169.254", # link-local / cloud metadata
        "0.0.0.0",         # unspecified
    ])
    def test_non_public_addresses_refused(self, monkeypatch, ip):
        _resolve_to(monkeypatch, ip)
        clean, reason = web._validate_url(f"http://sneaky.example/")
        assert clean is None
        assert "non-public" in reason

    def test_embedded_credentials_are_stripped(self, monkeypatch):
        _resolve_to(monkeypatch, "93.184.216.34")
        clean, reason = web._validate_url("https://user:secret@example.com/x")
        assert reason is None
        assert "secret" not in clean and "user" not in clean
        assert clean == "https://example.com/x"


# ── fetch_url (refusals happen before any curl call) ──────

class TestFetchUrl:
    def test_localhost_refused_without_network(self, monkeypatch, spy_run):
        _curl(monkeypatch)
        _resolve_to(monkeypatch, "127.0.0.1")
        out = web.fetch_url("http://localhost:8080/socket")
        assert "Refused" in out
        assert spy_run.calls == []  # never reached curl

    def test_file_scheme_refused_without_network(self, monkeypatch, spy_run):
        _curl(monkeypatch)
        out = web.fetch_url("file:///etc/shadow")
        assert "Refused" in out
        assert spy_run.calls == []

    def test_missing_curl_degrades_gracefully(self, monkeypatch):
        _curl(monkeypatch, present=False)
        out = web.fetch_url("http://example.com")
        assert "curl is not installed" in out

    def test_fetched_content_is_fenced_as_untrusted(self, monkeypatch, spy_run):
        _curl(monkeypatch)
        _resolve_to(monkeypatch, "93.184.216.34")
        spy_run.result = (True, "<html><body><p>hello world</p></body></html>")
        out = web.fetch_url("http://example.com")
        assert "Untrusted external content" in out
        assert "hello world" in out

    def test_builds_a_bounded_curl_command(self, monkeypatch, spy_run):
        _curl(monkeypatch)
        _resolve_to(monkeypatch, "93.184.216.34")
        spy_run.result = (True, "<p>x</p>")
        web.fetch_url("http://example.com/a")
        argv = spy_run.calls[-1]
        assert argv[0] == "curl"
        assert "--max-time" in argv and "--max-filesize" in argv
        assert "--proto" in argv and "=http,https" in argv


# ── HTML → text ───────────────────────────────────────────

class TestHtmlToText:
    def test_strips_tags_and_script(self):
        html = "<html><head><title>T</title></head><body>" \
               "<script>evil()</script><p>Real text</p><style>x{}</style></body></html>"
        text = web._html_to_text(html)
        assert "Real text" in text
        assert "evil" not in text and "x{}" not in text

    def test_collapses_whitespace(self):
        assert web._html_to_text("<p>a</p>   <p>b\n\nc</p>") == "a b c"


# ── DuckDuckGo result parsing ─────────────────────────────

class TestDdg:
    def test_href_decodes_redirect(self):
        raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Fdocs&rut=abc"
        assert web._ddg_href(raw) == "https://python.org/docs"

    def test_href_passes_through_direct_links(self):
        assert web._ddg_href("https://python.org") == "https://python.org"

    def test_parser_extracts_title_url_snippet(self):
        html = (
            '<div class="result">'
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpy.org">'
            'Python Site</a>'
            '<a class="result__snippet">The official site.</a>'
            '</div>'
        )
        p = web._DDGParser()
        p.feed(html)
        assert p.results == [
            {"title": "Python Site", "url": "https://py.org", "snippet": "The official site."}
        ]

    def test_web_search_formats_hits(self, monkeypatch, spy_run):
        _curl(monkeypatch)
        spy_run.result = (True,
            '<a class="result__a" href="https://py.org">Py</a>'
            '<a class="result__snippet">snip</a>')
        out = web.web_search("python")
        assert "Py" in out and "https://py.org" in out and "snip" in out
        # search hits DDG's fixed endpoint, not an arbitrary host
        assert "html.duckduckgo.com" in spy_run.calls[-1][-1]

    def test_web_search_no_hits(self, monkeypatch, spy_run):
        _curl(monkeypatch)
        spy_run.result = (True, "<html><body>nothing</body></html>")
        assert "No web results" in web.web_search("zzz")


# ── tier wiring ───────────────────────────────────────────

class TestTiers:
    def test_web_tools_are_registered_and_auto(self):
        for name in ("web_search", "fetch_url"):
            assert name in toolkit._REGISTRY
            assert tiers.classify(name) is Tier.AUTO
