# 0002: PostgreSQL for the usage ledger

**Status:** accepted

## Context

Every request produces a cost record. Later phases add per-key budgets that must block
requests once spent, API keys and an audit log. These need durable writes, exact arithmetic
on money and aggregate queries ("spend per key this month").

## Decision

Use PostgreSQL through SQLAlchemy 2 (async, asyncpg) with Alembic migrations. Store cost as
`NUMERIC(12, 8)` and handle it as `Decimal` in Python; return it as a JSON string.

## Alternatives considered

- **SQLite:** simplest, but a single writer and a poor fit for several gateway replicas on
  Azure Container Apps.
- **A time-series or analytics store (InfluxDB, ClickHouse):** strong at aggregation, weak at
  the transactional budget checks and relational data (keys, audit log) coming next.
- **Redis only:** fast counters, but not a durable ledger. Redis arrives in phase 3 for rate
  limiting, where losing a counter is acceptable.

## Consequences

- One well-known database for ledger, keys and audit log; managed on Azure as Azure Database
  for PostgreSQL.
- Tests run against a real PostgreSQL (Compose locally, a service container in CI) instead of
  an in-memory stand-in, so they catch dialect-specific bugs.
- Floats never touch money: summing many tiny float costs drifts.
