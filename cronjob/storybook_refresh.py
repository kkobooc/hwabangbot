import os
import json
import time
import logging
import datetime as dt

import requests
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import argparse

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_URL = "https://sb-openapi.sweetoffer.co.kr"
API_VERSION = os.getenv("API_VERSION", "20250806")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bdlab:bdlab25!!@postgresql.blendedlabs.xyz:5432/hwabang")
INIT_MALL_ID = os.getenv("MALL_ID", "kangkd78910")
INIT_ACCESS_KEY = os.getenv("ACCESS_KEY", "f0968a675feb1cd8823d1beae13a4eaf")
INIT_SECRET_KEY = os.getenv("SECRET_KEY", "b11979084a2051dc1fc22a0f9a39c33e2214ad50d763c1c58dd3092ca57b66fc0ac01bb675a5396402e8fdaa8eafc4ba10798ebea3dc16280b16603ec1aec0f8")
INIT_SECRET_KEY_EXPIRES_AT = os.getenv("SECRET_KEY_EXPIRES_AT", "2025-08-19T11:23:58+09:00")
INIT_REFRESH_KEY = os.getenv("REFRESH_KEY", "94f4441590d538f9d7749f5ca137b72db864cd4c38a3c46f3403e47762cecf4daf76e9fd4506ab8b474bd934b43e4df498603fc8ae50ab4a157c05b7b3af8b81")
INIT_REFRESH_KEY_EXPIRES_AT = os.getenv("REFRESH_KEY_EXPIRES_AT", "2025-08-28T11:23:58+09:00")

SLEEP_REFRESH = 1.05          # 초당 1회
RENEW_THRESHOLD_SEC = 3600    # 남은 1시간 이하면 갱신

def conn():
    return psycopg2.connect(DATABASE_URL)

def q1(sql, params=None):
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()

def qe(sql, params=None):
    with conn() as c, c.cursor() as cur:
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
    VALUES (%s, %s, %s, %s, %s, %s, now())
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

def refresh_once(access_key: str, mall_id: str, refresh_key: str) -> bool:
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

def main(mall_id: str, force: bool):
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL 필요")
    init_current_keys_if_needed(mall_id)
    keys = get_current_keys(mall_id)
    if not keys:
        raise SystemExit(f"current_keys에 mall_id={mall_id} 없음")

    left = seconds_left(keys["secret_key_expired_at"])
    logging.info(f"secret_key 남은 시간: {int(left)}초")
    if force or left <= RENEW_THRESHOLD_SEC or left < 0:
        logging.info("리프레시 시도")
        ok = refresh_once(keys["access_key"], mall_id, keys["refresh_key"])
        if ok:
            logging.info("리프레시 성공")
        else:
            logging.warning("리프레시 실패/스킵")
    else:
        logging.info("아직 유효시간 충분 — 리프레시 스킵")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mall-id", default=os.getenv("MALL_ID"), required=False)
    parser.add_argument("--force", action="store_true", help="만료 임박 여부와 무관하게 리프레시")
    args = parser.parse_args()
    if not args.mall_id:
        raise SystemExit("--mall-id 또는 MALL_ID 필요")
    main(args.mall_id, args.force)
