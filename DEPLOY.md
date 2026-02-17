# Deploy & seed (Railway)

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
