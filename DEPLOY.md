# Deploy & seed (Railway)

## If you see: `'$PORT' is not a valid port number`

The Dockerfile runs gunicorn on port **8080** (no `$PORT`). If you still see this error, Railway is likely **overriding** the container start command.

1. In Railway → your **service** → **Settings** (or **Deploy**).
2. Find **Start Command** / **Custom start command**. If it’s set (e.g. `gunicorn -b 0.0.0.0:$PORT run:app`), either:
   - **Clear it** so the image uses the Dockerfile `CMD`, or  
   - Set it to: `gunicorn -b 0.0.0.0:8080 run:app`
3. Redeploy.

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
   Choose the project and environment (e.g. production) when prompted.

3. **Run the seed against Railway’s database**:
   ```bash
   railway run python -m app.seed
   ```
   This uses Railway’s `DATABASE_URL` (and other env vars), so the seed runs against the live DB.

4. Log in at your site with e.g. **admin@local** / **admin1234**.

---

If you don’t use the CLI, you can run the same command from a **shell** in the Railway dashboard (if your plan supports it), or add a one-off **Run** step that executes `python -m app.seed` in the same environment as your service.
