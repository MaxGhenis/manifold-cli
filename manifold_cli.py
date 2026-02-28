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
from urllib.parse import urlencode

API = "https://api.manifold.markets/v0"


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
        f"Close:     {datetime.fromtimestamp(close / 1000).strftime('%Y-%m-%d') if close else 'n/a'}",
    ]
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
    close_ms = int(datetime.strptime(close, "%Y-%m-%d").timestamp() * 1000) if close else None
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


def do_bet(
    contract_id: str,
    outcome: str,
    amount: int,
    limit_prob: float | None = None,
    api_key: str | None = None,
) -> str:
    data: dict = {
        "amount": amount,
        "contractId": contract_id,
        "outcome": outcome.upper(),
    }
    if limit_prob is not None:
        data["limitProb"] = limit_prob

    d = api_request("POST", "/bet", data, api_key=api_key)
    shares = d.get("shares", "?")
    prob_after = d.get("probAfter", d.get("probability", "?"))
    if isinstance(shares, (int, float)):
        return f"Filled M${d.get('amount', '?')}  ->  {shares:.1f} shares  prob->{prob_after:.0%}"
    return f"Result: {json.dumps(d)[:200]}"


def do_sell(
    contract_id: str,
    outcome: str = "YES",
    shares: float | None = None,
    api_key: str | None = None,
) -> str:
    data: dict = {"outcome": outcome.upper()}
    if shares:
        data["shares"] = shares
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
        data["closeTime"] = int(datetime.strptime(close, "%Y-%m-%d").timestamp() * 1000)
    if question:
        data["question"] = question
    if visibility:
        data["visibility"] = visibility
    if not data:
        raise ValueError("Nothing to update (use -d, -c, -q, or -v)")
    api_request("POST", f"/market/{contract_id}/update", data, api_key=api_key)
    return f"Updated {contract_id}"


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
    except ManifoldError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
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
    sp.add_argument("amount", type=int, help="Mana amount")
    sp.add_argument("--limit-prob", type=float, help="Limit order probability 0.01-0.99")

    sp = sub.add_parser("sell", help="Sell shares")
    sp.add_argument("contract_id")
    sp.add_argument("--outcome", default="YES", choices=["YES", "NO"])
    sp.add_argument("--shares", type=float, help="Shares to sell (default: all)")

    sp = sub.add_parser("positions", help="Recent bets grouped by market")
    sp.add_argument("-n", "--limit", type=int, default=50)

    sp = sub.add_parser("update", help="Update market description/close/question")
    sp.add_argument("contract_id")
    sp.add_argument("-d", "--description", help="New markdown description")
    sp.add_argument("-c", "--close", help="New close date YYYY-MM-DD")
    sp.add_argument("-q", "--question", help="New question text")
    sp.add_argument("-v", "--visibility", choices=["public", "unlisted"])

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
        "bet": lambda: _run(do_bet, args.contract_id, args.outcome, args.amount, args.limit_prob),
        "sell": lambda: _run(do_sell, args.contract_id, args.outcome, args.shares),
        "positions": lambda: _run(do_positions, args.limit),
        "update": lambda: _run(
            do_update, args.contract_id, args.description, args.close, args.question, args.visibility
        ),
        "resolve": lambda: _run(do_resolve, args.contract_id, args.outcome, args.prob),
    }
    dispatch[args.cmd]()


if __name__ == "__main__":
    main()
