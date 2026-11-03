# AI RAG Chatbot

A Retrieval-Augmented Generation (RAG) chatbot built for sales teams — upload 
product spec sheets, pricing docs, or policy PDFs and get grounded, cited answers 
to pricing, feature, and policy questions instead of digging through documents manually.

**Live demo:** https://vaishali16-maker.github.io/AI-RAG-chatbot/ 
**Backend API:** https://ai-rag-chatbot-wxrb.onrender.com/
                

## How it works
1. Upload a PDF — text is extracted, cleaned, and split into overlapping chunks
2. Each chunk is embedded using Cohere's embeddings API and stored in Supabase (pgvector)
3. On a question, the query is embedded and matched against stored chunks via vector similarity search
4. Matched chunks are passed as context to an LLM (via Groq) to generate a grounded answer
5. Answers include the cited source chunk(s) and similarity score, so users can verify against the original text

### Agentic Mode (`/ask/agent`)
A second endpoint runs the same retrieval pipeline through a LangGraph-based
agent instead of the direct flow above:
1. A router node (LLM call) classifies the question as needing document
   retrieval or a direct reply — greetings and general questions skip
   retrieval entirely
2. If retrieval is needed, a custom LangChain retriever wraps the existing
   Supabase `match_documents` RPC to fetch relevant chunks
3. The LLM generates a grounded answer using LangChain's `ChatGroq`, with the
   same "don't guess" grounding rules as the standard `/ask` endpoint

### Tested Capabilities
Beyond basic Q&A, this system has been validated against harder query types:
- **Multi-fact reasoning**: correctly computes derived values (e.g. discounted 
  per-seat pricing) by combining multiple facts from the document
- **Cross-section synthesis**: connects related information from different 
  parts of a document (e.g. security certification + deployment options)
- **Hallucination resistance**: correctly declines to answer when information 
  isn't present in the document, rather than guessing
- **Policy edge-case reasoning**: compares dates/numbers against stated rules 
  (e.g. refund eligibility based on cancellation timing)

  ### Limitations & Next Steps
- Currently single-PDF per session — multi-document support planned
- No conversation memory yet — each question is independent
- Cold start (~50s) on Render free tier for the first request after inactivity
- Re-uploading the same file now correctly replaces old chunks instead of 
  duplicating them (fixed after discovering duplicate chunks were degrading 
  retrieval quality during testing)

## Stack
- **Backend:** FastAPI (Python)
- **Embeddings:** Cohere (`embed-english-light-v3.0`), via `langchain-cohere`
- **Vector store:** Supabase (pgvector)
- **LLM:** Groq (`openai/gpt-oss-20b`), via `langchain-groq`
- **Agent orchestration:** LangGraph (conditional routing) + a custom LangChain retriever
- **Frontend:** Static HTML/JS, hosted on GitHub Pages
- **Deployment:** Render (backend, free tier)

## Notes
- The backend runs on Render's free tier, which spins down after inactivity — the first request after idle time may take up to ~50 seconds to respond.
- Retrieval uses cosine similarity; the percentage shown next to each cited passage is the raw similarity score, not a fixed 0-100 confidence scale.

## Local setup

The Next.js app uses the local FastAPI backend at `http://127.0.0.1:8000` by
default. Keep the required service credentials in `backend/.env`:

```
SUPABASE_URL=
SUPABASE_KEY=
COHERE_API_KEY=
GROQ_API_KEY=
```

Install and run the backend from the repository root:

```bash
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

Set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in
`frontend/.env.local`. `NEXT_PUBLIC_API_URL` is optional; when omitted, the
frontend connects to the local backend above. Then start the frontend in a
second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). If the API URL changes,
restart the Next.js dev server so it picks up the updated environment.

To enable persistent answer feedback, run
`backend/migrations/20261031_message_feedback.sql` once in the Supabase SQL
Editor for the project used by `backend/.env`.
