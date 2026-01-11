import os, json
from typing import Any, TypedDict, List, Literal, Optional
from dotenv import load_dotenv

from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.prompts import ChatPromptTemplate

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.tools import tool
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, ToolMessage

from sqlalchemy import create_engine
from retriever import PGRawRetriever

load_dotenv()

OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL    = os.environ.get("OPENAI_BASE_URL") or None
LLM_MODEL          = os.environ.get("LLM_MODEL", "gpt-5-mini")
LLM_TEMPERATURE    = float(os.environ.get("LLM_TEMPERATURE", "0.3"))
EMBEDDING_MODEL    = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM      = int(os.environ.get("EMBEDDING_DIM", "1536"))

PG_CONN             = os.environ["PG_CONN"]
PGVECTOR_COLLECTION = os.environ.get("PGVECTOR_COLLECTION", "processed_content_embeddings")

RETRIEVER_SEARCH_TYPE = os.environ.get("RETRIEVER_SEARCH_TYPE", "mmr")
RETRIEVER_K           = int(os.environ.get("RETRIEVER_K", "20"))
RETRIEVER_FETCH_K     = int(os.environ.get("RETRIEVER_FETCH_K", "20"))

MAX_DOC_CHARS     = int(os.environ.get("MAX_DOC_CHARS", "400"))

SYSTEM_PROMPT_PATH = os.environ.get("SYSTEM_PROMPT_PATH", "system_prompt_ko.md")

# ---------------- OpenAI ----------------
LANGCHAIN_TRACING=os.environ.get("LANGCHAIN_TRACING", "false")
LANGCHAIN_ENDPOINT=os.environ.get("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
LANGCHAIN_API_KEY=os.environ.get("LANGCHAIN_API_KEY", "")
LANGCHAIN_PROJECT=os.environ.get("LANGCHAIN_PROJECT", "hwabangnet")

# ---------------- System Prompt ----------------
if os.path.exists(SYSTEM_PROMPT_PATH):
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT_TEXT = f.read()
else:
    SYSTEM_PROMPT_TEXT = (
        "너는 미술용품 사이트 화방넷의 친절한 AI 미술용품 큐레이터다. "
        "콘텐츠별 요점 요약 → 전체 통합 해석 → 관련 콘텐츠/상품 블록을 출력한다. "
        "미술 비관련 질문이면 미술 질문 대화로 유도한다."
    )

# ---------------- LLM/Embeddings ----------------
# (OPENAI_BASE_URL이 있으면 해당 엔드포인트 사용)
llm = ChatOpenAI(
    model=LLM_MODEL,
    temperature=LLM_TEMPERATURE,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    tags=["final"]  # ← synthesize 노드에서 쓰는 LLM이라면 최종답에만 달기
)

emb = OpenAIEmbeddings(
    model=EMBEDDING_MODEL,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL
)

# ---------------- PG Engine 생성 ----------------
engine = create_engine(PG_CONN)

# ---------------- Retriever 초기화 ----------------
retriever = PGRawRetriever(
    engine=engine,
    embeddings=emb,
    k=RETRIEVER_K,
    max_chars=MAX_DOC_CHARS,
    content_column="text_content",  # processed_content 테이블의 본문 컬럼명
    embedding_table="processed_content_embeddings",
    content_table="processed_content",
)

# ---------------- 상태 정의 ----------------
class QueryClassification(TypedDict):
    topic: Literal["art", "general"]
    confidence: float

class RAGState(TypedDict, total=False):
    query: str
    topic: Literal["art", "general"]
    confidence: float
    # 백워드 호환(없어도 동작)
    docs: List[Document]
    # 최종 출력
    answer: str
    # 디버깅/로그용
    messages: List[BaseMessage]
    errors: List[str]

def _init_state(state: RAGState) -> RAGState:
    state.setdefault("docs", [])
    state.setdefault("messages", [])
    state.setdefault("errors", [])
    state.setdefault("topic", "general")
    state.setdefault("confidence", 0.0)
    return state

# ---------------- Query Classifier ----------------
async def query_classifier(state: RAGState):
    """LLM 기반 쿼리 분류"""
    state = _init_state(state)
    classifier_llm = llm.with_structured_output(QueryClassification).with_config({"run_name": "classifier_llm"})

    classification_prompt = """
    다음 질문을 분석해서 미술/미술용품 관련인지 판단해주세요.
    - 미술/미술용품 관련이면 topic='art'
    - 아니면 topic='general'
    결과는 JSON( topic, confidence )으로.

    질문: {query}
    """
    try:
        result = await classifier_llm.ainvoke(classification_prompt.format(query=state["query"]))
        state["topic"] = result["topic"]
        state["confidence"] = float(result["confidence"])
    except Exception as e:
        state["topic"] = "general"
        state["confidence"] = 0.5
        state["errors"].append(f"classifier_error: {e}")
    return state

# ---------------- ReAct Agent Tools ----------------
@tool
def search_content(query: str) -> str:
    """미술 DB에서 관련 콘텐츠를 찾아 포맷팅된 문자열로 반환합니다."""
    try:
        docs = retriever.invoke(query)[:min(RETRIEVER_K, 5)]
        if not docs:
            return "(검색결과 없음)"
        
        lines = []
        for item in docs[:3]:  # 최대 3개만 표시
            md = item.metadata or {}
            snippet = (item.page_content or "")[:MAX_DOC_CHARS] + "..."
            
            lines.append(
                f"- 콘텐츠제목: {md.get('title', '제목 없음')}\n"
                f"  콘텐츠본문: {snippet}\n"
                f"  콘텐츠URL: https://hwabang.net{md.get('detail_url', '')}\n"
                f"  콘텐츠이미지: {md.get('image_url', '')}\n"
                f"  관련상품: {md.get('products', '')}"
            )
        
        return "\n".join(lines)
    except Exception as e:
        return f"(검색 오류: {e})"

@tool
def fetch_recommendations(query: str) -> str:
    """추천 상품 API를 호출해 포맷팅된 문자열로 반환합니다."""
    import httpx
    api_url = "https://cafe24-recommendation-app-df9427a2b14e.herokuapp.com/api/v1/search-recommendations/kangkd78910"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(api_url, params={"keyword": query, "limit": 5}, headers={"Accept": "application/json"})
            if r.status_code != 200:
                return f"(추천상품 API 오류: {r.status_code})"
            
            data = r.json()
            recs = data.get("recommendations", [])
            
            if not recs:
                return "(추천상품 없음)"
            
            out = []
            # 가격 문자열을 안전하게 정수 원화로 표기하는 유틸
            from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
            def format_won(val: Any) -> str:
                try:
                    d = Decimal(str(val))
                    q = int(d.to_integral_value(rounding=ROUND_HALF_UP))
                    return f"{q:,}원"
                except (InvalidOperation, ValueError, TypeError):
                    try:
                        q = int(float(val))
                        return f"{q:,}원"
                    except Exception:
                        return f"{val}원"

            for i, rec in enumerate(recs[:4], 1):
                price = rec.get('price', '0')
                sale_price = rec.get('sale_price', '0')

                # 숫자 비교용 안전 변환
                def to_int(val: Any) -> int:
                    try:
                        d = Decimal(str(val))
                        return int(d.to_integral_value(rounding=ROUND_HALF_UP))
                    except Exception:
                        try:
                            return int(float(val))
                        except Exception:
                            return 0

                p = to_int(price)
                s = to_int(sale_price)

                if p > 0 and s > 0 and p != s:
                    discounted = min(p, s)
                    regular = max(p, s)
                    price_display = f"{format_won(discounted)} <s>{format_won(regular)}</s>"
                else:
                    # 둘 중 하나가 0이거나 동일하면 할인가 없음 → 정가만 표기
                    regular = max(p, s)
                    price_display = format_won(regular)
                    
                out.append(
                    f"- 추천상품명{i}: {rec.get('product_name', '').replace('<br>', ' ')}\n"
                    f"  추천이미지{i}: {rec.get('image_small', '')}\n"
                    f"  추천링크{i}: {rec.get('product_url', '')}\n"
                    f"  추천상품번호{i}: {rec.get('product_no', '')}\n"
                    f"  추천상품가격{i}: {price_display}\n"
                )
            
            return "\n".join(out)
    except Exception as e:
        return f"(추천상품 오류: {e})"




# ---------------- Synthesizer ----------------
RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT_TEXT),
    ("human",
     "사용자 질문: {query}\n"
     "다음은 DB에서 찾은 콘텐츠들이다(최대 3개). 각 항목은 제목, 본문, URL, 이미지URL, 관련상품 순서다:\n"
     "{sources}\n\n"
     "다음은 추천 상품들이다(최대 3개):\n"
     "{recommendations}\n\n"
     "위 재료를 바탕으로 '콘텐츠별 요점 요약 → 전체 통합 해석 → 관련 콘텐츠/상품 블록' 형식으로 답변을 생성하라."
    )
])

async def synthesize_art(state: RAGState):
    """도구를 직접 호출하여 정보 수집 후 최종 답변 생성"""
    state = _init_state(state)
    
    # 검색 결과 가져오기 (이제 포맷팅된 문자열로 바로 반환)
    try:
        sources = search_content(state["query"])
    except Exception as e:
        sources = f"(검색 오류: {e})"
        state["errors"].append(f"search_content error: {e}")
    
    # 추천 상품 가져오기 (이제 포맷팅된 문자열로 바로 반환)
    try:
        recommendations_text = fetch_recommendations(state["query"])
    except Exception as e:
        recommendations_text = f"(추천상품 오류: {e})"
        state["errors"].append(f"fetch_recommendations error: {e}")

    # 대화 히스토리 포함: 직전 메시지들과 현재 질문을 함께 전달
    prior_messages: List[BaseMessage] = state.get("messages", [])
    current_prompt_messages = RAG_PROMPT.format_messages(
        query=state["query"],
        sources=sources,
        recommendations=recommendations_text
    )
    # 프롬프트 템플릿이 생성한 메시지 앞에 기존 히스토리를 붙임
    input_messages: List[BaseMessage] = [*prior_messages, *current_prompt_messages]

    # 여러 방법으로 run_name 설정 시도
    answer_llm = llm.with_config({
        "run_name": "answer_llm",
        "metadata": {"run_name": "answer_llm"},
        "tags": ["answer_llm"]
    })
    out = await answer_llm.ainvoke(input_messages)
    state["answer"] = out.content
    # 메모리에 이번 턴 히스토리 추가
    state["messages"].extend([
        HumanMessage(content=state["query"]),
        AIMessage(content=state["answer"])])
    return state

async def synthesize_general(state: RAGState):
    """일반 질문 기본 답변"""
    general_prompt = """
    이 시스템은 화방넷 미술용품 전문 상담에 특화되어 있으므로 미술용품 관련 질문이 아니면 미술 관련 질문으로 유도해라.
    미술용품과 관련이 없는 화방넷 고객 문의이면 아래 고객센터를 알려줘라.
    ```html
    <a data-cta="customer_center" href="https://hwabang.net/board/index.html?board_no=1">고객센터</a>
    ```
    질문: {query}
    """
    try:
        # 여러 방법으로 run_name 설정 시도
        answer_llm = llm.with_config({
            "run_name": "answer_llm",
            "metadata": {"run_name": "answer_llm"},
            "tags": ["answer_llm"]
        })
        prior_messages: List[BaseMessage] = state.get("messages", [])
        this_turn = [HumanMessage(content=general_prompt.format(query=state["query"]))]
        response = await answer_llm.ainvoke([*prior_messages, *this_turn])
        state["answer"] = response.content
        state["messages"].extend([
            HumanMessage(content=state["query"]),
            AIMessage(content=state["answer"])])
    except Exception as e:
        state["answer"] = "죄송합니다. 현재 답변을 생성할 수 없습니다. 미술 관련 질문이시라면 더 정확한 답변을 드릴 수 있어요! 😊"
        state.setdefault("errors", []).append(f"synthesize_general_error: {e}")
    return state

# ---------------- Graph ----------------
graph = StateGraph(RAGState)

graph.add_node("classify", query_classifier)
graph.add_node("synthesize_art", synthesize_art)
graph.add_node("synthesize_general", synthesize_general)

# 최신 권장사항: START에서 진입 엣지를 명시적으로 추가
graph.add_edge(START, "classify")

def route_after_classify(state: RAGState):
    return "synthesize_art" if state["topic"] == "art" else "synthesize_general"

graph.add_conditional_edges(
    "classify",
    route_after_classify,
    {"synthesize_art": "synthesize_art", "synthesize_general": "synthesize_general"}
)

graph.add_edge("synthesize_art", END)
graph.add_edge("synthesize_general", END)

app = graph.compile(checkpointer=MemorySaver())

# ---------------- Run ----------------
# if __name__ == "__main__":
#     # print(SYSTEM_PROMPT_TEXT)
#     import uuid
#     user_query = "유화 물감 뭐가 좋아요?"
#     thread_id = f"{THREAD_ID_PREFIX}{uuid.uuid4().hex}"

#     print("\n[LangGraph stream]")
#     for event in app.stream({"query": user_query}, config={"configurable": {"thread_id": thread_id}}):
#         print(event)  # 노드별 입출력 확인
#     print("\n[Final]")
#     result = app.invoke({"query": user_query}, config={"configurable": {"thread_id": thread_id}})
#     print(result["answer"])