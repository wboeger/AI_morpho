# Deploying GyroMorpho v2 to Railway

The app runs on Railway; code comes from GitHub (`wboeger/AI_morpho`). The
database and ~80 MB of structure images are **not** in git — they live on a
persistent Railway **Volume**, seeded once from a **GitHub Release** asset.

```
GitHub repo  ──code──▶  Railway service ──reads/writes──▶  Volume  (/data)
GitHub Release (data.zip) ──first-boot download──▶  Volume
Colleagues ──▶  https://<your-app>.up.railway.app   (the web UI; no Drive needed)
```

## One-time setup

### 1. Upload the seed archive to a GitHub Release
`data.zip` (db.sqlite + uploads/) is built locally and gitignored. Create it
when data changes:

```bash
python - <<'PY'
import sqlite3; c=sqlite3.connect('data/db.sqlite'); c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.close()
PY
( cd data && zip -r ../data.zip db.sqlite uploads -x "*.DS_Store" )
```

Then on GitHub: **Releases → Draft a new release** (tag e.g. `data-v1`) →
drag `data.zip` into *Attach binaries* → **Publish**. Copy the asset URL:

```
https://github.com/wboeger/AI_morpho/releases/download/data-v1/data.zip
```

### 2. Create the Railway service
- Railway → **New Project → Deploy from GitHub repo → wboeger/AI_morpho**.
- It auto-detects Python (Nixpacks) and uses `Procfile` / `railway.json`.

### 3. Add a Volume
- Service → **Variables/Settings → Volumes → New Volume**.
- Mount path: **`/data`**. (Railway auto-sets `RAILWAY_VOLUME_MOUNT_PATH=/data`,
  which `config.py` reads — DB + uploads go there automatically.)

### 4. Set environment variables
| Variable         | Value                                                        |
|------------------|-------------------------------------------------------------|
| `SECRET_KEY`     | a long random string (Flask session signing)                |
| `DATA_SEED_URL`  | the release asset URL from step 1                           |
| `ENABLE_BACKUPS` | `0`  (don't fill the volume with hourly image copies)       |
| `ADMIN_USERNAME` | `WalterB` (bootstrap admin if the DB has none — safety net) |
| `ADMIN_PASSWORD` | the admin password                                          |
| `GALAXY_API_KEY` | your usegalaxy.eu key (only if using phylogeny features)    |

**Access control:** the app is fully behind login — every page and image
requires authentication, and there is **no public sign-up**. Only the admin can
create accounts (Dashboard → *Register*, admin-only). The seeded database
already contains the admin user; `ADMIN_USERNAME`/`ADMIN_PASSWORD` only kick in
if the database starts empty (no seed), creating the admin automatically.
Colleagues get a username/password from you and sign in at the app URL.

### 5. Deploy
First boot: volume is empty → app downloads `data.zip` from `DATA_SEED_URL`,
unzips into `/data`, then starts. Subsequent boots skip the download (db.sqlite
already present — live data is never overwritten).

Generate a public domain: Service → **Settings → Networking → Generate Domain**.
Share that URL with colleagues.

## Updating the data later
The volume is the live master once running. To push a new baseline (e.g. fresh
import done locally), rebuild `data.zip`, upload as a **new** release asset,
update `DATA_SEED_URL`, and either wipe the volume or seed manually.

## Notes
- **SQLite + 1 worker / 8 threads** (`Procfile`) — correct for a small team.
  Heavy concurrent writing would warrant Postgres; not needed here.
- `torch`/`torchvision`/`opencv` are excluded from `requirements.txt` (U-Net
  training only) to keep the build small.
- Local dev is unchanged: `python run.py` still uses `./data`.
