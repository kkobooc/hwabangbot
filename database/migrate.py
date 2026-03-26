#!/usr/bin/env python3
"""
hwabangbot 데이터 마이그레이션 스크립트

기존 PostgreSQL → 새 pgvector PostgreSQL로 전체 테이블 복사.
- 테이블 1~7: psycopg2 COPY 프로토콜 (고속)
- 테이블 8 (embeddings): psycopg3 + pgvector batch INSERT

사용법:
  export OLD_DATABASE_URL="postgresql://user:pass@old-host:5432/db"
  export NEW_DATABASE_URL="postgresql://user:pass@new-host:5432/db"
  python migrate.py

  또는:
  python migrate.py --old-db "postgresql://..." --new-db "postgresql://..."
"""

import argparse
import io
import logging
import os
import sys
import time

import psycopg2
import psycopg
from pgvector.psycopg import register_vector
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# 마이그레이션 대상 테이블 (의존성 순서)
# (table_name, pk_column_for_sequence_reset)
# pk_column=None이면 시퀀스 리셋 불필요 (VARCHAR PK 등)
TABLES = [
    ("current_keys", None),
    ("key_refresh_log", "id"),
    ("boards", "id"),
    ("board_contents", "id"),
    ("content_details", "id"),
    ("crawl_checkpoint", "id"),
    ("processed_content", "id"),
]

EMBEDDING_TABLE = "processed_content_embeddings"
EMBEDDING_BATCH_SIZE = 500


def get_row_count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def reset_sequence(conn, table, pk_col):
    """SERIAL 시퀀스를 현재 max(pk) 값으로 리셋"""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, %s), COALESCE(MAX({pk_col}), 1)) FROM {table}",
            (table, pk_col),
        )
    conn.commit()


def get_columns(conn, table):
    """테이블 컬럼명 목록 조회 (순서 보장)"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = 'public' "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def copy_table(src_conn, dst_conn, table, pk_col):
    """COPY 프로토콜로 테이블 데이터 복사 (명시적 컬럼 지정)"""
    t0 = time.time()

    src_count = get_row_count(src_conn, table)
    if src_count == 0:
        log.info(f"  {table}: 0 rows (skip)")
        return

    # 소스 테이블 컬럼 순서 사용
    columns = get_columns(src_conn, table)
    col_list = ", ".join(columns)

    # Destination: TRUNCATE
    with dst_conn.cursor() as cur:
        cur.execute(f"TRUNCATE {table} CASCADE")
    dst_conn.commit()

    # COPY: source → buffer → destination (컬럼 명시)
    buf = io.BytesIO()
    with src_conn.cursor() as src_cur:
        src_cur.copy_expert(f"COPY {table}({col_list}) TO STDOUT", buf)

    buf.seek(0)
    with dst_conn.cursor() as dst_cur:
        dst_cur.copy_expert(f"COPY {table}({col_list}) FROM STDIN", buf)
    dst_conn.commit()

    # 시퀀스 리셋
    if pk_col:
        reset_sequence(dst_conn, table, pk_col)

    dst_count = get_row_count(dst_conn, table)
    elapsed = time.time() - t0
    status = "OK" if src_count == dst_count else "MISMATCH!"
    log.info(f"  {table}: {dst_count}/{src_count} rows ({elapsed:.1f}s) [{status}]")


def copy_embeddings(old_url, new_url):
    """pgvector embeddings 테이블을 psycopg3로 복사"""
    t0 = time.time()

    # psycopg3 호환 URL로 변환
    clean = lambda u: u.replace("+psycopg2", "").replace("+psycopg", "")

    src = psycopg.connect(clean(old_url), autocommit=False)
    dst = psycopg.connect(clean(new_url), autocommit=False)

    register_vector(src)
    register_vector(dst)

    # Source row count
    with src.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {EMBEDDING_TABLE}")
        src_count = cur.fetchone()[0]

    if src_count == 0:
        log.info(f"  {EMBEDDING_TABLE}: 0 rows (skip)")
        src.close()
        dst.close()
        return

    # Destination: TRUNCATE
    with dst.cursor() as cur:
        cur.execute(f"TRUNCATE {EMBEDDING_TABLE} CASCADE")
    dst.commit()

    # Server-side cursor로 batch fetch + INSERT
    copied = 0
    with src.cursor(name="emb_cursor") as src_cur:
        src_cur.execute(
            f"SELECT id, content_id, embedding, title, detail_url, image_url, products, created_at "
            f"FROM {EMBEDDING_TABLE} ORDER BY id"
        )

        while True:
            rows = src_cur.fetchmany(EMBEDDING_BATCH_SIZE)
            if not rows:
                break

            with dst.cursor() as dst_cur:
                dst_cur.executemany(
                    f"INSERT INTO {EMBEDDING_TABLE} "
                    f"(id, content_id, embedding, title, detail_url, image_url, products, created_at) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    f"ON CONFLICT (content_id) DO UPDATE SET "
                    f"embedding=EXCLUDED.embedding, title=EXCLUDED.title, "
                    f"detail_url=EXCLUDED.detail_url, image_url=EXCLUDED.image_url, "
                    f"products=EXCLUDED.products, created_at=EXCLUDED.created_at",
                    rows,
                )
            dst.commit()
            copied += len(rows)
            log.info(f"    embeddings: {copied}/{src_count} rows...")

    # BIGSERIAL 시퀀스 리셋
    with dst.cursor() as cur:
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 1)) FROM {EMBEDDING_TABLE}",
            (EMBEDDING_TABLE,),
        )
    dst.commit()

    # IVFFlat 인덱스 재구축
    log.info(f"    REINDEX embeddings...")
    with dst.cursor() as cur:
        cur.execute(f"REINDEX INDEX idx_{EMBEDDING_TABLE}_embedding")
    dst.commit()

    # 검증
    with dst.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {EMBEDDING_TABLE}")
        dst_count = cur.fetchone()[0]

    elapsed = time.time() - t0
    status = "OK" if src_count == dst_count else "MISMATCH!"
    log.info(f"  {EMBEDDING_TABLE}: {dst_count}/{src_count} rows ({elapsed:.1f}s) [{status}]")

    src.close()
    dst.close()


def main():
    parser = argparse.ArgumentParser(description="hwabangbot DB migration")
    parser.add_argument("--old-db", default=os.getenv("OLD_DATABASE_URL"), help="Source DB URL")
    parser.add_argument("--new-db", default=os.getenv("NEW_DATABASE_URL"), help="Destination DB URL")
    args = parser.parse_args()

    if not args.old_db or not args.new_db:
        log.error("OLD_DATABASE_URL과 NEW_DATABASE_URL이 필요합니다.")
        log.error("  export OLD_DATABASE_URL='postgresql://...'")
        log.error("  export NEW_DATABASE_URL='postgresql://...'")
        sys.exit(1)

    log.info("=" * 50)
    log.info("hwabangbot 데이터 마이그레이션 시작")
    log.info("=" * 50)

    # psycopg2 연결 (테이블 1~7)
    log.info("Source DB 연결 중...")
    src_conn = psycopg2.connect(args.old_db)
    log.info("Destination DB 연결 중...")
    dst_conn = psycopg2.connect(args.new_db)

    log.info("")
    log.info("[Phase 1] 일반 테이블 복사 (COPY 프로토콜)")
    log.info("-" * 40)

    t_total = time.time()
    for table, pk_col in TABLES:
        copy_table(src_conn, dst_conn, table, pk_col)

    src_conn.close()
    dst_conn.close()

    log.info("")
    log.info("[Phase 2] 임베딩 테이블 복사 (pgvector)")
    log.info("-" * 40)

    copy_embeddings(args.old_db, args.new_db)

    elapsed_total = time.time() - t_total
    log.info("")
    log.info("=" * 50)
    log.info(f"마이그레이션 완료! (총 {elapsed_total:.1f}s)")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
