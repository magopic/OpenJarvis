import { useCallback, useEffect, useRef, useState } from 'react';
import { MicVAD } from '@ricky0123/vad-web';
import { apiFetch, synthesizeSpeech, transcribeAudio } from '../lib/api';
import { encodeWavPCM16 } from '../lib/wav-encode';
import { useAppStore } from '../lib/store';

export type VoiceLoopState = 'idle' | 'listening' | 'thinking' | 'speaking';

// Transcriptions below this confidence are treated as VAD/STT misfires
// (background noise, cross-talk) and dropped before ever reaching the
// conversation -- observed clean speech scores ~1.0, observed misfires
// (misdetected language, garbled text) scored ~0.33-0.41 in testing.
const MIN_CONFIDENCE = 0.5;

/**
 * Hands-free voice loop: VAD-triggered STT -> Chat -> TTS -> playback,
 * auto-resuming listening after each turn. Barge-in interrupts playback
 * (not the "thinking" phase) when new speech is detected.
 *
 * Reuses the existing /v1/speech/transcribe, /v1/chat/completions and
 * /v1/speech/synthesize endpoints -- no new backend architecture. The
 * conversation sent to /v1/chat/completions is read fresh from
 * useAppStore on every turn -- the same store the normal typed chat
 * uses -- so voice and text share one history instead of the hook
 * keeping its own separate, ever-growing buffer.
 */
export function useVoiceLoop(model: string) {
  const [state, setState] = useState<VoiceLoopState>('idle');
  const [transcript, setTranscript] = useState('');
  const [reply, setReply] = useState('');
  const [error, setError] = useState<string | null>(null);

  const stateRef = useRef<VoiceLoopState>('idle');
  const activeRef = useRef(false);
  const vadRef = useRef<MicVAD | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const modelRef = useRef(model);
  modelRef.current = model;

  const setBoth = useCallback((s: VoiceLoopState) => {
    stateRef.current = s;
    setState(s);
  }, []);

  const stopPlayback = useCallback(() => {
    const el = audioRef.current;
    if (el && !el.paused) {
      el.pause();
      el.currentTime = 0;
    }
  }, []);

  const handleSpeechStart = useCallback(() => {
    // Barge-in: only defined for "while OpenJarvis is speaking". A speech
    // start during 'thinking' is dropped in handleSpeechEnd below rather
    // than interrupting an in-flight chat/TTS request.
    if (stateRef.current === 'speaking') {
      stopPlayback();
      setBoth('listening');
    }
  }, [setBoth, stopPlayback]);

  const handleSpeechEnd = useCallback(async (audio: Float32Array) => {
    if (!activeRef.current) return;
    if (stateRef.current === 'thinking') return; // drop overlapping segment

    setBoth('thinking');
    setError(null);
    try {
      const wavBlob = encodeWavPCM16(audio, 16000);
      const { text, confidence } = await transcribeAudio(wavBlob, 'voice-loop.wav', 'it');
      if (!text || !text.trim()) {
        setBoth('listening');
        return;
      }
      // Drop obvious VAD/STT misfires before they ever reach the
      // conversation, instead of letting bad context accumulate.
      if (confidence != null && confidence < MIN_CONFIDENCE) {
        setBoth('listening');
        return;
      }
      setTranscript(text);

      // Single source of truth: read the same conversation the normal
      // typed chat uses, and append only this turn's transcription.
      const storeMessages = useAppStore.getState().messages;
      const messages = [
        ...storeMessages.map((m) => ({ role: m.role, content: m.content })),
        { role: 'user' as const, content: text },
      ];

      const res = await apiFetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelRef.current, messages }),
      });
      if (!res.ok) throw new Error(`Chat request failed: ${res.status}`);
      const data = await res.json();
      // Only ever read the final .content -- never reasoning_content, which
      // the server-side adapter already strips before it reaches this field.
      const answer: string = data?.choices?.[0]?.message?.content ?? '';
      setReply(answer);

      if (!activeRef.current) return; // loop was stopped while awaiting chat
      setBoth('speaking');
      const audioBlob = await synthesizeSpeech(answer);
      if (!activeRef.current) return;

      const url = URL.createObjectURL(audioBlob);
      const el = audioRef.current!;
      el.src = url;
      await new Promise<void>((resolve) => {
        const cleanup = () => {
          el.removeEventListener('ended', cleanup);
          el.removeEventListener('pause', cleanup);
          resolve();
        };
        el.addEventListener('ended', cleanup);
        el.addEventListener('pause', cleanup); // barge-in stops playback via pause()
        el.play().catch(cleanup);
      });
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (activeRef.current) setBoth('listening');
    }
  }, [setBoth]);

  const start = useCallback(async () => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }
    if (!vadRef.current) {
      vadRef.current = await MicVAD.new({
        model: 'v5',
        baseAssetPath: '/',
        onnxWASMBasePath: '/',
        onSpeechStart: handleSpeechStart,
        onSpeechEnd: handleSpeechEnd,
      });
    }
    activeRef.current = true;
    await vadRef.current.start();
    setBoth('listening');
  }, [handleSpeechStart, handleSpeechEnd, setBoth]);

  const stop = useCallback(async () => {
    activeRef.current = false;
    await vadRef.current?.pause();
    stopPlayback();
    setBoth('idle');
  }, [setBoth, stopPlayback]);

  useEffect(() => {
    return () => {
      activeRef.current = false;
      vadRef.current?.destroy();
    };
  }, []);

  return {
    state,
    active: state !== 'idle',
    transcript,
    reply,
    error,
    start,
    stop,
  };
}
