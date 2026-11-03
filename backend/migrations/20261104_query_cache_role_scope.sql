-- Existing cached answers have no reliable role label, so invalidate them before scoping future entries.
ALTER TABLE public.query_cache
    ADD COLUMN IF NOT EXISTS role text;

DELETE FROM public.query_cache;

ALTER TABLE public.query_cache
    ALTER COLUMN role SET NOT NULL;

DO $$
DECLARE
    cached_query_function record;
BEGIN
    FOR cached_query_function IN
        SELECT p.oid::regprocedure AS signature
        FROM pg_proc AS p
        JOIN pg_namespace AS ns ON ns.oid = p.pronamespace
        WHERE ns.nspname = 'public'
          AND p.proname = 'match_cached_query'
    LOOP
        EXECUTE format('DROP FUNCTION %s', cached_query_function.signature);
    END LOOP;
END;
$$;

CREATE FUNCTION public.match_cached_query(
    query_embedding vector,
    p_tenant_id uuid,
    p_role text,
    match_threshold double precision DEFAULT 0.95
)
RETURNS TABLE (
    answer text,
    sources jsonb,
    similarity double precision
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
    SELECT
        cache.answer,
        cache.sources,
        (1 - (cache.embedding <=> query_embedding))::double precision AS similarity
    FROM public.query_cache AS cache
    WHERE cache.tenant_id = p_tenant_id
      AND cache.role = p_role
      AND cache.embedding IS NOT NULL
      AND 1 - (cache.embedding <=> query_embedding) >= match_threshold
    ORDER BY cache.embedding <=> query_embedding
    LIMIT 1;
$$;

REVOKE ALL ON FUNCTION public.match_cached_query(vector, uuid, text, double precision) FROM public;
GRANT EXECUTE ON FUNCTION public.match_cached_query(vector, uuid, text, double precision) TO service_role;
