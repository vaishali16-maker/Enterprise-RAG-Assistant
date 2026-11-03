alter table public.chunks
  add column if not exists suggested_questions jsonb not null default '[]'::jsonb;

create index if not exists chunks_content_english_fts_idx
  on public.chunks using gin (to_tsvector('english', coalesce(content, '')));

create or replace function public.search_chunks_lexical(
  p_query text,
  p_tenant_id uuid,
  p_role text,
  p_document_ids uuid[] default null,
  p_match_count integer default 12
)
returns table (
  document_id uuid,
  chunk_index integer,
  page_number integer,
  document_name text,
  content text,
  suggested_questions jsonb,
  lexical_score real
)
language sql
stable
set search_path = public
as $$
  with parsed_query as (
    select websearch_to_tsquery('english', p_query) as query
  )
  select
    c.document_id,
    c.chunk_index,
    c.page_number,
    d.name as document_name,
    c.content,
    c.suggested_questions,
    ts_rank_cd(to_tsvector('english', coalesce(c.content, '')), parsed_query.query)::real as lexical_score
  from public.chunks c
  join public.documents d on d.id = c.document_id
  cross join parsed_query
  where d.tenant_id = p_tenant_id
    and (coalesce(jsonb_array_length(to_jsonb(d.allowed_roles)), 0) = 0 or to_jsonb(d.allowed_roles) ? p_role)
    and (p_document_ids is null or c.document_id = any(p_document_ids))
    and to_tsvector('english', coalesce(c.content, '')) @@ parsed_query.query
  order by lexical_score desc
  limit greatest(1, least(p_match_count, 100));
$$;

revoke all on function public.search_chunks_lexical(text, uuid, text, uuid[], integer) from public;
grant execute on function public.search_chunks_lexical(text, uuid, text, uuid[], integer) to service_role;
