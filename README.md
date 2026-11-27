# MeetNotes

**MeetNotes** is a real-time AI meeting intelligence workspace built as a two-app monorepo (`backend/` + `frontend/`) orchestrated by Docker Compose.

## Architecture Overview

- **Backend (`backend/`)**: FastAPI + SQLAlchemy 2.0 (async `asyncpg`) + PostgreSQL (`pgvector` & `pg_trgm`) + Redis Pub/Sub + ARQ background worker + MinIO S3-compatible audio storage.
- **Frontend (`frontend/`)**: Next.js App Router + Tailwind CSS + Zustand + Web Audio `AudioWorklet` + Silero VAD (`@ricky0123/vad-web`) + IndexedDB (`idb`) offline ring-buffer.
- **Two-Pass AI Pipeline**:
  - **Pass 1 (Live)**: Sliding-window trigger (`>= 100` pending words or `>= 30s` elapsed) streams structured JSON patches (`notes.patch`) via Gemini Flash (`gemini-2.5-flash`) over Redis Pub/Sub.
  - **Pass 2 (Post-Meeting Consolidation)**: ARQ worker (`finalize_session_task`) synthesizes the final document with Gemini Pro (`gemini-2.5-pro`), strictly preserves any user-locked blocks (`is_user_edited = True`), generates 768-dim section & summary embeddings, and persists/purges audio per retention policy.
- **Zero-Key Dev / Demo Mode**: Leaving `DEEPGRAM_API_KEY` or `GEMINI_API_KEY` as placeholder (`your_deepgram_key` / `your_gemini_key` / `mock`) automatically activates deterministic in-process mock STT/LLM/embedding modes and a **Demo Conversation** simulator in the Recording Bar so every workflow works locally without external API keys.

## Quick Start (Docker Compose)

1. Review `.env` (already seeded for local development; replace `DEEPGRAM_API_KEY` and `GEMINI_API_KEY` to enable live cloud STT & Gemini):
   ```bash
   cp .env.example .env
   ```
2. Start all 6 services (`postgres`, `redis`, `minio`, `backend`, `worker`, `frontend`):
   ```bash
   make up
   ```
3. Run database migrations:
   ```bash
   make migrate
   ```
4. Open [http://localhost:3000](http://localhost:3000) (API docs at [http://localhost:8000/docs](http://localhost:8000/docs), MinIO Console at [http://localhost:9001](http://localhost:9001)).

## Running Tests

```bash
# Inside Docker
make test

# Or locally with backend/.venv
backend/.venv/bin/pytest backend/tests -v
```

## Keyboard Shortcuts (Workspace)

- `Cmd/Ctrl + Shift + P`: Pin high-priority moment at the current recording timestamp.
- `Enter` inside Scratchpad: Add manual user-locked note (`is_user_edited = True`).
