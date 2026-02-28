# manifold-cli

Lightweight CLI for [Manifold Markets](https://manifold.markets). Zero dependencies — uses only the Python standard library.

## Install

```bash
uv pip install git+https://github.com/MaxGhenis/manifold-cli.git
```

Or for development:

```bash
git clone https://github.com/MaxGhenis/manifold-cli.git
cd manifold-cli
uv pip install -e .
```

## Setup

Set your API key (get it from your [Manifold profile](https://manifold.markets/profile)):

```bash
export MANIFOLD_API_KEY="your-key-here"
```

## Usage

```bash
# Account
manifold me

# Search and browse
manifold search "egg prices"
manifold search "CPI" -f open -n 20
manifold market CONTRACT_ID
manifold slug my-market-slug

# Create a binary market
manifold create -q "Will X happen by 2026?" -p 65 -c 2026-12-31
manifold create -q "Will Y?" -p 50 -c 2026-06-01 -d "Resolves based on Z data source."

# Trade
manifold bet CONTRACT_ID yes 100
manifold bet CONTRACT_ID no 50 --limit-prob 0.40
manifold sell CONTRACT_ID
manifold sell CONTRACT_ID --outcome NO --shares 50

# Portfolio
manifold positions

# Resolve (market creator only)
manifold resolve CONTRACT_ID yes
manifold resolve CONTRACT_ID MKT --prob 70
```

## Why not an MCP?

For AI coding assistants, a CLI is more token-efficient than an MCP server (~25 tokens per call vs ~150 for raw curl). The Manifold REST API is simple enough that a thin CLI wrapper is all you need.

## License

Apache 2.0
