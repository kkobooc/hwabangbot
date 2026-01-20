import os, json, random, logging, sys
from typing import Any, TypedDict, List, Literal, Optional
from dotenv import load_dotenv

print("[APP] app.py loading started", flush=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)

print("[APP] importing langchain modules...", flush=True)
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

print("[APP] importing langgraph modules...", flush=True)
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

print("[APP] importing sqlalchemy...", flush=True)
from sqlalchemy import create_engine
from retriever import PGRawRetriever

load_dotenv()
print("[APP] dotenv loaded", flush=True)

OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL    = os.environ.get("OPENAI_BASE_URL") or None
LLM_MODEL          = os.environ.get("LLM_MODEL", "gpt-5-mini")
LLM_TEMPERATURE    = float(os.environ.get("LLM_TEMPERATURE", "0.3"))
EMBEDDING_MODEL    = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM      = int(os.environ.get("EMBEDDING_DIM", "1536"))

print("[APP] reading PG_CONN...", flush=True)
PG_CONN             = os.environ["PG_CONN"]
print(f"[APP] PG_CONN loaded (length={len(PG_CONN)})", flush=True)
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
print(f"[APP] Creating ChatOpenAI (model={LLM_MODEL})...", flush=True)
llm = ChatOpenAI(
    model=LLM_MODEL,
    temperature=LLM_TEMPERATURE,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    tags=["final"]  # ← synthesize 노드에서 쓰는 LLM이라면 최종답에만 달기
)
print("[APP] ChatOpenAI created", flush=True)

print(f"[APP] Creating OpenAIEmbeddings (model={EMBEDDING_MODEL})...", flush=True)
emb = OpenAIEmbeddings(
    model=EMBEDDING_MODEL,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL
)
print("[APP] OpenAIEmbeddings created", flush=True)

# ---------------- PG Engine 생성 ----------------
print("[APP] Creating SQLAlchemy engine...", flush=True)
engine = create_engine(PG_CONN)
print("[APP] SQLAlchemy engine created", flush=True)

# ---------------- Retriever 초기화 ----------------
print("[APP] Creating PGRawRetriever...", flush=True)
retriever = PGRawRetriever(
    engine=engine,
    embeddings=emb,
    k=RETRIEVER_K,
    max_chars=MAX_DOC_CHARS,
    content_column="text_content",  # processed_content 테이블의 본문 컬럼명
    embedding_table="processed_content_embeddings",
    content_table="processed_content",
)
print("[APP] PGRawRetriever created", flush=True)

# ---------------- 상태 정의 ----------------
class QueryClassification(TypedDict):
    topic: Literal["art", "general"]
    confidence: float

class RAGState(TypedDict, total=False):
    query: str
    topic: Literal["art", "general"]
    confidence: float
    # 검색용 키워드 (query_rewrite에서 생성)
    search_keyword: str
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
    state.setdefault("search_keyword", "")
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

# ---------------- Query Rewriter ----------------
class KeywordExtraction(TypedDict):
    keyword: str

async def query_rewrite(state: RAGState):
    """사용자 질문에서 미술 재료 검색용 키워드를 추출"""
    state = _init_state(state)

    rewrite_llm = llm.with_structured_output(KeywordExtraction).with_config({"run_name": "rewrite_llm"})

    rewrite_prompt = """
    사용자의 미술 관련 질문에서 **상품 검색에 사용할 핵심 키워드**를 1~3개 단어로 추출하세요.

    예시:
    - "유화 그릴 때 좋은 물감 추천해주세요" → "유화 물감"
    - "수채화 초보자인데 어떤 붓이 좋을까요?" → "수채화 붓"
    - "캔버스에 아크릴로 그리고 싶어요" → "아크릴 캔버스"
    - "세밀한 스케치용 연필 뭐가 좋아요?" → "세목 연필"
    - "마카로 일러스트 그리려고 해요" → "일러스트 마카"

    규칙:
    - 미술 재료/용품 카테고리를 포함 (물감, 붓, 캔버스, 연필, 팔레트 등)
    - 재료 종류를 포함 (유화, 수채화, 아크릴, 파스텔 등)
    - 간결하게 1~3단어로

    질문: {query}
    """

    try:
        result = await rewrite_llm.ainvoke(rewrite_prompt.format(query=state["query"]))
        state["search_keyword"] = result["keyword"]
        logging.info(f"[query_rewrite] 원본: '{state['query'][:50]}' → 키워드: '{state['search_keyword']}'")
    except Exception as e:
        # 실패 시 원본 쿼리 사용
        state["search_keyword"] = state["query"]
        state["errors"].append(f"query_rewrite_error: {e}")
        logging.error(f"[query_rewrite] 오류: {e}, 원본 쿼리 사용")

    return state

# ---------------- 콘텐츠/상품 검색 함수 ----------------
import asyncio

def _search_content_sync(query: str) -> str:
    """미술 DB에서 관련 콘텐츠를 찾아 포맷팅된 문자열로 반환합니다."""
    try:
        # 유사도 상위 10개를 가져와서 랜덤으로 3개 선택 (다양성 확보)
        docs = retriever.invoke(query)[:10]
        if not docs:
            return "(검색결과 없음)"

        # 상위 10개 중 랜덤으로 3개 선택 (단, 최소 유사도 보장을 위해 상위 절반에서 2개, 하위 절반에서 1개)
        if len(docs) >= 6:
            top_half = docs[:5]
            bottom_half = docs[5:10]
            selected = random.sample(top_half, min(2, len(top_half))) + random.sample(bottom_half, min(1, len(bottom_half)))
            random.shuffle(selected)
        else:
            selected = random.sample(docs, min(3, len(docs)))

        lines = []
        for i, item in enumerate(selected, 1):
            md = item.metadata or {}
            snippet = (item.page_content or "")[:MAX_DOC_CHARS] + "..."

            lines.append(
                f"- 콘텐츠제목_{i}: {md.get('title', '제목 없음')}\n"
                f"  콘텐츠본문_{i}: {snippet}\n"
                f"  콘텐츠URL_{i}: https://hwabang.net{md.get('detail_url', '')}\n"
                f"  콘텐츠이미지_{i}: {md.get('image_url', '')}\n"
                f"  관련상품_{i}: {md.get('products', '')}"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"(검색 오류: {e})"

async def search_content(query: str) -> str:
    """비동기 래퍼: 동기 retriever 호출을 별도 스레드에서 실행"""
    return await asyncio.to_thread(_search_content_sync, query)

async def fetch_recommendations(query: str) -> str:
    """추천 상품 API를 호출해 포맷팅된 문자열로 반환합니다."""
    import httpx
    api_url = "https://cafe24-recommendation-app-df9427a2b14e.herokuapp.com/api/v1/search-recommendations/kangkd78910"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(api_url, params={"keyword": query, "limit": 5}, headers={"Accept": "application/json"})
            if r.status_code != 200:
                return f"(추천상품 API 오류: {r.status_code})"

            data = r.json()
            recs = data.get("recommendations", [])

            # 디버그: 이미지 URL 확인 로깅
            for i, rec in enumerate(recs[:5], 1):
                img = rec.get('image_small', '')
                logging.info(f"[추천상품 #{i}] name={rec.get('product_name', '')[:30]}, image_small={img[:80] if img else 'EMPTY'}")
            
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
                img_url = rec.get('image_small', '')
                if not img_url:
                    logging.warning(f"[추천상품 #{i}] 이미지 URL 누락! product_name={rec.get('product_name', '')}")

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
                    
                # cafe24 도메인을 hwabang.net으로 변환
                product_url = rec.get('product_url', '')
                product_url = product_url.replace('kangkd78910.cafe24.com', 'hwabang.net')

                out.append(
                    f"- 추천상품명{i}: {rec.get('product_name', '').replace('<br>', ' ')}\n"
                    f"  추천이미지{i}: {rec.get('image_small', '')}\n"
                    f"  추천링크{i}: {product_url}\n"
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
     "사용자 질문: {query}\n\n"
     "=== 관련 콘텐츠 (sources) ===\n"
     "{sources}\n\n"
     "=== 추천 상품 (recommendations) ===\n"
     "{recommendations}\n\n"
     "중요 지시사항:\n"
     "1. 관련 콘텐츠: sources 데이터에서 _1, _2, _3 (최대 3개)의 '콘텐츠제목', '콘텐츠URL', '콘텐츠이미지', '콘텐츠본문' 값을 모두 추출하여 출력 형식에 삽입하라.\n"
     "2. 추천 상품: recommendations 데이터에서 1, 2, 3, 4 (최대 4개)의 '추천상품명', '추천이미지', '추천링크', '추천상품가격' 값을 모두 추출하여 출력 형식에 삽입하라.\n"
     "3. URL과 이미지 URL은 절대 임의로 생성하지 말고, 반드시 위 데이터에서 제공된 값만 그대로 사용하라.\n"
     "4. 제공된 데이터 개수만큼 모두 출력하라. 데이터가 4개면 4개 모두, 3개면 3개 모두 출력.\n"
    )
])

async def synthesize_art(state: RAGState):
    """도구를 직접 호출하여 정보 수집 후 최종 답변 생성"""
    state = _init_state(state)

    original_query = state["query"]
    # search_keyword가 있으면 검색에 사용, 없으면 원본 쿼리 사용
    search_kw = state.get("search_keyword") or original_query
    logging.info(f"[synthesize_art] 시작: 원본='{original_query[:50]}', 검색키워드='{search_kw}'")

    # 콘텐츠 검색 + 추천 상품 API 병렬 호출 (search_keyword 사용)
    async def safe_search_content():
        try:
            result = await search_content(search_kw)
            logging.info(f"[synthesize_art] search_content 성공 (키워드: {search_kw}, 길이: {len(result)})")
            return result
        except Exception as e:
            logging.error(f"[synthesize_art] search_content 오류: {e}")
            state["errors"].append(f"search_content error: {e}")
            return f"(검색 오류: {e})"

    async def safe_fetch_recommendations():
        try:
            result = await fetch_recommendations(search_kw)
            logging.info(f"[synthesize_art] fetch_recommendations 성공 (키워드: {search_kw}, 길이: {len(result)})")
            logging.info(f"[synthesize_art] recommendations 첫 300자: {result[:300]}")
            return result
        except Exception as e:
            logging.error(f"[synthesize_art] fetch_recommendations 오류: {e}")
            state["errors"].append(f"fetch_recommendations error: {e}")
            return f"(추천상품 오류: {e})"

    # 병렬 실행
    sources, recommendations_text = await asyncio.gather(
        safe_search_content(),
        safe_fetch_recommendations()
    )

    logging.info(f"[synthesize_art] 최종 - sources 길이: {len(sources)}, recommendations 길이: {len(recommendations_text)}")

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
graph.add_node("query_rewrite", query_rewrite)
graph.add_node("synthesize_art", synthesize_art)
graph.add_node("synthesize_general", synthesize_general)

# START → classify
graph.add_edge(START, "classify")

# classify → query_rewrite (art) 또는 synthesize_general (general)
def route_after_classify(state: RAGState):
    return "query_rewrite" if state["topic"] == "art" else "synthesize_general"

graph.add_conditional_edges(
    "classify",
    route_after_classify,
    {"query_rewrite": "query_rewrite", "synthesize_general": "synthesize_general"}
)

# query_rewrite → synthesize_art
graph.add_edge("query_rewrite", "synthesize_art")

graph.add_edge("synthesize_art", END)
graph.add_edge("synthesize_general", END)

print("[APP] Compiling graph...", flush=True)
app = graph.compile(checkpointer=MemorySaver())
print("[APP] ========== app.py fully loaded ==========", flush=True)

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