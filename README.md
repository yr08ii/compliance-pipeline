# compliance monitoring platform

Local, self-contained compliance transaction-monitoring platform.

## Run locally

1. Start Postgres. Either a native local instance (`brew services start postgresql@16`, with a `compliance` role/database) or `make db` (needs Docker Engine/Podman). Point `DATABASE_URL` at whichever you use.
2. Apply migrations: `make backend-migrate`
3. Seed data and run the pipeline: `make seed-run`
4. Build the frontend: `make frontend`
5. Serve everything: `make serve`
6. Open http://localhost:8000 to view the alert queue, then click Review to see the divergence panel.

## Once-off commands

These are run by hand, not on a schedule.

```bash
compliance-ingest <file.json>   # load a delivered payload (idempotent)
compliance-backfill --days 90   # fit baselines over history, once, at launch
```

Backfilled baselines are provisional: they are fitted on history nobody has
reviewed. Run in shadow mode and lean on peer comparison until dispositions
accumulate.

See `docs/superpowers/specs/2026-07-20-compliance-platform-design.md` for the full design.