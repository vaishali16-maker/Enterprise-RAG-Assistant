"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useConversations } from "@/lib/conversations";
import { ChatIcon } from "@/components/ChatIcons";

export function Sidebar({ mobileOpen = false, onClose = () => {} }: { mobileOpen?: boolean; onClose?: () => void }) {
  const { user, role, signOut, apiFetch } = useAuth();
  const { conversations, loading, error, deleteConversation } = useConversations();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [stats, setStats] = useState<{ cache_hit_rate: number; avg_latency_ms: number; total_requests: number } | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const [renames, setRenames] = useState<Record<string, string>>({});
  const activeConvoId = searchParams.get("conversation");
  const canManageDocs = ["admin", "hr", "manager"].includes((role ?? "").toLowerCase());
  const isAdmin = (role ?? "").toLowerCase() === "admin";

  const grouped = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    const seen = new Set<string>();
    const groups: Record<string, typeof conversations> = { Today: [], Yesterday: [], Earlier: [] };
    for (const item of conversations) {
      const title = (renames[item.id] || item.title || "New conversation").replace(/^['"“”]+|['"“”]+$/g, "").trim() || "New conversation";
      if (seen.has(title.toLowerCase())) continue;
      seen.add(title.toLowerCase());
      const created = new Date(item.created_at);
      groups[created >= today ? "Today" : created >= yesterday ? "Yesterday" : "Earlier"].push({ ...item, title });
    }
    return groups;
  }, [conversations, renames]);

  useEffect(() => {
    let active = true;
    const fetchStats = () => { apiFetch("/stats").then((response) => response.ok ? response.json() : null).then((value) => { if (active && value) setStats(value); }).catch(() => {}); };
    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => { active = false; clearInterval(interval); };
  }, [apiFetch]);

  const initials = (user?.email || "?").slice(0, 2).toUpperCase();
  async function removeConversation(id: string, title: string) {
    if (!confirm(`Delete “${title}” and all its messages?`)) return;
    setOpenMenuId(null);
    const deleted = await deleteConversation(id);
    if (deleted && activeConvoId === id) router.push("/");
  }

  return <><button aria-label="Close history" onClick={onClose} className={`mobile-drawer-backdrop ${mobileOpen ? "is-open" : ""}`} /><aside className={`app-sidebar m-3 flex h-[calc(100vh-1.5rem)] w-64 flex-col rounded-3xl bg-[#fdf8f3]/95 p-3 shadow-lg shadow-[#4a2c1a]/10 backdrop-blur-sm ${mobileOpen ? "mobile-open" : ""}`}>
    <div className="flex items-center gap-2 px-2 py-3"><div className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#6f4523] text-sm">🤖</div><span className="text-sm font-semibold text-[#3d2817]">Knowledge Assistant</span></div>
    <button onClick={() => { router.push("/"); onClose(); }} className="mb-3 flex w-full items-center gap-2 rounded-2xl bg-[#6f4523] px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-[#5a381c]">+ New chat</button>
    {canManageDocs && <Link href="/documents" onClick={onClose} className={`mb-3 flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium ${pathname === "/documents" ? "bg-[#e8d3b8] text-[#3d2817]" : "text-[#5e452f] hover:bg-[#f3e5d3]/60"}`}><ChatIcon name="file" size={15} /> Document Uploads</Link>}
    <div className="flex-1 overflow-y-auto"><p className="px-3 text-xs font-semibold uppercase tracking-wide text-[#5e452f]">History</p><p className="mb-1 px-3 text-[10px] text-[#5e452f]">Private to your account</p>
      {loading ? <div aria-label="Loading history" className="space-y-2 px-2 py-2"><div className="skeleton h-8 rounded-xl"/><div className="skeleton h-8 rounded-xl"/></div> : error ? <p className="px-3 py-2 text-xs text-red-800" role="alert">{error}</p> : conversations.length === 0 ? <p className="px-3 py-2 text-xs text-[#5e452f]">No conversations yet.</p> : Object.entries(grouped).map(([group, entries]) => entries.length > 0 && <section key={group}><p className="px-3 pt-2 text-[10px] font-semibold uppercase tracking-wide text-[#5e452f]">{group}</p><ul>{entries.map((item) => <li key={item.id} className="group relative flex items-center gap-1 rounded-2xl px-1" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpenMenuId(null); }} onKeyDown={(event) => { if (event.key === "Escape") setOpenMenuId(null); }}>
        <Link href={`/?conversation=${item.id}`} onClick={onClose} title={item.title} className={`min-w-0 flex-1 truncate rounded-2xl px-3 py-1.5 pr-8 text-sm ${activeConvoId === item.id ? "bg-[#e8d3b8] font-medium text-[#3d2817]" : "text-[#5e452f] hover:bg-[#f3e5d3]/60"}`}>{item.title}</Link>
        <button type="button" aria-label={`Actions for ${item.title}`} aria-haspopup="menu" aria-expanded={openMenuId === item.id} onClick={() => setOpenMenuId((value) => value === item.id ? null : item.id)} className="absolute right-1 top-1/2 -translate-y-1/2 cursor-pointer rounded-lg px-1.5 py-0.5 text-lg leading-none text-[#5e452f] opacity-0 hover:bg-[#e8d3b8] hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-100">⋯</button>
        {openMenuId === item.id && <div role="menu" className="absolute right-1 top-full z-30 mt-1 min-w-36 rounded-xl border border-[#e8d3b8] bg-white p-1.5 shadow-lg">
          <button type="button" role="menuitem" onClick={() => { const title = prompt("Rename conversation", item.title); if (title?.trim()) setRenames((value) => ({ ...value, [item.id]: title.trim().replace(/^['"“”]+|['"“”]+$/g, "") })); setOpenMenuId(null); }} className="w-full rounded-lg px-3 py-2 text-left text-xs text-[#5e3d21] hover:bg-[#f3e5d3]">Rename chat</button>
          <button type="button" role="menuitem" onClick={() => void removeConversation(item.id, item.title)} className="w-full rounded-lg px-3 py-2 text-left text-xs text-red-800 hover:bg-red-50">Delete chat</button>
        </div>}
      </li>)}</ul></section>)}
    </div>
    {isAdmin && <><button type="button" aria-expanded={debugOpen} onClick={() => setDebugOpen((value) => !value)} className="mb-2 rounded-lg px-3 py-1.5 text-left text-xs font-medium text-[#5e452f] hover:bg-[#f3e5d3]">{debugOpen ? "▾" : "▸"} Debug</button>{debugOpen && stats && stats.total_requests > 0 && <div className="mb-2 rounded-2xl bg-[#f3e5d3]/70 px-3 py-2 text-xs text-[#5e452f]"><p className="mb-1 font-semibold uppercase tracking-wide text-[10px]">Usage</p><p>{stats.total_requests} requests · {Math.round(stats.cache_hit_rate * 100)}% cached</p><p>avg {stats.avg_latency_ms}ms response</p></div>}</>}
    <div className="mt-2 border-t border-[#e8d3b8] pt-2"><div className="flex items-center gap-2 rounded-2xl px-2 py-2 hover:bg-[#f3e5d3]/60"><div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#6f4523] text-xs font-semibold text-white">{initials}</div><div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-[#3d2817]">{user?.email}</p><p className="text-xs text-[#5e452f]">{role?.toLowerCase() === "hr" ? "HR" : role ? role[0].toUpperCase() + role.slice(1) : "…"}</p></div><button onClick={signOut} title="Sign out" className="rounded-lg p-1.5 text-[#5e452f] hover:bg-[#e8d3b8] hover:text-[#3d2817]">⏻</button></div></div>
  </aside></>;
}
