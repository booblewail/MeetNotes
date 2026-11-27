import type {
  ActionItem,
  AgendaSection,
  AudioRetentionPolicy,
  AudioUrlResponse,
  BlockType,
  ExportFormat,
  ExportResponse,
  GlossaryTerm,
  NoteBlock,
  PinnedMoment,
  SearchResponse,
  SessionDetail,
  SessionSummary,
  Speaker,
  TokenResponse,
  User,
} from "./types.ts";

const TOKEN_STORAGE_KEY = "meetnotes_token";

export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
}

export function getWsBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) {
    return process.env.NEXT_PUBLIC_WS_URL;
  }
  const api = getApiBaseUrl();
  return api.replace(/^http/, "ws");
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // Ignore storage quota errors
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(options.headers || {});
  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") {
        detail = data.detail;
      }
    } catch {
      // ignore non-JSON body
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  // Auth
  register(payload: { email: string; password: string; full_name: string }) {
    return request<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  login(payload: { email: string; password: string }) {
    return request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getMe() {
    return request<User>("/auth/me");
  },
  updateMe(payload: {
    full_name?: string;
    default_audio_retention?: AudioRetentionPolicy;
  }) {
    return request<User>("/auth/me", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  // Sessions
  listSessions() {
    return request<SessionSummary[]>("/sessions");
  },
  createSession(payload: {
    title?: string;
    audio_retention?: AudioRetentionPolicy;
  }) {
    return request<SessionDetail>("/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getSession(sessionId: string) {
    return request<SessionDetail>(`/sessions/${sessionId}`);
  },
  updateSession(
    sessionId: string,
    payload: {
      title?: string;
      executive_summary?: string;
      audio_retention?: AudioRetentionPolicy;
    }
  ) {
    return request<SessionDetail>(`/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteSession(sessionId: string) {
    return request<void>(`/sessions/${sessionId}`, { method: "DELETE" });
  },
  renameSpeaker(sessionId: string, speakerId: string, displayName: string) {
    return request<Speaker>(
      `/sessions/${sessionId}/speakers/${speakerId}`,
      {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      }
    );
  },
  pinMoment(sessionId: string, offsetMs: number, label?: string) {
    return request<PinnedMoment>(`/sessions/${sessionId}/pin-moment`, {
      method: "POST",
      body: JSON.stringify({ offset_ms: offsetMs, label }),
    });
  },
  redactWindow(
    sessionId: string,
    windowMs = 120_000,
    currentOffsetMs?: number
  ) {
    return request<{
      redacted_segment_ids: string[];
      deleted_block_ids: string[];
      from_ms: number;
      to_ms: number;
    }>(`/sessions/${sessionId}/redact-window`, {
      method: "POST",
      body: JSON.stringify({
        window_ms: windowMs,
        current_offset_ms: currentOffsetMs,
      }),
    });
  },
  stopSession(sessionId: string, runInline = false) {
    const q = runInline ? "?run_inline=true" : "";
    return request<SessionDetail>(`/sessions/${sessionId}/stop${q}`, {
      method: "POST",
    });
  },
  getAudioUrl(sessionId: string) {
    const token = getStoredToken();
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    return request<AudioUrlResponse>(`/sessions/${sessionId}/audio-url${qs}`);
  },
  exportSession(sessionId: string, format: ExportFormat) {
    return request<ExportResponse>(`/sessions/${sessionId}/export`, {
      method: "POST",
      body: JSON.stringify({ format }),
    });
  },

  // Blocks, Sections, Action Items
  createBlock(
    sessionId: string,
    payload: {
      section_id?: string | null;
      block_type: BlockType;
      content: string;
      assignee?: string;
      due_date_text?: string;
    }
  ) {
    return request<NoteBlock>(`/sessions/${sessionId}/blocks`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateBlock(
    blockId: string,
    payload: {
      content?: string;
      block_type?: BlockType;
      section_id?: string | null;
      assignee?: string | null;
      due_date_text?: string | null;
      completed?: boolean;
    }
  ) {
    return request<NoteBlock>(`/blocks/${blockId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteBlock(blockId: string) {
    return request<void>(`/blocks/${blockId}`, { method: "DELETE" });
  },
  updateSection(sectionId: string, title: string) {
    return request<AgendaSection>(`/sections/${sectionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },
  updateActionItem(
    actionId: string,
    payload: {
      description?: string;
      assignee?: string | null;
      due_date_text?: string | null;
      completed?: boolean;
    }
  ) {
    return request<ActionItem>(`/action-items/${actionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  // Glossary
  listGlossary() {
    return request<GlossaryTerm[]>("/glossary");
  },
  addGlossaryTerm(term: string, boost = 1.5) {
    return request<GlossaryTerm>("/glossary", {
      method: "POST",
      body: JSON.stringify({ term, boost }),
    });
  },
  deleteGlossaryTerm(termId: string) {
    return request<void>(`/glossary/${termId}`, { method: "DELETE" });
  },

  // Hybrid Search
  search(query: string, limit = 12) {
    const qs = new URLSearchParams({ q: query, limit: String(limit) });
    return request<SearchResponse>(`/search?${qs.toString()}`);
  },
};
