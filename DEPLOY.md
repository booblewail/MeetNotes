# Deploying MeetNotes

You can deploy MeetNotes either on **$0 Free Tiers (Neon + Render + Vercel)** or on a **Single VPS (Docker Compose + Caddy)** with zero cold starts.

---

## Option 1: $0 Free Tier (Neon + Render + Vercel)

> Note: On Render's free tier, the backend sleeps after 15 minutes of inactivity (~30–50s wake-up on the first request), and ephemeral audio uses in-memory Redis (`REDIS_URL=memory://`) + inline Pass 2 consolidation.

### Step 1 — Create Postgres + `pgvector` on Neon
1. Create a free project at https://neon.tech (choose a region close to your Render region).
2. Copy the **Connection String** (e.g. `postgresql://user:pass@ep-....aws.neon.tech/neondb?sslmode=require`).
   - MeetNotes automatically converts `postgresql://` + `sslmode=require` into `asyncpg`-compatible SSL settings and creates `vector`, `pg_trgm`, and indexes on first boot via `alembic upgrade head`.

### Step 2 — Deploy the Backend on Render (Blueprint)
1. Go to https://dashboard.render.com → **New +** → **Blueprint** and connect `booblewail/MeetNotes`.
2. Render reads [`render.yaml`](./render.yaml) and prompts for:
   - `DATABASE_URL`: paste your Neon connection string
   - `DEEPGRAM_API_KEY`: your Deepgram key
   - `GEMINI_API_KEY`: your Gemini key
   - `CORS_ORIGINS`: leave empty/placeholder for a moment, then update to your Vercel URL once Step 3 finishes
3. Wait for the `meetnotes-api` service to build and verify `https://<your-render-service>.onrender.com/health` returns `{"status":"ok",...}`.

### Step 3 — Deploy the Frontend on Vercel
1. Go to https://vercel.com/new and import `booblewail/MeetNotes`.
2. Set **Root Directory** to `frontend`.
3. Add **Environment Variables**:
   - `NEXT_PUBLIC_API_URL` = `https://<your-render-service>.onrender.com`
   - `NEXT_PUBLIC_WS_URL` = `wss://<your-render-service>.onrender.com`
4. Click **Deploy**.
5. Once Vercel gives you `https://<your-app>.vercel.app`, go back to **Render → Environment** and set `CORS_ORIGINS` = `https://<your-app>.vercel.app` (this triggers a quick redeploy so browser requests & WebSockets are allowed).

*(Optional: Add `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME`, `S3_REGION=auto` on Render if you want persistent Cloudflare R2 audio playback; otherwise keep session retention set to `delete_after_processing`.)*

---

## Option 2: Single VPS (Docker Compose + Caddy)

This deploys MeetNotes onto a single Ubuntu/Debian VM (4 GB RAM recommended) with automatic HTTPS via Caddy, PostgreSQL 16 + `pgvector`, Redis + ARQ worker, Next.js standalone frontend, and Cloudflare R2 (or S3) for audio retention.

### 1. Prerequisites

1. A VPS with Docker Engine + Docker Compose v2 installed and ports `80` and `443` open.
2. Two DNS `A` (and optionally `AAAA`) records pointing to your VPS IP:
   - `app.yourdomain.com` (frontend)
   - `api.yourdomain.com` (FastAPI + WebSocket API)
3. A **Cloudflare R2** (or AWS S3) bucket named `meetnotes-audio` with an API token (`S3_ACCESS_KEY` / `S3_SECRET_KEY`).
   Add this CORS rule on the bucket so browser audio playback works via presigned URLs:

   ```json
   [
     {
       "AllowedOrigins": ["https://app.yourdomain.com"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": ["Range", "Authorization"],
       "ExposeHeaders": ["Accept-Ranges", "Content-Range", "Content-Length"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

### 2. Configure Production Environment

```bash
cp .env.prod.example .env.prod
```

Fill in `.env.prod`:
- Set `APP_DOMAIN`, `API_DOMAIN`, and `ACME_EMAIL`.
- Generate `JWT_SECRET_KEY` and `POSTGRES_PASSWORD` (e.g. `openssl rand -hex 32`).
- Set `DEEPGRAM_API_KEY` and `GEMINI_API_KEY`.
- Set your Cloudflare R2 / S3 `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`.

> Tip: If you want self-hosted MinIO instead of R2/S3 on the VPS, append `--profile local-s3` when running `docker compose`.

### 3. Build, Migrate & Start

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

The `migrate` service automatically runs `alembic upgrade head` once `postgres` is healthy before `backend` and `worker` start.

Verify health:

```bash
curl https://api.yourdomain.com/health
# Expected: {"status":"ok","app":"MeetNotes","stt_mode":"deepgram","llm_mode":"gemini"}
```

View logs & operations:

```bash
make prod-logs
make prod-up    # rebuild/restart after git pull
make prod-down  # stop stack
```
