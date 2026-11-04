import { useState, useCallback, useRef } from 'react';
import { streamChat } from '../api/client';
import type { ChatMessage, Citation } from '../types';

// The API caps history at 50 messages and 100,000 characters: send the newest
// that fit, so a long conversation keeps working instead of failing every send.
const MAX_HISTORY_MESSAGES = 50;
const MAX_HISTORY_CHARS = 100_000;

function recentHistory(messages: { role: string; content: string }[]) {
  const kept: { role: string; content: string }[] = [];
  let chars = 0;
  for (let i = messages.length - 1; i >= 0 && kept.length < MAX_HISTORY_MESSAGES; i--) {
    const { role, content } = messages[i];
    chars += content.length;
    if (chars > MAX_HISTORY_CHARS) break;
    kept.unshift({ role, content });
  }
  return kept;
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || isStreaming) return;

    setError(null);

    const userMsg: ChatMessage = {
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };

    const assistantMsg: ChatMessage = {
      role: 'assistant',
      content: '',
      citations: [],
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    const history = recentHistory(messages);

    abortRef.current = new AbortController();

    try {
      await streamChat(
        text,
        history,
        (citations: Citation[]) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant') {
              updated[updated.length - 1] = { ...last, citations };
            }
            return updated;
          });
        },
        (chunk: string) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant') {
              updated[updated.length - 1] = {
                ...last,
                content: last.content + chunk,
              };
            }
            return updated;
          });
        },
        () => {
          setIsStreaming(false);
        },
        (err: string) => {
          setError(err);
          setIsStreaming(false);
        },
        abortRef.current.signal,
      );
    } catch {
      setError('Failed to connect to chat service');
      setIsStreaming(false);
    }
  }, [isStreaming, messages]);

  const clearHistory = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    setMessages([]);
    setError(null);
    setIsStreaming(false);
  }, []);

  return { messages, sendMessage, isStreaming, error, clearHistory };
}
