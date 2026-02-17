# Deploy & seed (Railway)

## Startup: migrations + init once, then Gunicorn

The Dockerfile runs **one** init sequence before starting workers:

1. `flask db upgrade` — apply migrations (creates/updates tables).
2. `python init_db.py` — ensure tables exist and seed if DB is empty (no-op after first run).
3. `gunicorn ...` — start the app. **No** `db.create_all()` or seeding runs inside workers, so startup is fast and you avoid DB locking.

If you override **Start Command** in Railway, use the same sequence so init runs once:

`flask db upgrade && python init_db.py && exec gunicorn -b 0.0.0.0:8080 --timeout 120 run:app`

Set **FLASK_APP** = `run:app` in Railway variables if you override the start command (the Dockerfile sets it via ENV).

---

## If you see: `'$PORT' is not a valid port number`

The Dockerfile runs gunicorn on port **8080** (no `$PORT`). If you still see this error, Railway is likely **overriding** the container start command.

1. In Railway → your **service** → **Settings** (or **Deploy**).
2. Find **Start Command** / **Custom start command**. If it’s set (e.g. `gunicorn -b 0.0.0.0:$PORT run:app`), either:
   - **Clear it** so the image uses the Dockerfile `CMD`, or  
   - Set it to: `flask db upgrade && python init_db.py && exec gunicorn -b 0.0.0.0:8080 --timeout 120 run:app`
3. Redeploy.

---

## If you see: WORKER TIMEOUT when publishing a schedule

The Dockerfile uses **`--timeout 120`**. If workers still timeout at ~30s, Railway may be **overriding** the start command.

1. In Railway → your **service** → **Settings**.
2. Find **Start Command** / **Custom start command**.
3. Either **clear it** (so the Dockerfile CMD is used), or set it to:
   `flask db upgrade && python init_db.py && exec gunicorn -b 0.0.0.0:8080 --timeout 120 run:app`
4. Redeploy.

---

## If deploy fails with "No module named 'psycopg2'"

1. **Use the Dockerfile.** In Railway → your service → **Variables**, set:
   - **Name:** `RAILWAY_DOCKERFILE_PATH`
   - **Value:** `Dockerfile`  
   The Dockerfile explicitly installs `psycopg2-binary`, so the image will have it.

2. **Force a clean build** (so no old cached layer is used):
   - Redeploy and use **“Clear build cache”** / **“Redeploy without cache”** if Railway shows it, or  
   - Add a temporary variable (e.g. `CACHEBUST=1`) and redeploy, then remove it.

3. If you were using **Railpack** (no Dockerfile), add **Variables**: `RAILPACK_DISABLE_CACHES` = `*`, then redeploy.

---

## Seed production with demo data

**Warning:** The seed script **drops all tables** and recreates them, then fills them with demo data. Any existing production data will be lost.

1. **Install Railway CLI** (one-time):
   - https://docs.railway.app/develop/cli  
   - Or: `npm install -g @railway/cli`

2. **Log in and link your project** (from this repo root):
   ```bash
   railway login
   railway link
   ```
   Choose the project, **production** (or desired) environment, and **Postgres** (so the CLI injects DB vars). If `railway run` uses the internal DB URL and can’t connect from your machine, set `DATABASE_URL` to your Postgres **public** URL in Railway variables for the environment, or run the seed from Railway’s shell. The app in production uses the internal URL for speed.

3. **Run the seed against Railway’s database**:
   ```bash
   railway run python -m app.seed
   ```
   This uses Railway’s DB env vars (public URL when running locally), so the seed runs against the live DB.

4. Log in at your site with e.g. **admin@local** / **admin1234**.

---

## Production: Redis (queue + cache) for speed

1. **Add Redis** in Railway and link it to your web service so **REDIS_URL** is set.
2. **Run an RQ worker**: New service, same repo; Start Command: `python run_worker.py`; add **REDIS_URL** to its variables.
3. **Without Redis**: App still runs; email uses a thread and cache is in-memory.

---

If you don’t use the CLI, you can run the same command from a **shell** in the Railway dashboard (if your plan supports it), or add a one-off **Run** step that executes `python -m app.seed` in the same environment as your service.
