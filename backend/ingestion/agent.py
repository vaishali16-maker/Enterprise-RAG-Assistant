from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END
from backend.ingestion.langchain_components import llm, SupabaseMatchRetriever
from langchain_core.messages import HumanMessage


# State — this dict is passed between every node in the graph
class RAGState(TypedDict):
    question: str
    tenant_id: str
    role: str
    route: Literal["retrieve", "direct"]
    context: str
    sources: list
    answer: str
    
# Node 1: route — decide whether this question needs document search
ROUTER_PROMPT = """You are a router for a document Q&A assistant.
Decide if the user's message requires searching uploaded documents to answer,
or if it can be answered directly without any document lookup.
Reply with EXACTLY one word: "retrieve" or "direct".
Examples:
- "hi" -> direct
- "what can you do?" -> direct
- "thanks!" -> direct
- "who coined the term vibe coding?" -> retrieve
- "what does the report say about revenue?" -> retrieve
- "summarize the document" -> retrieve

Message: {question}
Answer:"""

# Node 1: router — decide whether this question needs document search
def route_question(state: RAGState) -> RAGState:
    try:
        decision = llm.invoke([HumanMessage(content=ROUTER_PROMPT.format(question=state["question"]))]
         ).content.strip().lower()
    except Exception:
        decision = "retrieve"
    state["route"] = "direct" if "direct" in decision else "retrieve"
    return state

# Node 2: retrieve — pull relevant chunks from the vector DB
def retrieve(state: RAGState) -> RAGState:
    retriever = SupabaseMatchRetriever(tenant_id=state["tenant_id"], role=state["role"])
    docs = retriever.invoke(state["question"])
    state["context"] = "\n".join(d.page_content for d in docs)
    state["sources"] = [d.metadata for d in docs]
    return state

# Node 3: generate — answer using context if present, otherwise answer plainly
DOC_PROMPT = """You are an AI document assistant.
Answer the user's question using ONLY the information provided in the context.
Rules:
- Give a clear, complete answer in a natural sentence.
- Do not add information that is not present in the context.
- If the answer is not present in the context, say:
"I could not find the answer in the document."
Context:{context}
Question:{question}
Answer:"""

DIRECT_PROMPT = """You are a friendly assistant for a document Q&A tool.
Answer the user's message briefly and naturally. Do not mention documents
or context since none were retrieved for this message.
Message:{question}
Answer:"""

# Node 3: generate — answer using context if present, otherwise answer plainly
def generate(state: RAGState) -> RAGState:
    if state["route"] == "retrieve":
        prompt = DOC_PROMPT.format(context=state["context"], question=state["question"])
    else:
        prompt = DIRECT_PROMPT.format(question=state["question"])
        state["sources"] = []

    try:
        state["answer"] = llm.invoke([HumanMessage(content=prompt)]).content
    except Exception as e:
        state["answer"] = f"Sorry, something went wrong generating a response: {e}"

    return state

#Build the graph of nodes and edges
def build_graph():
    graph = StateGraph(RAGState)
    graph.add_node("route", route_question)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.set_entry_point("route")
    graph.add_conditional_edges("route",lambda state: state["route"],
        {"retrieve": "retrieve", "direct": "generate"},
    )
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
rag_agent = build_graph()

# Entry point for the FastAPI route
def ask_agent(question: str, tenant_id: str, role: str) -> dict:
    result = rag_agent.invoke({"question": question, "tenant_id": tenant_id, "role": role})
    return {
        "question": question,
        "route": result["route"],
        "answer": result["answer"],
        "sources": result.get("sources", []),
    }

if __name__ == "__main__":
    import os
    q = input("Ask something: ")
    print(ask_agent(q, os.environ["DEFAULT_TENANT_ID"], os.environ.get("DEFAULT_ROLE", "admin")))