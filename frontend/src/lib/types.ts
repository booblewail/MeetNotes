export type AudioRetentionPolicy =
  | "delete_after_processing"
  | "retain_7d"
  | "retain_30d";

export type SessionStatus =
  | "created"
  | "recording"
  | "paused"
  | "processing"
  | "completed"
  | "failed";

export type BlockType =
  | "bullet"
  | "decision"
  | "action_item"
  | "open_question"
  | "scratchpad";

export type FilterKind =
  | "all"
  | "decision"
  | "action_item"
  | "open_question"
  | "locked";

export interface User {
  id: string;
  email: string;
  full_name: string;
  default_audio_retention: AudioRetentionPolicy;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface WordToken {
  word: string;
  start_ms: number;
  end_ms: number;
  confidence: number;
  speaker?: number | null;
}

export interface Speaker {
  id: string;
  session_id: string;
  diarization_index: number;
  display_name: string;
  color_hex: string;
}

export interface TranscriptSegment {
  id: string;
  session_id: string;
  speaker_id: string | null;
  speaker_label: string;
  speaker_color: string;
  text: string;
  start_ms: number;
  end_ms: number;
  confidence: number;
  has_low_confidence: boolean;
  is_redacted: boolean;
  words: WordToken[];
}

export interface InterimTranscript {
  speaker_label: string;
  text: string;
  start_ms: number;
  end_ms: number;
  confidence: number;
}

export interface AgendaSection {
  id: string;
  session_id: string;
  title: string;
  start_ms: number;
  order_index: number;
  is_user_edited: boolean;
}

export interface NoteBlock {
  id: string;
  session_id: string;
  section_id: string | null;
  block_type: BlockType;
  content: string;
  assignee: string | null;
  due_date_text: string | null;
  completed: boolean;
  source_segment_ids: string[];
  has_low_confidence: boolean;
  is_user_edited: boolean;
  order_index: number;
}

export interface ActionItem {
  id: string;
  session_id: string;
  block_id: string | null;
  description: string;
  assignee: string | null;
  due_date_text: string | null;
  completed: boolean;
  source_segment_ids: string[];
}

export interface PinnedMoment {
  offset_ms: number;
  label: string | null;
  created_at: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  status: SessionStatus;
  executive_summary: string | null;
  duration_ms: number;
  audio_retention: AudioRetentionPolicy;
  audio_available: boolean;
  agenda_preview: string[];
  decision_count: number;
  action_item_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionDetail {
  id: string;
  user_id: string;
  title: string;
  status: SessionStatus;
  executive_summary: string | null;
  duration_ms: number;
  audio_retention: AudioRetentionPolicy;
  audio_available: boolean;
  audio_expires_at: string | null;
  pinned_moments: PinnedMoment[];
  speakers: Speaker[];
  segments: TranscriptSegment[];
  sections: AgendaSection[];
  blocks: NoteBlock[];
  action_items: ActionItem[];
  created_at: string;
  updated_at: string;
}

export interface GlossaryTerm {
  id: string;
  term: string;
  boost: number;
  created_at: string;
}

export interface AudioUrlResponse {
  url: string;
  stream_url: string;
  expires_at: string | null;
}

export type ExportFormat = "markdown" | "slack_recap" | "followup_email";

export interface ExportResponse {
  format: ExportFormat;
  filename: string;
  content: string;
}

export interface SearchHit {
  kind: "section" | "session";
  session_id: string;
  session_title: string;
  section_id: string | null;
  section_title: string | null;
  start_ms: number;
  snippet: string;
  score: number;
  keyword_rank: number | null;
  semantic_rank: number | null;
  created_at: string;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
}

export type ServerWSEvent =
  | {
      type: "session.ready";
      session_id: string;
      status: SessionStatus;
      stt_mode: "deepgram" | "mock";
      llm_mode: "gemini" | "mock";
    }
  | {
      type: "transcript.interim";
      session_id: string;
      speaker_label: string;
      text: string;
      start_ms: number;
      end_ms: number;
      confidence: number;
    }
  | {
      type: "transcript.final";
      session_id: string;
      segment: TranscriptSegment;
      speaker: Speaker;
    }
  | {
      type: "moment.pinned";
      session_id: string;
      moment: PinnedMoment;
    }
  | {
      type: "notes.patch";
      session_id: string;
      created_sections: AgendaSection[];
      created_blocks: NoteBlock[];
      created_action_items: ActionItem[];
    }
  | {
      type: "block.updated";
      session_id: string;
      action: "created" | "updated" | "deleted";
      block: NoteBlock | null;
      block_id: string;
    }
  | {
      type: "section.updated";
      session_id: string;
      section: AgendaSection;
    }
  | {
      type: "speaker.updated";
      session_id: string;
      speaker: Speaker;
    }
  | {
      type: "session.redacted";
      session_id: string;
      from_ms: number;
      to_ms: number;
      redacted_segment_ids: string[];
      deleted_block_ids: string[];
    }
  | {
      type: "session.status";
      session_id: string;
      status: SessionStatus;
      duration_ms: number;
    }
  | {
      type: "session.consolidated";
      session_id: string;
      title: string;
      status: SessionStatus;
      executive_summary: string;
      sections: AgendaSection[];
      blocks: NoteBlock[];
      action_items: ActionItem[];
      audio_available: boolean;
    }
  | {
      type: "error";
      code: string;
      message: string;
    };
