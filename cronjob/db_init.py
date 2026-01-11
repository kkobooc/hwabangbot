#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_init.py
- 데이터베이스 테이블 자동 생성
- DDL 파일을 읽어 테이블이 없으면 생성
"""
import os
import logging
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DDL_PATH = Path(__file__).parent / "ddl" / "schema.sql"

def init_tables():
    """DDL 파일을 읽어 테이블 생성 (IF NOT EXISTS)"""
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL 환경변수가 필요합니다")

    if not DDL_PATH.exists():
        logging.warning(f"DDL 파일이 없습니다: {DDL_PATH}")
        return False

    ddl_sql = DDL_PATH.read_text(encoding="utf-8")

    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(ddl_sql)
            conn.commit()
        logging.info("테이블 초기화 완료")
        return True
    except Exception as e:
        logging.error(f"테이블 초기화 실패: {e}")
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_tables()
