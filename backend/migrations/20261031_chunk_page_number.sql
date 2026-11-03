-- Apply before deploying page-aware PDF ingestion.
alter table public.chunks
  add column if not exists page_number integer;

create index if not exists chunks_document_page_idx
  on public.chunks (document_id, page_number);

alter table public.messages
  add column if not exists low_confidence boolean not null default false,
  add column if not exists top_similarity double precision,
  add column if not exists follow_up_questions jsonb not null default '[]'::jsonb;
