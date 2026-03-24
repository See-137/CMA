import React, { useState, useRef, useEffect } from 'react';
import { X, Send, ChevronDown, ChevronRight, Database, Sparkles } from 'lucide-react';
import { useChat } from '../hooks/useChat';
import type { ChatMessage, Citation } from '../types';

interface ChatDrawerProps {
  open: boolean;
  onClose: () => void;
}

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
        <div className="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-teal-600 text-white text-sm">
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%]">
        <div className="px-4 py-2.5 rounded-2xl rounded-bl-md bg-slate-100 text-slate-800 text-sm whitespace-pre-wrap">
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

const ChatDrawer: React.FC<ChatDrawerProps> = ({ open, onClose }) => {
  const { messages, sendMessage, isStreaming, error, clearHistory } = useChat();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [open]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    sendMessage(input.trim());
    setInput('');
  };

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div className="fixed inset-0 bg-black/20 z-40" onClick={onClose} />
      )}

      {/* Drawer */}
      <div
        className={`fixed top-0 right-0 h-full w-96 bg-white shadow-elevated z-50 flex flex-col transition-transform duration-300 ease-in-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center h-8 w-8 rounded-lg bg-teal-50">
              <Sparkles className="h-4 w-4 text-teal-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-slate-900">Scrooge</h2>
              <p className="text-[10px] text-slate-400">CMA's cost intelligence</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {messages.length > 0 && (
              <button
                onClick={clearHistory}
                className="text-xs text-slate-400 hover:text-slate-600 px-2 py-1 rounded hover:bg-slate-50"
              >
                Clear
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
            >
              <X className="h-4 w-4 text-slate-400" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.length === 0 && (
            <div className="text-center py-8">
              <Sparkles className="h-8 w-8 text-slate-300 mx-auto mb-3" />
              <p className="text-sm text-slate-500 mb-1">Ask about your LLM costs</p>
              <p className="text-xs text-slate-400">
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
            <div className="px-3 py-2 rounded-lg bg-rose-50 text-rose-600 text-xs">
              {error}
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <form onSubmit={handleSubmit} className="px-4 py-3 border-t border-slate-200">
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your costs..."
              disabled={isStreaming}
              className="flex-1 input text-sm py-2"
            />
            <button
              type="submit"
              disabled={!input.trim() || isStreaming}
              className="flex items-center justify-center h-9 w-9 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </form>
      </div>
    </>
  );
};

export default ChatDrawer;
