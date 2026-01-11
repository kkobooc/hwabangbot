# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAG-based AI chatbot for 화방넷 (Hwabang), a Korean art supplies e-commerce platform. The chatbot acts as an art supplies curator, answering questions about art materials and providing product recommendations.

## Commands

### Frontend (Next.js)
```bash
cd frontend
pnpm install          # Install dependencies
pnpm dev              # Development server
pnpm build            # Production build
pnpm lint             # ESLint
```

### Backend (Python/LangGraph)
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api:api --host 0.0.0.0 --port 1387 --reload   # Run FastAPI server
```

### Cronjob Scripts
```bash
cd cronjob
pip install -r requirements.txt
python storybook_ingest.py      # Fetch content from SweetOffer API
python content_processor.py     # Process content for embeddings
```

## Architecture

```
hwabangbot/
├── backend/          # LangGraph + FastAPI backend
├── cronjob/          # Data ingestion pipeline
└── frontend/         # Next.js 15 + React 19 UI
```

### Data Flow
1. Frontend sends query via SSE to `/api/stream` (Next.js route)
2. Next.js proxies to Python backend at `http://34.64.194.4:1387/stream`
3. Backend classifies query (art vs general), retrieves from pgvector, generates answer via LLM
4. Streaming token-by-token response through SSE

### Backend LangGraph Workflow (`backend/app.py`)
- **State**: `RAGState` with query, topic classification, docs, answer, messages
- **Nodes**: `classify` → `synthesize_art` or `synthesize_general`
- **Tools**: `search_content` (pgvector search), `fetch_recommendations` (external API)
- **Entry point**: `app:app` (compiled StateGraph with MemorySaver checkpointer)

### Retriever (`backend/retriever.py`)
Custom `PGRawRetriever` performs vector similarity search against `processed_content_embeddings` table, joins with `processed_content` for metadata.

### Frontend Streaming (`frontend/app/api/stream/route.ts`)
Proxies SSE from backend, handles events: `token` (incremental text), `node` (workflow status), `final` (complete answer), `error`.

## Environment Variables

Copy `backend/.env.example` to `backend/.env`:
- `OPENAI_API_KEY`: Required for LLM and embeddings
- `PG_CONN`: PostgreSQL connection string (with pgvector)
- `PGVECTOR_COLLECTION`: Embeddings table name
- `RETRIEVER_K`, `RETRIEVER_FETCH_K`: Vector search parameters
- `SYSTEM_PROMPT_PATH`: Path to system prompt file (Korean markdown)

## Key Files

- `backend/system_prompt_ko.md`: Korean system prompt defining curator behavior and output format
- `backend/langgraph.json`: LangGraph deployment config
- `cronjob/ddl/processed_content.sql`: Database schema for content and embeddings
- `frontend/components.json`: shadcn/ui component configuration

## Deployment

- **Frontend**: Vercel (auto-synced from v0.app)
- **Backend**: GCP instance at 34.64.194.4:1387
- **Database**: PostgreSQL with pgvector at postgresql.blendedlabs.xyz
