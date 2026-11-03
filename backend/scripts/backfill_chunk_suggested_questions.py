"""Generate passage-specific follow-up questions for chunks already in Supabase."""

from backend.ingestion.ingestion import generate_chunk_questions, supabase


def main() -> None:
    page_size = 250
    offset = 0
    while True:
        rows = (
            supabase.table("chunks")
            .select("id, content, suggested_questions")
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
            .data or []
        )
        if not rows:
            break
        for chunk in rows:
            if chunk.get("suggested_questions"):
                continue
            try:
                questions = generate_chunk_questions(chunk.get("content", ""))
                supabase.table("chunks").update({"suggested_questions": questions}).eq("id", chunk["id"]).execute()
                print(f"Updated chunk {chunk['id']} with {len(questions)} questions")
            except Exception as error:
                print(f"Failed chunk {chunk.get('id')}: {error}")
        if len(rows) < page_size:
            break
        offset += page_size


if __name__ == "__main__":
    main()
