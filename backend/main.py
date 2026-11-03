import os
import json
import uuid
import time
import logging
from typing import Literal
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from backend.auth import CurrentUser, get_current_user, require_roles
from backend.document_permissions import ensure_document_manager, normalize_shared_roles, validate_pdf_upload
from backend.security import enforce_rate_limit
from backend.ingestion.ingestion import (
    get_pdf_url,
    supabase,
    search_documents,
    generate_answer,
    generate_answer_routed,
    generate_answer_stream,
    generate_faq,
    ingest_pdf,
    extract_text_from_pdf,
    clean_extracted_text,
    find_documents_by_name,
    list_documents,
    delete_document,
    check_cache,
    store_cache,
    log_request,
    is_simple_question,
    is_summary_question,
    merge_adjacent_chunks,
    expand_graph_context,
    MIN_SIMILARITY,
    get_neighbor_chunk_questions,
    select_chunk_followup_questions,
)
from backend.ingestion.agent import ask_agent

logger = logging.getLogger(__name__)
UPLOAD_RATE_LIMIT = int(os.environ.get("UPLOAD_RATE_LIMIT", "10"))
UPLOAD_RATE_WINDOW = int(os.environ.get("UPLOAD_RATE_WINDOW_SECONDS", "3600"))
QUESTION_RATE_LIMIT = int(os.environ.get("QUESTION_RATE_LIMIT", "30"))
QUESTION_RATE_WINDOW = int(os.environ.get("QUESTION_RATE_WINDOW_SECONDS", "60"))
app = FastAPI(title="Enterprise Knowledge Assistant API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://vaishali16-maker.github.io",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "https://ai-rag-chatbot-comw4kc59-vaishh1.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str


class AskWithHistory(Question):
    conversation_id: str | None = None
    document_ids: list[str] | None = None


class MessageFeedback(BaseModel):
    rating: Literal["helpful", "not_helpful"]


class DocumentAccessUpdate(BaseModel):
    allowed_roles: list[str] = Field(default_factory=list)


def _get_tenant_document(document_id: str, tenant_id: str) -> dict | None:
    rows = (
        supabase.table("documents")
        .select("id, name, tenant_id, uploaded_by, allowed_roles")
        .eq("id", document_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def _write_document_audit(
    user: CurrentUser,
    action: str,
    document_id: str,
    document_name: str,
    old_roles: list[str] | None = None,
    new_roles: list[str] | None = None,
) -> None:
    supabase.table("document_audit_log").insert({
        "tenant_id": user.tenant_id,
        "document_id": document_id,
        "document_name": document_name,
        "action": action,
        "actor_id": user.user_id,
        "actor_role": user.role,
        "old_allowed_roles": old_roles,
        "new_allowed_roles": new_roles,
    }).execute()


@app.get("/")
def home():
    return {"message": "Enterprise Knowledge Assistant API is running"}


@app.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {"email": user.email, "role": user.role, "tenant_id": user.tenant_id}


@app.get("/stats")
def get_stats(user: CurrentUser = Depends(get_current_user)):
    res = (
        supabase.table("request_log")
        .select("cache_hit, model, latency_ms")
        .eq("tenant_id", user.tenant_id)
        .order("id", desc=True)
        .limit(200)
        .execute()
    )
    rows = res.data or []
    total = len(rows)
    hits = sum(1 for r in rows if r["cache_hit"])
    avg_latency = int(sum(r["latency_ms"] or 0 for r in rows) / total) if total else 0
    return {
        "total_requests": total,
        "cache_hits": hits,
        "cache_hit_rate": round(hits / total, 3) if total else 0,
        "avg_latency_ms": avg_latency,
    }


@app.get("/documents")
def get_documents(user: CurrentUser = Depends(get_current_user)):
    documents = list_documents(user.tenant_id, user.role)
    document_ids = [document["id"] for document in documents if document.get("id")]
    if document_ids:
        try:
            chunks = supabase.table("chunks").select("document_id, suggested_questions").in_("document_id", document_ids).execute().data or []
            by_document: dict[str, list[str]] = {}
            for chunk in chunks:
                by_document.setdefault(chunk["document_id"], []).extend(chunk.get("suggested_questions") or [])
            for document in documents:
                seen: set[str] = set()
                questions = []
                for question in by_document.get(document["id"], []):
                    if question.casefold() not in seen:
                        seen.add(question.casefold())
                        questions.append(question)
                    if len(questions) == 20:
                        break
                document["suggested_questions"] = questions
        except Exception as e:
            logger.warning("Could not load document suggestions: %s", e)
            for document in documents:
                document["suggested_questions"] = []
    return {"documents": documents}


@app.delete("/documents/{document_id}")
def remove_document(document_id: str, user: CurrentUser = Depends(require_roles("admin", "hr", "manager"))):
    document = _get_tenant_document(document_id, user.tenant_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_document_manager(user.user_id, user.role, document)
    try:
        _write_document_audit(user, "delete", document_id, document["name"], document.get("allowed_roles"))
    except Exception as e:
        logger.exception("Could not audit document deletion")
        raise HTTPException(status_code=503, detail="Audit logging is unavailable; document was not deleted") from e
    if not delete_document(document_id, user.tenant_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted"}


@app.patch("/documents/{document_id}/access")
def update_document_access(
    document_id: str,
    data: DocumentAccessUpdate,
    user: CurrentUser = Depends(require_roles("admin", "hr", "manager")),
):
    document = _get_tenant_document(document_id, user.tenant_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_document_manager(user.user_id, user.role, document)
    allowed_roles = normalize_shared_roles(user.role, data.allowed_roles)
    try:
        result = (
            supabase.table("documents")
            .update({"allowed_roles": allowed_roles})
            .eq("id", document_id)
            .eq("tenant_id", user.tenant_id)
            .execute()
        )
        if result.data == []:
            raise HTTPException(status_code=404, detail="Document not found")
        _write_document_audit(user, "access_change", document_id, document["name"], document.get("allowed_roles"), allowed_roles)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Could not update document access")
        raise HTTPException(status_code=503, detail="Could not update document access and record the audit event") from e
    return {"document_id": document_id, "allowed_roles": allowed_roles, "message": "Document access updated"}


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    allowed_roles: str = Form(""),
    user: CurrentUser = Depends(require_roles("admin", "hr", "manager")),
):
    enforce_rate_limit(user.user_id, "upload", UPLOAD_RATE_LIMIT, UPLOAD_RATE_WINDOW)
    display_name = os.path.basename(file.filename or "")
    content = await file.read(20 * 1024 * 1024 + 1)
    validate_pdf_upload(display_name, content)
    roles = normalize_shared_roles(user.role, [r for r in allowed_roles.split(",") if r.strip()])

    upload_dir = os.path.join(os.path.dirname(__file__), "ingestion", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{uuid.uuid4().hex}.pdf")
    with open(file_path, "wb") as buffer:
        buffer.write(content)

    try:
        old_documents = find_documents_by_name(user.tenant_id, display_name)
        if user.role == "manager" and any(document.get("uploaded_by") != user.user_id for document in old_documents):
            raise HTTPException(status_code=403, detail="A manager cannot replace a document uploaded by someone else")
        result = ingest_pdf(
            file_path,
            tenant_id=user.tenant_id,
            original_name=display_name,
            allowed_roles=roles or None,
            uploaded_by=user.user_id,
        )
        try:
            _write_document_audit(user, "upload", result["document_id"], result["name"], new_roles=roles)
        except Exception as e:
            delete_document(result["document_id"], user.tenant_id)
            logger.exception("Could not audit document upload")
            raise HTTPException(status_code=503, detail="Audit logging is unavailable; upload was rolled back") from e
        for old_document in old_documents:
            old_id = old_document["id"]
            if delete_document(old_id, user.tenant_id):
                _write_document_audit(user, "delete", old_id, old_document["name"], old_document.get("allowed_roles"))
        try:
            full_text = clean_extracted_text(extract_text_from_pdf(file_path))
            suggested_questions = generate_faq(full_text)
        except Exception:
            suggested_questions = []
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        await file.close()

    return {
        "document_id": result["document_id"],
        "filename": result["name"],
        "chunks": result["chunks"],
        "message": "PDF uploaded and indexed successfully",
        "suggested_questions": suggested_questions,
    }


@app.post("/ask")
def ask_question(data: Question, user: CurrentUser = Depends(get_current_user)):
    enforce_rate_limit(user.user_id, "question", QUESTION_RATE_LIMIT, QUESTION_RATE_WINDOW)
    results = search_documents(data.question, user.tenant_id, user.role)
    context = "\n".join(item["content"] for item in results)
    answer = generate_answer(data.question, context)
    return {"question": data.question, "answer": answer, "sources": results}


@app.get("/conversations")
def list_conversations(user: CurrentUser = Depends(get_current_user)):
    res = (
        supabase.table("conversations")
        .select("id, title, created_at")
        .eq("user_id", user.user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"conversations": res.data or []}


@app.delete("/conversations/{conversation_id}")
def remove_conversation(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    owned_conversation = (
        supabase.table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
        .data
    )
    if not owned_conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    supabase.table("messages").delete().eq("conversation_id", conversation_id).execute()
    supabase.table("conversations").delete().eq("id", conversation_id).eq(
        "user_id", user.user_id
    ).execute()
    return {"message": "Conversation deleted"}


@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    convo = (
        supabase.table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("user_id", user.user_id)
        .execute()
        .data
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    res = (
        supabase.table("messages")
        .select("id, role, content, sources, created_at, low_confidence, top_similarity, follow_up_questions")
        .eq("conversation_id", conversation_id)
        .order("id")
        .execute()
    )
    messages = res.data or []
    if messages:
        try:
            feedback_rows = (
                supabase.table("message_feedback")
                .select("message_id, rating")
                .eq("user_id", user.user_id)
                .in_("message_id", [str(message["id"]) for message in messages])
                .execute()
                .data or []
            )
            feedback_by_message = {row["message_id"]: row["rating"] for row in feedback_rows}
            for message in messages:
                message["feedback"] = feedback_by_message.get(str(message["id"]))
        except Exception:
            # The app still loads chat history if the optional feedback migration
            # has not yet been applied.
            logger.warning("Message feedback table is unavailable", exc_info=True)
            for message in messages:
                message["feedback"] = None
    return {"messages": messages}


@app.post("/messages/{message_id}/feedback")
def submit_message_feedback(
    message_id: str,
    data: MessageFeedback,
    user: CurrentUser = Depends(get_current_user),
):
    message = (
        supabase.table("messages")
        .select("id, conversation_id")
        .eq("id", message_id)
        .limit(1)
        .execute()
        .data
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    conversation = (
        supabase.table("conversations")
        .select("id")
        .eq("id", message[0]["conversation_id"])
        .eq("user_id", user.user_id)
        .limit(1)
        .execute()
        .data
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        supabase.table("message_feedback").upsert(
            {
                "message_id": str(message[0]["id"]),
                "user_id": user.user_id,
                "rating": data.rating,
            },
            on_conflict="message_id",
        ).execute()
    except Exception as e:
        logger.exception("Could not save answer feedback")
        raise HTTPException(
            status_code=503,
            detail="Feedback storage is not ready. Apply the Supabase migration in backend/migrations.",
        ) from e
    return {"message_id": message_id, "rating": data.rating}

@app.get("/documents/{document_id}/graph")
def get_document_graph(document_id: str, user: CurrentUser = Depends(get_current_user)):
    visible_ids = {doc["id"] for doc in list_documents(user.tenant_id, user.role)}
    if document_id not in visible_ids:
        raise HTTPException(status_code=404, detail="Document not found")

    entities = (
        supabase.table("entities")
        .select("id, name, type")
        .eq("document_id", document_id)
        .execute()
        .data or []
    )
    relations = (
        supabase.table("relations")
        .select("source_entity_id, target_entity_id, relation")
        .eq("document_id", document_id)
        .execute()
        .data or []
    )
    return {"entities": entities, "relations": relations}

@app.get("/documents/{document_id}/file")
def get_document_file(document_id: str, user: CurrentUser = Depends(get_current_user)):
    visible_ids = {doc["id"] for doc in list_documents(user.tenant_id, user.role)}
    if document_id not in visible_ids:
        raise HTTPException(status_code=404, detail="Document not found")
    url = get_pdf_url(document_id)
    if not url:
        raise HTTPException(status_code=404, detail="File not available")
    return {"url": url}


@app.post("/ask/chat")
def ask_chat(data: AskWithHistory, user: CurrentUser = Depends(get_current_user)):
    enforce_rate_limit(user.user_id, "question", QUESTION_RATE_LIMIT, QUESTION_RATE_WINDOW)
    start = time.time()
    conv_id = data.conversation_id
    if conv_id:
        owned_conversation = (
            supabase.table("conversations")
            .select("id")
            .eq("id", conv_id)
            .eq("user_id", user.user_id)
            .limit(1)
            .execute()
            .data
        )
        if not owned_conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

    visible_documents = list_documents(user.tenant_id, user.role)
    visible_document_ids = {document["id"] for document in visible_documents}
    selected_document_ids = sorted(set(data.document_ids or []))
    unavailable_ids = set(selected_document_ids) - visible_document_ids
    if unavailable_ids:
        raise HTTPException(status_code=403, detail="One or more selected documents are unavailable")

    conversation_history: list[dict] = []
    if conv_id:
        try:
            prior_messages = (
                supabase.table("messages")
                .select("role, content, sources")
                .eq("conversation_id", conv_id)
                .order("id")
                .execute()
                .data or []
            )
            for item in prior_messages:
                if item.get("role") == "user":
                    conversation_history.append({"role": "user", "content": item.get("content", "")})
                elif item.get("role") == "assistant":
                    source_ids = {source.get("document_id") for source in (item.get("sources") or []) if source.get("document_id")}
                    if source_ids and source_ids.issubset(visible_document_ids):
                        conversation_history.append({"role": "assistant", "content": item.get("content", "")})
        except Exception:
            logger.warning("Could not load earlier messages for follow-up generation", exc_info=True)

    try:
        summary_question = is_summary_question(data.question)
        results = search_documents(
            data.question,
            user.tenant_id,
            user.role,
            match_count=8 if summary_question else 3,
            document_ids=selected_document_ids or None,
        )
        top_similarity = max((float(row.get("similarity") or 0) for row in results), default=0.0)
        low_confidence = not results or top_similarity < MIN_SIMILARITY
        cached = None
        follow_up_questions = []
        if low_confidence:
            answer = "I couldn't find a strong match in your documents. Try searching all documents or rephrasing your question."
            sources = []
            model_used = "low_confidence"
        else:
            if not selected_document_ids and not summary_question:
                candidate = check_cache(data.question, user.tenant_id, user.role)
                if candidate:
                    candidate_sources = candidate.get("sources") or []
                    candidate_ids = [source.get("document_id") for source in candidate_sources]
                    cached_score = max((float(source.get("similarity") or 0) for source in candidate_sources), default=0.0)
                    if all(doc_id in visible_document_ids for doc_id in candidate_ids) and cached_score >= MIN_SIMILARITY:
                        cached = candidate
            if cached:
                answer, sources = cached["answer"], cached.get("sources") or []
                model_used = "cache"
            else:
                model_used = "small" if is_simple_question(data.question) else "standard"
                doc_ids = list({r["document_id"] for r in results})
                context_results = merge_adjacent_chunks(results) if summary_question else results
                context = "\n".join(item["content"] for item in context_results) + expand_graph_context(doc_ids, data.question)
                answer = generate_answer_routed(data.question, context, use_small_model=(model_used == "small"))
                sources = results
                if not selected_document_ids:
                    store_cache(data.question, user.tenant_id, user.role, answer, sources)
            neighbor_questions = get_neighbor_chunk_questions(results, visible_document_ids)
            candidates = select_chunk_followup_questions(data.question, results, neighbor_questions, conversation_history)
            follow_up_questions = []
            for candidate in candidates:
                if len(follow_up_questions) == 3:
                    break
                try:
                    check = search_documents(candidate, user.tenant_id, user.role, match_count=1)
                    best_match = max((float(row.get("similarity") or 0) for row in check), default=0.0)
                    if best_match >= MIN_SIMILARITY:
                        follow_up_questions.append(candidate)
                except Exception:
                    logger.warning("Could not validate a follow-up suggestion", exc_info=True)
    except Exception as e:
        logger.exception("Question answering failed for user %s", user.user_id)
        raise HTTPException(status_code=502, detail=f"Question answering failed: {e}") from e

    if not conv_id:
        title = data.question[:60]
        conv_id = (
            supabase.table("conversations")
            .insert({"user_id": user.user_id, "title": title})
            .execute()
            .data[0]["id"]
        )

    latency_ms = int((time.time() - start) * 1000)
    log_request(user.tenant_id, user.user_id, data.question, bool(cached), model_used, latency_ms)

    inserted_messages = supabase.table("messages").insert([
        {
            "conversation_id": conv_id,
            "role": "user",
            "content": data.question,
        },
        {
            "conversation_id": conv_id,
            "role": "assistant",
            "content": answer,
            "sources": sources,
            "low_confidence": low_confidence,
            "top_similarity": top_similarity,
            "follow_up_questions": follow_up_questions,
        },
    ]).execute().data or []
    assistant_message_id = next(
        (message["id"] for message in inserted_messages if message.get("role") == "assistant"),
        None,
    )

    return {
        "question": data.question,
        "answer": answer,
        "sources": sources,
        "low_confidence": low_confidence,
        "top_similarity": top_similarity,
        "follow_up_questions": follow_up_questions,
        "conversation_id": conv_id,
        "assistant_message_id": assistant_message_id,
        "cache_hit": bool(cached),
        "model": model_used,
    }


@app.post("/ask/agent")
def ask_agent_route(data: Question, user: CurrentUser = Depends(get_current_user)):
    enforce_rate_limit(user.user_id, "question", QUESTION_RATE_LIMIT, QUESTION_RATE_WINDOW)
    return ask_agent(data.question, user.tenant_id, user.role)


@app.post("/ask/stream")
def ask_stream(data: Question, user: CurrentUser = Depends(get_current_user)):
    enforce_rate_limit(user.user_id, "question", QUESTION_RATE_LIMIT, QUESTION_RATE_WINDOW)
    results = search_documents(data.question, user.tenant_id, user.role)
    if not results:
        def empty_gen():
            yield "data: I could not find the answer in the document.\n\n"
        return StreamingResponse(empty_gen(), media_type="text/event-stream")
    context = "\n".join(item["content"] for item in results)

    def event_gen():
        sources_payload = json.dumps(
            [
                {
                    "source_file": r.get("source_file"),
                    "chunk_index": r.get("chunk_index"),
                    "similarity": r.get("similarity"),
                }
                for r in results
            ]
        )
        yield f"event: sources\ndata: {sources_payload}\n\n"
        for token in generate_answer_stream(data.question, context):
            safe_token = token.replace("\n", "\\n")
            yield f"data: {safe_token}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
