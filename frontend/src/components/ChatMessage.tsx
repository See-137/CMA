import { useState } from 'react';
import { ChevronDown, ChevronRight, Database } from 'lucide-react';
import type { ChatMessage as ChatMessageType, Citation } from '../types';

export function CitationsList({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!citations.length) return null;

  return (
    <div className="mt-2">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-muted transition-colors hover:text-secondary"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Database className="h-3 w-3" />
        {citations.length} {citations.length === 1 ? 'ledger entry' : 'ledger entries'}
      </button>
      {expanded && (
        <div className="mt-1.5 space-y-1 border-l-2 border-token pl-4">
          {citations.map((c, i) => (
            <div key={i} className="text-xs text-muted">
              <span className="mr-1 inline-block rounded bg-gold-400/15 px-1.5 py-0.5 font-semibold uppercase tracking-wide text-gold-700 dark:text-gold-300">
                {c.source_type}
              </span>
              <span>{c.snippet}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface MessageBubbleProps {
  msg: ChatMessageType;
  isStreaming?: boolean;
}

export function MessageBubble({ msg, isStreaming }: MessageBubbleProps) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-3 text-sm leading-relaxed text-white shadow-subtle">
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%]">
        <div className="card-ledger whitespace-pre-wrap rounded-2xl rounded-bl-md px-4 py-3 text-sm leading-relaxed text-primary">
          {msg.content}
          {isStreaming && !msg.content && (
            <span className="inline-flex gap-1">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gold-400" style={{ animationDelay: '0ms' }} />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gold-400" style={{ animationDelay: '150ms' }} />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gold-400" style={{ animationDelay: '300ms' }} />
            </span>
          )}
        </div>
        {msg.citations && <CitationsList citations={msg.citations} />}
      </div>
    </div>
  );
}
