from pathlib import Path
import os
import logging
import re
import json
import fitz  # pymupdf
import cohere
from dotenv import load_dotenv
from supabase import create_client, Client
try:
    import ollama
except ImportError:
    ollama = None
import requests
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Setup
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in your .env file")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
co = cohere.Client(os.environ.get("COHERE_API_KEY"))
EMBED_MODEL_NAME = "qwen3.5:4b"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-20b"
# Groq retired llama-3.1-8b-instant on 2026-08-16. Keep short questions on
# the supported model too, so the simple-question route does not fail.
GROQ_MODEL_SMALL = "openai/gpt-oss-20b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
try:
    MIN_SIMILARITY = min(1.0, max(0.0, float(os.environ.get("MIN_SIMILARITY", "0.40"))))
except ValueError:
    logger.warning("Invalid MIN_SIMILARITY; using default 0.40")
    MIN_SIMILARITY = 0.40


def _call_llm_with_model(messages: list[dict], model: str) -> str:
    if GROQ_API_KEY:
        try:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={"model": model, "messages": messages, "reasoning_format": "hidden"},
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        except Exception as e:
            raise RuntimeError(f"Groq generation failed: {e}") from e
    return _call_llm(messages)  

def _call_llm(messages: list[dict]) -> str:
    if GROQ_API_KEY:
        try:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "reasoning_format": "hidden",
                },
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            return content
        except Exception as e:
            raise RuntimeError(f"Groq generation failed: {e}") from e
    else:
        if ollama is None:
            raise RuntimeError("No GROQ_API_KEY set and ollama is not installed")
        try:
            response = ollama.chat(model=EMBED_MODEL_NAME, messages=messages, think=False)
            return response["message"]["content"].strip()
        except Exception as e:
            raise RuntimeError(
                f"Ollama generation failed — is 'ollama serve' running "
                f"and is '{EMBED_MODEL_NAME}' pulled? ({e})"
            ) from e


def _call_llm_stream(messages: list[dict]):
    if GROQ_API_KEY:
        try:
            with requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "stream": True,
                    "reasoning_format": "hidden",
                },
                stream=True,
                timeout=60,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    decoded = line.decode("utf-8")
                    if not decoded.startswith("data: "):
                        continue
                    payload = decoded[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
        except Exception as e:
            yield f"[error: Groq generation failed — {e}]"
    else:
        if ollama is None:
            yield "[error: No GROQ_API_KEY set and ollama is not installed]"
            return
        try:
            stream = ollama.chat(
                model=EMBED_MODEL_NAME, messages=messages, think=False, stream=True
            )
            for chunk in stream:
                token = chunk["message"]["content"]
                if token:
                    yield token
        except Exception as e:
            yield f"[error: Ollama generation failed — {e}]"

# 1. Extraction (layout-aware, with TOC-page filtering)
def _order_blocks_by_column(blocks: list, page_width: float) -> list:
    full_width_threshold = 0.6 * page_width
    full_width = []
    column_blocks = []
    for b in blocks:
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        if (x1 - x0) > full_width_threshold:
            full_width.append(b)
        else:
            column_blocks.append(b)

    full_width.sort(key=lambda b: b[1])
    mid_x = page_width / 2
    left = [b for b in column_blocks if (b[0] + b[2]) / 2 < mid_x]
    right = [b for b in column_blocks if (b[0] + b[2]) / 2 >= mid_x]
    left.sort(key=lambda b: b[1])
    right.sort(key=lambda b: b[1])
    if column_blocks:
        column_start_y = min(b[1] for b in column_blocks)
    else:
        column_start_y = float("inf")
    header_blocks = [b for b in full_width if b[1] < column_start_y]
    footer_blocks = [b for b in full_width if b[1] >= column_start_y]
    return header_blocks + left + right + footer_blocks


def _is_structural_page(block_texts: list[str]) -> bool:
    blocks = [b.strip() for b in block_texts if b.strip()]
    # Real contents/index pages have many short entries. A short policy page
    # (a heading, one paragraph and a footer) must not be treated as structural.
    if len(blocks) < 8:
        return False
    long_blocks = sum(1 for b in blocks if len(b) > 200)
    short_blocks = sum(1 for b in blocks if len(b) < 80)
    return long_blocks == 0 and (short_blocks / len(blocks)) > 0.6


def extract_text_from_pdf(file_path: str, skip_structural_pages: bool = True) -> str:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ValueError(f"Could not open '{path.name}' as a PDF: {e}") from e
    page_texts = []
    for page_num, page in enumerate(doc, start=1):
        try:
            blocks = page.get_text("blocks")
        except Exception as e:
            logger.warning("Skipping page %d of %s: %s", page_num, path.name, e)
            continue
        blocks = _order_blocks_by_column(blocks, page.rect.width)
        block_texts = [b[4] for b in blocks if b[4] and b[4].strip()]
        if not block_texts:
            continue
        if skip_structural_pages and _is_structural_page(block_texts):
            logger.info("Skipping page %d of %s (looks structural, not prose)", page_num, path.name)
            continue
        page_text = "\n".join(b.strip() for b in block_texts)
        page_texts.append(page_text)

    doc.close()
    full_text = "\n\n".join(page_texts)
    if not full_text.strip():
        raise ValueError(
            f"No extractable body text found in '{path.name}' "
            "(it may be a scanned/image-only PDF, or entirely front-matter)"
        )
    return full_text


def extract_pdf_pages(file_path: str, skip_structural_pages: bool = True) -> list[tuple[int, str]]:
    """Return body text with its one-based PDF page number."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")
    doc = fitz.open(str(path))
    pages: list[tuple[int, str]] = []
    for page_number, page in enumerate(doc, start=1):
        blocks = _order_blocks_by_column(page.get_text("blocks"), page.rect.width)
        texts = [block[4] for block in blocks if block[4] and block[4].strip()]
        if not texts or (skip_structural_pages and _is_structural_page(texts)):
            logger.info("Skipping page %d of %s (empty or structural)", page_number, path.name)
            continue
        content = clean_extracted_text(" ".join(texts))
        if content:
            pages.append((page_number, content))
    doc.close()
    if not pages:
        raise ValueError(f"No extractable body text found in '{path.name}'")
    return pages


def chunk_pdf_pages(file_path: str) -> list[tuple[int, str]]:
    return [(page, chunk) for page, text in extract_pdf_pages(file_path) for chunk in chunk_text(text)]


# 2. Cleanup + Chunking (sentence-boundary, with overlap)
def clean_extracted_text(text: str) -> str:
    text = re.sub(r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF]', '', text)  # emojis and symbols -> empty string
    text = re.sub(r'[•·.]{3,}', ' ', text)  # replace 3+ bullet points/dots with a single space
    text = re.sub(r'\s+', ' ', text)  # collapse whitespace
    return text.strip()


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) <= chunk_size:
            current += (" " if current else "") + sentence
        else:
            if current:
                chunks.append(current.strip())
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + " " + sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks


def generate_chunk_questions(content: str) -> list[str]:
    prompt = (
        "Write 3 short questions, each fully answerable using only the passage below. "
        "Use the user's point of view (I/my), keep every question under 12 words, "
        "and ask about specific facts stated in the passage. One question per line, no numbering.\n\n"
        f"Passage:\n{content}"
    )
    questions: list[str] = []
    try:
        raw = _call_llm_with_model([{"role": "user", "content": prompt}], GROQ_MODEL_SMALL)
        for line in raw.splitlines():
            candidate = re.sub(r"^\s*(?:[-*\d.)]+\s*)", "", line).strip(" \"'`")
            candidate = re.sub(r"\s+", " ", candidate)
            if candidate.endswith("?") and len(candidate.split()) < 12 and re.search(r"\b(i|me|my|we|our)\b", candidate, re.IGNORECASE):
                if candidate.casefold() not in {existing.casefold() for existing in questions}:
                    questions.append(candidate)
            if len(questions) == 3:
                break
    except Exception as e:
        logger.warning("Could not generate questions for chunk: %s", e)
    fallback = ["What does this passage tell me?", "What facts should I know from this passage?"]
    for question in fallback:
        if len(questions) >= 2:
            break
        if question.casefold() not in {existing.casefold() for existing in questions}:
            questions.append(question)
    return questions[:3]


# 3. Ingestion (document record + linked chunks)
VALID_ROLES = ["employee", "manager", "hr", "admin"]


def find_documents_by_name(tenant_id: str, name: str) -> list[dict]:
    try:
        res = (
            supabase.table("documents")
            .select("id, name, uploaded_by, allowed_roles")
            .eq("tenant_id", tenant_id)
            .eq("name", name)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(f"Failed to look up existing document '{name}': {e}") from e
    return res.data or []


def ingest_pdf(
    file_path: str,
    tenant_id: str,
    original_name: Optional[str] = None,
    allowed_roles: Optional[list] = None,
    uploaded_by: Optional[str] = None,
) -> dict:
    path = Path(file_path)
    name = original_name or path.name
    page_chunks = chunk_pdf_pages(file_path)
    chunks = [chunk for _, chunk in page_chunks]
    text = "\n".join(chunks)
    if not chunks:
        raise ValueError(f"'{name}' produced no chunks after splitting")
    try:
        from backend.ingestion.langchain_components import embeddings as lc_embeddings
        vectors = lc_embeddings.embed_documents(chunks)
    except Exception as e:
        raise RuntimeError(f"Embedding failed for '{name}': {e}") from e

    doc_row = {"tenant_id": tenant_id, "name": name}
    if allowed_roles:
        doc_row["allowed_roles"] = allowed_roles
    if uploaded_by:
        doc_row["uploaded_by"] = uploaded_by
    try:
        document_id = supabase.table("documents").insert(doc_row).execute().data[0]["id"]
    except Exception as e:
        raise RuntimeError(f"Failed to create document record for '{name}': {e}") from e

    questions_by_chunk = [generate_chunk_questions(chunk) for chunk in chunks]
    rows = [
        {
            "document_id": document_id,
            "chunk_index": idx,
            "page_number": page_number,
            "content": chunk,
            "embedding": vector,
            "suggested_questions": questions,
        }
        for idx, ((page_number, chunk), vector, questions) in enumerate(zip(page_chunks, vectors, questions_by_chunk))
    ]
    try:
        for start in range(0, len(rows), 100):
            supabase.table("chunks").insert(rows[start:start + 100]).execute()
    except Exception as e:
        supabase.table("documents").delete().eq("id", document_id).execute()
        raise RuntimeError(f"Failed to store chunks for '{name}': {e}") from e

    logger.info("Ingested '%s': %d chunks stored", name, len(chunks))
    extract_graph(document_id, text)
    upload_pdf_to_storage(document_id, file_path)
    return {"document_id": document_id, "name": name, "chunks": len(chunks)}

def upload_pdf_to_storage(document_id: str, file_path: str):
    try:
        with open(file_path, "rb") as f:
            supabase.storage.from_("documents").upload(
                f"{document_id}.pdf", f, {"content-type": "application/pdf"}
            )
    except Exception as e:
        logger.warning("Failed to store PDF for %s: %s", document_id, e)


def get_pdf_url(document_id: str) -> Optional[str]:
    try:
        res = supabase.storage.from_("documents").create_signed_url(f"{document_id}.pdf", 3600)
        return res.get("signedURL") or res.get("signed_url")
    except Exception:
        return None

def list_documents(tenant_id: str, role: str) -> list[dict]:
    try:
        res = (
            supabase.table("documents")
            .select("id, name, allowed_roles, created_at, uploaded_by")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(f"Failed to list documents: {e}") from e
    # A NULL/empty allowed_roles value means the uploader chose "everyone".
    # Filter in Python so Postgres NULL semantics do not hide those documents.
    return [
        doc
        for doc in (res.data or [])
        if not doc.get("allowed_roles") or role in doc["allowed_roles"]
    ]


def delete_document(document_id: str, tenant_id: str) -> bool:
    try:
        res = (
            supabase.table("documents")
            .delete()
            .eq("id", document_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(f"Failed to delete document {document_id}: {e}") from e
    deleted = bool(res.data)
    if deleted:
        try:
            supabase.storage.from_("documents").remove([f"{document_id}.pdf"])
        except Exception as e:
            logger.warning("Failed to remove stored PDF for %s: %s", document_id, e)
    return deleted


# 4. Retrieval (always scoped to a tenant and role)
def search_documents(
    query: str,
    tenant_id: str,
    role: str,
    match_count: int = 3,
    match_threshold: float = 0.15,
    document_ids: Optional[list[str]] = None,
):
    try:
        from backend.ingestion.langchain_components import embeddings as lc_embeddings
        query_vector = lc_embeddings.embed_query(query)
    except Exception as e:
        raise RuntimeError(f"Failed to embed query: {e}") from e
    candidate_pool = max(match_count * 4, 12)
    try:
        vector_result = supabase.rpc(
            "match_chunks",
            {
                "query_embedding": query_vector,
                "match_threshold": match_threshold,
                "match_count": max(candidate_pool, 100) if document_ids else candidate_pool,
                "p_tenant_id": tenant_id,
                "p_role": role,
            },
        ).execute()
    except Exception as e:
        raise RuntimeError(f"Vector search failed: {e}") from e
    vector_rows = vector_result.data or []
    if document_ids:
        selected_ids = set(document_ids)
        vector_rows = [row for row in vector_rows if row.get("document_id") in selected_ids]

    term_groups = _query_term_groups(query)
    lexical_rows: list[dict] = []
    try:
        lexical_result = supabase.rpc("search_chunks_lexical", {
            "p_query": _expanded_lexical_query(query),
            "p_tenant_id": tenant_id,
            "p_role": role,
            "p_document_ids": document_ids,
            "p_match_count": candidate_pool,
        }).execute()
        lexical_rows = lexical_result.data or []
    except Exception as e:
        logger.warning("Hybrid lexical retrieval is unavailable; using vector results only: %s", e)

    best_lexical_rank = max((float(row.get("lexical_score") or 0) for row in lexical_rows), default=0.0)
    merged: dict[tuple[str, int], dict] = {}
    for row in vector_rows:
        key = (str(row.get("document_id")), int(row.get("chunk_index") or 0))
        merged[key] = {**row, "_vector_score": float(row.get("similarity") or 0), "_lexical_score": 0.0}
    for row in lexical_rows:
        key = (str(row.get("document_id")), int(row.get("chunk_index") or 0))
        item = merged.get(key, {**row, "_vector_score": 0.0})
        item["_lexical_score"] = float(row.get("lexical_score") or 0)
        item.setdefault("document_name", row.get("document_name"))
        item.setdefault("content", row.get("content"))
        item.setdefault("suggested_questions", row.get("suggested_questions") or [])
        merged[key] = item

    rows = []
    for row in merged.values():
        vector_score = float(row.get("_vector_score") or 0)
        lexical_score = float(row.get("_lexical_score") or 0)
        coverage = _content_term_coverage(row.get("content", ""), term_groups)
        rank_score = lexical_score / best_lexical_rank if best_lexical_rank else 0.0
        row["similarity"] = round(min(1.0, max(vector_score, 0.5 * vector_score + 0.35 * coverage + 0.15 * rank_score)), 4)
        row["source_file"] = row.get("document_name")
        rows.append(row)
    rows.sort(key=lambda row: row.get("similarity", 0), reverse=True)
    rows = rows[:match_count]

    if rows:
        try:
            doc_ids = list({row["document_id"] for row in rows if row.get("document_id")})
            indices = list({row["chunk_index"] for row in rows if row.get("chunk_index") is not None})
            if doc_ids and indices:
                metadata = supabase.table("chunks").select("document_id, chunk_index, page_number, suggested_questions").in_("document_id", doc_ids).in_("chunk_index", indices).execute().data or []
                by_chunk = {(item["document_id"], item["chunk_index"]): item for item in metadata}
                for row in rows:
                    item = by_chunk.get((row.get("document_id"), row.get("chunk_index")), {})
                    row["page_number"] = row.get("page_number", item.get("page_number"))
                    row["suggested_questions"] = row.get("suggested_questions") or item.get("suggested_questions") or []
        except Exception as e:
            logger.warning("Could not load chunk metadata; apply the chunk metadata migration: %s", e)
    for row in rows:
        row.pop("_vector_score", None)
        row.pop("_lexical_score", None)
    return rows


def _query_term_groups(query: str) -> list[set[str]]:
    stop_words = {"can", "could", "i", "me", "my", "we", "our", "the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or", "it", "this", "that", "do", "does", "did", "how", "what", "when", "where", "who", "why", "over"}
    tokens = set(re.findall(r"[a-z0-9]+", query.lower())) - stop_words
    carry_words = {"carry", "carries", "carried", "carryover", "carryforward"}
    groups: list[set[str]] = []
    if tokens & carry_words:
        groups.append({"carry", "carries", "carried", "carrying", "carryover", "carryforward", "forward"})
        tokens -= carry_words
    leave_words = {"annual", "leave", "vacation", "pto", "holiday", "time", "off"}
    if tokens & leave_words:
        groups.append({"annual", "leave", "vacation", "pto", "holiday", "time", "off"})
        tokens -= leave_words
    groups.extend({token} for token in sorted(tokens))
    return groups


def _content_term_coverage(content: str, term_groups: list[set[str]]) -> float:
    if not term_groups:
        return 0.0
    words = set(re.findall(r"[a-z0-9]+", content.lower()))
    return sum(bool(words & group) for group in term_groups) / len(term_groups)


def _expanded_lexical_query(query: str) -> str:
    terms = re.findall(r"[a-z0-9]+", query.lower())
    stop_words = {"can", "could", "i", "me", "my", "we", "our", "the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or", "it", "this", "that", "do", "does", "did", "how", "what", "when", "where", "who", "why", "happens", "after", "if"}
    carry_words = {"carry", "carries", "carried", "carryover", "carryforward"}
    leave_words = {"annual", "leave", "vacation", "pto", "holiday", "time", "off"}
    expanded: list[str] = []
    for term in terms:
        if term in stop_words or term == "over":
            continue
        if term in carry_words:
            if not any("carry" in value for value in expanded):
                expanded.append("(carry OR carried OR carryover OR forward)")
        elif term in leave_words:
            if not any('"time off"' in value for value in expanded):
                expanded.append('(leave OR vacation OR pto OR holiday OR "time off" OR "annual leave")')
        else:
            expanded.append(term)
    return " OR ".join(expanded) or query


def generate_followup_questions(
    question: str,
    answer: str,
    sources: list[dict],
    history: list[dict] | None = None,
) -> list[str]:
    history_text = "\n".join(
        f'{item.get("role", "").upper()}: {item.get("content", "")}'
        for item in (history or [])
        if item.get("content")
    )
    passages = "\n\n".join(
        f'[{index + 1}] {source.get("document_name") or source.get("source_file") or "Document"}'
        f' (page {source.get("page_number") or "unknown"}): {source.get("content", "")}'
        for index, source in enumerate(sources)
        if source.get("content")
    )
    if not passages:
        return []
    prompt = (
        "Suggest up to 8 short, specific follow-up QUESTIONS for the user. "
        "Use only facts and topics covered by the retrieved passages. Write from the user's point of view, "
        "for example, 'How do I request annual leave?'. Every question must be under 12 words. "
        "Do not ask any question already asked or answered in the conversation history, including the current question. "
        "Return only one question per line, no numbering or explanations.\n\n"
        f"Earlier conversation (oldest first):\n{history_text or '(none)'}\n\n"
        f"Current question: {question}\nCurrent answer: {answer}\n\nRetrieved passages:\n{passages}"
    )
    try:
        raw = _call_llm_with_model([{"role": "user", "content": prompt}], GROQ_MODEL_SMALL)
    except Exception as e:
        logger.warning("Follow-up generation failed: %s", e)
        return []

    history_items = history or []
    prior_questions = {" ".join(str(item.get("content", "")).lower().split()) for item in history_items if item.get("role") == "user"}
    prior_questions.add(" ".join(question.lower().split()))
    prior_answer_terms = [set(re.findall(r"\b[a-z0-9]+\b", str(item.get("content", "")).lower())) for item in history_items if item.get("role") == "assistant"]
    stopwords = {"how", "what", "when", "where", "who", "why", "can", "could", "do", "does", "did", "i", "me", "my", "the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or", "it", "this", "that"}
    seen: set[str] = set()
    candidates: list[str] = []
    for line in raw.splitlines():
        candidate = re.sub(r"^\s*(?:[-*\d.)]+\s*)", "", line).strip(" \"'`")
        candidate = re.sub(r"\s+", " ", candidate)
        normalized = " ".join(re.sub(r"[^a-z0-9 ]", "", candidate.lower()).split())
        if not candidate.endswith("?") or len(candidate.split()) >= 12 or not normalized:
            continue
        if not re.search(r"\b(i|me|my|we|our)\b", candidate, flags=re.IGNORECASE):
            continue
        if normalized in seen or " ".join(candidate.lower().split()) in prior_questions:
            continue
        key_terms = set(normalized.split()) - stopwords
        if len(key_terms) >= 2 and any(len(key_terms & previous_terms) / len(key_terms) >= 0.8 for previous_terms in prior_answer_terms):
            continue
        seen.add(normalized)
        candidates.append(candidate)
    return candidates[:8]


def get_neighbor_chunk_questions(sources: list[dict], allowed_document_ids: set[str]) -> list[str]:
    wanted: set[tuple[str, int]] = set()
    for source in sources:
        document_id = source.get("document_id")
        chunk_index = source.get("chunk_index")
        if document_id in allowed_document_ids and isinstance(chunk_index, int):
            wanted.update((document_id, index) for index in (chunk_index - 1, chunk_index + 1) if index >= 0)
    if not wanted:
        return []
    doc_ids = list({document_id for document_id, _ in wanted})
    indices = list({index for _, index in wanted})
    try:
        rows = supabase.table("chunks").select("document_id, chunk_index, suggested_questions").in_("document_id", doc_ids).in_("chunk_index", indices).execute().data or []
    except Exception as e:
        logger.warning("Could not load neighboring chunk questions: %s", e)
        return []
    questions: list[str] = []
    for row in rows:
        if (row.get("document_id"), row.get("chunk_index")) in wanted:
            questions.extend(row.get("suggested_questions") or [])
    return questions


def select_chunk_followup_questions(
    current_question: str,
    sources: list[dict],
    neighbor_questions: list[str],
    history: list[dict] | None = None,
) -> list[str]:
    prior_questions = {
        " ".join(re.findall(r"[a-z0-9]+", str(item.get("content", "")).lower()))
        for item in history or [] if item.get("role") == "user"
    }
    prior_questions.add(" ".join(re.findall(r"[a-z0-9]+", current_question.lower())))
    question_stop_words = {"how", "what", "when", "where", "who", "why", "can", "could", "do", "does", "did", "i", "me", "my", "we", "our", "the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or", "it", "this", "that"}
    prior_answer_terms = [set(re.findall(r"[a-z0-9]+", str(item.get("content", "")).lower())) for item in history or [] if item.get("role") == "assistant"]
    candidates = [question for source in sources for question in (source.get("suggested_questions") or [])]
    candidates.extend(neighbor_questions)
    seen: set[str] = set()
    selected: list[str] = []
    for question in candidates:
        candidate = re.sub(r"\s+", " ", str(question)).strip()
        normalized = " ".join(re.findall(r"[a-z0-9]+", candidate.lower()))
        if not candidate.endswith("?") or len(candidate.split()) >= 12 or not normalized:
            continue
        if not re.search(r"\b(i|me|my|we|our)\b", candidate, re.IGNORECASE):
            continue
        candidate_terms = set(normalized.split()) - question_stop_words
        duplicate_question = any(
            candidate_terms and len(candidate_terms & (set(previous.split()) - question_stop_words)) / len(candidate_terms) >= 0.8
            for previous in prior_questions
        )
        already_answered = len(candidate_terms) >= 2 and any(
            len(candidate_terms & answered_terms) / len(candidate_terms) >= 0.8
            for answered_terms in prior_answer_terms
        )
        if normalized in seen or duplicate_question or already_answered:
            continue
        seen.add(normalized)
        selected.append(candidate)
    return selected


# 4b. Semantic cache
def check_cache(question: str, tenant_id: str, role: str, threshold: float = 0.95):
    try:
        from backend.ingestion.langchain_components import embeddings as lc_embeddings
        query_vector = lc_embeddings.embed_query(question)
    except Exception:
        return None
    try:
        result = supabase.rpc(
            "match_cached_query",
            {
                "query_embedding": query_vector,
                "p_tenant_id": tenant_id,
                "p_role": role,
                "match_threshold": threshold,
            },
        ).execute()
    except Exception:
        return None
    rows = result.data or []
    return rows[0] if rows else None


def store_cache(question: str, tenant_id: str, role: str, answer: str, sources: list):
    try:
        from backend.ingestion.langchain_components import embeddings as lc_embeddings
        query_vector = lc_embeddings.embed_query(question)
        supabase.table("query_cache").insert(
            {
                "tenant_id": tenant_id,
                "role": role,
                "question": question,
                "embedding": query_vector,
                "answer": answer,
                "sources": sources,
            }
        ).execute()
    except Exception as e:
        logger.warning("Failed to store cache entry: %s", e)


def log_request(tenant_id, user_id, question, cache_hit, model, latency_ms):
    try:
        supabase.table("request_log").insert(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "question": question,
                "cache_hit": cache_hit,
                "model": model,
                "latency_ms": latency_ms,
            }
        ).execute()
    except Exception as e:
        logger.warning("Failed to log request: %s", e)


def is_simple_question(question: str) -> bool:
    """Cheap heuristic router: short, single-fact-looking questions -> small model."""
    q = question.strip().lower()
    word_count = len(q.split())
    complex_signals = ["compare", "difference", "why", "explain", "summarize", "and", "vs"]
    if word_count > 15:
        return False
    if any(sig in q for sig in complex_signals):
        return False
    return True


def is_summary_question(question: str) -> bool:
    normalized = question.lower()
    return any(term in normalized for term in ("summarize", "summarise", "summarization", "summary", "overview", "outline", "key points", "main points", "recap", "sum up", "tl;dr"))


def merge_adjacent_chunks(results: list[dict]) -> list[dict]:
    ordered = sorted(results, key=lambda row: (str(row.get("document_id", "")), int(row.get("chunk_index", 0))))
    merged: list[dict] = []
    for source in ordered:
        if merged:
            previous = merged[-1]
            adjacent = (
                previous.get("document_id") == source.get("document_id")
                and isinstance(previous.get("_last_chunk_index"), int)
                and isinstance(source.get("chunk_index"), int)
                and source["chunk_index"] <= previous["_last_chunk_index"] + 1
            )
            if adjacent:
                previous["content"] = f'{previous["content"]}\n{source.get("content", "")}'
                previous["_last_chunk_index"] = source["chunk_index"]
                previous["similarity"] = max(float(previous.get("similarity") or 0), float(source.get("similarity") or 0))
                continue
        merged.append({**source, "_last_chunk_index": source.get("chunk_index")})
    for item in merged:
        item.pop("_last_chunk_index", None)
    return sorted(merged, key=lambda row: float(row.get("similarity") or 0), reverse=True)


# 5. Generation
def generate_answer(query: str, context: str) -> str:
    system_prompt = f"""You are an AI document assistant.
Answer the user's question using ONLY the information provided in the context.
Rules:
- Give a clear, complete answer in a natural sentence.
- Do not answer with only a few words when a complete sentence is possible.
- Directly answer what the user asked.
- Do not add information that is not present in the context.
- Use only facts stated in the passages. Do not present roadmap or future items as existing features. If something is not in the passages, say it is not covered.
- If the answer is not present in the context, say:
"I could not find the answer in the document."
- Keep the answer concise and easy to understand.
Context:{context}
Question:{query}
Answer:"""
    return _call_llm([{"role": "user", "content": system_prompt}])


def generate_answer_routed(query: str, context: str, use_small_model: bool) -> str:
    system_prompt = f"""You are an AI document assistant.
Answer the user's question using ONLY the information provided in the context.
Rules:
- Give a clear, complete answer in a natural sentence.
- Do not add information that is not present in the context.
- Use only facts stated in the passages. Do not present roadmap or future items as existing features. If something is not in the passages, say it is not covered.
- If the answer is not present in the context, say:
"I could not find the answer in the document."
Context:{context}
Question:{query}
Answer:"""
    model = GROQ_MODEL_SMALL if use_small_model else GROQ_MODEL
    return _call_llm_with_model([{"role": "user", "content": system_prompt}], model)


def generate_answer_stream(query: str, context: str):
    system_prompt = f"""You are an AI document assistant.
Answer the user's question using ONLY the information provided in the context.
Rules:
- Give a clear, complete answer in a natural sentence.
- Do not answer with only a few words when a complete sentence is possible.
- Directly answer what the user asked.
- Do not add information that is not present in the context.
- Use only facts stated in the passages. Do not present roadmap or future items as existing features. If something is not in the passages, say it is not covered.
- If the answer is not present in the context, say:
"I could not find the answer in the document."
- Keep the answer concise and easy to understand.
Context:{context}
Question:{query}
Answer:"""
    for token in _call_llm_stream([{"role": "user", "content": system_prompt}]):
        yield token


def _sample_text(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    slice_size = max_chars // 3
    start = text[:slice_size]
    mid_point = len(text) // 2
    middle = text[mid_point - slice_size // 2: mid_point + slice_size // 2]
    end = text[-slice_size:]
    return f"{start}\n...\n{middle}\n...\n{end}"


def generate_faq(text: str, num_questions: int = 4) -> list[dict]:
    excerpt = _sample_text(text, max_chars=4000)
    prompt = f"""You are helping someone quickly understand a company document.
Read the document excerpt below and write {num_questions} short, realistic questions
an employee might ask about it (e.g. policies, procedures, benefits, deadlines),
each with a concise, accurate answer based ONLY on the text.

Respond with ONLY a JSON array, no other text, no markdown code fences, in this exact shape:
[{{"question": "...", "answer": "..."}}, ...]

Document excerpt:{excerpt}
JSON array:"""
    try:
        raw = _call_llm([{"role": "user", "content": prompt}])
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            raw = match.group(0)
        pairs = json.loads(raw)
        cleaned = [
            {"question": p["question"].strip(), "answer": p["answer"].strip()}
            for p in pairs
            if isinstance(p, dict) and p.get("question") and p.get("answer")
        ]
        return cleaned[:num_questions]
    except Exception as e:
        logger.warning(
            "FAQ generation failed, skipping suggestions: %s | raw response: %r",
            e, locals().get("raw")
        )
        return []

def extract_graph(document_id: str, text: str):
    excerpt = _sample_text(text, max_chars=6000)
    prompt = f"""Extract key entities and relationships from this document excerpt.
Respond with ONLY a JSON object, no other text:
{{"entities": [{{"name": "...", "type": "..."}}],
  "relations": [{{"source": "...", "relation": "...", "target": "..."}}]}}
Keep it to the 10-15 most important entities/relations.
Document excerpt:{excerpt}
JSON:"""
    try:
        raw = _call_llm([{"role": "user", "content": prompt}])
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        data = json.loads(raw)
        name_to_id = {}
        for e in data.get("entities", []):
            row = supabase.table("entities").insert(
                {"document_id": document_id, "name": e["name"], "type": e.get("type")}
            ).execute().data[0]
            name_to_id[e["name"]] = row["id"]
        for r in data.get("relations", []):
            src, tgt = name_to_id.get(r["source"]), name_to_id.get(r["target"])
            if src and tgt:
                supabase.table("relations").insert(
                    {"document_id": document_id, "source_entity_id": src,
                     "target_entity_id": tgt, "relation": r["relation"]}
                ).execute()
    except Exception as e:
        logger.warning("Graph extraction failed: %s", e)


def expand_graph_context(document_ids: list[str], question: str) -> str:
    if not document_ids:
        return ""
    try:
        rels = (
            supabase.table("relations")
            .select("relation, source_entity_id, target_entity_id")
            .in_("document_id", document_ids)
            .execute().data or []
        )
        ent_ids = {r["source_entity_id"] for r in rels} | {r["target_entity_id"] for r in rels}
        if not ent_ids:
            return ""
        ents = {e["id"]: e["name"] for e in supabase.table("entities").select("id, name").in_("id", list(ent_ids)).execute().data or []}
        facts = [f"{ents.get(r['source_entity_id'],'?')} {r['relation']} {ents.get(r['target_entity_id'],'?')}" for r in rels]
        return "\nRelated facts:\n" + "\n".join(facts[:15])
    except Exception:
        return ""


# Main (CLI) — uses env defaults, matching the /docs testing setup
def ask(query: str) -> str:
    tenant_id = os.environ.get("DEFAULT_TENANT_ID")
    role = os.environ.get("DEFAULT_ROLE", "admin")
    if not tenant_id:
        return "DEFAULT_TENANT_ID is not set in .env — cannot run CLI query."
    results = search_documents(query, tenant_id=tenant_id, role=role)
    if not results:
        return "I could not find the answer in the document."
    context = "\n".join(item["content"] for item in results)
    return generate_answer(query, context)



if __name__ == "__main__":
    try:
        user_query = input("Ask a question about the PDF: ")
        print(ask(user_query))
    except Exception as e:
        logger.error("Something went wrong: %s", e)
