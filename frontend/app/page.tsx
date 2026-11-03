"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RequireAuth } from "@/lib/require-auth";
import { useAuth } from "@/lib/auth-context";
import { Sidebar } from "@/components/Sidebar";
import { DocumentPanel } from "@/components/DocumentPanel";
import { AnswerCard, type AnswerMessage, type AnswerSource } from "@/components/AnswerCard";
import { ChatIcon } from "@/components/ChatIcons";
import { SourceDrawer } from "@/components/SourceDrawer";

export default function HomePage() {
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [documentSuggestions, setDocumentSuggestions] = useState<Record<string, string[]>>({});
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [mobileDrawer, setMobileDrawer] = useState<"history" | "knowledge" | null>(null);
  const handleDocumentsLoaded = useCallback((docs: { id: string; name: string; suggested_questions?: string[] }[]) => {
    setDocumentSuggestions(Object.fromEntries(docs.map((doc) => [doc.id, doc.suggested_questions ?? []])));
    setTotalDocuments(docs.length);
  }, []);
  const suggestions = [...new Set((selectedDocumentIds.length ? selectedDocumentIds : Object.keys(documentSuggestions))
    .flatMap((id) => documentSuggestions[id] ?? []))].slice(0, 4);
  return (
    <RequireAuth>
      <div className="flex h-screen bg-[#f8f1e8]">
        <Sidebar mobileOpen={mobileDrawer === "history"} onClose={() => setMobileDrawer(null)} />
        <Chat selectedDocumentIds={selectedDocumentIds} totalDocuments={totalDocuments} emptySuggestions={suggestions} onOpenDrawer={setMobileDrawer} />
        <DocumentPanel
          selectedDocumentIds={selectedDocumentIds}
          onSelectionChange={setSelectedDocumentIds}
          onDocumentsLoaded={handleDocumentsLoaded}
          mobileOpen={mobileDrawer === "knowledge"}
          onClose={() => setMobileDrawer(null)}
        />
      </div>
    </RequireAuth>
  );
}

function Chat({ selectedDocumentIds, totalDocuments, emptySuggestions, onOpenDrawer }: { selectedDocumentIds: string[]; totalDocuments: number; emptySuggestions: string[]; onOpenDrawer: (drawer: "history" | "knowledge") => void }) {
  const { apiFetch } = useAuth();
  const searchParams = useSearchParams();
  const conversationId = searchParams.get("conversation");
  const promptKey = searchParams.get("prompt");
  const [messageState, setMessageState] = useState<{
    conversationId: string | null;
    items: AnswerMessage[];
  }>({ conversationId, items: [] });
  const messages = useMemo(
    () => messageState.conversationId === conversationId ? messageState.items : [],
    [messageState, conversationId]
  );
  const [draft, setDraft] = useState({ prompt: promptKey, value: promptKey ?? "" });
  const input = draft.prompt === promptKey ? draft.value : promptKey ?? "";
  const [busy, setBusy] = useState(false);
  const [streamingId, setStreamingId] = useState<string | number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedbackBusyId, setFeedbackBusyId] = useState<string | number | null>(null);
  const [copiedId, setCopiedId] = useState<string | number | null>(null);
  const [sourceDrawer, setSourceDrawer] = useState<AnswerSource | null>(null);
  const [conversationTitle, setConversationTitle] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stopReveal = useRef(false);
  const localMessageCounter = useRef(0);

  const closeSourceDrawer = useCallback(() => setSourceDrawer(null), []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamingId]);

  useEffect(() => {
    if (!conversationId) return;
    let active = true;
    apiFetch(`/conversations/${conversationId}/messages`)
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.detail || `Could not load conversation (${response.status})`);
        }
        return response.json();
      })
      .then((data) => {
        if (active) {
          setMessageState({ conversationId, items: data.messages || [] });
          setError(null);
        }
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Could not load conversation");
      });
    return () => { active = false; };
  }, [conversationId, apiFetch]);

  useEffect(() => {
    if (!conversationId) return;
    let active = true;
    void Promise.resolve().then(() => apiFetch("/conversations")).then((response) => response.ok ? response.json() : null).then((data) => {
      const match = data?.conversations?.find((item: { id: string; title: string }) => item.id === conversationId);
      if (active && match) setConversationTitle(match.title);
    }).catch(() => {});
    return () => { active = false; };
  }, [conversationId, apiFetch]);

  async function sendQuestion(rawQuestion: string, appendUser = true) {
    const question = rawQuestion.trim();
    if (!question || busy) return;
    stopReveal.current = false;
    if (appendUser) {
      setMessageState((current) => ({
        conversationId,
        items: [
          ...(current.conversationId === conversationId ? current.items : []),
          { role: "user", content: question },
        ],
      }));
    }
    setDraft({ prompt: promptKey, value: "" });
    setBusy(true);
    setError(null);

    try {
      const response = await apiFetch("/ask/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, conversation_id: conversationId, document_ids: selectedDocumentIds }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || `Request failed (${response.status})`);
      }
      const data = await response.json();
      const nextConversationId = data.conversation_id as string;
      localMessageCounter.current += 1;
      const assistantId = (data.assistant_message_id ?? `local-${localMessageCounter.current}`) as string | number;
      if (!conversationId) {
        window.history.replaceState(null, "", `/?conversation=${nextConversationId}`);
        window.dispatchEvent(new Event("conversations:refresh"));
      }

      setMessageState((current) => ({
        conversationId: nextConversationId,
        items: [
          ...(current.conversationId === conversationId ? current.items : []),
          { id: assistantId, role: "assistant", content: data.answer || "", sources: data.sources || [], low_confidence: Boolean(data.low_confidence), top_similarity: data.top_similarity, follow_up_questions: data.follow_up_questions, original_question: question },
        ],
      }));
      if (data.low_confidence) { setStreamingId(null); return; }
      setMessageState((current) => ({ ...current, items: current.items.map((message) => message.id === assistantId ? { ...message, content: "" } : message) }));
      setStreamingId(assistantId);

      // The existing chat API returns JSON after generation, so reveal its answer in paced text chunks.
      const tokens: string[] = data.answer.match(/\S+\s*/g) ?? [data.answer];
      for (let tokenIndex = 0; tokenIndex < tokens.length; tokenIndex += 1) {
        if (stopReveal.current) break;
        const visibleAnswer = tokens.slice(0, tokenIndex + 1).join("");
        setMessageState((current) => ({
          ...current,
          items: current.items.map((message) => message.id === assistantId
            ? { ...message, content: visibleAnswer }
            : message),
        }));
        await new Promise((resolve) => setTimeout(resolve, 22));
      }
      setStreamingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    void sendQuestion(input);
  }

  function stopStreaming() {
    stopReveal.current = true;
    setStreamingId(null);
  }

  async function openSourceDocument(source: AnswerSource) {
    setSourceDrawer(source);
  }

  async function submitFeedback(message: AnswerMessage, rating: "helpful" | "not_helpful") {
    if (message.id === undefined || String(message.id).startsWith("local-")) return;
    setFeedbackBusyId(message.id);
    setError(null);
    try {
      const response = await apiFetch(`/messages/${message.id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || `Could not save feedback (${response.status})`);
      }
      setMessageState((current) => ({
        ...current,
        items: current.items.map((item) => item.id === message.id ? { ...item, feedback: rating } : item),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save feedback");
    } finally {
      setFeedbackBusyId(null);
    }
  }

  async function copyAnswer(message: AnswerMessage) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedId(message.id ?? null);
      window.setTimeout(() => setCopiedId(null), 1600);
    } catch {
      setError("Clipboard access was blocked by the browser.");
    }
  }

  function regenerateAnswer(messageIndex: number) {
    const previousQuestion = [...messages.slice(0, messageIndex)].reverse().find((message) => message.role === "user");
    if (previousQuestion) void sendQuestion(previousQuestion.content, false);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendQuestion(input);
    }
  }

  const isLoadingConversation = Boolean(
    conversationId && messageState.conversationId !== conversationId && !error
  );

  return (
    <div className="m-3 ml-0 flex min-w-0 flex-1 flex-col rounded-3xl bg-[var(--cream)] shadow-lg shadow-[#4a2c1a]/10">
      <header className="flex items-center gap-2 border-b border-[var(--border-soft)] px-4 py-4 sm:px-6">
        <button type="button" aria-label="Open conversation history" onClick={() => onOpenDrawer("history")} className="mobile-sidebar-trigger chat-icon-action"><ChatIcon name="menu" size={18} /></button>
        <div className="min-w-0 flex-1 text-center">
        <h1 className="text-sm font-semibold text-[var(--brown-deep)]">
          {conversationId ? conversationTitle || "Conversation" : "New conversation"}
        </h1>
        <p className="mt-1 text-xs text-[var(--brown-subtle)]">
          Searching {selectedDocumentIds.length || totalDocuments} of {totalDocuments} documents
        </p>
        </div>
        <button type="button" aria-label="Open knowledge base" onClick={() => onOpenDrawer("knowledge")} className="mobile-sidebar-trigger chat-icon-action"><ChatIcon name="library" size={18} /></button>
      </header>

      <main className="flex-1 overflow-y-auto scroll-pt-8 px-6 pb-6 pt-10" aria-live="polite" aria-busy={busy}>
        {messages.length === 0 && !isLoadingConversation && !error && (
          <div className="mx-auto max-w-2xl pt-14">
            <div className="mb-7 text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#6f4523] text-white">
                <ChatIcon name="library" size={24} />
              </div>
              <h2 className="text-base font-semibold text-[var(--brown-deep)]">What would you like to know?</h2>
              <p className="mt-1 text-sm text-[var(--brown-subtle)]">Ask a question grounded in the documents you can access.</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {emptySuggestions.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => setDraft({ prompt: promptKey, value: prompt })}
                  className="rounded-xl border border-[var(--border-soft)] bg-white/90 p-3 text-left text-sm text-[#4a3829] shadow-sm transition-colors hover:bg-[#f3e5d3]"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {isLoadingConversation && (
          <div className="mx-auto max-w-2xl space-y-4" aria-label="Loading conversation">
            <div className="chat-skeleton ml-auto h-12 w-48 rounded-2xl" />
            <div className="chat-skeleton h-28 w-3/4 rounded-2xl" />
          </div>
        )}

        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {messages.map((message, index) => message.role === "user" ? (
            <div key={message.id ?? `user-${index}`} className="ml-auto max-w-[85%] scroll-mt-8 whitespace-pre-wrap rounded-2xl bg-[#6f4523] px-4 py-3 text-sm text-white shadow-sm">
              {message.content}
            </div>
          ) : (
            <AnswerCard
              key={message.id ?? `assistant-${index}`}
              message={message}
              streaming={streamingId === message.id}
              copied={copiedId === message.id}
              feedbackBusy={feedbackBusyId === message.id}
              suggestions={index === messages.length - 1 ? message.follow_up_questions ?? [] : []}
              onSourceOpen={openSourceDocument}
              onCopy={() => void copyAnswer(message)}
              onRegenerate={() => regenerateAnswer(index)}
              onFeedback={(rating) => void submitFeedback(message, rating)}
              onSuggestion={(text) => setDraft({ prompt: promptKey, value: text })}
              onRephrase={(question) => { const previous = [...messages.slice(0, index)].reverse().find((item) => item.role === "user"); setDraft({ prompt: promptKey, value: question || previous?.content || "" }); inputRef.current?.focus(); }}
            />
          ))}
          {busy && streamingId === null && (
            <div className="chat-skeleton h-12 max-w-40 rounded-2xl px-4 py-3 text-sm text-[var(--brown-subtle)]" role="status">
              Thinking…
            </div>
          )}
          {error && (
            <div className="flex items-center gap-3 rounded-xl border border-[#c98e8e] bg-[#fff5f3] px-3 py-2 text-sm text-[#762e2e]" role="alert">
              <p className="flex-1">{error}</p>
              {conversationId && (
                <button type="button" onClick={() => window.location.reload()} className="chat-action border border-[#9b6868] text-[#762e2e]">
                  Retry
                </button>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      <form onSubmit={handleSubmit} className="border-t border-[var(--border-soft)] px-6 py-4">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setDraft({ prompt: promptKey, value: event.target.value })}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question…"
            rows={1}
            className="max-h-36 min-h-12 flex-1 resize-y rounded-xl border border-[#c8aa87] bg-white px-3 py-3 text-sm text-[var(--brown-deep)] placeholder:text-[#5e452f] focus:border-[#6f4523] focus:outline-none"
          />
          {streamingId !== null ? (
            <button type="button" onClick={stopStreaming} className="inline-flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-[#9a7149] bg-[#f3e5d3] px-4 text-sm font-semibold text-[#3d2817] hover:bg-[#e8d3b8]">
              <ChatIcon name="stop" /> Stop
            </button>
          ) : busy ? (
            <button type="button" disabled className="inline-flex min-h-12 cursor-wait items-center gap-2 rounded-xl bg-[#bba58e] px-4 text-sm font-semibold text-white">
              Thinking…
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="inline-flex min-h-12 cursor-pointer items-center gap-2 rounded-xl bg-[#6f4523] px-4 text-sm font-semibold text-white transition-colors hover:bg-[#5a381c] disabled:cursor-not-allowed disabled:bg-[#d4c2ae] disabled:text-[#6b5744]"
            >
              Send <ChatIcon name="send" />
            </button>
          )}
        </div>
        <p className="mx-auto mt-1 max-w-3xl text-[11px] text-[#5e452f]">Enter to send · Shift+Enter for a new line</p>
      </form>

      {sourceDrawer && <SourceDrawer source={sourceDrawer} onClose={closeSourceDrawer} />}
    </div>
  );
}
