# Frontend

Next.js (React, TypeScript) client for the Enterprise RAG Assistant.
See the [project README](../README.md) for the overview, architecture and full setup.

## Run locally

Run `npm install`, copy `.env.example` to `.env.local` and fill in the values, then `npm run dev`.
Open http://localhost:3000. The backend must be running (see the project README).

## Environment variables

| Name | Purpose |
|---|---|
| NEXT_PUBLIC_SUPABASE_URL | Supabase project URL |
| NEXT_PUBLIC_SUPABASE_ANON_KEY | Supabase public (anon) key |
| NEXT_PUBLIC_API_URL | Backend URL, for example http://127.0.0.1:8000 |

Only public values belong here. Never put the Supabase service key or any Groq or Cohere key in the frontend.
