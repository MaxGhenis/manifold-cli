"""Tests for manifold_cli — mocks all HTTP calls."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from manifold_cli import (
    ManifoldError,
    _amount_to_target,
    _get_pool,
    _parse_close_ms,
    _prob_to_value,
    _value_to_prob,
    api_request,
    do_autobet,
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

    def test_pseudo_numeric_value(self):
        d = {
            "question": "How many?",
            "id": "num1",
            "outcomeType": "PSEUDO_NUMERIC",
            "probability": 0.5,
            "value": 42,
            "volume": 10,
            "totalLiquidity": 250,
            "uniqueBettorCount": 2,
            "closeTime": None,
        }
        out = format_market(d)
        assert "Prob:      50%" in out
        assert "Value:     42" in out


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

    def test_limit_prob_caps_to_dry_run_fill(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        market = {"probability": 0.49, "totalLiquidity": 250}
        preview = {
            "amount": 5.5,
            "shares": 10,
            "probBefore": 0.49,
            "probAfter": 0.48,
            "isFilled": False,
        }
        real = {
            "amount": 5.5,
            "shares": 10,
            "probBefore": 0.49,
            "probAfter": 0.48,
        }
        mock_urlopen.side_effect = [
            _make_response(market),
            _make_response(preview),
            _make_response(real),
        ]
        out = do_bet("abc", "no", 100, limit_prob=0.48)
        assert "Filled M$5.5" in out
        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["amount"] == 5.5
        assert body["limitProb"] == 0.48

    def test_limit_value_converts_to_probability(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        market = {
            "outcomeType": "PSEUDO_NUMERIC",
            "probability": 0.4909090909,
            "value": 42,
            "min": 15,
            "max": 70,
            "totalLiquidity": 250,
        }
        preview = {
            "amount": 5.5,
            "shares": 10,
            "probBefore": 0.4909090909,
            "probAfter": 0.4818181818,
            "isFilled": False,
        }
        real = {
            "amount": 5.5,
            "shares": 10,
            "probBefore": 0.4909090909,
            "probAfter": 0.4818181818,
        }
        mock_urlopen.side_effect = [
            _make_response(market),
            _make_response(preview),
            _make_response(real),
        ]
        out = do_bet("abc", "no", 100, limit_value=41.5)
        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["limitProb"] == pytest.approx((41.5 - 15) / (70 - 15))
        assert body["amount"] == 5.5
        assert "value 41.5" in out

    def test_limit_value_rejects_binary_market(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        mock_urlopen.return_value = _make_response({"outcomeType": "BINARY", "probability": 0.5})
        with pytest.raises(ValueError, match="PSEUDO_NUMERIC"):
            do_bet("abc", "no", 100, limit_value=41.5)


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


# ── _amount_to_target (CPMM formula) ────────────────────────────────


class TestAmountToTarget:
    def test_yes_bet_moves_prob_up(self):
        # pool YES=100, NO=10 => p=10/110=9.09%, target=15%
        a = _amount_to_target(100, 10, 0.15, "YES")
        # Verify: final pools YES=1000/(10+a), NO=10+a
        n_final = 10 + a
        y_final = 100 * 10 / n_final
        p_final = n_final / (y_final + n_final)
        assert abs(p_final - 0.15) < 0.001

    def test_no_bet_moves_prob_down(self):
        # pool YES=100, NO=10 => p=9.09%, target=5%
        a = _amount_to_target(100, 10, 0.05, "NO")
        y_final = 100 + a
        n_final = 100 * 10 / y_final
        p_final = n_final / (y_final + n_final)
        assert abs(p_final - 0.05) < 0.001

    def test_already_past_target_returns_zero(self):
        # p=9.09%, buying YES to target 5% => wrong direction
        assert _amount_to_target(100, 10, 0.05, "YES") == 0.0
        # p=9.09%, buying NO to target 15% => wrong direction
        assert _amount_to_target(100, 10, 0.15, "NO") == 0.0

    def test_already_at_target_returns_zero(self):
        # p=10/110=9.09%, target=9.09%
        assert _amount_to_target(100, 10, 10 / 110, "YES") == 0.0

    def test_large_move(self):
        # pool YES=50, NO=50 => p=50%, target=90%
        a = _amount_to_target(50, 50, 0.90, "YES")
        n_final = 50 + a
        y_final = 50 * 50 / n_final
        p_final = n_final / (y_final + n_final)
        assert abs(p_final - 0.90) < 0.001
        assert a > 0


# ── _get_pool ────────────────────────────────────────────────────────


class TestGetPool:
    def test_binary_market(self):
        market = {"pool": {"YES": 100, "NO": 50}}
        assert _get_pool(market, None) == (100, 50)

    def test_multi_choice_answer(self):
        market = {
            "answers": [
                {"id": "a1", "pool": {"YES": 80, "NO": 20}},
                {"id": "a2", "pool": {"YES": 60, "NO": 40}},
            ]
        }
        assert _get_pool(market, "a2") == (60, 40)

    def test_missing_answer_returns_none(self):
        market = {"answers": [{"id": "a1", "pool": {"YES": 80, "NO": 20}}]}
        assert _get_pool(market, "missing") is None

    def test_no_pool_returns_none(self):
        assert _get_pool({}, None) is None


# ── pseudo-numeric conversion ─────────────────────────────────────────


class TestPseudoNumericConversion:
    def test_linear_value_to_probability(self):
        market = {"outcomeType": "PSEUDO_NUMERIC", "min": 15, "max": 70}
        assert _value_to_prob(market, 41.5) == pytest.approx((41.5 - 15) / 55)
        assert _prob_to_value(market, (41.5 - 15) / 55) == pytest.approx(41.5)

    def test_log_value_to_probability(self):
        market = {"outcomeType": "PSEUDO_NUMERIC", "min": 1, "max": 100, "isLogScale": True}
        probability = _value_to_prob(market, 10)
        assert _prob_to_value(market, probability) == pytest.approx(10)

    def test_value_outside_range_raises(self):
        market = {"outcomeType": "PSEUDO_NUMERIC", "min": 15, "max": 70}
        with pytest.raises(ValueError, match="outside market range"):
            _value_to_prob(market, 100)


# ── do_autobet ────────────────────────────────────────────────────────


class TestDoAutobet:
    def test_value_targets_use_market_value(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("MANIFOLD_API_KEY", "k")
        me = {"balance": 1000}
        market = {
            "outcomeType": "PSEUDO_NUMERIC",
            "probability": 0.4909090909,
            "value": 42,
            "min": 15,
            "max": 70,
            "totalLiquidity": 250,
        }
        preview = {
            "amount": 5.5,
            "shares": 10,
            "probBefore": 0.4909090909,
            "probAfter": 0.4818181818,
            "isFilled": False,
        }
        mock_urlopen.side_effect = [
            _make_response(me),
            _make_response(market),
            _make_response(preview),
        ]
        out = do_autobet("abc", "41.5", dry_run=True, value_targets=True)
        assert "42 ->    41.5 value" in out
        assert "NO M$5.5" in out
