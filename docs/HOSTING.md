# Hosting the recorded demo

A hosted DeepDive is a **read-only viewer of recorded runs**. It starts no research, calls no model, search or page fetch, and holds no provider key.

**Status.** The project owner reports a deployment on Render at https://deepdive-demo.onrender.com/, built from the publication commit `f7d4a5a` (the
previous version of this repository). Its availability and the version it serves were not verified for this document. **This version has not been
deployed.** It changes how the demo database is provided (built into the image instead of created on a disk at start-up); see "Deploying this version"
and "Rollback".

## What the hosted demo shows, and what it does not

- **It shows the cleared recorded dataset:** two development research runs from 21-22 Oct 2026 (HTTP/2 and HTTP/3 RFCs, a partial report; a private
  company's profit figure, an empty "insufficient evidence" report) and one controlled development check (22 Oct). The content is unchanged in this version.
- **Withheld text cannot be inspected there.** For a page whose redistribution rights are not cleared, the site shows its URL, title, retrieval date and
  content hash, but its text, the quotes taken from it and its search snippets are replaced by a marker. A visitor can follow the link to the original
  page; the site itself cannot show that the quote was in it. Run 1 collected 8 pages, of which 5 are withheld.
- **It does not contain the completed live run of 28 Oct 2026** (the Git merge/rebase question, see the README). All 8 of that run's sources are outside the
  cleared list, so the run is shown only in a locally recorded walkthrough made from a private copy of its database. Nothing from its evidence is in this
  repository or in the demo data.

The cleared list (Wikipedia, IETF RFCs, PostgreSQL documentation) is this project's publication policy for page text, not a legal determination.

## What runs

One process serves everything from one origin:

| Path | What |
|---|---|
| `/` and any deep link (`/#/runs/1/report`, `/runs/1/evidence`) | the production React build (`frontend/dist`); unknown paths return `index.html` so a refresh reaches the client router |
| `/api/*` | the FastAPI application, **read-only** (`PUBLIC_READ_ONLY=1`) |
| `/health` | liveness: the process is up (no database, no provider) |
| `/ready` | readiness: the database opens read-only and holds runs (200 with `{"status": "ready", "runs": N}`, else 503) |

Entry point: `uvicorn backend.public:app --workers 1`. One worker, because the full application runs its background jobs in-process; the demo starts none.

### Read-only is enforced in four independent places
1. `ReadOnlyMiddleware` answers every request that is not GET, HEAD or OPTIONS with **403** `{"detail": {"error": "read_only", ...}}` before routing
   (research, retry, verify, complete, sources, and anything added later). Interactive API docs are not served.
2. SQLite is opened with `mode=ro`; a write raises even if a handler tried one. The demo database is a plain (non-WAL) file, so no `-wal`/`-shm` files appear.
3. No provider client can be built (`get_mesh_client`, `build_services` raise), and the deployment is given no keys.
4. The database file is built while the image is built and is owned by root with mode 0444 in a 0555 directory, so the unprivileged server process
   could not change it even if the three checks above failed. The server never creates, upgrades or changes a database at start-up: it refuses to start if
   the file is missing or older than the code (schema version), instead of changing it.

Provider credentials never reach the browser: the frontend calls only same-origin `/api/*`, and `MESH_API_KEY` / `TAVILY_API_KEY` are not set on the host.
A visitor therefore cannot trigger paid work: there is no write path, no key and no client.

## The demo data, built offline

Committed inputs (no database file is committed):

| File | What |
|---|---|
| `demo/seed.sql.gz` (222 KB) | a sanitized dump of the two recorded research runs, at database schema version 8 |
| `demo/updates/0002-a1a2-v5.json.gz` (4 KB) | update package: run 3, the recorded A1/A2 verifier-attribution-v5 development check (its text-bearing sources are two RFC 9113 passages), schema 8 |
| `demo/demo_info.json` | the labels, notice and per-run notes shown in the UI |

`python -m backend.demo_seed --build DEST` turns them into the served database, before any server starts:

1. load the seed at its own schema version (8) and record it in the update ledger as `0001-base`;
2. apply every update package in id order with the checks a deployment always used: backup first, one transaction, foreign keys enforced, primary-key
   collisions and duplicate imports refused (by ledger id and by run content), integrity re-checked. A package built for the current schema is applied after
   the upgrade; any other schema mismatch refuses the build;
3. upgrade to the current schema (12) with the application's own upgrade, which only adds the newer tables; existing rows are untouched;
4. switch to a plain rollback journal, check integrity, foreign keys and the schema version, and open the result read-only exactly as the server will;
5. rename it into place. The work happens in `DEST.building`; a failure leaves no database and no partial file, and an existing `DEST` is never overwritten.

`python -m backend.demo_seed --migrate-copy SRC DEST` makes a checked, upgraded **copy** of an existing demo database (for example a backup of the previous
version's disk); `SRC` is only read. A copy of a database made the way the previous version made it on its disk (seed, then update 0002 at a start-up)
contains exactly the same rows and ids as a fresh build (tested).

How the seed and packages are made from private run databases, and what they withhold:
- `scripts/build_demo_seed.py` and `scripts/build_demo_update.py` insert rows into a fresh database; they never copy a private file;
- text is kept only for sources on the cleared list. A withheld page keeps its URL, title, retrieval date and content hash; its snapshot, passages, the
  quotes taken from it and its search snippets become a marker;
- any other text value that shares a run of six or more words with a withheld page (or with the four rights-reserved evaluation sources) has that run
  replaced; text of published sources is never altered, so quotes stay verbatim in their passages;
- the scripts re-check the result and refuse to write if anything withheld remains outside published text.

Recorded runs are labelled in the UI (a banner on every page, a note on each run) with their real limitations.

### Adding a recorded run
Build its package with the next id (`scripts/build_demo_update.py`, which applies the same withholding), commit it under `demo/updates/`, add its note to
`demo/demo_info.json`, rebuild the image (the build applies it) and deploy. Only a run whose text-bearing sources are all cleared can be shown with its
evidence; anything else appears as markers.

`python -m backend.demo_update --status | --dry-run | --apply PACKAGE` still work on a database file that no server is using (for example a copy), with the
same backup-first and refusal rules; `tests/test_demo_update.py` covers them.

## Run it locally (production mode)

```bash
cd frontend && npm ci && npm run build && cd ..                  # Node 22.11
uv sync
uv run python -m backend.demo_seed --build data/demo-build/deepdive-demo.db   # a NEW path; an existing file is never overwritten
PUBLIC_READ_ONLY=1 DATABASE_PATH=data/demo-build/deepdive-demo.db \
  uv run uvicorn backend.public:app --host 127.0.0.1 --port 8000 --workers 1
node scripts/smoke_hosted_demo.mjs http://127.0.0.1:8000           # HTTP checks, including every refused write
```

Stop with Ctrl+C (or stop only the process you started; never by program name or port). To rebuild, build to a new path or delete your own local build.

With Docker:
```bash
docker build -t deepdive-demo .
docker run --rm -p 8000:8000 deepdive-demo
```
The image builds the React app in a Node stage, installs the locked Python dependencies with `uv sync --frozen --no-dev`, builds the demo database
(above), runs as an unprivileged user (uid 10001) and listens on `$PORT`. It needs no volume.

## Configuration (no secrets)

| Variable | Value | |
|---|---|---|
| `PUBLIC_READ_ONLY` | `1` | required; `backend.public` refuses to start without it |
| `DATABASE_PATH` | `/app/demo-db/deepdive-demo.db` | the database built into the image (the image's default, and set in `render.yaml`) |
| `PORT` | set by the platform | the image defaults to 8000 |
| `FRONTEND_DIST`, `DEMO_SEED_PATH`, `DEMO_UPDATES_DIR`, `DEMO_INFO_PATH` | defaults inside the image | rarely changed |

Do **not** set `MESH_API_KEY`, `TAVILY_API_KEY` or any model variable on the host; they are not used.

## Storage, restart and backup

- **No persistent storage is needed.** The database is part of the image, so a restart or redeploy serves exactly the same file. Its content comes from the
  committed seed and packages, which are the source of truth.
- **The previous version's disk.** The previous version kept its database on a 1 GB Render disk at `/data` (`/data/deepdive.db`, created from the seed and
  then updated at start-up). This version neither reads nor writes it. `render.yaml` keeps the disk attached so its data survives for a rollback (removing a
  disk from a service deletes its data). It can be removed later, as a separate decision, once rollback is no longer needed.
- **One instance only** while the disk is attached (Render does not allow scaling a service that has a disk).

## Deploying this version (not done; needs the owner's approval)

1. Before changing anything, back up the current disk database from the Render shell:
   `python -m backend.demo_seed --backup /data/backups/deepdive-before-v2-<UTC date>.db` (the current service's `DATABASE_PATH` points at
   `/data/deepdive.db`; the command reads it and refuses to overwrite). Render also keeps daily disk snapshots.
2. Record the currently deployed commit (`f7d4a5a` is reported) and the service's environment.
3. Set `DATABASE_PATH=/app/demo-db/deepdive-demo.db` together with the deploy of this version's commit (a Blueprint sync of `render.yaml`, or the
   dashboard with this commit selected). The two must go together: this version refuses to open the old schema-8 file at `/data/deepdive.db`, and the
   previous image cannot use the new path. Changing an environment variable alone can redeploy the current commit, which would then fail its health check.
4. Deploy with auto-deploy off. A service with a disk has a short downtime during each deploy. Check `/ready` (`{"status": "ready", "runs": 3}`), then run
   `node scripts/smoke_hosted_demo.mjs https://<service>.onrender.com`.

## Rollback

Redeploy the previous commit (`f7d4a5a`) and set `DATABASE_PATH` back to `/data/deepdive.db`. The disk was never touched by this version, so the previous
version finds its database exactly as it left it. If anything went wrong with the disk itself, restore the backup from step 1 or a Render snapshot.

## Health checks
Point the platform's health check at `/ready` (the Blueprint does). `/health` is for a simple liveness probe. Both are unauthenticated and reveal nothing
but status and the run count.

## Provider notes (read 23 Oct 2026; re-check before buying)

Requirements of this version: a container runtime; **one** always-on instance with at least 256 MB RAM; HTTPS; no outbound access; no secrets; no
persistent storage (the previous version needed a 1 GB disk, which stays attached only for a rollback). On 23 Oct 2026 Render listed the Starter web service
at $7/month and disks at $0.25/GB/month; the Free plan (no disk) sleeps after 15 minutes without traffic. Fly.io listed shared-cpu-1x machines from
$2.19/month. Sources: render.com/pricing.md, render.com/docs/disks, render.com/docs/free, docs.fly.io/about/pricing.

## What was and was not checked
See "Checks of this version" in `PUBLIC_RELEASE.md`. Not checked: the Render Blueprint through Render itself, any deployment of this version, behaviour
behind Render's proxy and TLS, and load.
