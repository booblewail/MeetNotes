"use client";

import { useCallback, useEffect, useRef } from "react";
import { getStoredToken, getWsBaseUrl } from "@/lib/api";
import {
  countBufferedChunks,
  drainSessionChunks,
  enqueueAudioChunk,
  purgeBufferedWindow,
} from "@/lib/offlineBuffer";
import type { ServerWSEvent } from "@/lib/types";
import { useSessionStore } from "@/store/sessionStore";

export type ClientControlMessage =
  | { type: "pause" }
  | { type: "resume" }
  | { type: "pin_moment"; offset_ms: number; label?: string }
  | { type: "redact_window"; window_ms?: number; current_offset_ms?: number }
  | { type: "stop_session" }
  | { type: "sync_state" }
  | {
      type: "simulate_utterance";
      speaker_label: string;
      text: string;
      start_ms?: number;
      end_ms?: number;
      confidence?: number;
      force_pass1?: boolean;
    };

export function packAudioFrame(
  offsetMs: number,
  pcmBuffer: ArrayBuffer
): ArrayBuffer {
  const frame = new ArrayBuffer(4 + pcmBuffer.byteLength);
  const view = new DataView(frame);
  view.setUint32(0, Math.max(0, Math.floor(offsetMs)), true);
  new Uint8Array(frame, 4).set(new Uint8Array(pcmBuffer));
  return frame;
}

export function useSessionSocket(sessionId: string | null, enabled = true) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const unmountedRef = useRef(false);

  const applyServerEvent = useSessionStore((s) => s.applyServerEvent);
  const setWsConnected = useSessionStore((s) => s.setWsConnected);
  const setBufferedChunkCount = useSessionStore((s) => s.setBufferedChunkCount);

  const flushOfflineBuffer = useCallback(async () => {
    if (!sessionId) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    await drainSessionChunks(sessionId, (chunk) => {
      if (ws.readyState !== WebSocket.OPEN) return false;
      ws.send(packAudioFrame(chunk.offsetMs, chunk.pcmBuffer));
      return true;
    });
    const remaining = await countBufferedChunks(sessionId);
    setBufferedChunkCount(remaining);
  }, [sessionId, setBufferedChunkCount]);

  useEffect(() => {
    unmountedRef.current = false;
    if (!sessionId || !enabled) return;

    const connect = () => {
      if (unmountedRef.current) return;
      const token = getStoredToken();
      if (!token) return;

      const url = `${getWsBaseUrl()}/ws/sessions/${sessionId}?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setWsConnected(true);
        void flushOfflineBuffer();
      };

      ws.onmessage = (msgEvent) => {
        if (typeof msgEvent.data !== "string") return;
        try {
          const parsed = JSON.parse(msgEvent.data) as ServerWSEvent;
          applyServerEvent(parsed);
        } catch {
          // Ignore malformed frame
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
        if (unmountedRef.current) return;
        const status = useSessionStore.getState().status;
        if (status === "completed") return;
        const delayMs = Math.min(15000, 1000 * 2 ** attemptRef.current);
        attemptRef.current += 1;
        reconnectTimerRef.current = setTimeout(connect, delayMs);
      };

      ws.onerror = () => {
        // onclose handles reconnect
      };
    };

    connect();
    void countBufferedChunks(sessionId).then(setBufferedChunkCount);

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setWsConnected(false);
    };
  }, [
    sessionId,
    enabled,
    applyServerEvent,
    flushOfflineBuffer,
    setBufferedChunkCount,
    setWsConnected,
  ]);

  const sendAudioChunk = useCallback(
    async (offsetMs: number, pcmBuffer: ArrayBuffer) => {
      if (!sessionId) return;
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(packAudioFrame(offsetMs, pcmBuffer));
        return;
      }
      await enqueueAudioChunk(sessionId, offsetMs, pcmBuffer);
      const count = await countBufferedChunks(sessionId);
      setBufferedChunkCount(count);
    },
    [sessionId, setBufferedChunkCount]
  );

  const sendControl = useCallback(
    async (payload: ClientControlMessage): Promise<boolean> => {
      if (payload.type === "redact_window" && sessionId) {
        const winMs = payload.window_ms ?? 120_000;
        const toMs = payload.current_offset_ms ?? Date.now();
        await purgeBufferedWindow(sessionId, Math.max(0, toMs - winMs), toMs);
        const count = await countBufferedChunks(sessionId);
        setBufferedChunkCount(count);
      }
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
        return true;
      }
      return false;
    },
    [sessionId, setBufferedChunkCount]
  );

  return {
    sendAudioChunk,
    sendControl,
    flushOfflineBuffer,
  };
}
