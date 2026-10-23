# Hosting the recorded demo

A hosted DeepDive is a **read-only viewer of recorded runs**. It starts no research, calls no model, search or page fetch, and holds no provider key. Nothing in this repository has been deployed; this page says how to run it and what it needs.

## What runs

One process serves everything from one origin:

| Path | What |
|---|---|
| `/` and any deep link (`/#/runs/1/report`, `/runs/1/evidence`) | the production React build (`frontend/dist`); unknown paths return `index.html` so a refresh reaches the client router |
| `/api/*` | the FastAPI application, **read-only** (`PUBLIC_READ_ONLY=1`) |
| `/health` | liveness: the process is up (no database, no provider) |
| `/ready` | readiness: the database opens read-only and holds runs (200, else 503) |

Entry point: `uvicorn backend.public:app --workers 1`. One worker, because the full application runs its background jobs in-process; the demo starts none, but the setting keeps the process model identical.

### Read-only is enforced in three independent places
1. `ReadOnlyMiddleware` answers every request that is not GET, HEAD or OPTIONS with **403** `{"detail": {"error": "read_only", ...}}` before routing (research, retry, verify, complete, sources, and anything added later). Interactive API docs are not served.
2. SQLite is opened with `mode=ro`; a write raises even if a handler tried one. The demo database is a plain (non-WAL) file, so no `-wal`/`-shm` files appear.
3. No provider client can be built (`get_mesh_client`, `build_services` raise), and the deployment is given no keys.

Provider credentials never reach the browser: the frontend calls only same-origin `/api/*`, and `MESH_API_KEY` / `TAVILY_API_KEY` are simply not set on the host.

### The demo data
`demo/seed.sql.gz` (222 KB, committed) is a sanitized dump of the two recorded research runs (the development check is added by an update, below) (run 1: HTTP/2 and HTTP/3 RFCs, a partial report; run 2: a private company's profit figure, an empty "insufficient evidence" report). `demo/demo_info.json` holds the labels shown in the UI.
`scripts/build_demo_seed.py` makes the seed from a **private** run database; it does not copy that file. It creates a fresh database and inserts rows, applying the same withholding rules as the public evaluation export:

- text is kept only for sources on an allow-list (Wikipedia, IETF RFCs, PostgreSQL documentation; the same sources whose text the public dataset keeps). Run 1 collected 8 pages: 3 are published, **5 are withheld** (the page keeps its URL, title, retrieval date and content hash; its snapshot, passages, the quotes taken from it and its search snippets become a marker);
- any other text value that shares a run of six or more words with a withheld page (or with the four rights-reserved evaluation sources) has that run replaced;
- text of published sources is never altered, so quotes stay verbatim in their passages;
- the script re-checks the result and refuses to write the seed if anything withheld remains outside published text.

Recorded runs are labelled in the UI (banner on every page, a note on each run) with their real limitations: run 1's report is partial and was assembled before answer-relevance judging existed ("not judged"), run 2's is empty.
Checked on the running server: all 44 GET endpoints of both runs (reports, claims, passages, sources, searches, events, usage) contain no phrase from a withheld page.

## Updating the persistent demo database (versioned, backed up, duplicate-refusing)

The deployed database lives on the volume and is **never rebuilt from the seed**, so changing `demo/seed.sql.gz` does not change a running deployment. New recorded runs reach it through an **update package** in `demo/updates/` (`<id>.json.gz`, applied in id order):

| Package | Adds |
|---|---|
| `0002-a1a2-v5` | run 3, the recorded A1/A2 verifier-attribution-v5 development check: one claim checked twice against two subquestions (direct answer for the current revision, related context and an open requirement for the original publication). Labelled "Controlled development check (not a research run)"; its text-bearing sources are the two RFC 9113 passages. |

`scripts/build_demo_update.py` builds a package from a private run database. It applies the same sanitization as the seed, shifts every id past the base seed's (so existing runs keep their ids and nothing collides), rewrites passage ids and id-valued JSON keys, and refuses to build if an id-like key is unknown or anything withheld remains.
`backend/demo_update.py` applies it, at **start-up** of `backend.public` (before the read-only server begins serving; there is no HTTP path to trigger it) or by hand:

```bash
python -m backend.demo_update --status                     # the ledger: which updates are applied
python -m backend.demo_update --dry-run                    # check every package, write nothing
python -m backend.demo_update --apply demo/updates/0002-a1a2-v5.json.gz --backup-dir /data/backups
```

What it guarantees, each covered by `tests/test_demo_update.py`:
1. **Backup first:** a consistent SQLite backup (`/data/backups/deepdive-before-<id>-<UTC>.db`, never overwritten) is written and checked (integrity, same runs) before any change.
2. **One transaction:** foreign keys enforced, primary-key collisions refused, any failure rolls everything back. A database from before the ledger existed is recognised as the base seed and gets a ledger.
3. **Existing runs are untouched:** every row present before is present afterwards, byte for byte.
4. **Duplicates are refused**, twice over: by the ledger (`demo_updates`, id already applied) and by content (a run with the same question and creation time exists, so a lost ledger or a restored older backup cannot import it again). A package built for a different database (other base runs, other schema version) is refused. The restart that follows every deploy finds the update already applied and does nothing.
5. **A refused update never takes the demo down:** the server keeps serving what it has, logs the reason, and `/ready` carries it in `update_error`; `/api/demo` lists only runs that exist, so no link to a missing run appears.

A new volume is seeded and then updated the same way, and reaches identical data and ids (tested). To publish another run: build its package with the next id, commit it under `demo/updates/`, add its note to `demo/demo_info.json`, and redeploy; the start-up applies it once.
Rollback: stop the service, restore the backup file over `deepdive.db` (the ledger then no longer lists the update), and remove the package from the image before the next start, or it will be applied again.

## Run it locally (production mode)

```bash
cd frontend && npm ci && npm run build && cd ..          # Node 22.11
uv sync
PUBLIC_READ_ONLY=1 DATABASE_PATH=./data/demo-volume/deepdive.db \
  uv run uvicorn backend.public:app --host 127.0.0.1 --port 8000 --workers 1
node scripts/smoke_hosted_demo.mjs http://127.0.0.1:8000    # 24 HTTP checks, including every refused write
```

On first start `backend.demo_seed` creates the database from `demo/seed.sql.gz` if the file does not exist; an existing database is **never touched**. Stop with Ctrl+C (or stop only the process you started; never by program name or port).

With Docker (built and run locally, see "What was and was not checked"):
```bash
docker build -t deepdive-demo .
docker run --rm -p 8000:8000 -v deepdive-data:/data deepdive-demo
```
The image builds the React app in a Node stage, installs the locked Python dependencies with `uv sync --frozen --no-dev`, runs as an unprivileged user (the entrypoint fixes ownership of a root-owned mounted volume, then drops privileges), and listens on `$PORT`.

## Configuration (no secrets)

| Variable | Value | |
|---|---|---|
| `PUBLIC_READ_ONLY` | `1` | required; `backend.public` refuses to start without it |
| `DATABASE_PATH` | `/data/deepdive.db` | on the persistent volume |
| `PORT` | set by the platform | the image defaults to 8000 |
| `FRONTEND_DIST`, `DEMO_SEED_PATH`, `DEMO_INFO_PATH` | defaults inside the image | rarely changed |

Do **not** set `MESH_API_KEY`, `TAVILY_API_KEY` or any model variable on the host; they are not used.

## Volume, restart and backup

- **Persistent storage:** SQLite lives on a volume mounted at `/data` (1 GB is ample: the database is about 1.2 MB). The database is created once from the seed and then left alone, so it survives restarts and redeploys. Checked locally: after a stop and restart on the same volume the database file was byte-identical, the API output identical, and a row added to the volume's database while the server was down was still there.
- **One instance only.** A SQLite file on a volume is for one instance; Render does not allow scaling a service that has a disk.
- **Backup:** `python -m backend.demo_seed --backup /data/backups/deepdive-<date>.db` writes a consistent copy (SQLite online backup) and refuses to overwrite. The seed in git is the source of truth for the demo content, so the volume holds nothing that cannot be rebuilt: to restore or update, `python -m backend.demo_seed --reset` moves the current file aside (`*.replaced-<time>`) and recreates it from the seed.
- **Platform snapshots:** Render snapshots a disk automatically once every 24 hours and keeps each for at least seven days; restores are all-or-nothing (Render docs, read 23 Oct 2026).

## Health checks
Point the platform's health check at `/ready` (the Blueprint does). `/health` is for a simple liveness probe. Both are unauthenticated and reveal nothing but status and the run count.

## Provider requirements and current prices (read 23 Oct 2026; re-check before buying)

Requirements: a container (or Python 3.12 + Node build) runtime; **one** always-on instance with ≥ 256 MB RAM (the server measured about 96 MB resident after the full check); a persistent volume of ≥ 1 GB; HTTPS; no outbound access needed at all; no secrets.

| | Render | Fly.io |
|---|---|---|
| Compute | Starter web service, **$7/month** (512 MB, under 1 CPU); Free ($0, 512 MB) exists but cannot attach a disk, spins down after 15 minutes without traffic and has an ephemeral filesystem | shared-cpu-1x **$2.19/month (256 MB)** or **$3.69/month (512 MB)**, billed per second while running |
| Persistent storage | disk **$0.25/GB/month**; paid services only; single instance; a few seconds of downtime per deploy; daily automatic snapshots kept ≥ 7 days | volume **$0.15/GB/month**; snapshots $0.08/GB/month, first 10 GB free |
| Free tier | Free web service only (no disk) | none for new organizations (a trial of up to 2 hours of Machine time or 7 days); a card is required to continue |
| Estimate for this demo | about **$7.25/month** (Starter + 1 GB) plus any workspace plan fee (not verified here; the Hobby workspace lists 5 GB of outbound traffic included, then $0.15/GB) | about **$2.34 to $3.84/month** (machine + 1 GB volume) |
| What is prepared | `render.yaml` (checked against Render's published Blueprint JSON schema; `render blueprints validate` could not run because it needs a logged-in CLI) | nothing: a `fly.toml` and the Fly CLI would be needed |

Sources: render.com/pricing.md, render.com/docs/disks, render.com/docs/free, render.com/docs/web-services, docs.fly.io/about/pricing.

**Recommendation:** Render Starter with a 1 GB disk. It costs a few dollars more than Fly.io but needs no CLI or extra file: connect the repository, create the Blueprint, and it builds from the Dockerfile with the disk, health check and environment already set, with automatic daily disk snapshots. Fly.io is the cheaper option if the cost matters more than setup effort. The Free Render plan would also serve the demo (the seed is rebuilt on every start), but it has no persistent storage and sleeps after 15 minutes, so it does not meet the persistence requirement and gives a slow first load.

### Render steps (when you decide to deploy; none done)
1. Push the publication branch (see PUBLIC_RELEASE.md), then in Render choose New → Blueprint and select the repository; `render.yaml` defines the service, the 1 GB disk at `/data` and the two environment variables. Auto-deploy is off.
2. After the first deploy: open the service URL, check `/ready`, then run `node scripts/smoke_hosted_demo.mjs https://<service>.onrender.com` from your machine.
3. Add no secrets. Leave scaling at one instance.

## What was and was not checked
Checked locally (production build, one worker, owned process): startup on an empty volume, 24 HTTP checks (health, readiness, deep links, API, refused writes), citations and refresh in a real browser (Edge) with the page's Content-Security-Policy active and no console errors, a 390 px layout, axe (WCAG 2 A/AA) without violations, persistence across a restart, and the withheld-text crawl. Python tests: `tests/test_public_demo.py` (9). Frontend: `src/test/demo.test.tsx` (4).
Checked in Docker (Docker Desktop 29.7.2, Linux engine; 23 Oct 2026): the image builds (204 MB; a first build kept a 459 MB uv cache, fixed with `--no-cache`); the container starts, `/ready` returns 200 with 2 runs and its health check turns healthy; the 24 smoke checks pass; the server process runs as uid 10001 (not root); on a fresh volume and on a volume owned by root (the Render disk case) the entrypoint fixes ownership of `/data`, drops privileges and creates the database; a row added to the volume survives a container stop and start; the 44 GET endpoints contain no withheld text. The image holds no `.env`, no `.db` file, no credential pattern, no key or model variable (its environment is `PUBLIC_READ_ONLY`, `DATABASE_PATH`, `PORT` and Python settings), and `/data` is empty at build time.
Not checked: the Render Blueprint through Render itself, any real deployment, behaviour behind Render's proxy and TLS, and load.
