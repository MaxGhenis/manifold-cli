"""Lightweight CLI for Manifold Markets.

Zero dependencies — uses only the Python standard library.

Usage:
    manifold me
    manifold search "tomato prices"
    manifold market CONTRACT_ID
    manifold create -q "Will X happen?" -p 72 -c 2026-04-15
    manifold bet CONTRACT_ID yes 100
    manifold sell CONTRACT_ID
    manifold positions
    manifold resolve CONTRACT_ID yes
    manifold slug MY-MARKET-SLUG
    manifold update CONTRACT_ID -d "New description"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from math import log
from urllib.parse import urlencode

API = "https://api.manifold.markets/v0"


def _parse_close_ms(date_str: str) -> int:
    """Parse YYYY-MM-DD to epoch milliseconds."""
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)


class ManifoldError(Exception):
    """Raised on API errors."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"API error {status}: {body}")


def get_api_key() -> str:
    """Resolve API key from env var or macOS Keychain."""
    key = os.environ.get("MANIFOLD_API_KEY", "")
    if not key:
        try:
            result = subprocess.run(
                [os.path.expanduser("~/.claude/manage-secret.sh"), "get", "MANIFOLD_API_KEY"],
                capture_output=True,
                text=True,
                check=True,
            )
            key = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return key


def api_request(
    method: str,
    path: str,
    data: dict | None = None,
    auth: bool = True,
    api_key: str | None = None,
) -> dict | list:
    """Make an API request. Returns parsed JSON."""
    url = f"{API}{path}"
    headers = {"Content-Type": "application/json"}
    if auth:
        key = api_key or get_api_key()
        if not key:
            raise ManifoldError(401, "MANIFOLD_API_KEY not set and not found in keychain")
        headers["Authorization"] = f"Key {key}"

    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ManifoldError(e.code, e.read().decode()) from e


# ── Commands ──────────────────────────────────────────────────────────
# Each returns a string (for testability) and the CLI wrapper prints it.


def do_me(api_key: str | None = None) -> str:
    d = api_request("GET", "/me", api_key=api_key)
    return f"{d['name']} (@{d['username']})  Balance: M${d['balance']:.0f}"


def do_search(
    query: str,
    limit: int = 10,
    filter_: str | None = None,
) -> str:
    qp: dict = {"term": query, "limit": limit}
    if filter_:
        qp["filter"] = filter_
    markets = api_request("GET", f"/search-markets?{urlencode(qp)}", auth=False)
    lines = []
    for m in markets:
        prob = m.get("probability")
        prob_str = f"{prob:.0%}" if prob is not None else "n/a"
        status = "RESOLVED" if m.get("isResolved") else "open"
        lines.append(f"  {m['id'][:12]}  {prob_str:>5}  {status:<8}  {m['question'][:70]}")
    return "\n".join(lines)


def format_market(d: dict) -> str:
    prob = d.get("probability")
    close = d.get("closeTime")
    lines = [
        f"Question:  {d['question']}",
        f"ID:        {d['id']}",
        f"URL:       {d.get('url', 'n/a')}",
        f"Prob:      {prob:.0%}" if prob is not None else "Prob:      n/a",
        f"Volume:    M${d.get('volume', 0):.0f}",
        f"Liquidity: M${d.get('totalLiquidity', 0):.0f}",
        f"Bettors:   {d.get('uniqueBettorCount', 0)}",
        f"Close:     {datetime.fromtimestamp(close // 1000).strftime('%Y-%m-%d') if close else 'n/a'}",
    ]
    if d.get("outcomeType") == "PSEUDO_NUMERIC" and d.get("value") is not None:
        lines.insert(4, f"Value:     {_format_number(d['value'])}")
    answers = d.get("answers")
    if answers:
        lines.append("Answers:")
        for a in answers:
            p = a.get("probability", 0)
            lines.append(f"  {a['id']:<16} {p:>5.1%}  {a['text']}")
    return "\n".join(lines)


def do_market(market_id: str) -> str:
    d = api_request("GET", f"/market/{market_id}", auth=False)
    return format_market(d)


def do_slug(slug: str) -> str:
    d = api_request("GET", f"/slug/{slug}", auth=False)
    return format_market(d)


def do_create(
    question: str,
    prob: int,
    close: str | None = None,
    description: str | None = None,
    liquidity: int = 250,
    visibility: str = "public",
    api_key: str | None = None,
) -> str:
    close_ms = _parse_close_ms(close) if close else None
    data: dict = {
        "outcomeType": "BINARY",
        "question": question,
        "initialProb": prob,
        "visibility": visibility,
        "liquidityTier": liquidity,
    }
    if close_ms:
        data["closeTime"] = close_ms
    if description:
        data["descriptionMarkdown"] = description

    d = api_request("POST", "/market", data, api_key=api_key)
    return f"Created: {d.get('url', d.get('id', '?'))}\nID:      {d['id']}\nProb:    {d.get('probability', '?')}"


def _amount_to_target(pool_yes: float, pool_no: float, target: float, outcome: str) -> float:
    """Mana needed to move CPMM price from current to target probability.

    CPMM invariant: pool_YES * pool_NO = k (constant).
    Current p(YES) = pool_NO / (pool_YES + pool_NO).

    Buying YES with amount a:
      final pools -> YES: k/(n+a), NO: n+a
      p_final = (n+a)^2 / (k + (n+a)^2)
      => a = sqrt(target * y * n / (1 - target)) - n

    Buying NO with amount a:
      final pools -> YES: y+a, NO: k/(y+a)
      p_final = k / ((y+a)^2 + k)
      => a = sqrt(y * n * (1 - target) / target) - y
    """
    y, n = pool_yes, pool_no
    current = n / (y + n) if (y + n) > 0 else 0.5

    if outcome == "YES":
        if target <= current:
            return 0.0
        return max(0.0, (target * y * n / (1 - target)) ** 0.5 - n)
    else:
        if target >= current:
            return 0.0
        return max(0.0, (y * n * (1 - target) / target) ** 0.5 - y)


def _get_pool(market: dict, answer_id: str | None) -> tuple[float, float] | None:
    """Extract YES/NO pool for a binary market or a specific multi-choice answer."""
    if answer_id:
        for a in market.get("answers", []):
            if a["id"] == answer_id:
                pool = a.get("pool", {})
                return pool.get("YES", 0), pool.get("NO", 0)
        return None
    pool = market.get("pool", {})
    if "YES" in pool and "NO" in pool:
        return pool["YES"], pool["NO"]
    return None


def _format_number(value: float) -> str:
    """Format a market value compactly without hiding meaningful decimals."""
    if abs(value - round(value)) < 1e-9:
        return f"{value:.0f}"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _is_pseudo_numeric(market: dict) -> bool:
    return market.get("outcomeType") == "PSEUDO_NUMERIC"


def _require_numeric_bounds(market: dict) -> tuple[float, float]:
    if not _is_pseudo_numeric(market):
        raise ValueError("--limit-value/--value-targets only works for PSEUDO_NUMERIC markets")
    min_value = market.get("min")
    max_value = market.get("max")
    if min_value is None or max_value is None:
        raise ValueError("Pseudo-numeric market is missing min/max bounds")
    return float(min_value), float(max_value)


def _value_to_prob(market: dict, value: float) -> float:
    """Map a pseudo-numeric displayed value to its underlying probability."""
    min_value, max_value = _require_numeric_bounds(market)
    if value < min_value or value > max_value:
        raise ValueError(
            f"Target value {_format_number(value)} outside market range "
            f"{_format_number(min_value)}-{_format_number(max_value)}"
        )

    if market.get("isLogScale"):
        base = max_value - min_value + 1
        return log(value - min_value + 1, base)
    return (value - min_value) / (max_value - min_value)


def _prob_to_value(market: dict, probability: float) -> float:
    """Map a pseudo-numeric underlying probability to its displayed value."""
    min_value, max_value = _require_numeric_bounds(market)
    if market.get("isLogScale"):
        return (max_value - min_value + 1) ** probability + min_value - 1
    return min_value + (max_value - min_value) * probability


def _get_probability(market: dict, answer_id: str | None = None) -> float | None:
    """Extract the displayed probability for binary, numeric, or one multi answer."""
    if answer_id:
        for a in market.get("answers", []):
            if a["id"] == answer_id:
                return a.get("probability")
        return None
    return market.get("probability")


def _target_to_probability(
    market: dict,
    target: float,
    answer_id: str | None = None,
    *,
    value_target: bool = False,
) -> float:
    if value_target:
        if answer_id:
            raise ValueError("--value-targets is not supported with answer IDs")
        return _value_to_prob(market, target)
    return target


def _format_target(market: dict, probability: float, *, value_target: bool = False) -> str:
    if value_target:
        return _format_number(_prob_to_value(market, probability))
    return f"{probability:.1%}"


def _dry_run_bet(
    contract_id: str,
    outcome: str,
    amount: float,
    limit_prob: float | None = None,
    answer_id: str | None = None,
    api_key: str | None = None,
) -> dict:
    data: dict = {
        "amount": amount,
        "contractId": contract_id,
        "outcome": outcome.upper(),
        "dryRun": True,
    }
    if limit_prob is not None:
        data["limitProb"] = limit_prob
    if answer_id is not None:
        data["answerId"] = answer_id
    return api_request("POST", "/bet", data, api_key=api_key)


def _cap_amount_with_dry_run(
    contract_id: str,
    outcome: str,
    amount: float,
    limit_prob: float,
    answer_id: str | None = None,
    api_key: str | None = None,
) -> tuple[float, dict]:
    """Ask Manifold how much would fill before the limit and use that amount."""
    preview = _dry_run_bet(contract_id, outcome, amount, limit_prob, answer_id, api_key)
    fill_amount = float(preview.get("amount", 0) or 0)
    if fill_amount <= 0:
        return 0.0, preview
    return min(float(amount), fill_amount), preview


def do_bet(
    contract_id: str,
    outcome: str,
    amount: float,
    limit_prob: float | None = None,
    limit_value: float | None = None,
    answer_id: str | None = None,
    api_key: str | None = None,
) -> str:
    if limit_prob is not None and limit_value is not None:
        raise ValueError("Use either --limit-prob or --limit-value, not both")

    market = api_request("GET", f"/market/{contract_id}", auth=False)
    liquidity = market.get("totalLiquidity", 0)
    value_target = limit_value is not None
    if limit_value is not None:
        limit_prob = _value_to_prob(market, float(limit_value))

    # Cap bet at the amount that would move price to limit_prob. Use Manifold's
    # own dry-run calculation because pseudo-numeric and p-weighted CPMM pools
    # do not always follow the simple x*y=k formula.
    if limit_prob is not None:
        capped_amount, preview = _cap_amount_with_dry_run(
            contract_id, outcome, amount, limit_prob, answer_id, api_key
        )
        prob_before = preview.get("probBefore")
        if capped_amount <= 0:
            current = prob_before if isinstance(prob_before, (int, float)) else _get_probability(market, answer_id)
            current_str = f"{current:.1%}" if isinstance(current, (int, float)) else "unknown"
            target_str = _format_target(market, limit_prob, value_target=value_target)
            units = "value" if value_target else "prob"
            return f"No bet: price already at/past target ({current_str} vs {units} {target_str})"
        if capped_amount < amount:
            target_str = _format_target(market, limit_prob, value_target=value_target)
            print(
                f"Capping M${_format_number(amount)} -> M${_format_number(capped_amount)} "
                f"(amount to reach target {target_str})",
                file=sys.stderr,
            )
            amount = capped_amount

    data: dict = {
        "amount": amount,
        "contractId": contract_id,
        "outcome": outcome.upper(),
    }
    if limit_prob is not None:
        data["limitProb"] = limit_prob
    if answer_id is not None:
        data["answerId"] = answer_id

    d = api_request("POST", "/bet", data, api_key=api_key)
    shares = d.get("shares", "?")
    prob_before = d.get("probBefore", "?")
    prob_after = d.get("probAfter", d.get("probability", "?"))
    if isinstance(shares, (int, float)):
        prob_str = f"{prob_before:.0%}->{prob_after:.0%}" if isinstance(prob_before, (int, float)) else f"->{prob_after:.0%}"
        value_str = ""
        if _is_pseudo_numeric(market) and isinstance(prob_after, (int, float)):
            value_str = f"  value {_format_number(_prob_to_value(market, prob_after))}"
        return (
            f"Filled M${_format_number(float(d.get('amount', 0)))}  ->  {shares:.1f} shares  "
            f"prob {prob_str}{value_str}  (liq M${liquidity:.0f})"
        )
    return f"Result: {json.dumps(d)[:200]}"


def do_sell(
    contract_id: str,
    outcome: str = "YES",
    shares: float | None = None,
    answer_id: str | None = None,
    api_key: str | None = None,
) -> str:
    data: dict = {"outcome": outcome.upper()}
    if shares:
        data["shares"] = shares
    if answer_id is not None:
        data["answerId"] = answer_id
    d = api_request("POST", f"/market/{contract_id}/sell", data, api_key=api_key)
    return f"Sold: {json.dumps(d)[:200]}"


def do_positions(limit: int = 50, api_key: str | None = None) -> str:
    me = api_request("GET", "/me", api_key=api_key)
    user_id = me["id"]
    bets = api_request("GET", f"/bets?{urlencode({'userId': user_id, 'limit': limit})}", api_key=api_key)
    by_contract: dict[str, list] = {}
    for b in bets:
        cid = b.get("contractId", "?")
        by_contract.setdefault(cid, []).append(b)

    lines = []
    for cid, contract_bets in by_contract.items():
        total_shares = sum(b.get("shares", 0) for b in contract_bets)
        total_spent = sum(b.get("amount", 0) for b in contract_bets)
        outcome = contract_bets[0].get("outcome", "?")
        lines.append(f"  {cid[:12]}  {outcome:>3}  {total_shares:>8.1f} shares  M${total_spent:>6.0f} spent")
    return "\n".join(lines)


def do_update(
    contract_id: str,
    description: str | None = None,
    close: str | None = None,
    question: str | None = None,
    visibility: str | None = None,
    api_key: str | None = None,
) -> str:
    data: dict = {}
    if description:
        data["descriptionMarkdown"] = description
    if close:
        data["closeTime"] = _parse_close_ms(close)
    if question:
        data["question"] = question
    if visibility:
        data["visibility"] = visibility
    if not data:
        raise ValueError("Nothing to update (use -d, -c, -q, or -v)")
    api_request("POST", f"/market/{contract_id}/update", data, api_key=api_key)
    return f"Updated {contract_id}"


def _kelly_fraction(p_true: float, p_market: float) -> tuple[str, float]:
    """Return (side, kelly_fraction) for a bet. Positive fraction = bet."""
    if p_true > p_market:
        return "YES", (p_true - p_market) / (1 - p_market)
    elif p_true < p_market:
        return "NO", (p_market - p_true) / p_market
    return "YES", 0.0



def do_autobet(
    contract_id: str,
    targets: str,
    fraction: float = 0.5,
    dry_run: bool = False,
    value_targets: bool = False,
    api_key: str | None = None,
) -> str:
    """Auto-bet with Kelly + CPMM cap. Handles single and multi-target.

    Single:  '0.65' (binary) or 'answer_id=0.13' (one multi-choice answer)
    Multi:   'id1=0.13,id2=0.03,id3=0.77'  (re-fetches between bets)
    Pseudo-numeric values: pass value_targets=True and use displayed values.
    """
    me = api_request("GET", "/me", api_key=api_key)
    bankroll = me["balance"]
    market = api_request("GET", f"/market/{contract_id}", auth=False)
    liquidity = market.get("totalLiquidity", 0)
    is_multi = bool(market.get("answers"))

    # Parse targets
    target_map: dict[str | None, float] = {}
    if "=" in targets:
        for pair in targets.split(","):
            k, v = pair.strip().split("=")
            target_map[k.strip()] = float(v.strip())
    else:
        target_map[None] = float(targets)

    lines = [f"Bankroll M${bankroll:.0f}  |  {fraction:.0%} Kelly  |  Liq M${liquidity:.0f}", ""]

    # Build plan from current state
    plan: list[dict] = []
    for answer_id, p_true in target_map.items():
        try:
            p_true = _target_to_probability(market, p_true, answer_id, value_target=value_targets)
        except ValueError as e:
            lines.append(f"  {answer_id or 'binary'}: {e}")
            continue
        p_market = _get_probability(market, answer_id)
        if p_market is None:
            lines.append(f"  {answer_id or 'binary'}: probability not found, skipping")
            continue
        side, kelly = _kelly_fraction(p_true, p_market)
        bet_kelly = kelly * fraction * bankroll
        if bet_kelly < 1:
            bet = 0.0
            target_hit = False
        else:
            bet, preview = _cap_amount_with_dry_run(contract_id, side, bet_kelly, p_true, answer_id, api_key)
            target_hit = bool(preview) and not preview.get("isFilled", True)

        label = "market" if not market.get("answers") else "answer"
        if answer_id:
            for a in market.get("answers", []):
                if a["id"] == answer_id:
                    label = a["text"]
                    break

        plan.append({
            "answer_id": answer_id, "label": label, "side": side,
            "p_market": p_market, "p_true": p_true,
            "kelly_amt": bet_kelly, "target_hit": target_hit, "bet": bet,
        })

    # Sort biggest edge first
    plan.sort(key=lambda x: abs(x["p_true"] - x["p_market"]), reverse=True)

    # Display plan
    for p in plan:
        current_str = _format_target(market, p["p_market"], value_target=value_targets)
        target_str = _format_target(market, p["p_true"], value_target=value_targets)
        unit = "value" if value_targets else "prob"
        cap_note = "target cap" if p["target_hit"] else "Kelly"
        lines.append(
            f"  {p['label']:<20} {current_str:>7} -> {target_str:>7} {unit:<5}  "
            f"{p['side']:>3} M${_format_number(p['bet'])}"
            f"  ({cap_note}; Kelly M${_format_number(p['kelly_amt'])})"
        )

    if dry_run:
        lines.append("\nDry run. Remove -n/--dry-run to execute.")
        return "\n".join(lines)

    # Execute: re-fetch market between each bet for multi-choice accuracy
    lines.append("")
    for p in plan:
        if p["bet"] < 1:
            lines.append(f"  {p['label']}: skip (< M$1)")
            continue

        # Re-fetch immediately before betting so dry-run sizing uses the latest
        # price and previous bets in the same command are accounted for.
        market = api_request("GET", f"/market/{contract_id}", auth=False)
        p_market_now = _get_probability(market, p["answer_id"])
        if p_market_now is None:
            lines.append(f"  {p['label']}: probability not found")
            continue
        side, kelly = _kelly_fraction(p["p_true"], p_market_now)
        bet_kelly = kelly * fraction * bankroll
        if bet_kelly < 1:
            lines.append(f"  {p['label']}: skip (< M$1 Kelly)")
            continue
        bet, preview = _cap_amount_with_dry_run(contract_id, side, bet_kelly, p["p_true"], p["answer_id"], api_key)
        if bet < 1:
            target_str = _format_target(market, p["p_true"], value_target=value_targets)
            current_str = _format_target(market, p_market_now, value_target=value_targets)
            lines.append(f"  {p['label']}: already at target ({current_str} vs {target_str})")
            continue

        data: dict = {
            "amount": bet,
            "contractId": contract_id,
            "outcome": side,
            "limitProb": p["p_true"],
        }
        if p["answer_id"]:
            data["answerId"] = p["answer_id"]

        d = api_request("POST", "/bet", data, api_key=api_key)
        shares = d.get("shares", 0)
        prob_after = d.get("probAfter", 0)
        after = _format_target(market, prob_after, value_target=value_targets) if isinstance(prob_after, (int, float)) else "?"
        lines.append(
            f"  {p['label']}: {side} M${_format_number(float(d.get('amount', bet)))} "
            f"-> {shares:.1f} shares  now {after}"
        )

    return "\n".join(lines)


def do_resolve(
    contract_id: str,
    outcome: str,
    prob: int | None = None,
    api_key: str | None = None,
) -> str:
    outcome = outcome.upper()
    data: dict = {"outcome": outcome}
    if outcome == "MKT" and prob is not None:
        data["probabilityInt"] = prob
    d = api_request("POST", f"/market/{contract_id}/resolve", data, api_key=api_key)
    return f"Resolved: {json.dumps(d)[:200]}"


# ── CLI wrappers ──────────────────────────────────────────────────────


def _run(fn, *args, **kwargs) -> None:
    try:
        print(fn(*args, **kwargs))
    except (ManifoldError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="manifold", description="Manifold Markets CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("me", help="Account info and balance")

    sp = sub.add_parser("search", help="Search markets")
    sp.add_argument("query")
    sp.add_argument("-n", "--limit", type=int, default=10)
    sp.add_argument("-f", "--filter", choices=["open", "closed", "resolved"])

    sp = sub.add_parser("market", help="Market details by ID")
    sp.add_argument("id")

    sp = sub.add_parser("slug", help="Market details by slug")
    sp.add_argument("slug")

    sp = sub.add_parser("create", help="Create binary market")
    sp.add_argument("-q", "--question", required=True)
    sp.add_argument("-p", "--prob", type=int, required=True, help="Initial probability 1-99")
    sp.add_argument("-c", "--close", help="Close date YYYY-MM-DD")
    sp.add_argument("-d", "--description", help="Markdown description")
    sp.add_argument("-l", "--liquidity", type=int, default=250, help="Liquidity tier (default 250)")
    sp.add_argument("-v", "--visibility", default="public", choices=["public", "unlisted"])

    sp = sub.add_parser("bet", help="Place a bet")
    sp.add_argument("contract_id")
    sp.add_argument("outcome", choices=["yes", "no", "YES", "NO"])
    sp.add_argument("amount", type=float, help="Mana amount")
    sp.add_argument("--limit-prob", type=float, help="Target probability — caps bet to avoid overshooting")
    sp.add_argument("--limit-value", type=float, help="Target displayed value for PSEUDO_NUMERIC markets")
    sp.add_argument("-a", "--answer-id", help="Answer ID for multi-choice markets")

    sp = sub.add_parser("sell", help="Sell shares")
    sp.add_argument("contract_id")
    sp.add_argument("--outcome", default="YES", choices=["YES", "NO"])
    sp.add_argument("--shares", type=float, help="Shares to sell (default: all)")
    sp.add_argument("-a", "--answer-id", help="Answer ID for multi-choice markets")

    sp = sub.add_parser("positions", help="Recent bets grouped by market")
    sp.add_argument("-n", "--limit", type=int, default=50)

    sp = sub.add_parser("update", help="Update market description/close/question")
    sp.add_argument("contract_id")
    sp.add_argument("-d", "--description", help="New markdown description")
    sp.add_argument("-c", "--close", help="New close date YYYY-MM-DD")
    sp.add_argument("-q", "--question", help="New question text")
    sp.add_argument("-v", "--visibility", choices=["public", "unlisted"])

    sp = sub.add_parser("autobet", help="Kelly-sized target auto-bet")
    sp.add_argument("contract_id")
    sp.add_argument(
        "targets",
        help="True probs: '0.65' (binary), 'id=0.13' (single), 'id1=0.13,id2=0.03' (multi)",
    )
    sp.add_argument("-f", "--fraction", type=float, default=0.5, help="Kelly fraction (default 0.5)")
    sp.add_argument("--value-targets", action="store_true", help="Interpret targets as displayed pseudo-numeric values")
    sp.add_argument("-n", "--dry-run", action="store_true", help="Show plan without betting")

    sp = sub.add_parser("resolve", help="Resolve a market")
    sp.add_argument("contract_id")
    sp.add_argument("outcome", choices=["yes", "no", "YES", "NO", "MKT", "CANCEL"])
    sp.add_argument("--prob", type=int, help="Probability 1-99 (for MKT resolution)")

    args = parser.parse_args(argv)

    dispatch = {
        "me": lambda: _run(do_me),
        "search": lambda: _run(do_search, args.query, args.limit, args.filter),
        "market": lambda: _run(do_market, args.id),
        "slug": lambda: _run(do_slug, args.slug),
        "create": lambda: _run(
            do_create, args.question, args.prob, args.close, args.description, args.liquidity, args.visibility
        ),
        "bet": lambda: _run(
            do_bet, args.contract_id, args.outcome, args.amount, args.limit_prob, args.limit_value, args.answer_id,
        ),
        "sell": lambda: _run(do_sell, args.contract_id, args.outcome, args.shares, args.answer_id),
        "positions": lambda: _run(do_positions, args.limit),
        "update": lambda: _run(
            do_update, args.contract_id, args.description, args.close, args.question, args.visibility
        ),
        "autobet": lambda: _run(
            do_autobet, args.contract_id, args.targets, args.fraction, args.dry_run, args.value_targets,
        ),
        "resolve": lambda: _run(do_resolve, args.contract_id, args.outcome, args.prob),
    }
    dispatch[args.cmd]()


if __name__ == "__main__":
    main()
