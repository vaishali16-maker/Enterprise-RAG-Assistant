"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "./auth-context";

export type Convo = { id: string; title: string; created_at: string };

export function useConversations() {
  const { apiFetch } = useAuth();
  const [conversations, setConversations] = useState<Convo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch("/conversations");
      if (!res.ok) throw new Error("Unable to load conversations");
      const data = await res.json();
      setConversations(data.conversations || []);
      setError(null);
    } catch {
      setError("Could not load your history. Check that the local API is running.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    let active = true;
    const handleRefresh = () => { if (active) void refresh(); };
    void Promise.resolve().then(handleRefresh);
    window.addEventListener("conversations:refresh", handleRefresh);
    return () => {
      active = false;
      window.removeEventListener("conversations:refresh", handleRefresh);
    };
  }, [refresh]);

  async function deleteConversation(id: string): Promise<boolean> {
    setError(null);
    try {
      const res = await apiFetch(`/conversations/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Could not delete conversation (${res.status})`);
      }
      setConversations((current) => current.filter((conversation) => conversation.id !== id));
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete conversation.");
      return false;
    }
  }

  return { conversations, loading, error, deleteConversation, refresh };
}
