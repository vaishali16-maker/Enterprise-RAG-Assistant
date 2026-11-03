"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { ChatIcon } from "@/components/ChatIcons";
import type { AnswerSource } from "@/components/AnswerCard";

export function SourceDrawer({ source, onClose }: { source: AnswerSource; onClose: () => void }) {
  const { apiFetch } = useAuth();
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [entered, setEntered] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const beginClose = useCallback(() => {
    setEntered(false);
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(onClose, 360);
  }, [onClose]);

  const loadPdf = useCallback(async () => {
    if (!source.document_id) return;
    try {
      const response = await apiFetch(`/documents/${source.document_id}/file`);
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || `Could not open this PDF (${response.status})`);
      }
      const data = await response.json();
      if (!data.url) throw new Error("The PDF is not available in storage.");
      setPdfUrl(data.url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open this PDF.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, source.document_id]);

  useEffect(() => {
    const priorFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = requestAnimationFrame(() => {
      setEntered(true);
      closeButtonRef.current?.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        beginClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not(:disabled), iframe'
      ));
      if (!focusable.length) {
        event.preventDefault();
        closeButtonRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    void Promise.resolve().then(loadPdf);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = priorOverflow;
      if (closeTimer.current) clearTimeout(closeTimer.current);
      priorFocus?.focus();
    };
  }, [beginClose, loadPdf]);

  const title = source.document_name || source.source_file || "Source document";
  const page = source.page_number ?? source.page;
  const pdfHref = pdfUrl && typeof page === "number" ? `${pdfUrl}#page=${page}` : pdfUrl;

  return (
    <div className="fixed inset-0 z-[100] flex justify-end" role="presentation">
      <button
        type="button"
        aria-label="Close source preview"
        tabIndex={-1}
        onClick={beginClose}
        className={`absolute inset-0 bg-black/35 transition-opacity duration-[350ms] ${entered ? "opacity-100" : "opacity-0"}`}
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-drawer-title"
        className={`source-drawer relative z-10 flex h-full w-full max-w-2xl transform flex-col border-l border-[#d8bea0] bg-[#fdf8f3] shadow-2xl transition-transform duration-[350ms] ease-out ${entered ? "translate-x-0" : "translate-x-full"}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-3 border-b border-[#e8d3b8] px-5 py-4">
          <div className="min-w-0">
            <h2 id="source-drawer-title" className="truncate text-sm font-semibold text-[#3d2817]" title={title}>{title}</h2>
            <p className="mt-1 text-xs text-[#5e452f]">
              {typeof page === "number" ? `Page ${page}` : `Passage ${(source.chunk_index ?? 0) + 1}`}
            </p>
          </div>
          <button ref={closeButtonRef} type="button" onClick={beginClose} aria-label="Close PDF preview" className="chat-icon-action">
            <ChatIcon name="close" size={18} />
          </button>
        </header>

        {source.content && (
          <div className="border-b border-[#e8d3b8] bg-[#faf3e8] px-5 py-3">
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#5e452f]">Cited passage</p>
            <blockquote className="rounded-md border-l-4 border-[#9b6b35] bg-[#fff0c2] px-3 py-2 text-sm leading-relaxed text-[#3d2817]">
              <mark className="bg-transparent text-inherit">{source.content}</mark>
            </blockquote>
          </div>
        )}

        <div className="flex min-h-0 flex-1 flex-col p-4">
          {loading ? (
            <div className="flex-1 animate-pulse rounded-xl bg-[#eee1d0]" aria-label="Loading PDF preview" />
          ) : error ? (
            <div className="m-auto max-w-sm text-center" role="alert">
              <p className="mb-3 text-sm text-[#6b2e2e]">{error}</p>
              <button
                type="button"
                onClick={() => { setLoading(true); setError(null); void loadPdf(); }}
                className="chat-action border border-[#b58b61]"
              >Retry</button>
            </div>
          ) : pdfHref ? (
            <iframe
              title={`PDF preview: ${title}`}
              src={pdfHref}
              className="min-h-0 flex-1 rounded-lg border border-[#d8bea0] bg-white"
            />
          ) : null}
        </div>

        <footer className="border-t border-[#e8d3b8] px-5 py-3">
          {pdfHref ? (
            <a href={pdfHref} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-[#5e3d21] underline underline-offset-2 hover:bg-[#f3e5d3]">
              Open in new tab <ChatIcon name="external" size={15} />
            </a>
          ) : (
            <span className="text-xs text-[#5e452f]">PDF preview unavailable</span>
          )}
        </footer>
      </aside>
    </div>
  );
}
