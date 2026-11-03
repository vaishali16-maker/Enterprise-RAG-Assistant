"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RequireAuth } from "@/lib/require-auth";
import { useAuth } from "@/lib/auth-context";
import { Sidebar } from "@/components/Sidebar";
import Link from "next/link";
import { useRouter } from "next/navigation";

type Doc = {
  id: string;
  name: string;
  allowed_roles: string[];
  created_at: string;
  uploaded_by?: string;
};

const ALL_ROLES = ["employee", "manager", "hr", "admin"];

export default function DocumentsPage() {
  return (
    <RequireAuth>
      <div className="flex h-screen">
        <Sidebar />
        <div className="m-3 ml-0 flex-1 overflow-y-auto rounded-3xl bg-[#fdf8f3]/80 shadow-lg shadow-[#4a2c1a]/10 backdrop-blur-sm">
          <DocumentsManager />
        </div>
      </div>
    </RequireAuth>
  );
}

function DocumentsManager() {
  const { role, user, apiFetch, apiUpload } = useAuth();
  const router = useRouter();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const docsSnapshot = useRef<Doc[]>([]);
  const pendingDocsLoad = useRef<Promise<boolean> | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadStage, setUploadStage] = useState<"uploading" | "indexing" | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [viewingId, setViewingId] = useState<string | null>(null);

  const normalizedRole = (role ?? "").toLowerCase();
  const canManage = ["admin", "hr", "manager"].includes(normalizedRole);

  const fetchDocs = useCallback(async (): Promise<Doc[]> => {
    const res = await apiFetch("/documents");
    if (!res.ok) throw new Error(await getResponseError(res, "Could not load documents"));
    const data = await res.json();
    return Array.isArray(data.documents) ? data.documents : [];
  }, [apiFetch]);

  const loadDocs = useCallback(async (): Promise<boolean> => {
    if (pendingDocsLoad.current) return pendingDocsLoad.current;

    setLoading(true);
    setError(null);
    const request = (async () => {
      try {
        const loadedDocs = await fetchDocs();
        docsSnapshot.current = loadedDocs;
        setDocs(loadedDocs);
        return true;
      } catch (err) {
        setError(
          err instanceof Error
            ? `${err.message}. ${docsSnapshot.current.length ? "Showing the last loaded list." : ""}`.trim()
            : "Could not load documents. Check that the backend is running."
        );
        return false;
      } finally {
        setLoading(false);
      }
    })();

    pendingDocsLoad.current = request;
    try {
      return await request;
    } finally {
      if (pendingDocsLoad.current === request) pendingDocsLoad.current = null;
    }
  }, [fetchDocs]);

  useEffect(() => {
    let active = true;
    fetchDocs()
      .then((loadedDocs) => {
        if (!active) return;
        docsSnapshot.current = loadedDocs;
        setDocs(loadedDocs);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        const message = err instanceof Error ? err.message : "Could not load documents.";
        setError(`${message}. ${docsSnapshot.current.length ? "Showing the last loaded list." : ""}`.trim());
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [fetchDocs]);

  function toggleRole(r: string) {
    setSelectedRoles((cur) =>
      cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]
    );
  }

  function chooseFile(nextFile?: File) {
    setNotice(null);
    setError(null);
    setSuggestedQuestions([]);
    if (!nextFile) return;
    if (!nextFile.name.toLowerCase().endsWith(".pdf")) {
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setError("Choose a PDF file to upload.");
      return;
    }
    setFile(nextFile);
  }

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setUploadStage("uploading");
    setUploadProgress(0);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("allowed_roles", selectedRoles.join(","));
      const res = await apiUpload(
        "/upload",
        form,
        setUploadProgress,
        () => {
          setUploadProgress(100);
          setUploadStage("indexing");
        }
      );
      if (!res.ok) {
        throw new Error(await getResponseError(res, "Upload failed"));
      }
      const uploadResult = await res.json();
      setSuggestedQuestions(
        Array.isArray(uploadResult.suggested_questions)
          ? uploadResult.suggested_questions.filter((question: unknown): question is string => typeof question === "string")
          : []
      );
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setSelectedRoles([]);
      const refreshed = await loadDocs();
      setNotice(
        refreshed
          ? "Document uploaded and indexed successfully."
          : "Upload succeeded, but the document list could not be refreshed."
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      setUploadStage(null);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this document?")) return;
    setDeletingId(id);
    setError(null);
    setNotice(null);
    try {
      const res = await apiFetch(`/documents/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await getResponseError(res, "Delete failed"));
      const remainingDocs = docsSnapshot.current.filter((doc) => doc.id !== id);
      docsSnapshot.current = remainingDocs;
      setDocs(remainingDocs);
      setNotice("Document deleted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleView(id: string) {
    setViewingId(id);
    setError(null);
    setNotice(null);

    // Open the tab during the click gesture so the browser does not block it
    // while the signed file URL is being fetched.
    const newTab = window.open("about:blank", "_blank");
    if (!newTab) {
      setError("Your browser blocked the document tab. Allow pop-ups for localhost and try again.");
      setViewingId(null);
      return;
    }
    newTab.opener = null;

    try {
      const res = await apiFetch(`/documents/${id}/file`);
      if (!res.ok) throw new Error(await getResponseError(res, "Could not open this document"));
      const data = await res.json();
      if (typeof data.url !== "string" || !data.url) {
        throw new Error("The document file is not available in storage.");
      }
      newTab.location.href = data.url;
    } catch (err) {
      newTab.close();
      setError(err instanceof Error ? err.message : "Could not open this document");
    } finally {
      setViewingId(null);
    }
  }

  if (!canManage) {
    return (
      <div className="p-8">
        <p className="text-sm text-[#a68a6d]">
          You don&apos;t have permission to manage documents.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl p-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[#3d2817]">Documents</h1>
        <button
          type="button"
          onClick={loadDocs}
          disabled={loading}
          className="cursor-pointer rounded-lg px-3 py-1.5 text-sm text-[#6f4523] hover:bg-[#f3e5d3] disabled:cursor-wait disabled:opacity-50"
        >
          {loading ? "Refreshing…" : "Refresh list"}
        </button>
      </div>

      <form
        onSubmit={handleUpload}
        className="mb-8 rounded-2xl border border-[#e8d3b8] bg-white/90 p-5 shadow-sm"
      >
        <h2 className="mb-3 text-sm font-medium text-[#3d2817]">Upload a document</h2>

        <label
          htmlFor="pdf-upload"
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={(e) => { e.preventDefault(); setDragging(false); }}
          onDrop={(e) => { e.preventDefault(); setDragging(false); chooseFile(e.dataTransfer.files[0]); }}
          className={`mb-3 flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-8 text-center ${dragging ? "border-[#6f4523] bg-[#f3e5d3]" : "border-[#d9c3a4] bg-[#faf3e8] hover:border-[#6f4523] hover:bg-[#f3e5d3]"}`}
        >
          <span className="mb-2 text-2xl">📄</span>
          <span className="text-sm font-medium text-[#3d2817]">
            {file ? file.name : "Click to choose a PDF"}
          </span>
          <span className="mt-1 text-xs text-[#a68a6d]">or drag and drop here</span>
          <input
            ref={fileInputRef}
            id="pdf-upload"
            type="file"
            accept="application/pdf"
            onChange={(e) => chooseFile(e.target.files?.[0])}
            className="hidden"
          />
        </label>

        <p className="mb-2 text-xs text-[#a68a6d]">
          Visible to (leave all unchecked for everyone):
        </p>
        <div className="mb-4 flex gap-3">
          {ALL_ROLES.filter((r) => normalizedRole !== "manager" || ["employee", "manager"].includes(r)).map((r) => (
            <label key={r} className="flex items-center gap-1.5 text-sm text-[#6b5744]">
              <input
                type="checkbox"
                checked={selectedRoles.includes(r)}
                onChange={() => toggleRole(r)}
                className="accent-[#6f4523]"
              />
              {r}
            </label>
          ))}
          {normalizedRole === "manager" && <p className="mt-2 text-xs text-[#5e452f]">HR and admin access is always included.</p>}
        </div>
        <button
          type="submit"
          disabled={!file || uploading || loading}
          className="cursor-pointer rounded-xl bg-[#6f4523] px-4 py-2 text-sm font-medium text-white hover:bg-[#5a381c] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? "Uploading…" : "Upload"}
        </button>
      </form>

      {error && <p className="mb-4 text-sm text-red-600" role="alert">{error}</p>}
      {notice && <p className="mb-4 text-sm text-green-700" role="status">{notice}</p>}
      {uploading && (
        <div className="mb-5 rounded-xl border border-[#e8d3b8] bg-white/80 p-3" role="status">
          <p className="mb-2 text-xs text-[#6b5744]">
            {uploadStage === "indexing"
              ? "PDF uploaded. Extracting text and building its search index…"
              : `Uploading PDF… ${uploadProgress}%`}
          </p>
          <div className="h-2 overflow-hidden rounded-full bg-[#f0e2cc]" aria-label="Upload progress">
            <div
              className="h-full rounded-full bg-[#6f4523] transition-[width] duration-200"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      )}
      {suggestedQuestions.length > 0 && (
        <div className="mb-5 rounded-2xl border border-[#e8d3b8] bg-white/90 p-4">
          <h2 className="mb-2 text-sm font-medium text-[#3d2817]">Try asking about this document</h2>
          <ul className="flex flex-wrap gap-2">
            {suggestedQuestions.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  onClick={() => router.push(`/?prompt=${encodeURIComponent(question)}`)}
                  className="cursor-pointer rounded-full border border-[#d9c3a4] px-3 py-1.5 text-left text-xs text-[#6f4523] hover:bg-[#f3e5d3]"
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="rounded-2xl border border-[#e8d3b8] bg-white/90 shadow-sm">
        {loading ? (
          <p className="p-5 text-sm text-[#a68a6d]">Loading…</p>
        ) : docs.length === 0 ? (
          <p className="p-5 text-sm text-[#a68a6d]">No documents yet.</p>
        ) : (
          <ul className="divide-y divide-[#f0e2cc]">
            {docs.map((d) => (
              <li key={d.id} className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm font-medium text-[#3d2817]">{d.name}</p>
                  <p className="text-xs text-[#a68a6d]">
                    {d.allowed_roles?.join(", ") || "all roles"}
                  </p>
                </div>
                <div className="flex items-center gap-4">
                  <Link
                    href={`/documents/${d.id}/graph`}
                    className="text-sm text-[#6f4523] hover:text-[#5a381c]"
                  >
                    View Graph
                  </Link>
                  <button
                    type="button"
                    disabled={deletingId !== null || viewingId !== null}
                    onClick={() => handleDelete(d.id)}
                    hidden={normalizedRole === "manager" && d.uploaded_by !== user?.id}
                    className="cursor-pointer text-sm text-red-600 hover:text-red-700 disabled:cursor-wait disabled:opacity-50"
                  >
                    {deletingId === d.id ? "Deleting…" : "Delete"}
                  </button>
                  <button
                    type="button"
                    disabled={viewingId !== null || deletingId !== null}
                    onClick={() => handleView(d.id)}
                    className="cursor-pointer text-sm text-[#6f4523] hover:text-[#5a381c] disabled:cursor-wait disabled:opacity-50"
                  >
                    {viewingId === d.id ? "Opening…" : "View"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

async function getResponseError(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null);
  const detail = body?.detail;
  return typeof detail === "string" ? detail : `${fallback} (HTTP ${response.status})`;
}
