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
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlencode

API = "https://api.manifold.markets/v0"


def _key() -> str:
    key = os.environ.get("MANIFOLD_API_KEY", "")
    if not key:
        try:
            result = subprocess.run(
                [os.path.expanduser("~/.claude/manage-secret.sh"), "get", "MANIFOLD_API_KEY"],
                capture_output=True, text=True, check=True,
            )
            key = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if not key:
        print("Error: set MANIFOLD_API_KEY or add it to keychain", file=sys.stderr)
        sys.exit(1)
    return key


def _req(method: str, path: str, data: dict | None = None, auth: bool = True) -> dict | list:
    import urllib.error
    import urllib.request

    url = f"{API}{path}"
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Key {_key()}"

    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"API error {e.code}: {err}", file=sys.stderr)
        sys.exit(1)


# ── Commands ──────────────────────────────────────────────────────────


def cmd_me(_args: argparse.Namespace) -> None:
    d = _req("GET", "/me")
    print(f"{d['name']} (@{d['username']})  Balance: M${d['balance']:.0f}")


def cmd_search(args: argparse.Namespace) -> None:
    qp: dict = {"term": args.query, "limit": args.limit}
    if args.filter:
        qp["filter"] = args.filter
    markets = _req("GET", f"/search-markets?{urlencode(qp)}", auth=False)
    for m in markets:
        prob = m.get("probability")
        prob_str = f"{prob:.0%}" if prob is not None else "n/a"
        status = "RESOLVED" if m.get("isResolved") else "open"
        print(f"  {m['id'][:12]}  {prob_str:>5}  {status:<8}  {m['question'][:70]}")


def cmd_market(args: argparse.Namespace) -> None:
    d = _req("GET", f"/market/{args.id}", auth=False)
    _print_market(d)


def cmd_slug(args: argparse.Namespace) -> None:
    d = _req("GET", f"/slug/{args.slug}", auth=False)
    _print_market(d)


def _print_market(d: dict) -> None:
    prob = d.get("probability")
    print(f"Question:  {d['question']}")
    print(f"ID:        {d['id']}")
    print(f"URL:       {d.get('url', 'n/a')}")
    print(f"Prob:      {prob:.0%}" if prob is not None else "Prob:      n/a")
    print(f"Volume:    M${d.get('volume', 0):.0f}")
    print(f"Liquidity: M${d.get('totalLiquidity', 0):.0f}")
    print(f"Bettors:   {d.get('uniqueBettorCount', 0)}")
    close = d.get("closeTime")
    print(f"Close:     {datetime.fromtimestamp(close / 1000).strftime('%Y-%m-%d') if close else 'n/a'}")


def cmd_create(args: argparse.Namespace) -> None:
    close_ms = int(datetime.strptime(args.close, "%Y-%m-%d").timestamp() * 1000) if args.close else None
    data: dict = {
        "outcomeType": "BINARY",
        "question": args.question,
        "initialProb": args.prob,
        "visibility": args.visibility,
        "liquidityTier": args.liquidity,
    }
    if close_ms:
        data["closeTime"] = close_ms
    if args.description:
        data["descriptionMarkdown"] = args.description

    d = _req("POST", "/market", data)
    print(f"Created: {d.get('url', d.get('id', '?'))}")
    print(f"ID:      {d['id']}")
    print(f"Prob:    {d.get('probability', '?')}")


def cmd_bet(args: argparse.Namespace) -> None:
    data: dict = {
        "amount": args.amount,
        "contractId": args.contract_id,
        "outcome": args.outcome.upper(),
    }
    if args.limit_prob is not None:
        data["limitProb"] = args.limit_prob

    d = _req("POST", "/bet", data)
    shares = d.get("shares", "?")
    prob_after = d.get("probAfter", d.get("probability", "?"))
    if isinstance(shares, (int, float)):
        print(f"Filled M${d.get('amount', '?')}  ->  {shares:.1f} shares  prob->{prob_after:.0%}")
    else:
        print(f"Result: {json.dumps(d)[:200]}")


def cmd_sell(args: argparse.Namespace) -> None:
    data: dict = {"outcome": args.outcome.upper()}
    if args.shares:
        data["shares"] = args.shares
    d = _req("POST", f"/market/{args.contract_id}/sell", data)
    print(f"Sold: {json.dumps(d)[:200]}")


def cmd_positions(args: argparse.Namespace) -> None:
    me = _req("GET", "/me")
    user_id = me["id"]
    bets = _req("GET", f"/bets?{urlencode({'userId': user_id, 'limit': args.limit})}")
    by_contract: dict[str, list] = {}
    for b in bets:
        cid = b.get("contractId", "?")
        by_contract.setdefault(cid, []).append(b)

    for cid, contract_bets in by_contract.items():
        total_shares = sum(b.get("shares", 0) for b in contract_bets)
        total_spent = sum(b.get("amount", 0) for b in contract_bets)
        outcome = contract_bets[0].get("outcome", "?")
        print(f"  {cid[:12]}  {outcome:>3}  {total_shares:>8.1f} shares  M${total_spent:>6.0f} spent")


def cmd_update(args: argparse.Namespace) -> None:
    data: dict = {}
    if args.description:
        data["descriptionMarkdown"] = args.description
    if args.close:
        data["closeTime"] = int(datetime.strptime(args.close, "%Y-%m-%d").timestamp() * 1000)
    if args.question:
        data["question"] = args.question
    if args.visibility:
        data["visibility"] = args.visibility
    if not data:
        print("Nothing to update (use -d, -c, -q, or -v)", file=sys.stderr)
        sys.exit(1)
    _req("POST", f"/market/{args.contract_id}/update", data)
    print(f"Updated {args.contract_id}")


def cmd_resolve(args: argparse.Namespace) -> None:
    outcome = args.outcome.upper()
    data: dict = {"outcome": outcome}
    if outcome == "MKT":
        data["probabilityInt"] = args.prob
    d = _req("POST", f"/market/{args.contract_id}/resolve", data)
    print(f"Resolved: {json.dumps(d)[:200]}")


# ── Parser ────────────────────────────────────────────────────────────


def main() -> None:
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

    args = parser.parse_args()
    {
        "me": cmd_me,
        "search": cmd_search,
        "market": cmd_market,
        "slug": cmd_slug,
        "create": cmd_create,
        "bet": cmd_bet,
        "sell": cmd_sell,
        "positions": cmd_positions,
        "update": cmd_update,
        "resolve": cmd_resolve,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
