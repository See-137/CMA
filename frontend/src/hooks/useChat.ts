import { useState, useCallback, useRef } from 'react';
import { streamChat } from '../api/client';
import type { ChatMessage, Citation } from '../types';

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

    const history = messages.map((m) => ({ role: m.role, content: m.content }));

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
