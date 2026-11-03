"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { useRouter } from "next/navigation";
import { createClient } from "./supabase";

type AuthContextType = {
  user: User | null;
  session: Session | null;
  loading: boolean;
  role: string | null;
  signOut: () => Promise<void>;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  apiUpload: (
    path: string,
    body: FormData,
    onProgress: (percent: number) => void,
    onUploaded: () => void,
  ) => Promise<Response>;
};

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const supabase = useMemo(() => createClient(), []);
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const accessToken = session?.access_token;

  const apiFetch = useCallback(async (path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    const apiUrl = (
      process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"
    ).replace(/\/+$/, "");
    try {
      return await fetch(`${apiUrl}${path}`, { ...init, headers });
    } catch {
      throw new Error(
        `Cannot reach the local API at ${apiUrl}. Make sure the backend is running.`
      );
    }
  }, [accessToken]);

  const apiUpload = useCallback((
    path: string,
    body: FormData,
    onProgress: (percent: number) => void,
    onUploaded: () => void,
  ) => new Promise<Response>((resolve, reject) => {
    const apiUrl = (
      process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"
    ).replace(/\/+$/, "");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiUrl}${path}`);
    if (accessToken) {
      xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.upload.onload = onUploaded;
    xhr.onload = () => {
      if (xhr.status === 0) {
        reject(new Error(`Cannot reach the local API at ${apiUrl}. Make sure the backend is running.`));
        return;
      }
      const headers = new Headers();
      xhr.getAllResponseHeaders().trim().split(/[\r\n]+/).forEach((line) => {
        const separator = line.indexOf(":");
        if (separator > 0) {
          headers.set(line.slice(0, separator).trim(), line.slice(separator + 1).trim());
        }
      });
      resolve(new Response(xhr.responseText, {
        status: xhr.status,
        statusText: xhr.statusText,
        headers,
      }));
    };
    xhr.onerror = () => reject(
      new Error(`Cannot reach the local API at ${apiUrl}. Make sure the backend is running.`)
    );
    xhr.onabort = () => reject(new Error("Upload was cancelled."));
    xhr.send(body);
  }), [accessToken]);

  useEffect(() => {
    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
      setUser(newSession?.user ?? null);
      if (!newSession) setRole(null);
    });

    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setUser(data.session?.user ?? null);
      if (!data.session) setRole(null);
      setLoading(false);
    }).catch(() => {
      setSession(null);
      setUser(null);
      setRole(null);
      setLoading(false);
    });

    return () => listener.subscription.unsubscribe();
  }, [supabase]);

  useEffect(() => {
    if (!session) return;
    let active = true;
    apiFetch("/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const normalized = String(data?.role ?? "").trim().toLowerCase();
        if (active) setRole(normalized || null);
      })
      .catch(() => { if (active) setRole(null); });
    return () => { active = false; };
  }, [session, apiFetch]);

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
  }

  return (
    <AuthContext.Provider value={{ user, session, loading, role, signOut, apiFetch, apiUpload }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
