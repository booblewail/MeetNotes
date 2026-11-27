import { create } from "zustand";
import type {
  ActionItem,
  AgendaSection,
  AudioRetentionPolicy,
  FilterKind,
  InterimTranscript,
  NoteBlock,
  PinnedMoment,
  ServerWSEvent,
  SessionDetail,
  SessionStatus,
  Speaker,
  TranscriptSegment,
} from "../lib/types.ts";

export interface AudioSliceRequest {
  startMs: number;
  endMs: number;
  triggerKey: number;
}

export interface SessionStoreState {
  sessionId: string | null;
  title: string;
  status: SessionStatus;
  executiveSummary: string | null;
  durationMs: number;
  audioRetention: AudioRetentionPolicy;
  audioAvailable: boolean;
  createdAt: string;
  pinnedMoments: PinnedMoment[];
  speakers: Speaker[];
  segments: TranscriptSegment[];
  interimSegment: InterimTranscript | null;
  sections: AgendaSection[];
  blocks: NoteBlock[];
  actionItems: ActionItem[];

  activeFilter: FilterKind;
  activeSectionId: string | null;
  highlightedSegmentIds: string[];
  activeAudioSlice: AudioSliceRequest | null;

  wsConnected: boolean;
  sttMode: "deepgram" | "mock";
  llmMode: "gemini" | "mock";
  bufferedChunkCount: number;
  lastError: string | null;

  hydrateSession: (detail: SessionDetail) => void;
  reset: () => void;
  setWsConnected: (connected: boolean) => void;
  setBufferedChunkCount: (count: number) => void;
  setActiveFilter: (filter: FilterKind) => void;
  setActiveSectionId: (sectionId: string | null) => void;
  setTitle: (title: string) => void;
  setExecutiveSummary: (summary: string | null) => void;
  setDurationMs: (durationMs: number) => void;
  highlightSegments: (segmentIds: string[], playAudio?: boolean) => void;
  playAudioAtMs: (startMs: number) => void;
  clearAudioSlice: () => void;
  applyBlockLocal: (block: NoteBlock) => void;
  removeBlockLocal: (blockId: string) => void;
  applySectionLocal: (section: AgendaSection) => void;
  applySpeakerLocal: (speaker: Speaker) => void;
  applyActionItemLocal: (item: ActionItem) => void;
  applyServerEvent: (event: ServerWSEvent) => void;
}

const initialState = {
  sessionId: null as string | null,
  title: "Untitled Meeting",
  status: "created" as SessionStatus,
  executiveSummary: null as string | null,
  durationMs: 0,
  audioRetention: "retain_7d" as AudioRetentionPolicy,
  audioAvailable: false,
  createdAt: "",
  pinnedMoments: [] as PinnedMoment[],
  speakers: [] as Speaker[],
  segments: [] as TranscriptSegment[],
  interimSegment: null as InterimTranscript | null,
  sections: [] as AgendaSection[],
  blocks: [] as NoteBlock[],
  actionItems: [] as ActionItem[],

  activeFilter: "all" as FilterKind,
  activeSectionId: null as string | null,
  highlightedSegmentIds: [] as string[],
  activeAudioSlice: null as AudioSliceRequest | null,

  wsConnected: false,
  sttMode: "mock" as "deepgram" | "mock",
  llmMode: "mock" as "gemini" | "mock",
  bufferedChunkCount: 0,
  lastError: null as string | null,
};

function upsertById<T extends { id: string }>(list: T[], item: T): T[] {
  const idx = list.findIndex((x) => x.id === item.id);
  if (idx === -1) return [...list, item];
  const next = list.slice();
  next[idx] = item;
  return next;
}

export const useSessionStore = create<SessionStoreState>((set, get) => ({
  ...initialState,

  hydrateSession(detail) {
    set({
      sessionId: detail.id,
      title: detail.title,
      status: detail.status,
      executiveSummary: detail.executive_summary,
      durationMs: detail.duration_ms,
      audioRetention: detail.audio_retention,
      audioAvailable: detail.audio_available,
      createdAt: detail.created_at,
      pinnedMoments: detail.pinned_moments || [],
      speakers: detail.speakers || [],
      segments: detail.segments || [],
      sections: detail.sections || [],
      blocks: detail.blocks || [],
      actionItems: detail.action_items || [],
      interimSegment: null,
      activeSectionId: detail.sections?.[0]?.id ?? null,
      lastError: null,
    });
  },

  reset() {
    set({ ...initialState });
  },

  setWsConnected(wsConnected) {
    set({ wsConnected });
  },

  setBufferedChunkCount(bufferedChunkCount) {
    set({ bufferedChunkCount });
  },

  setActiveFilter(activeFilter) {
    set({ activeFilter });
  },

  setActiveSectionId(activeSectionId) {
    set({ activeSectionId });
  },

  setTitle(title) {
    set({ title });
  },

  setExecutiveSummary(executiveSummary) {
    set({ executiveSummary });
  },

  setDurationMs(durationMs) {
    set((s) => ({ durationMs: Math.max(s.durationMs, durationMs) }));
  },

  highlightSegments(segmentIds, playAudio = true) {
    const segs = get().segments.filter((seg) => segmentIds.includes(seg.id));
    const earliest = segs.reduce(
      (min, seg) => Math.min(min, seg.start_ms),
      segs[0]?.start_ms ?? 0
    );
    set((state) => ({
      highlightedSegmentIds: segmentIds,
      activeAudioSlice:
        playAudio && state.audioAvailable && segs.length > 0
          ? {
              startMs: Math.max(0, earliest - 1000),
              endMs: earliest + 9000,
              triggerKey: Date.now(),
            }
          : state.activeAudioSlice,
    }));
  },

  playAudioAtMs(startMs) {
    if (!get().audioAvailable) return;
    set({
      activeAudioSlice: {
        startMs: Math.max(0, startMs - 1000),
        endMs: startMs + 9000,
        triggerKey: Date.now(),
      },
    });
  },

  clearAudioSlice() {
    set({ activeAudioSlice: null });
  },

  applyBlockLocal(block) {
    set((s) => ({ blocks: upsertById(s.blocks, block) }));
  },

  removeBlockLocal(blockId) {
    set((s) => ({
      blocks: s.blocks.filter((b) => b.id !== blockId),
      actionItems: s.actionItems.filter((a) => a.block_id !== blockId),
    }));
  },

  applySectionLocal(section) {
    set((s) => ({ sections: upsertById(s.sections, section) }));
  },

  applySpeakerLocal(speaker) {
    set((s) => ({
      speakers: upsertById(s.speakers, speaker),
      segments: s.segments.map((seg) =>
        seg.speaker_id === speaker.id
          ? {
              ...seg,
              speaker_label: speaker.display_name,
              speaker_color: speaker.color_hex,
            }
          : seg
      ),
    }));
  },

  applyActionItemLocal(item) {
    set((s) => ({
      actionItems: upsertById(s.actionItems, item),
      blocks: item.block_id
        ? s.blocks.map((b) =>
            b.id === item.block_id
              ? {
                  ...b,
                  content: item.description,
                  assignee: item.assignee,
                  due_date_text: item.due_date_text,
                  completed: item.completed,
                  is_user_edited: true,
                }
              : b
          )
        : s.blocks,
    }));
  },

  applyServerEvent(event) {
    switch (event.type) {
      case "session.ready":
        set({
          status: event.status,
          sttMode: event.stt_mode,
          llmMode: event.llm_mode,
          lastError: null,
        });
        break;

      case "transcript.interim":
        set({
          interimSegment: {
            speaker_label: event.speaker_label,
            text: event.text,
            start_ms: event.start_ms,
            end_ms: event.end_ms,
            confidence: event.confidence,
          },
        });
        break;

      case "transcript.final":
        set((s) => ({
          interimSegment: null,
          durationMs: Math.max(s.durationMs, event.segment.end_ms),
          speakers: upsertById(s.speakers, event.speaker),
          segments: upsertById(s.segments, event.segment).sort(
            (a, b) => a.start_ms - b.start_ms
          ),
        }));
        break;

      case "moment.pinned":
        set((s) => {
          const exists = s.pinnedMoments.some(
            (m) =>
              m.offset_ms === event.moment.offset_ms &&
              m.created_at === event.moment.created_at
          );
          return exists
            ? s
            : { pinnedMoments: [...s.pinnedMoments, event.moment] };
        });
        break;

      case "notes.patch":
        set((s) => {
          let sections = s.sections;
          for (const sec of event.created_sections) {
            sections = upsertById(sections, sec);
          }
          let blocks = s.blocks;
          for (const blk of event.created_blocks) {
            blocks = upsertById(blocks, blk);
          }
          let actionItems = s.actionItems;
          for (const ai of event.created_action_items) {
            actionItems = upsertById(actionItems, ai);
          }
          return {
            sections: sections.sort(
              (a, b) => a.order_index - b.order_index || a.start_ms - b.start_ms
            ),
            blocks: blocks.sort((a, b) => a.order_index - b.order_index),
            actionItems,
            activeSectionId:
              s.activeSectionId ?? event.created_sections[0]?.id ?? null,
          };
        });
        break;

      case "block.updated":
        set((s) => {
          if (event.action === "deleted") {
            return {
              blocks: s.blocks.filter((b) => b.id !== event.block_id),
              actionItems: s.actionItems.filter(
                (a) => a.block_id !== event.block_id
              ),
            };
          }
          if (!event.block) return s;
          return {
            blocks: upsertById(s.blocks, event.block).sort(
              (a, b) => a.order_index - b.order_index
            ),
          };
        });
        break;

      case "section.updated":
        set((s) => ({
          sections: upsertById(s.sections, event.section).sort(
            (a, b) => a.order_index - b.order_index || a.start_ms - b.start_ms
          ),
        }));
        break;

      case "speaker.updated":
        get().applySpeakerLocal(event.speaker);
        break;

      case "session.redacted": {
        const redactedSet = new Set(event.redacted_segment_ids);
        const deletedBlkSet = new Set(event.deleted_block_ids);
        set((s) => ({
          segments: s.segments.filter((seg) => !redactedSet.has(seg.id)),
          blocks: s.blocks
            .filter((b) => !deletedBlkSet.has(b.id))
            .map((b) => ({
              ...b,
              source_segment_ids: b.source_segment_ids.filter(
                (id) => !redactedSet.has(id)
              ),
            })),
          actionItems: s.actionItems.filter(
            (a) => !a.block_id || !deletedBlkSet.has(a.block_id)
          ),
        }));
        break;
      }

      case "session.status":
        set((s) => ({
          status: event.status,
          durationMs: Math.max(s.durationMs, event.duration_ms),
        }));
        break;

      case "session.consolidated":
        set({
          title: event.title,
          status: event.status,
          executiveSummary: event.executive_summary,
          sections: event.sections,
          blocks: event.blocks,
          actionItems: event.action_items,
          audioAvailable: event.audio_available,
        });
        break;

      case "error":
        set({ lastError: event.message });
        break;
    }
  },
}));
