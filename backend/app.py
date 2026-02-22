import os, json, random, logging, sys
from typing import Annotated, Any, TypedDict, List, Literal, Optional
import operator
from dotenv import load_dotenv

print("[APP] app.py loading started", flush=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)

print("[APP] importing langchain modules...", flush=True)
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

print("[APP] importing langgraph modules...", flush=True)
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
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
    streaming=True,  # ← 토큰 단위 스트리밍 활성화
    tags=["final"]
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
engine = create_engine(PG_CONN, pool_size=5, max_overflow=10)
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
class RAGState(TypedDict, total=False):
    query: str
    topic: Literal["art", "general"]
    confidence: float
    # 검색용 키워드 (query_rewrite에서 생성)
    content_keyword: str   # 콘텐츠 검색용 (의도/행위 포함)
    product_keyword: str   # 상품 API용 (상품 카테고리)
    # 최종 출력
    answer: str
    # 대화 히스토리 (add_messages 리듀서로 자동 누적)
    messages: Annotated[list[BaseMessage], add_messages]
    errors: Annotated[list[str], operator.add]

# ---------------- Query Classifier + Rewriter (통합) ----------------
class ClassifyAndRewrite(TypedDict):
    topic: Literal["art", "general"]
    confidence: float
    content_keyword: str   # 콘텐츠 검색용 (의도/행위 포함)
    product_keyword: str   # 상품 API용 (상품 카테고리)

async def classify_and_rewrite(state: RAGState):
    """LLM 1회 호출로 분류 + 키워드 추출 동시 수행"""
    import time
    t0 = time.time()

    combined_llm = llm.with_structured_output(ClassifyAndRewrite).with_config({"run_name": "classify_rewrite_llm"})

    combined_prompt = """
    다음 질문을 분석하세요.

    ## 1단계: 분류
    - 미술/미술용품 관련 질문이면 topic='art'
    - 아니면 topic='general'
    - confidence: 확신도 (0.0 ~ 1.0)

    ## 2단계: 키워드 추출 (topic='art'인 경우만 의미 있음)
    - content_keyword: 콘텐츠 검색용 (질문 의도 포함, 2~5단어)
    - product_keyword: 상품 검색용 (2~4단어)
      - 단순 카테고리명만 쓰지 말고, 질문에 언급된 미술 관련 수식어를 포함하라
      - 포함해야 할 수식어 유형: 용도(수채화용, 세밀화용), 재질/결(황목, 중목, 세목),
        수준(초보자용, 전문가용), 규격/사이즈(6호, F형), 특성(소프트, 하드, 두꺼운),
        기법(유화, 아크릴, 수채) 등

    예시:
    | 질문 | topic | content_keyword | product_keyword |
    |------|-------|-----------------|-----------------|
    | "유화 물감 추천해주세요" | art | "유화 물감 추천" | "유화 물감" |
    | "아크릴 붓 세척 방법" | art | "아크릴 붓 세척 방법" | "아크릴 붓 세척" |
    | "오늘 날씨 어때?" | general | "" | "" |
    | "색연필 가격대별 추천" | art | "색연필 가격대별 추천" | "색연필" |
    | "초보자용 수채화 물감 추천" | art | "초보자 수채화 물감 추천" | "초보자용 수채화 물감" |
    | "세밀화 그리기 좋은 펜" | art | "세밀화 펜 추천" | "세밀화용 펜" |
    | "황목 캔버스 추천해주세요" | art | "황목 캔버스 추천" | "황목 캔버스" |
    | "소프트 파스텔 초보자 추천" | art | "소프트 파스텔 초보자 추천" | "초보자용 소프트 파스텔" |

    질문: {query}
    """

    try:
        result = await combined_llm.ainvoke(combined_prompt.format(query=state["query"]))
        updates = {
            "topic": result["topic"],
            "confidence": float(result["confidence"]),
            "content_keyword": result.get("content_keyword", ""),
            "product_keyword": result.get("product_keyword", ""),
        }
        logging.info(f"[TIMING] classify_and_rewrite: {time.time()-t0:.2f}초")
        logging.info(f"[classify_and_rewrite] topic={updates['topic']}, 콘텐츠='{updates['content_keyword']}', 상품='{updates['product_keyword']}'")
        return updates
    except Exception as e:
        logging.error(f"[classify_and_rewrite] 오류: {e}")
        return {
            "topic": "general",
            "confidence": 0.5,
            "content_keyword": state["query"],
            "product_keyword": state["query"],
            "errors": [f"classify_and_rewrite_error: {e}"],
        }

# ---------------- 콘텐츠/상품 검색 함수 ----------------
import asyncio
import httpx

_http_client = httpx.AsyncClient(timeout=10.0)

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
            # LLM이 요약하도록 300자 전달
            snippet = (item.page_content or "")[:300]

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
    api_url = "https://cafe24-recommendation-app-df9427a2b14e.herokuapp.com/api/v1/search-recommendations/kangkd78910"
    try:
        r = await _http_client.get(api_url, params={"keyword": query, "limit": 5}, headers={"Accept": "application/json"})
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
                regular = max(p, s)
                price_display = format_won(regular)

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
    import time
    t0 = time.time()
    errors = []

    original_query = state["query"]
    content_kw = state.get("content_keyword") or original_query
    product_kw = state.get("product_keyword") or original_query
    logging.info(f"[synthesize_art] 시작: 원본='{original_query[:50]}', 콘텐츠키워드='{content_kw}', 상품키워드='{product_kw}'")

    async def safe_search_content():
        t_start = time.time()
        try:
            result = await search_content(content_kw)
            logging.info(f"[TIMING] search_content: {time.time()-t_start:.2f}초")
            return result
        except Exception as e:
            logging.error(f"[synthesize_art] search_content 오류: {e}")
            errors.append(f"search_content error: {e}")
            return f"(검색 오류: {e})"

    async def safe_fetch_recommendations():
        t_start = time.time()
        try:
            result = await fetch_recommendations(product_kw)
            logging.info(f"[TIMING] fetch_recommendations: {time.time()-t_start:.2f}초")
            return result
        except Exception as e:
            logging.error(f"[synthesize_art] fetch_recommendations 오류: {e}")
            errors.append(f"fetch_recommendations error: {e}")
            return f"(추천상품 오류: {e})"

    t_parallel = time.time()
    sources, recommendations_text = await asyncio.gather(
        safe_search_content(),
        safe_fetch_recommendations()
    )
    logging.info(f"[TIMING] 병렬검색 총: {time.time()-t_parallel:.2f}초")

    prior_messages: List[BaseMessage] = state.get("messages", [])
    current_prompt_messages = RAG_PROMPT.format_messages(
        query=state["query"],
        sources=sources,
        recommendations=recommendations_text
    )
    input_messages: List[BaseMessage] = [*prior_messages, *current_prompt_messages]

    answer_llm = llm.with_config({
        "run_name": "answer_llm",
        "metadata": {"run_name": "answer_llm"},
        "tags": ["answer_llm"]
    })
    t_llm = time.time()
    out = await answer_llm.ainvoke(input_messages)
    logging.info(f"[TIMING] answer_llm: {time.time()-t_llm:.2f}초")
    logging.info(f"[TIMING] synthesize_art 총: {time.time()-t0:.2f}초")

    result = {
        "answer": out.content,
        "messages": [
            HumanMessage(content=state["query"]),
            AIMessage(content=out.content),
        ],
    }
    if errors:
        result["errors"] = errors
    return result

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
        answer_llm = llm.with_config({
            "run_name": "answer_llm",
            "metadata": {"run_name": "answer_llm"},
            "tags": ["answer_llm"]
        })
        prior_messages: List[BaseMessage] = state.get("messages", [])
        this_turn = [HumanMessage(content=general_prompt.format(query=state["query"]))]
        response = await answer_llm.ainvoke([*prior_messages, *this_turn])
        return {
            "answer": response.content,
            "messages": [
                HumanMessage(content=state["query"]),
                AIMessage(content=response.content),
            ],
        }
    except Exception as e:
        return {
            "answer": "죄송합니다. 현재 답변을 생성할 수 없습니다. 미술 관련 질문이시라면 더 정확한 답변을 드릴 수 있어요! 😊",
            "errors": [f"synthesize_general_error: {e}"],
        }

# ---------------- Graph ----------------
graph = StateGraph(RAGState)

graph.add_node("classify_and_rewrite", classify_and_rewrite)
graph.add_node("synthesize_art", synthesize_art)
graph.add_node("synthesize_general", synthesize_general)

# START → classify_and_rewrite
graph.add_edge(START, "classify_and_rewrite")

# classify_and_rewrite → synthesize_art (art) 또는 synthesize_general (general)
def route_after_classify(state: RAGState):
    return "synthesize_art" if state["topic"] == "art" else "synthesize_general"

graph.add_conditional_edges(
    "classify_and_rewrite",
    route_after_classify,
    {"synthesize_art": "synthesize_art", "synthesize_general": "synthesize_general"}
)

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