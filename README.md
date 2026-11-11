# jobagent

A LangGraph multi-agent system that finds remote/freelance engineering roles, screens them
against a fit bar, tailors a resume that **cannot contain a claim absent from the master
profile**, applies through deterministic ATS adapters where possible, and escalates to a
human everywhere else.

Status: **M1** (skeleton + tracker) implemented. M2–M7 are typed stubs — the whole graph
renders today, but only the tracker lane executes.

```
supervisor
 ├─ discovery   [M2]  portal/API fan-out → normalize → dedupe (fingerprint, then cosine >0.92)
 ├─ screener    [M3]  hard filters → LLM fit score → threshold
 ├─ tailor      [M4]  extract requirements → select evidence → render → VERIFY CLAIMS
 ├─ apply       [M5/6] route → {ats | portal_form | linkedin_easy | browser_agent | manual}
 ├─ tracker     [M1]  persist → status machine → CSV export      ← the implemented one
 └─ inbox       [M7]  poll Gmail → classify → escalate interviews
```

## Quickstart

```bash
make setup          # uv pins python 3.12 and installs everything
make up             # Postgres + pgvector on host port 5433 (needs Docker Desktop running)
make migrate        # alembic upgrade head
make doctor         # report every unmet dependency before you hit it mid-run
make test           # 211 unit tests — green even with the Docker daemon down
make test-all       # 350 tests, incl. 139 against real Postgres
make graph          # render the full graph as Mermaid, no database needed
make demo           # M1 end-to-end: seed 4 postings → dedupe → persist → CSV. No network.
make run            # full pipeline; stops at the M2 stub for now. DRY-RUN. See below.
```

`make demo` is the honest end-to-end proof of M1. It seeds four postings in which two are
the same job cross-listed on different boards (different legal suffix, a `(m/f/d) [Remote]`
title tag, differently-punctuated location) and two are same-company-same-title but
`Remote (US only)` vs `Remote - India`. Four in, three out: the cross-listing collapses,
and the two regions do **not** — because a US-only role and an India role are not
interchangeable, and merging them would bury the one you are eligible for.

## Safety

Four independent layers stop this program from submitting an application you didn't intend.

1. **Dry-run is the default.** There is no `--dry-run` flag. There is a `--live` flag.
2. **Two keys.** `--live` is honoured only when `JOBAGENT_ALLOW_LIVE=1` is also set in the
   environment. Neither the flag alone nor the env var alone is sufficient.
3. **A choke point, not a check.** No node inspects the dry-run flag. Every irreversible
   action goes through `safety.submit_gate()`, and adapters are handed a `SafePage` that is
   the only route to a click.
4. **A mechanical backstop at the I/O boundary.** In dry-run the browser context aborts
   every non-GET request to the job's origin, and `SafePage.click()` refuses submit-shaped
   selectors after dumping a screenshot and the DOM. Code that has never heard of the flag
   still cannot POST.

Plus, in the database: a partial unique index makes a second *live* submit for the same job
a `UniqueViolation`, regardless of retries, forgotten checks, or a resumed run.

## Ground truth

`jobagent/profile/master_profile.yaml` is the **only** source of resume claims. M4's
`verify_claims` node extracts every factual assertion from a generated resume and checks it
against that file; anything unsupported fails the job into the manual queue rather than
being submitted. It fails closed and never soft-warns.

## Layout

| Path | |
|---|---|
| `jobagent/graph/state.py` | Pydantic domain models, `RunState`/`JobState`, reducers |
| `jobagent/normalize.py` | company/title/location normalization behind the fingerprint |
| `jobagent/store/models.py` | SQLAlchemy ORM — the single source of schema truth |
| `jobagent/store/transitions.py` | legal status transitions, as data |
| `jobagent/safety.py` | the dry-run choke point |
| `jobagent/adapters/registry.py` | add a job board = one file, no graph change |
