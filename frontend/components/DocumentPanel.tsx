"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { ChatIcon } from "@/components/ChatIcons";

type Doc = { id: string; name: string; allowed_roles: string[]; suggested_questions?: string[] };

export function DocumentPanel({ selectedDocumentIds, onSelectionChange, onDocumentsLoaded, mobileOpen = false, onClose = () => {} }: {
  selectedDocumentIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onDocumentsLoaded: (docs: Doc[]) => void;
  mobileOpen?: boolean;
  onClose?: () => void;
}) {
  const { apiFetch, role } = useAuth();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const canManage = ["admin", "hr", "manager"].includes((role ?? "").toLowerCase());
  const loadDocs = useCallback(() => {
    setLoading(true); setLoadError(false);
    return apiFetch("/documents").then((response) => {
      if (!response.ok) throw new Error("Unable to load documents");
      return response.json();
    }).then((data) => {
      const nextDocs: Doc[] = data.documents || [];
      setDocs(nextDocs);
      onDocumentsLoaded(nextDocs);
    }).catch(() => setLoadError(true)).finally(() => setLoading(false));
  }, [apiFetch, onDocumentsLoaded]);
  useEffect(() => { void Promise.resolve().then(loadDocs); }, [loadDocs]);
  const count = selectedDocumentIds.length || docs.length;

  return <><button aria-label="Close knowledge base" onClick={onClose} className={`mobile-drawer-backdrop ${mobileOpen ? "is-open" : ""}`} /><aside className={`document-panel app-knowledge m-3 ml-0 flex w-64 flex-col rounded-3xl bg-[#fdf8f3]/95 p-4 shadow-lg shadow-[#4a2c1a]/10 backdrop-blur-sm ${mobileOpen ? "mobile-open" : ""}`}>
    <div className="mb-3 flex items-center justify-between gap-2"><p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[#5e452f]"><ChatIcon name="library" size={15} /> Knowledge Base</p>{canManage && <Link href="/documents" onClick={onClose} className="rounded-lg border border-[#d8bea0] px-3 py-1.5 text-xs font-medium text-[#5e3d21] hover:bg-[#f3e5d3]">Manage</Link>}</div>
    {loading ? <div className="space-y-2" aria-label="Loading documents"><div className="skeleton h-14 rounded-xl"/><div className="skeleton h-14 rounded-xl"/></div> : loadError ? <div role="alert" className="text-xs text-[#6b2e2e]">Could not load documents. Check that the local API is running.<button onClick={() => void loadDocs()} className="mt-2 block rounded-lg border border-[#b58b61] px-3 py-1.5 text-[#5e3d21]">Retry</button></div> : docs.length === 0 ? <p className="text-xs text-[#5e452f]">No documents available to you yet.</p> : <>
      <p className="mb-2 text-[11px] text-[#5e452f]">Searching {count} of {docs.length} documents</p>
      <button type="button" onClick={() => onSelectionChange([])} disabled={!selectedDocumentIds.length} className="mb-2 cursor-pointer text-left text-xs text-[#5e3d21] underline underline-offset-2 disabled:cursor-default disabled:no-underline disabled:opacity-60">Search all documents</button>
      <ul className="space-y-2 overflow-y-auto">{docs.map((doc) => {
        const ext = doc.name.split(".").pop()?.toLowerCase();
        const fileType = ext === "pdf" ? "PDF" : ["doc", "docx"].includes(ext || "") ? "DOC" : (ext || "FILE").toUpperCase();
        return <li key={doc.id} className="rounded-xl bg-white/80 px-3 py-2"><label className="flex cursor-pointer items-start gap-2">
          <input type="checkbox" checked={selectedDocumentIds.includes(doc.id)} onChange={() => onSelectionChange(selectedDocumentIds.includes(doc.id) ? selectedDocumentIds.filter((id) => id !== doc.id) : [...selectedDocumentIds, doc.id])} className="mt-0.5 accent-[#6f4523]" aria-label={`Limit search to ${doc.name}`} />
          <span className="flex min-w-0 flex-1 items-start gap-1.5"><ChatIcon name="file" size={15} className="mt-0.5 shrink-0 text-[#6f4523]"/><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-[#3d2817]" title={doc.name}>{doc.name}</span><span className="mb-1 block text-[9px] font-semibold tracking-wide text-[#5e452f]">{fileType}</span><span className="flex flex-wrap gap-1">{doc.allowed_roles?.length ? doc.allowed_roles.map((r) => <span key={r} className="rounded-full bg-[#f3e5d3] px-1.5 py-0.5 text-[9px] font-medium text-[#5e452f]">{r}</span>) : <span className="text-[10px] text-[#5e452f]">all roles</span>}</span></span></span>
        </label></li>;
      })}</ul>
    </>}
  </aside></>;
}
