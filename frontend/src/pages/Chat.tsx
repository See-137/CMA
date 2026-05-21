import { useState, useRef, useEffect } from 'react';
import {
  Send,
  Trash2,
  Coins,
  TrendingUp,
  AlertTriangle,
  ArrowRightLeft,
} from 'lucide-react';
import { useChat } from '../hooks/useChat';
import { api } from '../api/client';
import { MessageBubble } from '../components/ChatMessage';
import type { ChatStatus } from '../types';

const starterQuestions = [
  { text: 'Which agent is burning the most money?', icon: Coins },
  { text: 'Why did costs spike recently?', icon: TrendingUp },
  { text: 'Show me everything that broke today', icon: AlertTriangle },
  { text: "GPT-4 vs Claude — who's robbing me blind?", icon: ArrowRightLeft },
];

export default function Chat() {
  const { messages, sendMessage, isStreaming, error, clearHistory } = useChat();
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api.get<ChatStatus>('/api/v1/chat/status').then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    sendMessage(input.trim());
    setInput('');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      {/* Header */}
      <header className="flex items-center justify-between pb-4">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gold-leaf shadow-glow-gold">
            <Coins className="h-[22px] w-[22px] text-emerald-950" />
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight text-primary">
              Ask Scrooge
            </h1>
            <p className="text-sm text-muted">
              Keeper of the books
              {status && (
                <span className="numeral">
                  {' '}· {status.events_indexed} entries indexed · {status.llm_model}
                </span>
              )}
            </p>
          </div>
        </div>
        {messages.length > 0 && (
          <button onClick={clearHistory} className="btn-secondary text-sm">
            <Trash2 className="h-4 w-4" />
            Clear
          </button>
        )}
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto rounded-xl border border-token bg-surface-2 p-6">
        <div className="mx-auto max-w-3xl space-y-4">
          {messages.length === 0 ? (
            <div className="py-12 text-center">
              <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-gold-leaf shadow-glow-gold">
                <Coins className="h-8 w-8 text-emerald-950" />
              </div>
              <h2 className="font-display text-xl font-semibold text-primary">
                Bah. Humbug. Let's see the damage.
              </h2>
              <p className="mx-auto mb-8 mt-2 max-w-md text-sm text-muted">
                I'm Scrooge — I've counted every token your agents have squandered, and
                I have opinions. Ask away.
              </p>

              <div className="mx-auto grid max-w-lg grid-cols-1 gap-3 sm:grid-cols-2">
                {starterQuestions.map(({ text, icon: Icon }) => (
                  <button
                    key={text}
                    onClick={() => sendMessage(text)}
                    className="group flex items-center gap-3 rounded-xl border border-token bg-surface px-4 py-3 text-left transition-all hover:-translate-y-px hover:border-gold-400/60 hover:shadow-card"
                  >
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gold-400/15 text-gold-700 dark:text-gold-300">
                      <Icon className="h-4 w-4" />
                    </div>
                    <span className="text-sm text-secondary group-hover:text-primary">{text}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map((msg, i) => (
                <MessageBubble
                  key={i}
                  msg={msg}
                  isStreaming={isStreaming && i === messages.length - 1 && msg.role === 'assistant'}
                />
              ))}
              {error && (
                <div className="rounded-xl border border-oxblood-500/30 bg-oxblood-500/10 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
                  {error}
                </div>
              )}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="pt-4">
        <div className="mx-auto flex max-w-3xl items-end gap-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="What's bleeding your budget this time?"
            disabled={isStreaming}
            rows={1}
            className="input w-full resize-none py-3 text-sm"
          />
          <button
            type="submit"
            disabled={!input.trim() || isStreaming}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </form>
    </div>
  );
}
