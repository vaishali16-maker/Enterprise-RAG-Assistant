"""Rebuild chunk rows with page metadata after applying the page-number migration."""
from __future__ import annotations

import tempfile
from pathlib import Path

from backend.ingestion.ingestion import chunk_pdf_pages, generate_chunk_questions, supabase
from backend.ingestion.langchain_components import embeddings


def main() -> None:
    documents = supabase.table("documents").select("id, name").execute().data or []
    for document in documents:
        document_id = document["id"]
        old_chunks = supabase.table("chunks").select("*").eq("document_id", document_id).execute().data or []
        replacing = False
        try:
            pdf_bytes = supabase.storage.from_("documents").download(f"{document_id}.pdf")
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_pdf:
                temp_pdf.write(pdf_bytes)
                temp_path = Path(temp_pdf.name)
            try:
                page_chunks = chunk_pdf_pages(str(temp_path))
            finally:
                temp_path.unlink(missing_ok=True)
            texts = [chunk for _, chunk in page_chunks]
            vectors = embeddings.embed_documents(texts)
            replacement = [
                {"document_id": document_id, "chunk_index": index, "page_number": page, "content": text, "embedding": vector, "suggested_questions": generate_chunk_questions(text)}
                for index, ((page, text), vector) in enumerate(zip(page_chunks, vectors))
            ]
            replacing = True
            supabase.table("chunks").delete().eq("document_id", document_id).execute()
            for offset in range(0, len(replacement), 100):
                supabase.table("chunks").insert(replacement[offset:offset + 100]).execute()
            print(f"Updated {document['name']}: {len(replacement)} chunks")
        except Exception as error:
            print(f"Failed {document['name']}: {error}")
            if replacing:
                supabase.table("chunks").delete().eq("document_id", document_id).execute()
            if replacing and old_chunks:
                for offset in range(0, len(old_chunks), 100):
                    supabase.table("chunks").insert(old_chunks[offset:offset + 100]).execute()
                print(f"Restored original chunks for {document['name']}")


if __name__ == "__main__":
    main()
