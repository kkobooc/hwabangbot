import os, uuid, json, sys

# 즉시 출력 (버퍼링 방지)
print("[API] ========== BACKEND STARTING ==========", flush=True)
print(f"[API] Python version: {sys.version}", flush=True)
print(f"[API] PG_CONN set: {'YES' if os.environ.get('PG_CONN') else 'NO'}", flush=True)
print(f"[API] OPENAI_API_KEY set: {'YES' if os.environ.get('OPENAI_API_KEY') else 'NO'}", flush=True)
print(f"[API] PORT: {os.environ.get('PORT', 'not set')}", flush=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import StreamingResponse

print("[API] FastAPI imports done", flush=True)
print("[API] Starting import of langgraph app...", flush=True)

try:
    from app import app as langgraph_app
    print("[API] langgraph app imported successfully", flush=True)
except Exception as e:
    print(f"[API] FATAL: Failed to import langgraph app: {e}", flush=True)
    import traceback
    traceback.print_exc()
    raise

api = FastAPI(title="hwabang-ai API", version="0.1.0")

@api.on_event("startup")
async def startup_event():
    try:
        from app import emb, engine
        from sqlalchemy import text
        await emb.aembed_query("warmup")
        with engine.begin() as c:
            c.execute(text("SELECT 1"))
        print("[API] Connection warmup complete", flush=True)
    except Exception as e:
        print(f"[API] Warmup warning: {e}", flush=True)
    print("[API] FastAPI startup complete - ready to serve requests", flush=True)

@api.on_event("shutdown")
async def shutdown_event():
    print("[API] FastAPI shutting down...", flush=True)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryIn(BaseModel):
    query: str
    thread_id: str | None = None

@api.get("/")
def root():
    return {
        "message": "hwabang-ai API", 
        "endpoints": ["health", "invoke", "stream"]
    }

@api.get("/health")
def health():
    return {"status": "ok"}

@api.post("/invoke")
def invoke(body: QueryIn):
    thread = body.thread_id or f"api-{uuid.uuid4().hex}"
    result = langgraph_app.invoke(
        {"query": body.query},
        config={"configurable": {"thread_id": thread}}
    )
    return {"thread_id": thread, "answer": result.get("answer")}

# Support GET with query string parameters (query, thread_id)
@api.get("/invoke")
def invoke_get(query: str, thread_id: str | None = None):
    thread = thread_id or f"api-{uuid.uuid4().hex}"
    result = langgraph_app.invoke(
        {"query": query},
        config={"configurable": {"thread_id": thread}}
    )
    return {"thread_id": thread, "answer": result.get("answer")}

async def sse_event(event: str, data: dict) -> str:
    """SSE 포맷: event:<name>\ndata:<json>\n\n"""
    return f"event: {event}\n" + f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

@api.post("/stream")
async def stream(body: QueryIn):
    thread = body.thread_id or f"api-{uuid.uuid4().hex}"
    
    async def event_stream():
        # 즉시 start 이벤트 전송 (클라이언트 타임아웃 방지)
        yield await sse_event("start", {"thread_id": thread})

        try:
            first_chain_end = False

            # astream_events로 세부 이벤트까지 받기
            async for ev in langgraph_app.astream_events(
                {"query": body.query},
                config={"configurable": {"thread_id": thread}},
                version="v1"
            ):
                kind = ev["event"]
                data = ev.get("data", {})
                name = data.get("name", "")
                
                # 첫 번째 on_chain_end 이후만 허용 (분류 완료 후)
                if kind == "on_chain_end" and not first_chain_end:
                    first_chain_end = True
                    continue
                
                # 토큰 단위 스트리밍 (on_chat_model_stream) - answer_llm만 허용
                if kind == "on_chat_model_stream" and first_chain_end:
                    # answer_llm 태그가 있는 경우에만 스트리밍 (classifier_llm, rewrite_llm 제외)
                    tags = ev.get("tags", [])
                    if "answer_llm" not in tags:
                        continue

                    chunk = data.get("chunk", {})
                    text = getattr(chunk, "content", None) if chunk else None

                    if text:
                        yield await sse_event("token", {"content": text})
                
                # 노드 시작/종료 이벤트
                elif kind in ["on_chain_start", "on_chain_end"]:
                    name = data.get("name", "")
                    if name in ["classify_and_rewrite", "synthesize_art", "synthesize_general"]:
                        yield await sse_event("node", {"name": name, "status": kind})
                
                # 최종 결과
                elif kind == "on_chain_end" and data.get("name") == "__start__":
                    output = data.get("output", {})
                    if "answer" in output:
                        yield await sse_event("final", {"answer": output["answer"]})
                        
        except Exception as e:
            yield await sse_event("error", {"message": str(e)})
    
    headers = {
        "Cache-Control": "no-cache", 
        "X-Accel-Buffering": "no"  # nginx 버퍼링 방지
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

# GET stream endpoint with query string parameters
@api.get("/stream")
async def stream_get(query: str, thread_id: str | None = None):
    thread = thread_id or f"api-{uuid.uuid4().hex}"

    async def event_stream():
        # 즉시 start 이벤트 전송 (클라이언트 타임아웃 방지)
        yield await sse_event("start", {"thread_id": thread})

        try:
            first_chain_end = False

            async for ev in langgraph_app.astream_events(
                {"query": query},
                config={"configurable": {"thread_id": thread}},
                version="v1"
            ):
                kind = ev["event"]
                data = ev.get("data", {})
                name = data.get("name", "")

                if kind == "on_chain_end" and not first_chain_end:
                    first_chain_end = True
                    continue

                if kind == "on_chat_model_stream" and first_chain_end:
                    # answer_llm 태그가 있는 경우에만 스트리밍 (classifier_llm, rewrite_llm 제외)
                    tags = ev.get("tags", [])
                    if "answer_llm" not in tags:
                        continue

                    chunk = data.get("chunk", {})
                    text = getattr(chunk, "content", None) if chunk else None
                    if text:
                        yield await sse_event("token", {"content": text})

                elif kind in ["on_chain_start", "on_chain_end"]:
                    name = data.get("name", "")
                    if name in ["classify_and_rewrite", "synthesize_art", "synthesize_general"]:
                        yield await sse_event("node", {"name": name, "status": kind})

                elif kind == "on_chain_end" and data.get("name") == "__start__":
                    output = data.get("output", {})
                    if "answer" in output:
                        yield await sse_event("final", {"answer": output["answer"]})

        except Exception as e:
            yield await sse_event("error", {"message": str(e)})

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)