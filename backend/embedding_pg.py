import os, math, time
from typing import List, Dict, Any
from dotenv import load_dotenv

import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector  # ★ 벡터 어댑터 등록
from langchain_openai import OpenAIEmbeddings

# ---------- 환경변수 ----------
load_dotenv()
raw = os.environ["PG_CONN"]
PG_CONN = raw.replace("+psycopg2", "").replace("+psycopg", "")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

COLL_TABLE = "processed_content_embeddings"
SRC_TABLE  = "processed_content"

BATCH_SIZE = 128  # OpenAI 임베딩 배치 요청 크기
MAX_TEXT_CHARS = int(os.getenv("MAX_DOC_CHARS", "3000"))

# ---------- 임베딩 모델 ----------
EMB = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=OPENAI_API_KEY)

def to_text(row: Dict[str, Any]) -> str:
    """제목 + 해시태그 + 본문 앞부분을 합쳐 단일 텍스트로 만듦."""
    parts = [
        (row.get("title") or "").strip(),
        " ".join(row.get("hashtags") or []),
        (row.get("text_content") or "")[:MAX_TEXT_CHARS],
    ]
    # 공백 정리
    return "\n".join([p for p in parts if p]).strip()

def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

def fetch_rows_for_embedding(conn) -> List[Dict[str, Any]]:
    """
    변경분만(신규 또는 수정된 콘텐츠) 가져오기.
    - processed_content_embeddings.created_at < processed_content.modified_at 이거나
    - 임베딩이 아직 없는 행
    """
    q = f"""
    SELECT c.id, c.title, c.detail_url, c.image_url, c.products, c.hashtags, c.text_content,
           c.modified_at,
           e.created_at AS embedded_at
    FROM {SRC_TABLE} c
    LEFT JOIN {COLL_TABLE} e
      ON e.content_id = c.id
    WHERE c.published = true
      AND (
           e.content_id IS NULL
        OR (c.modified_at IS NOT NULL AND e.created_at IS NOT NULL AND c.modified_at > e.created_at)
      )
    """
    with conn.cursor() as cur:
        cur.execute(q)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def upsert_embeddings(conn, rows: List[Dict[str, Any]], vectors: List[List[float]]):
    """
    (content_id, embedding, title, detail_url, image_url, products) upsert
    """
    assert len(rows) == len(vectors)
    q = sql.SQL(f"""
        INSERT INTO {COLL_TABLE}
            (content_id, embedding, title, detail_url, image_url, products)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_id) DO UPDATE SET
            embedding   = EXCLUDED.embedding,
            title       = EXCLUDED.title,
            detail_url  = EXCLUDED.detail_url,
            image_url  = EXCLUDED.image_url,
            products    = EXCLUDED.products,
            created_at  = now()
    """)
    data = []
    for r, v in zip(rows, vectors):
        # psycopg3 + pgvector: register_vector() 했으면 Python list[float] 그대로 바인드 가능
        data.append((
            r["id"],
            v,
            r.get("title"),
            r.get("detail_url"),
            r.get("image_url"),
            r.get("products"),     # TEXT[] -> list[str] 그대로
        ))
    with conn.cursor() as cur:
        cur.executemany(q, data)

def main():
    # 접속 + pgvector 어댑터 등록
    with psycopg.connect(PG_CONN, autocommit=False) as conn:
        register_vector(conn)  # ★ 리스트를 vector 타입으로 자동 변환

        # 보장: 테이블/스키마가 없다면(최초) 만들어두기
        with conn.cursor() as cur:
            cur.execute(f"""
            CREATE TABLE IF NOT EXISTS processed_content_embeddings (
              id BIGSERIAL PRIMARY KEY,
              content_id BIGINT NOT NULL REFERENCES processed_content(id) ON DELETE CASCADE,
              embedding vector({EMBEDDING_DIM}) NOT NULL,
              title TEXT,
              detail_url TEXT,
              image_url TEXT,
              products TEXT[],
              created_at TIMESTAMPTZ DEFAULT now(),
              UNIQUE(content_id)
            );
            """)
        conn.commit()

        rows = fetch_rows_for_embedding(conn)
        if not rows:
            print("변경된(또는 신규) 콘텐츠가 없습니다. ✅")
            return

        # 텍스트 준비
        texts = [to_text(r) for r in rows]

        # 배치 임베딩 + 업서트
        total = len(rows)
        print(f"{total}개의 문서 임베딩/업서트 진행...")
        done = 0
        for idxs in chunked(list(range(total)), BATCH_SIZE):
            batch_texts = [texts[i] for i in idxs]
            # OpenAI 임베딩 호출 (배치)
            vecs = EMB.embed_documents(batch_texts)

            batch_rows = [rows[i] for i in idxs]
            upsert_embeddings(conn, batch_rows, vecs)
            conn.commit()

            done += len(idxs)
            print(f"- {done}/{total} 완료")

        print("모든 임베딩/업서트 완료")

if __name__ == "__main__":
    main()
