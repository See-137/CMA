import { useState, useRef, useEffect } from 'react';
import { X, Send, Coins } from 'lucide-react';
import { useChat } from '../hooks/useChat';
import { MessageBubble } from './ChatMessage';

interface ChatDrawerProps {
  open: boolean;
  onClose: () => void;
}

export default function ChatDrawer({ open, onClose }: ChatDrawerProps) {
  const { messages, sendMessage, isStreaming, error, clearHistory } = useChat();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus the input once the open transition finishes (no hardcoded delay race).
  useEffect(() => {
    if (!open) return;
    const node = drawerRef.current;
    if (!node) return;
    const focus = () => inputRef.current?.focus();
    node.addEventListener('transitionend', focus, { once: true });
    const fallback = window.setTimeout(focus, 400);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCloseRef.current();
    document.addEventListener('keydown', onKey);
    return () => {
      node.removeEventListener('transitionend', focus);
      window.clearTimeout(fallback);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    sendMessage(input.trim());
    setInput('');
  };

  return (
    <>
      {open && <div className="fixed inset-0 z-40 bg-ink-950/40" onClick={onClose} />}

      <div
        ref={drawerRef}
        role="dialog"
        aria-label="Ask Scrooge"
        className={`fixed right-0 top-0 z-50 flex h-full w-96 flex-col bg-surface shadow-elevated transition-transform duration-300 ease-in-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="absolute inset-y-0 left-0 w-px bg-gradient-to-b from-transparent via-gold-400/40 to-transparent" />

        {/* Header */}
        <div className="flex items-center justify-between border-b border-token px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gold-leaf">
              <Coins className="h-4 w-4 text-emerald-950" />
            </div>
            <div>
              <h2 className="font-display text-sm font-semibold text-primary">Scrooge</h2>
              <p className="text-[10px] text-muted">Keeper of the books</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {messages.length > 0 && (
              <button
                onClick={clearHistory}
                className="rounded px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-secondary"
              >
                Clear
              </button>
            )}
            <button
              onClick={onClose}
              aria-label="Close"
              className="rounded-lg p-1.5 text-muted transition-colors hover:bg-surface-2 hover:text-primary"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {messages.length === 0 && (
            <div className="py-8 text-center">
              <Coins className="mx-auto mb-3 h-8 w-8 text-gold-400" />
              <p className="mb-1 text-sm text-secondary">Ask about your ledger</p>
              <p className="text-xs text-muted">
                Try "Which agent costs the most?" or "Show me failed requests"
              </p>
            </div>
          )}
          {messages.map((msg, i) => (
            <MessageBubble
              key={i}
              msg={msg}
              isStreaming={isStreaming && i === messages.length - 1 && msg.role === 'assistant'}
            />
          ))}
          {error && (
            <div className="rounded-lg bg-oxblood-500/10 px-3 py-2 text-xs text-oxblood-600 dark:text-oxblood-300">
              {error}
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <form onSubmit={handleSubmit} className="border-t border-token px-4 py-3">
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your costs..."
              disabled={isStreaming}
              className="input flex-1 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={!input.trim() || isStreaming}
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
