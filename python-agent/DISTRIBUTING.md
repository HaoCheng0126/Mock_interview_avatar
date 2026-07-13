# Distributing This Demo

Use the packaging script instead of zipping the working directory directly.

```bash
cd python-agent
scripts/package_python_agent.sh python-agent-demo
```

The archive is written to:

```text
dist/python-agent-demo.zip
```

The package contains:

- `python-agent/` source, tests, quickstarts, and example configs
- `frontend/` files required by the browser demos
- `.env.example`
- `config/*.example.yaml`

The package excludes local-only files:

- `.venv/`, `node_modules/`
- `.omc/`, `.claude/`, `.superpowers/`
- `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`
- screenshots, test reports, and local progress notes
- real top-level runtime configs such as `config/products.yaml` and `config/crypto_market.yaml`

Before sending a package, run:

```bash
scripts/package_python_agent.sh python-agent-demo
zipinfo -1 dist/python-agent-demo.zip | rg '(\.venv|node_modules|\.omc|\.claude|__pycache__|\.pytest_cache|\.ruff_cache)'
unzip -p dist/python-agent-demo.zip | rg 'lk_live_[A-Za-z0-9]{20,}|/Users/'
```

Both `rg` commands should print nothing.
