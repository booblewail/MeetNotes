# Deploying MeetNotes to a Single VPS (Docker Compose + Caddy)

This guide deploys MeetNotes onto a single Ubuntu/Debian VM (4 GB RAM recommended) with automatic HTTPS via Caddy, PostgreSQL 16 + `pgvector`, Redis + ARQ worker, Next.js standalone frontend, and Cloudflare R2 (or S3) for audio retention.

---

## 1. Prerequisites

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

---

## 2. Configure Production Environment

```bash
cp .env.prod.example .env.prod
```

Fill in `.env.prod`:
- Set `APP_DOMAIN`, `API_DOMAIN`, and `ACME_EMAIL`.
- Generate `JWT_SECRET_KEY` and `POSTGRES_PASSWORD` (e.g. `openssl rand -hex 32`).
- Set `DEEPGRAM_API_KEY` and `GEMINI_API_KEY`.
- Set your Cloudflare R2 / S3 `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`.

> Tip: If you want self-hosted MinIO instead of R2/S3 on the VPS, append `--profile local-s3` when running `docker compose` (and route an S3 subdomain or leave `S3_PUBLIC_ENDPOINT_URL` proxied cleanly).

---

## 3. Build, Migrate & Start

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

The `migrate` service automatically runs `alembic upgrade head` once `postgres` is healthy before `backend` and `worker` start.

Verify health:

```bash
curl https://api.yourdomain.com/health
# Expected: {"status":"ok","app":"MeetNotes","stt_mode":"deepgram","llm_mode":"gemini"}
```

View logs:

```bash
make prod-logs
```

---

## 4. Operations

- **Deploy updates**:
  ```bash
  git pull
  make prod-up
  ```
- **Back up Postgres**:
  ```bash
  docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U meetnotes meetnotes | gzip > backup-$(date +%F).sql.gz
  ```
- **Stop stack**:
  ```bash
  make prod-down
  ```
