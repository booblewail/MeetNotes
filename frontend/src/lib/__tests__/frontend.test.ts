import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { serializeSessionMarkdown } from "../markdown.ts";
import { applyInlineFormat, parseInlineTokens } from "../inlineFormat.ts";
import {
  countBufferedChunks,
  drainSessionChunks,
  enqueueAudioChunk,
  purgeBufferedWindow,
  resetMemoryOfflineBuffer,
} from "../offlineBuffer.ts";
import { useSessionStore } from "../../store/sessionStore.ts";

describe("MeetNotes Frontend Core", () => {
  beforeEach(() => {
    resetMemoryOfflineBuffer();
    useSessionStore.getState().reset();
  });

  it("serializes structured session Markdown with locks, decisions, and actions", () => {
    const md = serializeSessionMarkdown({
      title: "Architecture Sync",
      status: "completed",
      durationMs: 125000,
      executiveSummary: "Team aligned on OAuth2 + pgvector hybrid search.",
      pinnedMoments: [{ offset_ms: 42000, label: "Auth decision", created_at: "" }],
      sections: [
        {
          id: "sec-1",
          session_id: "sess-1",
          title: "Authentication",
          start_ms: 0,
          order_index: 0,
          is_user_edited: false,
        },
      ],
      blocks: [
        {
          id: "blk-1",
          session_id: "sess-1",
          section_id: "sec-1",
          block_type: "decision",
          content: "Adopt OAuth2 with PKCE",
          assignee: null,
          due_date_text: null,
          completed: false,
          source_segment_ids: ["seg-1"],
          has_low_confidence: false,
          is_user_edited: true,
          order_index: 0,
        },
        {
          id: "blk-2",
          session_id: "sess-1",
          section_id: "sec-1",
          block_type: "action_item",
          content: "Benchmark pgvector HNSW index",
          assignee: "Alice",
          due_date_text: "Friday",
          completed: false,
          source_segment_ids: ["seg-2"],
          has_low_confidence: false,
          is_user_edited: false,
          order_index: 1,
        },
      ],
    });

    assert.match(md, /# Architecture Sync/);
    assert.match(md, /02:05/);
    assert.match(md, /\*\*Decision:\*\* Adopt OAuth2 with PKCE \[Locked\]/);
    assert.match(
      md,
      /- \[ \] Benchmark pgvector HNSW index \(@Alice, due Friday\)/
    );
  });

  it("queues offline audio chunks in FIFO order and purges redacted windows", async () => {
    const s = "sess-offline";
    await enqueueAudioChunk(s, 1000, new Uint8Array([1, 2]).buffer);
    await enqueueAudioChunk(s, 5000, new Uint8Array([3, 4]).buffer);
    await enqueueAudioChunk(s, 9000, new Uint8Array([5, 6]).buffer);

    assert.equal(await countBufferedChunks(s), 3);

    // Redact [4000, 10000] drops the 5000ms and 9000ms chunks
    const removed = await purgeBufferedWindow(s, 4000, 10000);
    assert.equal(removed, 2);
    assert.equal(await countBufferedChunks(s), 1);

    const flushedOffsets: number[] = [];
    const flushedCount = await drainSessionChunks(s, (chunk) => {
      flushedOffsets.push(chunk.offsetMs);
      return true;
    });
    assert.equal(flushedCount, 1);
    assert.deepEqual(flushedOffsets, [1000]);
    assert.equal(await countBufferedChunks(s), 0);
  });

  it("applies Pass 1 patches, redaction, and Pass 2 consolidation in sessionStore", () => {
    const store = useSessionStore.getState();
    store.applyServerEvent({
      type: "transcript.final",
      session_id: "s1",
      speaker: {
        id: "sp-1",
        session_id: "s1",
        diarization_index: 0,
        display_name: "Speaker 1",
        color_hex: "#3B82F6",
      },
      segment: {
        id: "seg-1",
        session_id: "s1",
        speaker_id: "sp-1",
        speaker_label: "Speaker 1",
        speaker_color: "#3B82F6",
        text: "Let's use OAuth2.",
        start_ms: 1000,
        end_ms: 3000,
        confidence: 0.96,
        has_low_confidence: false,
        is_redacted: false,
        words: [],
      },
    });

    store.applyServerEvent({
      type: "notes.patch",
      session_id: "s1",
      created_sections: [
        {
          id: "sec-1",
          session_id: "s1",
          title: "Auth",
          start_ms: 1000,
          order_index: 0,
          is_user_edited: false,
        },
      ],
      created_blocks: [
        {
          id: "blk-1",
          session_id: "s1",
          section_id: "sec-1",
          block_type: "decision",
          content: "Use OAuth2",
          assignee: null,
          due_date_text: null,
          completed: false,
          source_segment_ids: ["seg-1"],
          has_low_confidence: false,
          is_user_edited: false,
          order_index: 0,
        },
      ],
      created_action_items: [],
    });

    assert.equal(useSessionStore.getState().segments.length, 1);
    assert.equal(useSessionStore.getState().sections.length, 1);
    assert.equal(useSessionStore.getState().blocks.length, 1);

    // Redact seg-1 removes both segment and unedited dependent block
    store.applyServerEvent({
      type: "session.redacted",
      session_id: "s1",
      from_ms: 0,
      to_ms: 5000,
      redacted_segment_ids: ["seg-1"],
      deleted_block_ids: ["blk-1"],
    });
    assert.equal(useSessionStore.getState().segments.length, 0);
    assert.equal(useSessionStore.getState().blocks.length, 0);
  });

  it("parses and toggles Notion-style inline formatting tokens safely", () => {
    const tokens = parseInlineTokens(
      "Use **OAuth2** with *PKCE*, `jwt`, ~~legacy~~, ==critical==, [Docs](https://oauth.net) and [Bad](javascript:alert(1))"
    );
    assert.deepEqual(
      tokens.filter((t) => t.type !== "text"),
      [
        { type: "bold", text: "OAuth2" },
        { type: "italic", text: "PKCE" },
        { type: "code", text: "jwt" },
        { type: "strike", text: "legacy" },
        { type: "highlight", text: "critical" },
        { type: "link", label: "Docs", href: "https://oauth.net" },
      ]
    );

    const wrapped = applyInlineFormat("Adopt OAuth2", 6, 12, "bold");
    assert.equal(wrapped.nextValue, "Adopt **OAuth2**");
    const unwrapped = applyInlineFormat(
      wrapped.nextValue,
      wrapped.nextStart,
      wrapped.nextEnd,
      "bold"
    );
    assert.equal(unwrapped.nextValue, "Adopt OAuth2");
  });
});
