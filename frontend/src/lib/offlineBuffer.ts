import { openDB, type DBSchema, type IDBPDatabase } from "idb";

export interface BufferedAudioChunk {
  seq?: number;
  sessionId: string;
  offsetMs: number;
  pcmBuffer: ArrayBuffer;
  createdAt: number;
}

interface MeetNotesOfflineDB extends DBSchema {
  audio_chunks: {
    key: number;
    value: BufferedAudioChunk;
    indexes: { "by-session": string };
  };
}

const DB_NAME = "meetnotes_offline_buffer";
const STORE_NAME = "audio_chunks";
const MAX_CHUNKS_PER_SESSION = 6000; // ~10 minutes at 100ms per chunk

// In-memory ring fallback for SSR or Node unit test environments without IndexedDB
let memorySeq = 1;
const memoryStore = new Map<number, BufferedAudioChunk>();

function hasIndexedDB(): boolean {
  return typeof indexedDB !== "undefined";
}

let dbPromise: Promise<IDBPDatabase<MeetNotesOfflineDB>> | null = null;

async function getDb(): Promise<IDBPDatabase<MeetNotesOfflineDB>> {
  if (!dbPromise) {
    dbPromise = openDB<MeetNotesOfflineDB>(DB_NAME, 1, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME, {
            keyPath: "seq",
            autoIncrement: true,
          });
          store.createIndex("by-session", "sessionId", { unique: false });
        }
      },
    });
  }
  return dbPromise;
}

export async function enqueueAudioChunk(
  sessionId: string,
  offsetMs: number,
  pcmBuffer: ArrayBuffer
): Promise<number> {
  const entry: BufferedAudioChunk = {
    sessionId,
    offsetMs,
    pcmBuffer,
    createdAt: Date.now(),
  };

  if (!hasIndexedDB()) {
    const seq = memorySeq++;
    memoryStore.set(seq, { ...entry, seq });
    // Enforce ring-buffer cap
    const sessionKeys = Array.from(memoryStore.values())
      .filter((c) => c.sessionId === sessionId)
      .sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    while (sessionKeys.length > MAX_CHUNKS_PER_SESSION) {
      const oldest = sessionKeys.shift();
      if (oldest?.seq !== undefined) memoryStore.delete(oldest.seq);
    }
    return seq;
  }

  const db = await getDb();
  const tx = db.transaction(STORE_NAME, "readwrite");
  const seq = await tx.store.add(entry);
  const idx = tx.store.index("by-session");
  const keys = await idx.getAllKeys(sessionId);
  if (keys.length > MAX_CHUNKS_PER_SESSION) {
    const excess = keys.slice(0, keys.length - MAX_CHUNKS_PER_SESSION);
    for (const oldKey of excess) {
      await tx.store.delete(oldKey);
    }
  }
  await tx.done;
  return Number(seq);
}

export async function listSessionChunks(
  sessionId: string
): Promise<BufferedAudioChunk[]> {
  if (!hasIndexedDB()) {
    return Array.from(memoryStore.values())
      .filter((c) => c.sessionId === sessionId)
      .sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  }
  const db = await getDb();
  const items = await db.getAllFromIndex(STORE_NAME, "by-session", sessionId);
  return items.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
}

export async function removeChunkBySeq(seq: number): Promise<void> {
  if (!hasIndexedDB()) {
    memoryStore.delete(seq);
    return;
  }
  const db = await getDb();
  await db.delete(STORE_NAME, seq);
}

export async function drainSessionChunks(
  sessionId: string,
  sender: (chunk: BufferedAudioChunk) => boolean
): Promise<number> {
  const chunks = await listSessionChunks(sessionId);
  let sent = 0;
  for (const chunk of chunks) {
    const ok = sender(chunk);
    if (!ok) break;
    if (chunk.seq !== undefined) {
      await removeChunkBySeq(chunk.seq);
    }
    sent += 1;
  }
  return sent;
}

export async function purgeBufferedWindow(
  sessionId: string,
  fromMs: number,
  toMs: number
): Promise<number> {
  const chunks = await listSessionChunks(sessionId);
  let removed = 0;
  for (const chunk of chunks) {
    if (
      chunk.offsetMs >= fromMs &&
      chunk.offsetMs <= toMs &&
      chunk.seq !== undefined
    ) {
      await removeChunkBySeq(chunk.seq);
      removed += 1;
    }
  }
  return removed;
}

export async function countBufferedChunks(sessionId: string): Promise<number> {
  if (!hasIndexedDB()) {
    let count = 0;
    for (const c of memoryStore.values()) {
      if (c.sessionId === sessionId) count += 1;
    }
    return count;
  }
  const db = await getDb();
  return db.countFromIndex(STORE_NAME, "by-session", sessionId);
}

export function resetMemoryOfflineBuffer(): void {
  memoryStore.clear();
  memorySeq = 1;
}
