#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embedding_sync.py
- processed_content 테이블에서 변경된 콘텐츠를 찾아 임베딩 생성
- pgvector를 사용하여 processed_content_embeddings 테이블에 저장
"""
import os
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv

import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector
from langchain_openai import OpenAIEmbeddings

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------- 환경변수 ----------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL 환경변수가 필요합니다")

# psycopg3 호환을 위해 드라이버 접미사 제거
PG_CONN = DATABASE_URL.replace("+psycopg2", "").replace("+psycopg", "")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY 환경변수가 필요합니다")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

COLL_TABLE = "processed_content_embeddings"
SRC_TABLE = "processed_content"

BATCH_SIZE = 128
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
    return "\n".join([p for p in parts if p]).strip()


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]


def create_embeddings_table(conn):
    """임베딩 테이블 생성 (IF NOT EXISTS)"""
    with conn.cursor() as cur:
        # pgvector 확장 활성화
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # 테이블 생성
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {COLL_TABLE} (
          id BIGSERIAL PRIMARY KEY,
          content_id BIGINT NOT NULL REFERENCES {SRC_TABLE}(id) ON DELETE CASCADE,
          embedding vector({EMBEDDING_DIM}) NOT NULL,
          title TEXT,
          detail_url TEXT,
          image_url TEXT,
          products TEXT[],
          created_at TIMESTAMPTZ DEFAULT now(),
          UNIQUE(content_id)
        );
        """)
        # 벡터 검색 인덱스
        cur.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{COLL_TABLE}_embedding
        ON {COLL_TABLE} USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
        """)
    conn.commit()
    logging.info("임베딩 테이블 확인/생성 완료")


def fetch_rows_for_embedding(conn) -> List[Dict[str, Any]]:
    """
    변경분만(신규 또는 수정된 콘텐츠) 가져오기.
    - 임베딩이 아직 없는 행
    - processed_content.modified_at > embeddings.created_at 인 행
    """
    q = f"""
    SELECT c.id, c.title, c.detail_url, c.image_url, c.products, c.hashtags, c.text_content,
           c.modified_at,
           e.created_at AS embedded_at
    FROM {SRC_TABLE} c
    LEFT JOIN {COLL_TABLE} e ON e.content_id = c.id
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
    """임베딩 upsert"""
    assert len(rows) == len(vectors)
    q = sql.SQL(f"""
        INSERT INTO {COLL_TABLE}
            (content_id, embedding, title, detail_url, image_url, products)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_id) DO UPDATE SET
            embedding   = EXCLUDED.embedding,
            title       = EXCLUDED.title,
            detail_url  = EXCLUDED.detail_url,
            image_url   = EXCLUDED.image_url,
            products    = EXCLUDED.products,
            created_at  = now()
    """)
    data = []
    for r, v in zip(rows, vectors):
        data.append((
            r["id"],
            v,
            r.get("title"),
            r.get("detail_url"),
            r.get("image_url"),
            r.get("products"),
        ))
    with conn.cursor() as cur:
        cur.executemany(q, data)


def main():
    with psycopg.connect(PG_CONN, autocommit=False) as conn:
        register_vector(conn)
        create_embeddings_table(conn)

        rows = fetch_rows_for_embedding(conn)
        if not rows:
            logging.info("변경된(또는 신규) 콘텐츠가 없습니다")
            return

        texts = [to_text(r) for r in rows]
        total = len(rows)
        logging.info(f"{total}개의 문서 임베딩/업서트 진행...")

        done = 0
        for idxs in chunked(list(range(total)), BATCH_SIZE):
            batch_texts = [texts[i] for i in idxs]
            vecs = EMB.embed_documents(batch_texts)

            batch_rows = [rows[i] for i in idxs]
            upsert_embeddings(conn, batch_rows, vecs)
            conn.commit()

            done += len(idxs)
            logging.info(f"- {done}/{total} 완료")

        logging.info("모든 임베딩/업서트 완료")


if __name__ == "__main__":
    main()
