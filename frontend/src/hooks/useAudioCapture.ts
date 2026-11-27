"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";

interface UseAudioCaptureOptions {
  initialElapsedMs?: number;
  onSpeechChunk: (offsetMs: number, pcmBuffer: ArrayBuffer) => void;
}

const PRE_ROLL_CHUNKS = 4; // 400ms pre-speech ring buffer so first syllable isn't clipped
const HANGOVER_CHUNKS = 8; // 800ms hangover after speech ends
const RMS_SPEECH_THRESHOLD = 0.015;

export function useAudioCapture({
  initialElapsedMs = 0,
  onSpeechChunk,
}: UseAudioCaptureOptions) {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [isSpeechActive, setIsSpeechActive] = useState(false);
  const [vadEngine, setVadEngine] = useState<"silero" | "energy">("energy");
  const [micLevel, setMicLevel] = useState(0);
  const [elapsedMs, setElapsedMs] = useState(initialElapsedMs);
  const [micError, setMicError] = useState<string | null>(null);

  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const vadInstanceRef = useRef<{ destroy?: () => void; pause?: () => void; start?: () => void } | null>(null);
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const accumulatedMsRef = useRef(initialElapsedMs);
  const startedAtPerfRef = useRef<number | null>(null);
  const isPausedRef = useRef(false);
  const isRecordingRef = useRef(false);
  const sileroSpeechRef = useRef(false);
  const sileroActiveRef = useRef(false);
  const hangoverCounterRef = useRef(0);
  const preRollRef = useRef<Array<{ offsetMs: number; buf: ArrayBuffer }>>([]);

  const setDurationMs = useSessionStore((s) => s.setDurationMs);

  useEffect(() => {
    if (!isRecording) {
      accumulatedMsRef.current = initialElapsedMs;
      setElapsedMs(initialElapsedMs);
    }
  }, [initialElapsedMs, isRecording]);

  const currentOffsetMs = useCallback(() => {
    if (!isRecordingRef.current || isPausedRef.current || startedAtPerfRef.current === null) {
      return accumulatedMsRef.current;
    }
    return accumulatedMsRef.current + Math.max(0, Math.floor(performance.now() - startedAtPerfRef.current));
  }, []);

  const acquireWakeLock = useCallback(async () => {
    if (typeof navigator === "undefined" || !("wakeLock" in navigator)) return;
    try {
      wakeLockRef.current = await navigator.wakeLock.request("screen");
    } catch {
      // Wake Lock not supported or battery saver active
    }
  }, []);

  const releaseWakeLock = useCallback(async () => {
    if (wakeLockRef.current) {
      try {
        await wakeLockRef.current.release();
      } catch {
        // ignore
      }
      wakeLockRef.current = null;
    }
  }, []);

  const teardownMedia = useCallback(async () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (vadInstanceRef.current) {
      try {
        vadInstanceRef.current.destroy?.();
      } catch {
        // ignore
      }
      vadInstanceRef.current = null;
    }
    sileroActiveRef.current = false;
    sileroSpeechRef.current = false;
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }
    if (audioCtxRef.current) {
      try {
        await audioCtxRef.current.close();
      } catch {
        // ignore
      }
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) {
        track.stop();
      }
      streamRef.current = null;
    }
    await releaseWakeLock();
    setMicLevel(0);
    setIsSpeechActive(false);
  }, [releaseWakeLock]);

  const start = useCallback(async () => {
    setMicError(null);
    if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setMicError("Microphone capture requires a modern browser over HTTPS or localhost.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      await audioCtx.audioWorklet.addModule("/audio/pcm-processor.js");

      const source = audioCtx.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioCtx, "pcm-processor", {
        processorOptions: { targetSampleRate: 16000 },
      });
      const silentSink = audioCtx.createGain();
      silentSink.gain.value = 0;

      workletNode.port.onmessage = (event) => {
        if (!isRecordingRef.current || isPausedRef.current) return;
        const { pcm16, rms } = event.data as { pcm16: ArrayBuffer; rms: number };
        const clampedLevel = Math.min(1, rms * 5);
        setMicLevel(clampedLevel);

        const offset = currentOffsetMs();
        const activeSpeech = sileroActiveRef.current
          ? sileroSpeechRef.current
          : rms >= RMS_SPEECH_THRESHOLD;

        if (activeSpeech) {
          hangoverCounterRef.current = HANGOVER_CHUNKS;
          setIsSpeechActive(true);
          while (preRollRef.current.length > 0) {
            const pre = preRollRef.current.shift();
            if (pre) onSpeechChunk(pre.offsetMs, pre.buf);
          }
          onSpeechChunk(offset, pcm16);
        } else if (hangoverCounterRef.current > 0) {
          hangoverCounterRef.current -= 1;
          setIsSpeechActive(true);
          onSpeechChunk(offset, pcm16);
        } else {
          setIsSpeechActive(false);
          preRollRef.current.push({ offsetMs: offset, buf: pcm16 });
          if (preRollRef.current.length > PRE_ROLL_CHUNKS) {
            preRollRef.current.shift();
          }
        }
      };

      source.connect(workletNode);
      workletNode.connect(silentSink);
      silentSink.connect(audioCtx.destination);
      workletNodeRef.current = workletNode;

      // Dynamically initialize Silero VAD (@ricky0123/vad-web) on the client
      try {
        const vadWeb = await import("@ricky0123/vad-web");
        const micVad = await vadWeb.MicVAD.new({
          stream,
          baseAssetPath: "/audio/",
          onnxWASMBasePath: "/audio/",
          onSpeechStart: () => {
            sileroSpeechRef.current = true;
            setIsSpeechActive(true);
          },
          onSpeechEnd: () => {
            sileroSpeechRef.current = false;
          },
          onVADMisfire: () => {
            sileroSpeechRef.current = false;
          },
        });
        micVad.start();
        vadInstanceRef.current = micVad;
        sileroActiveRef.current = true;
        setVadEngine("silero");
      } catch {
        sileroActiveRef.current = false;
        setVadEngine("energy");
      }

      isRecordingRef.current = true;
      isPausedRef.current = false;
      startedAtPerfRef.current = performance.now();
      setIsRecording(true);
      setIsPaused(false);
      await acquireWakeLock();

      timerRef.current = setInterval(() => {
        const nowMs = currentOffsetMs();
        setElapsedMs(nowMs);
        setDurationMs(nowMs);
      }, 250);
    } catch (err) {
      await teardownMedia();
      setMicError(
        err instanceof Error ? err.message : "Could not access microphone"
      );
    }
  }, [acquireWakeLock, currentOffsetMs, onSpeechChunk, setDurationMs, teardownMedia]);

  const pause = useCallback(async () => {
    if (!isRecordingRef.current || isPausedRef.current) return;
    accumulatedMsRef.current = currentOffsetMs();
    startedAtPerfRef.current = null;
    isPausedRef.current = true;
    setIsPaused(true);
    setElapsedMs(accumulatedMsRef.current);
    setMicLevel(0);
    setIsSpeechActive(false);
    vadInstanceRef.current?.pause?.();
    await releaseWakeLock();
  }, [currentOffsetMs, releaseWakeLock]);

  const resume = useCallback(async () => {
    if (!isRecordingRef.current || !isPausedRef.current) return;
    startedAtPerfRef.current = performance.now();
    isPausedRef.current = false;
    setIsPaused(false);
    vadInstanceRef.current?.start?.();
    await acquireWakeLock();
  }, [acquireWakeLock]);

  const stop = useCallback(async () => {
    const finalMs = currentOffsetMs();
    accumulatedMsRef.current = finalMs;
    startedAtPerfRef.current = null;
    isRecordingRef.current = false;
    isPausedRef.current = false;
    setIsRecording(false);
    setIsPaused(false);
    setElapsedMs(finalMs);
    setDurationMs(finalMs);
    await teardownMedia();
    return finalMs;
  }, [currentOffsetMs, setDurationMs, teardownMedia]);

  useEffect(() => {
    return () => {
      void teardownMedia();
    };
  }, [teardownMedia]);

  return {
    isRecording,
    isPaused,
    isSpeechActive,
    vadEngine,
    micLevel,
    elapsedMs,
    micError,
    currentOffsetMs,
    start,
    pause,
    resume,
    stop,
  };
}
