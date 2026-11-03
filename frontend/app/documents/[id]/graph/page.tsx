"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { RequireAuth } from "@/lib/require-auth";
import { useAuth } from "@/lib/auth-context";
import { Sidebar } from "@/components/Sidebar";

type Entity = { id: number; name: string; type: string | null };
type Relation = { source_entity_id: number; target_entity_id: number; relation: string };

export default function GraphPage() {
  return (
    <RequireAuth>
      <div className="flex h-screen">
        <Sidebar />
        <div className="m-3 ml-0 flex-1 overflow-y-auto rounded-3xl bg-[#fdf8f3]/80 p-8 shadow-lg shadow-[#4a2c1a]/10 backdrop-blur-sm">
          <GraphView />
        </div>
      </div>
    </RequireAuth>
  );
}

function GraphView() {
  const { apiFetch } = useAuth();
  const params = useParams();
  const documentId = params.id as string;
  const [entities, setEntities] = useState<Entity[]>([]);
  const [relations, setRelations] = useState<Relation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    apiFetch(`/documents/${documentId}/graph`)
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => null);
          throw new Error(body?.detail || `Could not load graph (${r.status})`);
        }
        return r.json();
      })
      .then((d) => {
        if (!active) return;
        setEntities(d.entities || []);
        setRelations(d.relations || []);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Could not load graph");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [documentId, apiFetch]);

  // Only show entities that actually appear in a relationship
  const connectedIds = new Set(
    relations.flatMap((r) => [r.source_entity_id, r.target_entity_id])
  );
  const connected = entities.filter((e) => connectedIds.has(e.id));
  const byId = Object.fromEntries(entities.map((e) => [e.id, e]));

  const n = connected.length;
  const radius = 160;
  const size = 480;
  const center = size / 2;
  const positions = Object.fromEntries(
    connected.map((e, i) => {
      const angle = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
      return [e.id, { x: center + radius * Math.cos(angle), y: center + radius * Math.sin(angle) }];
    })
  );

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[#3d2817]">Document Knowledge Graph</h1>
        <Link href="/documents" className="text-sm text-[#a68a6d] hover:text-[#3d2817]">
          Back to documents
        </Link>
      </div>

      {loading ? (
        <p className="text-sm text-[#a68a6d]">Loading…</p>
      ) : error ? (
        <p className="text-sm text-red-600" role="alert">{error}</p>
      ) : connected.length === 0 ? (
        <p className="text-sm text-[#a68a6d]">
          No connected relationships found for this document yet.
        </p>
      ) : (
        <>
          <div className="mb-6 overflow-x-auto rounded-2xl border border-[#e8d3b8] bg-white/90 p-4 shadow-sm">
            <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="mx-auto block">
              {relations.map((r, i) => {
                const a = positions[r.source_entity_id];
                const b = positions[r.target_entity_id];
                if (!a || !b) return null;
                const mx = (a.x + b.x) / 2;
                const my = (a.y + b.y) / 2;
                return (
                  <g key={i}>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#d9c3a4" strokeWidth={1.5} />
                    <text x={mx} y={my} fontSize="8" fill="#a68a6d" textAnchor="middle">
                      {r.relation.length > 20 ? r.relation.slice(0, 20) + "…" : r.relation}
                    </text>
                  </g>
                );
              })}
              {connected.map((e) => {
                const p = positions[e.id];
                return (
                  <g key={e.id}>
                    <circle cx={p.x} cy={p.y} r={6} fill="#6f4523" />
                    <text
                      x={p.x}
                      y={p.y + (p.y < center ? -12 : 20)}
                      textAnchor="middle"
                      fontSize="11"
                      fontWeight={600}
                      fill="#3d2817"
                    >
                      {e.name.length > 20 ? e.name.slice(0, 20) + "…" : e.name}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>

          <div className="rounded-2xl border border-[#e8d3b8] bg-white/90 shadow-sm">
            <p className="border-b border-[#f0e2cc] px-4 py-2 text-xs font-semibold uppercase tracking-wide text-[#a68a6d]">
              Relationships ({relations.length})
            </p>
            <ul className="divide-y divide-[#f0e2cc]">
              {relations.map((r, i) => (
                <li key={i} className="px-4 py-2 text-sm text-[#3d2817]">
                  <span className="font-medium">{byId[r.source_entity_id]?.name}</span>{" "}
                  <span className="text-[#a68a6d]">{r.relation}</span>{" "}
                  <span className="font-medium">{byId[r.target_entity_id]?.name}</span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
