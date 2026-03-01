# manifold-cli

Lightweight CLI for [Manifold Markets](https://manifold.markets). Zero dependencies — uses only the Python standard library.

[![CI](https://github.com/MaxGhenis/manifold-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxGhenis/manifold-cli/actions/workflows/ci.yml)
[![Python 3.10–3.14](https://img.shields.io/badge/python-3.10–3.14-blue)](https://python.org)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Install

```bash
# From PyPI (coming soon)
# pip install manifold-cli

# From GitHub
uv pip install git+https://github.com/MaxGhenis/manifold-cli.git

# For development
git clone https://github.com/MaxGhenis/manifold-cli.git
cd manifold-cli
uv pip install -e '.[dev]'
```

## Authentication

Get your API key from your [Manifold profile](https://manifold.markets/profile) and set it as an environment variable:

```bash
export MANIFOLD_API_KEY="your-key-here"
```

Read-only commands (`search`, `market`, `slug`) work without authentication. All other commands require an API key.

## Commands

### `me` — Account info

```bash
manifold me
# Max Ghenis (@MaxGhenis)  Balance: M$19706
```

### `search` — Find markets

```bash
manifold search "egg prices"
manifold search "CPI" -f open        # Filter: open, closed, resolved
manifold search "election" -n 20     # Limit results (default: 10)
```

Output columns: `ID  PROB  STATUS  QUESTION`

### `market` — Market details by ID

```bash
manifold market abc123def456
# Question:  Will egg prices exceed $4/dozen by March 2026?
# ID:        abc123def456
# URL:       https://manifold.markets/user/market-slug
# Prob:      33%
# Volume:    M$500
# Liquidity: M$250
# Bettors:   5
# Close:     2026-03-31
```

### `slug` — Market details by slug

```bash
manifold slug my-market-slug
```

Same output as `market`.

### `create` — Create a binary market

```bash
manifold create -q "Will X happen by 2026?" -p 65 -c 2026-12-31
manifold create -q "Will Y?" -p 50 -c 2026-06-01 -d "Resolves based on Z data."
manifold create -q "Q?" -p 50 -l 500 -v unlisted
```

| Flag | Description | Required |
|------|-------------|----------|
| `-q, --question` | Market question | Yes |
| `-p, --prob` | Initial probability (1-99) | Yes |
| `-c, --close` | Close date (`YYYY-MM-DD`) | No |
| `-d, --description` | Markdown description | No |
| `-l, --liquidity` | Liquidity tier (default: 250) | No |
| `-v, --visibility` | `public` or `unlisted` (default: `public`) | No |

### `bet` — Place a bet

```bash
manifold bet CONTRACT_ID yes 100
manifold bet CONTRACT_ID no 50 --limit-prob 0.40   # Limit order
```

| Argument | Description |
|----------|-------------|
| `contract_id` | Market ID |
| `outcome` | `yes` or `no` |
| `amount` | Mana amount (integer) |
| `--limit-prob` | Limit order probability (0.01-0.99) |

### `sell` — Sell shares

```bash
manifold sell CONTRACT_ID
manifold sell CONTRACT_ID --outcome NO --shares 50
```

### `positions` — Recent bets

```bash
manifold positions
manifold positions -n 100    # More history (default: 50)
```

### `update` — Edit a market

```bash
manifold update CONTRACT_ID -d "New markdown description"
manifold update CONTRACT_ID -c 2026-12-31
manifold update CONTRACT_ID -q "Updated question text"
manifold update CONTRACT_ID -v unlisted
```

### `resolve` — Resolve a market

```bash
manifold resolve CONTRACT_ID yes
manifold resolve CONTRACT_ID no
manifold resolve CONTRACT_ID MKT --prob 70    # Probabilistic resolution
manifold resolve CONTRACT_ID CANCEL
```

## Python API

The CLI functions are importable for use in scripts:

```python
from manifold_cli import do_search, do_bet, do_me, api_request

# Search (no auth needed)
print(do_search("egg prices", limit=5))

# Account info
print(do_me(api_key="your-key"))

# Place a bet
print(do_bet("contract_id", "yes", 100, api_key="your-key"))

# Raw API call
data = api_request("GET", "/market/CONTRACT_ID", auth=False)
```

## Development

```bash
git clone https://github.com/MaxGhenis/manifold-cli.git
cd manifold-cli
uv sync --dev
uv run pytest tests/ -v
```

Tests mock all HTTP calls — no API key or network needed.

## Why a CLI instead of an MCP?

For AI coding assistants, a CLI is ~6x more token-efficient than an MCP server (~25 tokens per call vs ~150 for tool-use JSON). The Manifold REST API is simple enough that a thin CLI wrapper is the right abstraction.

## License

[Apache 2.0](LICENSE)
