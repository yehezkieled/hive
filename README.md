# Hive

Multi-maestro AI agent orchestration platform built natively on Claude Code.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
python -m hive
```

## Development

```bash
ruff check src/ tests/
ruff format src/ tests/
pytest
```
