"""Tests for manifold_cli — mocks all HTTP calls."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from manifold_cli import (
    ManifoldError,
    _parse_close_ms,
    api_request,
    do_bet,
    do_me,
    do_search,
    do_update,
    format_market,
    get_api_key,
    main,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_urlopen():
    """Patch urllib.request.urlopen to return controlled responses."""
    with patch("manifold_cli.urllib.request.urlopen") as m:
        yield m


def _make_response(data: dict | list, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── _parse_close_ms ───────────────────────────────────────────────────


class TestParseCloseMs:
    def test_valid_date(self):
        ms = _parse_close_ms("2026-06-01")
        assert isinstance(ms, int)
        assert ms > 0

    def test_roundtrip(self):
        from datetime import datetime

        ms = _parse_close_ms("2026-01-15")
        dt = datetime.fromtimestamp(ms // 1000)
        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 15


# ── get_api_key ───────────────────────────────────────────────────────


class TestGetApiKey:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "test-key-123")
        assert get_api_key() == "test-key-123"

    def test_empty_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("MANIFOLD_API_KEY", raising=False)
        with patch("manifold_cli.subprocess.run", side_effect=FileNotFoundError):
            assert get_api_key() == ""

    def test_keychain_fallback(self, monkeypatch):
        monkeypatch.delenv("MANIFOLD_API_KEY", raising=False)
        mock_result = MagicMock()
        mock_result.stdout = "keychain-key-456\n"
        with patch("manifold_cli.subprocess.run", return_value=mock_result):
            assert get_api_key() == "keychain-key-456"


# ── api_request ───────────────────────────────────────────────────────


class TestApiRequest:
    def test_get_no_auth(self, mock_urlopen):
        mock_urlopen.return_value = _make_response({"id": "abc"})
        result = api_request("GET", "/market/abc", auth=False)
        assert result == {"id": "abc"}
        req = mock_urlopen.call_args[0][0]
        assert "Authorization" not in req.headers

    def test_get_with_auth(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "my-key")
        mock_urlopen.return_value = _make_response({"name": "Max"})
        result = api_request("GET", "/me")
        assert result["name"] == "Max"
        req = mock_urlopen.call_args[0][0]
        assert req.headers["Authorization"] == "Key my-key"

    def test_post_sends_json_body(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({"success": True})
        api_request("POST", "/bet", data={"amount": 100})
        req = mock_urlopen.call_args[0][0]
        assert req.method == "POST"
        body = json.loads(req.data)
        assert body["amount"] == 100

    def test_http_error_raises(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        import urllib.error

        err = urllib.error.HTTPError(
            url="https://api.manifold.markets/v0/me",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=MagicMock(read=lambda: b"bad key"),
        )
        mock_urlopen.side_effect = err
        with pytest.raises(ManifoldError) as exc_info:
            api_request("GET", "/me")
        assert exc_info.value.status == 403

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("MANIFOLD_API_KEY", raising=False)
        with patch("manifold_cli.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(ManifoldError) as exc_info:
                api_request("GET", "/me", auth=True)
            assert exc_info.value.status == 401

    def test_explicit_api_key(self, mock_urlopen, monkeypatch):
        monkeypatch.delenv("MANIFOLD_API_KEY", raising=False)
        mock_urlopen.return_value = _make_response({"ok": True})
        api_request("GET", "/me", api_key="explicit-key")
        req = mock_urlopen.call_args[0][0]
        assert req.headers["Authorization"] == "Key explicit-key"


# ── format_market ─────────────────────────────────────────────────────


class TestFormatMarket:
    def test_basic(self):
        d = {
            "question": "Will X?",
            "id": "abc123",
            "url": "https://manifold.markets/u/abc",
            "probability": 0.72,
            "volume": 500,
            "totalLiquidity": 250,
            "uniqueBettorCount": 5,
            "closeTime": 1776276768000,
        }
        out = format_market(d)
        assert "Will X?" in out
        assert "abc123" in out
        assert "72%" in out
        assert "M$500" in out
        assert "M$250" in out
        assert "5" in out

    def test_no_probability(self):
        d = {
            "question": "Open Q",
            "id": "xyz",
            "closeTime": None,
        }
        out = format_market(d)
        assert "n/a" in out

    def test_no_close_time(self):
        d = {
            "question": "Q",
            "id": "id1",
            "closeTime": None,
        }
        out = format_market(d)
        assert "Close:     n/a" in out


# ── do_me ─────────────────────────────────────────────────────────────


class TestDoMe:
    def test_output(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response(
            {"name": "Max Ghenis", "username": "MaxGhenis", "balance": 19705.5}
        )
        out = do_me()
        assert "Max Ghenis" in out
        assert "@MaxGhenis" in out
        assert "M$19706" in out or "M$19705" in out


# ── do_search ─────────────────────────────────────────────────────────


class TestDoSearch:
    def test_formats_results(self, mock_urlopen):
        mock_urlopen.return_value = _make_response([
            {"id": "abc123456789", "probability": 0.65, "isResolved": False, "question": "Will eggs cost more?"},
            {"id": "def987654321", "probability": 0.30, "isResolved": True, "question": "Will milk go up?"},
        ])
        out = do_search("eggs")
        assert "abc123456789" in out
        assert "65%" in out
        assert "open" in out
        assert "RESOLVED" in out

    def test_empty_results(self, mock_urlopen):
        mock_urlopen.return_value = _make_response([])
        out = do_search("nonexistent query xyzzy")
        assert out == ""

    def test_no_probability(self, mock_urlopen):
        mock_urlopen.return_value = _make_response([
            {"id": "aaa", "isResolved": False, "question": "Q?"},
        ])
        out = do_search("q")
        assert "n/a" in out


# ── do_bet ────────────────────────────────────────────────────────────


class TestDoBet:
    def test_filled(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({
            "amount": 100,
            "shares": 130.5,
            "probAfter": 0.80,
        })
        out = do_bet("abc", "yes", 100)
        assert "130.5" in out
        assert "80%" in out

    def test_outcome_uppercased(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({"shares": 50, "amount": 50, "probAfter": 0.4})
        do_bet("abc", "no", 50)
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        assert body["outcome"] == "NO"

    def test_limit_prob(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({"shares": 10, "amount": 10, "probAfter": 0.5})
        do_bet("abc", "yes", 10, limit_prob=0.60)
        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["limitProb"] == 0.60


# ── do_update ─────────────────────────────────────────────────────────


class TestDoUpdate:
    def test_description(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({"success": True})
        out = do_update("abc", description="New desc")
        assert "Updated abc" in out
        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["descriptionMarkdown"] == "New desc"

    def test_nothing_raises(self):
        with pytest.raises(ValueError, match="Nothing to update"):
            do_update("abc")

    def test_close_date(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({"success": True})
        do_update("abc", close="2026-06-01")
        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert "closeTime" in body
        assert isinstance(body["closeTime"], int)


# ── CLI main() ────────────────────────────────────────────────────────


class TestMain:
    def test_search(self, mock_urlopen, capsys):
        mock_urlopen.return_value = _make_response([
            {"id": "test123", "probability": 0.5, "isResolved": False, "question": "Test?"},
        ])
        main(["search", "test"])
        out = capsys.readouterr().out
        assert "test123" in out

    def test_me(self, mock_urlopen, monkeypatch, capsys):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response(
            {"name": "Test", "username": "test", "balance": 100}
        )
        main(["me"])
        out = capsys.readouterr().out
        assert "Test" in out
        assert "M$100" in out

    def test_no_command_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_api_error_exits(self, mock_urlopen, monkeypatch, capsys):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="u", code=500, msg="err", hdrs={}, fp=MagicMock(read=lambda: b"server error")
        )
        with pytest.raises(SystemExit):
            main(["me"])
        err = capsys.readouterr().err
        assert "500" in err
