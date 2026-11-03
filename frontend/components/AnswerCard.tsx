"use client";

import { useMemo, useState } from "react";
import { ChatIcon } from "@/components/ChatIcons";

export type AnswerSource = {
  document_id?: string;
  document_name?: string;
  source_file?: string;
  content?: string;
  chunk_index?: number;
  page_number?: number;
  page?: number;
  similarity?: number;
};

export type AnswerMessage = {
  id?: string | number;
  role: "user" | "assistant";
  content: string;
  sources?: AnswerSource[];
  feedback?: "helpful" | "not_helpful" | null;
  low_confidence?: boolean;
  top_similarity?: number;
  follow_up_questions?: string[];
  original_question?: string;
};

const EMPTY_SOURCES: AnswerSource[] = [];

type Props = {
  message: AnswerMessage;
  streaming?: boolean;
  copied?: boolean;
  feedbackBusy?: boolean;
  suggestions?: string[];
  onSourceOpen: (source: AnswerSource) => void;
  onCopy: () => void;
  onRegenerate: () => void;
  onFeedback: (rating: "helpful" | "not_helpful") => void;
  onSuggestion: (text: string) => void;
  onRephrase: (question: string) => void;
};

function groupSources(sources: AnswerSource[]) {
  const groups = new Map<string, { name: string; passages: AnswerSource[] }>();
  for (const source of sources) {
    const name = source.document_name || source.source_file || "Document source";
    const key = source.document_id || name;
    const group = groups.get(key) ?? { name, passages: [] };
    group.passages.push(source);
    groups.set(key, group);
  }
  return [...groups.values()];
}

function groupPageSources(sources: AnswerSource[]) {
  const ordered = [...sources].sort((left, right) => (right.similarity ?? 0) - (left.similarity ?? 0));
  const pages = new Map<string, AnswerSource>();
  ordered.forEach((source, index) => {
    const document = source.document_id || source.document_name || source.source_file || "document";
    const page = source.page_number ?? source.page;
    const pageKey = typeof page === "number" ? `page:${page}` : `chunk:${source.chunk_index ?? index}`;
    const key = `${document}:${pageKey}`;
    const current = pages.get(key);
    if (!current) {
      pages.set(key, { ...source });
      return;
    }
    const excerpts = new Set([current.content, source.content].filter((content): content is string => Boolean(content)));
    current.content = [...excerpts].join("\n\n");
    current.similarity = Math.max(current.similarity ?? 0, source.similarity ?? 0);
  });
  return [...pages.values()];
}

export function AnswerCard({
  message,
  streaming = false,
  copied = false,
  feedbackBusy = false,
  suggestions = [],
  onSourceOpen,
  onCopy,
  onRegenerate,
  onFeedback,
  onSuggestion,
  onRephrase,
}: Props) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const sources = message.sources ?? EMPTY_SOURCES;
  const pageSources = useMemo(() => groupPageSources(sources), [sources]);
  const citationSources = pageSources.slice(0, 5);
  const bestSourceMatch = pageSources.reduce((best, source) => Math.max(best, source.similarity ?? 0), 0);
  const groups = useMemo(() => groupSources(pageSources), [pageSources]);
  const hasInlineCitations = /\[\d+\]/.test(message.content);
  const answerParts = message.content.split(/(\[\d+\])/g);

  if (message.low_confidence) {
    return <article role="status" className="max-w-[90%] rounded-2xl border border-[#d5a34d] bg-[#fff6df] px-4 py-4 text-sm text-[#4e391a] shadow-sm">
      <p className="font-semibold">I couldn&apos;t find a strong match in your documents.</p>
      <p className="mt-1 text-xs text-[#5e452f]">I couldn&apos;t find this in the documents available to you.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => onRephrase(message.original_question ?? "")} className="rounded-lg border border-[#9b6b35] bg-white/70 px-3 py-2 text-xs font-semibold text-[#4e391a] hover:bg-[#f3e5d3]">Rephrase question</button>
      </div>
    </article>;
  }

  return (
    <article className="answer-card max-w-[90%] rounded-2xl border border-[#e8d3b8] bg-white/95 px-4 py-3 text-sm text-[#3d2817] shadow-sm">
      {typeof message.top_similarity === "number" && message.top_similarity >= 0.4 && message.top_similarity <= 0.6 && <span title={`Best match ${Math.round(message.top_similarity * 100)}%. Check the sources.`} aria-label={`Moderate confidence. Best match ${Math.round(message.top_similarity * 100)}%. Check the sources.`} className="mb-2 inline-flex cursor-help rounded-full bg-[#f3e5d3] px-2 py-1 text-[10px] font-semibold text-[#5e452f]">Moderate confidence</span>}
      <p className="whitespace-pre-wrap leading-relaxed">
        {answerParts.map((part, index) => {
          const citation = /^\[(\d+)\]$/.exec(part);
          if (!citation || message.low_confidence) return <span key={`text-${index}`}>{part}</span>;
          const sourceIndex = Number(citation[1]) - 1;
          const source = citationSources[sourceIndex];
          if (!source) return <span key={`text-${index}`}>{part}</span>;
          return (
            <button
              key={`citation-${index}`}
              type="button"
              aria-label={`Source ${sourceIndex + 1}`}
              title={source.page_number ?? source.page ? `p.${source.page_number ?? source.page}` : `Passage ${source.chunk_index !== undefined ? source.chunk_index + 1 : sourceIndex + 1}`}
              onClick={() => onSourceOpen(source)}
              className="citation-chip mx-0.5 inline-flex h-5 min-w-5 cursor-pointer items-center justify-center rounded-full border border-[#b58b61] bg-[#f3e5d3] px-1 text-[10px] font-semibold leading-none text-[#4a2c1a] hover:bg-[#e8d3b8]"
            >
              {citation[0]}
            </button>
          );
        })}
        {!message.low_confidence && !hasInlineCitations && !streaming && citationSources.map((source, index) => (
          <button
            key={`citation-tail-${source.document_id}-${source.chunk_index ?? index}`}
            type="button"
            aria-label={`Source ${index + 1}`}
            title={source.page_number ?? source.page ? `p.${source.page_number ?? source.page}` : `Passage ${source.chunk_index !== undefined ? source.chunk_index + 1 : index + 1}`}
            onClick={() => onSourceOpen(source)}
            className="citation-chip mx-0.5 inline-flex h-5 min-w-5 cursor-pointer items-center justify-center rounded-full border border-[#b58b61] bg-[#f3e5d3] px-1 text-[10px] font-semibold leading-none text-[#4a2c1a] hover:bg-[#e8d3b8]"
          >
            [{index + 1}]
          </button>
        ))}
        {streaming && <span className="stream-cursor ml-0.5" aria-label="Response is streaming" />}
      </p>

      {!message.low_confidence && sources.length > 0 && (
        <section className="mt-3 border-t border-[#e8d3b8] pt-2">
          <button
            type="button"
            aria-expanded={sourcesOpen}
            aria-controls={`sources-${message.id ?? "latest"}`}
            onClick={() => setSourcesOpen((open) => !open)}
            className="flex w-full cursor-pointer items-center justify-between rounded-lg py-1 text-left text-xs font-medium text-[#5e452f] hover:bg-[#faf3e8]"
          >
            <span>Sources: {groups.length} {groups.length === 1 ? "document" : "documents"}, {pageSources.length} {pageSources.length === 1 ? "passage" : "passages"}</span>
            <ChatIcon name="chevron" className={`transition-transform duration-[400ms] ${sourcesOpen ? "rotate-180" : ""}`} />
          </button>
          <div
            id={`sources-${message.id ?? "latest"}`}
            className={`overflow-hidden transition-[max-height,opacity] duration-[400ms] ease-in-out ${sourcesOpen ? "max-h-[900px] opacity-100" : "max-h-0 opacity-0"}`}
            aria-hidden={!sourcesOpen}
          >
            <div className="space-y-3 pb-2 pt-1">
              {groups.map((group) => (
                <div key={group.name}>
                  <h3 className="mb-1 truncate text-xs font-semibold text-[#4a2c1a]" title={group.name}>{group.name}</h3>
                  <ul className="space-y-1.5">
                    {[...group.passages].sort((left, right) => (right.similarity ?? 0) - (left.similarity ?? 0)).map((source, index) => {
                      const lowMatch = bestSourceMatch > 0 && typeof source.similarity === "number" && source.similarity < bestSourceMatch * 0.5;
                      const pageNumber = source.page_number ?? source.page;
                      const label = typeof pageNumber === "number"
                        ? `Page ${pageNumber}`
                        : `Passage ${typeof source.chunk_index === "number" ? source.chunk_index + 1 : index + 1}`;
                      return (
                        <li key={`${source.document_id ?? group.name}-${source.chunk_index ?? index}`}>
                          <button
                            type="button"
                            onClick={() => onSourceOpen(source)}
                            className={`group flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg bg-[#faf3e8] px-2.5 py-2 text-left text-xs text-[#4a3829] hover:bg-[#f3e5d3] ${lowMatch ? "opacity-50" : ""}`}
                          >
                            <span className="min-w-0 truncate">{label}{lowMatch && <span className="ml-2 font-medium">Low match</span>}</span>
                            <span className="flex shrink-0 items-center gap-1 font-medium text-[#5e452f]">
                              {typeof source.similarity === "number" ? `${Math.round(source.similarity * 100)}%` : ""}
                              <span className="flex items-center gap-1 text-[#5e3d21]"><span className="hidden group-hover:inline">Open</span><ChatIcon name="external" size={13} /></span>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {!streaming && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-[#e8d3b8] pt-2" role="group" aria-label="Answer actions">
            <button type="button" onClick={onCopy} className="chat-action" aria-label={copied ? "Copied answer" : "Copy answer"} title={copied ? "Copied" : "Copy"}>
              <ChatIcon name={copied ? "check" : "copy"} /> <span>{copied ? "Copied" : "Copy"}</span>
            </button>
            <button type="button" onClick={onRegenerate} className="chat-action" aria-label="Regenerate answer" title="Regenerate">
              <ChatIcon name="refresh" /> <span>Regenerate</span>
            </button>
            <span className="ml-auto flex items-center gap-1">
              <button type="button" onClick={() => onFeedback("helpful")} disabled={feedbackBusy} aria-label="Mark answer helpful" aria-pressed={message.feedback === "helpful"} className={`chat-icon-action ${message.feedback === "helpful" ? "selected" : ""}`}>
                <ChatIcon name="up" />
              </button>
              <button type="button" onClick={() => onFeedback("not_helpful")} disabled={feedbackBusy} aria-label="Mark answer not helpful" aria-pressed={message.feedback === "not_helpful"} className={`chat-icon-action ${message.feedback === "not_helpful" ? "selected" : ""}`}>
                <ChatIcon name="down" />
              </button>
            </span>
          </div>
          {suggestions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Follow-up suggestions">
              {suggestions.map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => onSuggestion(suggestion)} className="suggestion-chip">
                  {suggestion}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </article>
  );
}
