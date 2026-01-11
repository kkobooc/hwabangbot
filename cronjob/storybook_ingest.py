#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sweetoffer_ingest.py
- SweetOffer 데이터 수집(보드/목록/상세) + 증분 체크포인트
"""
import os
import time
import json
import logging
import datetime as dt
from typing import List, Dict, Any
import warnings

import requests
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import argparse

# SSL 경고 메시지 제거
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 테이블 자동 생성
from db_init import init_tables
init_tables()

BASE_URL = "https://sb-openapi.sweetoffer.co.kr"
API_VERSION = os.getenv("API_VERSION", "20250806")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL 환경변수가 필요합니다")
INIT_MALL_ID = os.getenv("MALL_ID")
INIT_ACCESS_KEY = os.getenv("ACCESS_KEY")
INIT_SECRET_KEY = os.getenv("SECRET_KEY")
INIT_SECRET_KEY_EXPIRES_AT = os.getenv("SECRET_KEY_EXPIRES_AT")
INIT_REFRESH_KEY = os.getenv("REFRESH_KEY")
INIT_REFRESH_KEY_EXPIRES_AT = os.getenv("REFRESH_KEY_EXPIRES_AT")

# rate limits
SLEEP_REFRESH = 1.2     # refresh 초당 1회
SLEEP_CONTENT = 0.3     # contents 초당 5회 여유

RENEW_THRESHOLD_SEC = 3600

def conn():
    return psycopg2.connect(DATABASE_URL)

def q1(sql, params=None):
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()

def qa(sql, params=None):
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()

def qe(sql, params=None, many=False):
    with conn() as c, c.cursor() as cur:
        if many:
            psycopg2.extras.execute_batch(cur, sql, params, page_size=500)
        else:
            cur.execute(sql, params)

def init_current_keys_if_needed(mall_id: str):
    row = q1("SELECT 1 FROM current_keys WHERE mall_id=%s", (mall_id,))
    if row:
        return
    if not (INIT_ACCESS_KEY and INIT_SECRET_KEY and INIT_REFRESH_KEY):
        logging.warning("환경변수에 초기 키가 없어 current_keys 초기화 생략")
        return
    qe("""
    INSERT INTO current_keys (mall_id, access_key, secret_key, secret_key_expired_at, refresh_key, refresh_key_expired_at, updated_at)
    VALUES (%s,%s,%s,%s,%s,%s, now())
    ON CONFLICT (mall_id) DO NOTHING
    """, (mall_id, INIT_ACCESS_KEY, INIT_SECRET_KEY, INIT_SECRET_KEY_EXPIRES_AT, INIT_REFRESH_KEY, INIT_REFRESH_KEY_EXPIRES_AT))
    logging.info("current_keys 초기화 완료")

def get_current_keys(mall_id: str):
    return q1("SELECT * FROM current_keys WHERE mall_id=%s", (mall_id,))

def _to_datetime(ts):
    """
    ts 가 문자열(ISO8601) 또는 datetime 둘 다 안전하게 datetime(타임존 포함)으로 변환
    """
    if ts is None:
        return None
    if isinstance(ts, dt.datetime):
        # tz 미포함이면 UTC로 가정(필요 시 Asia/Seoul로 바꿔도 됨)
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    if isinstance(ts, str):
        # "Z" 처리 + fromisoformat 호환
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    raise TypeError(f"Unsupported ts type: {type(ts)}")
    
def seconds_left(ts) -> float:
    """
    만료시각(ts)까지 남은 초. ts가 str이든 datetime이든 받아서 처리.
    """
    t = _to_datetime(ts)
    if t is None:
        return -1
    now = dt.datetime.now(t.tzinfo) if t.tzinfo else dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    return (t - now).total_seconds()

def log_refresh(mall_id: str, raw: dict, status: str, detail: str=None):
    d = raw.get("result", {}).get("data", {}) if raw else {}
    qe("""
    INSERT INTO key_refresh_log (mall_id, access_key, secret_key, secret_key_expired_at, refresh_key, refresh_key_expired_at, status, detail, raw_json, created_at)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
    """, (
        mall_id,
        d.get("access_key"),
        d.get("secret_key"),
        d.get("secret_key_expired_at"),
        d.get("refresh_key"),
        d.get("refresh_key_expired_at"),
        status,
        detail,
        json.dumps(raw) if raw else None
    ))

def upsert_current_keys_from_refresh_payload(mall_id: str, raw: dict):
    d = raw.get("result", {}).get("data", {})
    qe("""
    INSERT INTO current_keys (mall_id, access_key, secret_key, secret_key_expired_at, refresh_key, refresh_key_expired_at, updated_at)
    VALUES (%s,%s,%s,%s,%s,%s, now())
    ON CONFLICT (mall_id) DO UPDATE SET
      access_key=EXCLUDED.access_key,
      secret_key=EXCLUDED.secret_key,
      secret_key_expired_at=EXCLUDED.secret_key_expired_at,
      refresh_key=EXCLUDED.refresh_key,
      refresh_key_expired_at=EXCLUDED.refresh_key_expired_at,
      updated_at=now()
    """, (
        mall_id,
        d.get("access_key"),
        d.get("secret_key"),
        d.get("secret_key_expired_at"),
        d.get("refresh_key"),
        d.get("refresh_key_expired_at"),
    ))

def refresh_secret_key(access_key: str, mall_id: str, refresh_key: str) -> bool:
    url = f"{BASE_URL}/refresh_keys/{access_key}/{mall_id}/new_secret_key/{refresh_key}"
    try:
        r = requests.get(url, verify=False, timeout=30)
        raw = r.json() if r.content else {}
        status = "success" if r.ok else f"failed_{r.status_code}"
        if r.status_code == 422:
            status = "skipped_422"
        log_refresh(mall_id, raw, status, None if r.ok else r.text)
        if r.ok:
            upsert_current_keys_from_refresh_payload(mall_id, raw)
        time.sleep(SLEEP_REFRESH)
        return r.ok
    except Exception as e:
        log_refresh(mall_id, {}, "failed_exception", str(e))
        time.sleep(SLEEP_REFRESH)
        return False

def ensure_valid_secret(mall_id: str):
    keys = get_current_keys(mall_id)
    if not keys:
        raise RuntimeError(f"current_keys에 mall_id={mall_id} 없음")
    left = seconds_left(keys["secret_key_expired_at"])
    if left < 0 or left <= RENEW_THRESHOLD_SEC:
        logging.info("secret_key 갱신 시도")
        ok = refresh_secret_key(keys["access_key"], mall_id, keys["refresh_key"])
        if not ok:
            logging.warning("리프레시 실패/스킵 — 기존 키로 진행")
    return get_current_keys(mall_id)

def headers(mall_id: str, access_key: str, secret_key: str):
    return {
        "api-mall-id": mall_id,
        "api-access-key": access_key,
        "api-secret-key": secret_key,
        "api-version": API_VERSION
    }

def get_boards(mall_id: str, access_key: str, secret_key: str) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/v1/boards"
    r = requests.get(url, headers=headers(mall_id, access_key, secret_key), verify=False, timeout=30)
    r.raise_for_status()
    print(r.text)
    data = r.json()["result"]["data"]["boards"]
    time.sleep(SLEEP_CONTENT)
    return data

def upsert_boards(mall_id: str, boards: List[Dict[str, Any]]):
    sql = """
    INSERT INTO boards (mall_id, board_no, board_name, fetched_at, raw_json)
    VALUES (%s,%s,%s, now(), %s)
    ON CONFLICT (mall_id, board_no) DO UPDATE SET
      board_name=EXCLUDED.board_name,
      fetched_at=now(),
      raw_json=EXCLUDED.raw_json
    """
    rows = [(mall_id, b["board_no"], b.get("board_name"), json.dumps(b)) for b in boards]
    if rows:
        qe(sql, rows, many=True)
        logging.info(f"Inserted/Updated {len(rows)} boards for mall_id={mall_id}")

def get_contents_page(mall_id: str, access_key: str, secret_key: str, board_no: int, prev_content_pk: int=None) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/v1/boards/{board_no}/contents"
    params = {}
    if prev_content_pk is not None:
        params["prev__content_pk"] = prev_content_pk
    r = requests.get(url, headers=headers(mall_id, access_key, secret_key), params=params, verify=False, timeout=60)
    r.raise_for_status()
    items = r.json()["result"]["data"]["items"]
    time.sleep(SLEEP_CONTENT)
    return items

def upsert_contents_list(mall_id: str, board_no: int, items: List[Dict[str, Any]]):
    sql = """
    INSERT INTO board_contents (
      mall_id, board_no, content_pk, provider, shop_no, content_no, title,
      published, modified_at, fetched_at, raw_json
    )
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
    ON CONFLICT (mall_id, board_no, content_pk) DO UPDATE SET
      provider=EXCLUDED.provider,
      shop_no=EXCLUDED.shop_no,
      content_no=EXCLUDED.content_no,
      title=EXCLUDED.title,
      published=EXCLUDED.published,
      modified_at=EXCLUDED.modified_at,
      fetched_at=now(),
      raw_json=EXCLUDED.raw_json
    """
    def iso(ts): return ts if ts is None else dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    rows = []
    for it in items:
        rows.append((
            mall_id, board_no, it["content_pk"], it.get("provider"), it.get("shop_no"), it.get("content_no"),
            it.get("title"), it.get("published"), iso(it.get("modified_at")), json.dumps(it)
        ))
    if rows:
        qe(sql, rows, many=True)
        logging.info(f"Inserted/Updated {len(rows)} content_list items for mall_id={mall_id}, board_no={board_no}")

def get_checkpoint(mall_id: str, board_no: int):
    row = q1("SELECT last_content_pk FROM crawl_checkpoint WHERE mall_id=%s AND board_no=%s", (mall_id, board_no))
    return row["last_content_pk"] if row else None

def set_checkpoint(mall_id: str, board_no: int, last_pk: int):
    qe("""
    INSERT INTO crawl_checkpoint (mall_id, board_no, last_content_pk, updated_at)
    VALUES (%s,%s,%s, now())
    ON CONFLICT (mall_id, board_no) DO UPDATE SET
      last_content_pk=EXCLUDED.last_content_pk,
      updated_at=now()
    """, (mall_id, board_no, last_pk))
    logging.info(f"Updated checkpoint for mall_id={mall_id}, board_no={board_no}, last_pk={last_pk}")

def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def get_content_details(mall_id: str, access_key: str, secret_key: str, board_no: int, content_numbers: List[int]) -> List[Dict[str, Any]]:
    results = []
    for group in chunked(content_numbers, 10):  # API 제한: 한 번에 10개까지만 처리 가능
        ids = ",".join(map(str, group))
        url = f"{BASE_URL}/v1/boards/{board_no}/contents/{ids}"
        r = requests.get(url, headers=headers(mall_id, access_key, secret_key), verify=False, timeout=60)
        r.raise_for_status()
        results.extend(r.json()["result"]["data"]["details"])
        time.sleep(SLEEP_CONTENT)
    return results

def upsert_content_details(mall_id: str, board_no: int, details: List[Dict[str, Any]]):
    sql = """
    INSERT INTO content_details (
      mall_id, board_no, content_pk, content_no, title, detail_url, member_id,
      likes, views, scraps, comments, shares, published,
      modified_at, issued_at, published_at, fetched_at, raw_json
    ) VALUES (
      %s,%s,%s,%s,%s,%s,%s,
      %s,%s,%s,%s,%s,%s,
      %s,%s,%s, now(), %s
    )
    ON CONFLICT (mall_id, board_no, content_pk) DO UPDATE SET
      content_no=EXCLUDED.content_no,
      title=EXCLUDED.title,
      detail_url=EXCLUDED.detail_url,
      member_id=EXCLUDED.member_id,
      likes=EXCLUDED.likes,
      views=EXCLUDED.views,
      scraps=EXCLUDED.scraps,
      comments=EXCLUDED.comments,
      shares=EXCLUDED.shares,
      published=EXCLUDED.published,
      modified_at=EXCLUDED.modified_at,
      issued_at=EXCLUDED.issued_at,
      published_at=EXCLUDED.published_at,
      fetched_at=now(),
      raw_json=EXCLUDED.raw_json
    """
    def iso(ts): return ts if ts is None else dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    rows = []
    for d in details:
        rows.append((
            mall_id, board_no, d["content_pk"], d.get("content_no"), d.get("title"), d.get("detail_url"),
            d.get("member_id"),
            d.get("likes"), d.get("views"), d.get("scraps"), d.get("comments"), d.get("shares"),
            d.get("published"),
            iso(d.get("modified_at")), iso(d.get("issued_at")), iso(d.get("published_at")),
            json.dumps(d)
        ))
    if rows:
        qe(sql, rows, many=True)
        logging.info(f"Inserted/Updated {len(rows)} content_details for mall_id={mall_id}, board_no={board_no}")

def crawl_board_incremental(mall_id: str, access_key: str, secret_key: str, board_no: int):
    last_pk = get_checkpoint(mall_id, board_no)
    prev = last_pk if last_pk is not None else None
    max_seen = last_pk or 0

    collected = []
    while True:
        items = get_contents_page(mall_id, access_key, secret_key, board_no, prev)
        if not items:
            break
        upsert_contents_list(mall_id, board_no, items)
        collected.extend(items)
        prev = items[-1]["content_pk"]
        max_seen = max(max_seen, prev)

    if not collected:
        logging.info(f"[board {board_no}] 신규 없음")
        return

    # 상세 일괄
    content_nos = [it["content_no"] for it in collected if it.get("content_no") is not None]
    if content_nos:
        details = get_content_details(mall_id, access_key, secret_key, board_no, content_nos)
        upsert_content_details(mall_id, board_no, details)

    set_checkpoint(mall_id, board_no, max_seen)
    logging.info(f"[board {board_no}] 완료 last_content_pk={max_seen}")

def run(mode: str, mall_id: str):
    # 키 준비
    init_current_keys_if_needed(mall_id)
    keys = ensure_valid_secret(mall_id)
    access_key, secret_key = keys["access_key"], keys["secret_key"]

    # 보드 동기화
    boards = get_boards(mall_id, access_key, secret_key)
    upsert_boards(mall_id, boards)
    board_nos = [b["board_no"] for b in boards]

    # full 모드는 체크포인트를 무시하고 0부터 긁고 싶은 경우, 사전에 crawl_checkpoint 행 삭제 권장
    for bno in board_nos:
        crawl_board_incremental(mall_id, access_key, secret_key, bno)
    
    # 상위 콘텐츠 전처리 (별도 파일로 분리됨)
    logging.info("상위 콘텐츠 전처리 시작")
    try:
        from content_processor import process_top_content
        processed_count = process_top_content(mall_id)
        logging.info(f"전처리 완료: {processed_count}개 콘텐츠 처리됨")
    except ImportError:
        logging.warning("content_processor.py를 찾을 수 없어 전처리를 건너뜁니다")
    except Exception as e:
        logging.error(f"전처리 중 오류 발생: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mall-id", default=os.getenv("MALL_ID"), required=False)
    parser.add_argument("--mode", choices=["incremental", "full"], default="incremental")
    args = parser.parse_args()

    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL 필요")
    if not args.mall_id:
        raise SystemExit("--mall-id 또는 MALL_ID 필요")

    run(args.mode, args.mall_id)
