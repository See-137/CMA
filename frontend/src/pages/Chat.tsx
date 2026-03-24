import React, { useState, useRef, useEffect } from 'react';
import {
  Send,
  Sparkles,
  Trash2,
  Database,
  ChevronDown,
  ChevronRight,
  TrendingUp,
  AlertTriangle,
  DollarSign,
  ArrowRightLeft,
} from 'lucide-react';
import { useChat } from '../hooks/useChat';
import { api } from '../api/client';
import type { ChatMessage, ChatStatus, Citation } from '../types';

const starterQuestions = [
  {
    text: 'Which agent is burning the most money?',
    icon: DollarSign,
    color: 'text-teal-600 bg-teal-50',
  },
  {
    text: 'Why did costs spike recently?',
    icon: TrendingUp,
    color: 'text-amber-600 bg-amber-50',
  },
  {
    text: 'Show me everything that broke today',
    icon: AlertTriangle,
    color: 'text-rose-600 bg-rose-50',
  },
  {
    text: "GPT-4 vs Claude — who's ripping me off more?",
    icon: ArrowRightLeft,
    color: 'text-violet-600 bg-violet-50',
  },
];

const CitationsList: React.FC<{ citations: Citation[] }> = ({ citations }) => {
  const [expanded, setExpanded] = useState(false);

  if (!citations.length) return null;

  return (
    <div className="mt-2">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 transition-colors"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Database className="h-3 w-3" />
        {citations.length} source{citations.length !== 1 ? 's' : ''}
      </button>
      {expanded && (
        <div className="mt-1.5 space-y-1 pl-4 border-l-2 border-slate-200">
          {citations.map((c, i) => (
            <div key={i} className="text-xs text-slate-500">
              <span className="inline-block px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 font-medium mr-1">
                {c.source_type}
              </span>
              <span className="text-slate-400">{c.snippet}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const MessageBubble: React.FC<{ msg: ChatMessage; isStreaming?: boolean }> = ({
  msg,
  isStreaming,
}) => {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[70%] px-4 py-3 rounded-2xl rounded-br-md bg-teal-600 text-white text-sm leading-relaxed">
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[75%]">
        <div className="px-4 py-3 rounded-2xl rounded-bl-md bg-white border border-slate-200 text-slate-800 text-sm leading-relaxed whitespace-pre-wrap shadow-subtle">
          {msg.content}
          {isStreaming && !msg.content && (
            <span className="inline-flex gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '300ms' }} />
            </span>
          )}
        </div>
        {msg.citations && <CitationsList citations={msg.citations} />}
      </div>
    </div>
  );
};

const Chat: React.FC = () => {
  const { messages, sendMessage, isStreaming, error, clearHistory } = useChat();
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api
      .get<ChatStatus>('/api/v1/chat/status')
      .then(setStatus)
      .catch(() => {});
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

  const handleStarter = (text: string) => {
    sendMessage(text);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* Header */}
      <div className="flex items-center justify-between pb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center h-10 w-10 rounded-xl bg-gradient-to-br from-teal-400 to-teal-600 shadow-glow-teal">
            <Sparkles className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Ask Scrooge</h1>
            <p className="text-sm text-slate-500">
              CMA's cost intelligence assistant
              {status && (
                <span className="text-slate-400">
                  {' '}&middot; {status.events_indexed} events indexed &middot; {status.llm_model}
                </span>
              )}
            </p>
          </div>
        </div>
        {messages.length > 0 && (
          <button
            onClick={clearHistory}
            className="btn-secondary flex items-center gap-2 text-sm"
          >
            <Trash2 className="h-4 w-4" />
            Clear
          </button>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto rounded-xl bg-slate-50 border border-slate-200 p-6">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 ? (
            <div className="py-12 text-center">
              <Sparkles className="h-12 w-12 text-slate-300 mx-auto mb-4" />
              <h2 className="text-lg font-semibold text-slate-700 mb-2">
                Hi, I'm Scrooge.
              </h2>
              <p className="text-sm text-slate-500 mb-8 max-w-md mx-auto">
                Yes, that Scrooge. I've indexed your events, spotted some patterns,
                and I have strong feelings about what I found.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg mx-auto">
                {starterQuestions.map(({ text, icon: Icon, color }) => (
                  <button
                    key={text}
                    onClick={() => handleStarter(text)}
                    className="flex items-center gap-3 px-4 py-3 rounded-xl bg-white border border-slate-200 hover:border-teal-300 hover:shadow-card text-left transition-all group"
                  >
                    <div className={`flex items-center justify-center h-8 w-8 rounded-lg ${color}`}>
                      <Icon className="h-4 w-4" />
                    </div>
                    <span className="text-sm text-slate-600 group-hover:text-slate-900">
                      {text}
                    </span>
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
                <div className="px-4 py-3 rounded-xl bg-rose-50 border border-rose-200 text-rose-600 text-sm">
                  {error}
                </div>
              )}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input area */}
      <form onSubmit={handleSubmit} className="pt-4">
        <div className="max-w-3xl mx-auto flex items-end gap-3">
          <div className="flex-1 relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="What's burning your budget this time?"
              disabled={isStreaming}
              rows={1}
              className="input text-sm py-3 pr-12 resize-none w-full"
            />
          </div>
          <button
            type="submit"
            disabled={!input.trim() || isStreaming}
            className="flex items-center justify-center h-11 w-11 rounded-xl bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shrink-0"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </form>
    </div>
  );
};

export default Chat;
