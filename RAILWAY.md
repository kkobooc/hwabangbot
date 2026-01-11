# Railway 배포 가이드

## 1. Railway 프로젝트 생성

```bash
# Railway CLI 설치
npm install -g @railway/cli

# 로그인
railway login
```

## 2. PostgreSQL 설정

1. Railway 대시보드에서 **Add New Service** → **Database** → **PostgreSQL**
2. PostgreSQL 생성 후 **Variables** 탭에서 `DATABASE_URL` 복사
3. pgvector 활성화 (Railway PostgreSQL은 기본 지원)

```sql
-- Railway PostgreSQL 콘솔에서 실행
CREATE EXTENSION IF NOT EXISTS vector;
```

## 3. Backend 배포

```bash
cd backend
railway init
railway link  # 프로젝트 선택
railway up
```

**환경변수 설정** (Railway 대시보드 → backend 서비스 → Variables):
```
DATABASE_URL=${{Postgres.DATABASE_URL}}  # Railway 참조 변수
PG_CONN=${{Postgres.DATABASE_URL}}
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
```

## 4. Frontend 배포

```bash
cd frontend
railway init
railway link
railway up
```

**환경변수 설정**:
```
BACKEND_URL=https://backend-production-xxxx.up.railway.app
```

> Railway 내부 통신 시: `http://backend.railway.internal:8000`

## 5. Cronjob 배포

```bash
cd cronjob
railway init
railway link
railway up
```

**환경변수 설정**:
```
DATABASE_URL=${{Postgres.DATABASE_URL}}
OPENAI_API_KEY=sk-...
MALL_ID=kangkd78910
ACCESS_KEY=...
SECRET_KEY=...
REFRESH_KEY=...
```

**Cron 스케줄 설정** (Railway 대시보드 → cronjob 서비스 → Settings → Cron):
```
# 매일 오전 3시 (KST 기준 UTC+9 → UTC 18시)
0 18 * * *
```

**Cron 실행 명령어** (Dockerfile CMD 또는 Settings에서 설정):
```bash
python storybook_ingest.py --mall-id $MALL_ID && python embedding_sync.py
```

## 6. 서비스 간 연결

Railway 대시보드에서 서비스 간 연결:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Frontend   │────▶│   Backend   │────▶│  PostgreSQL │
└─────────────┘     └─────────────┘     └─────────────┘
                           ▲
                    ┌──────┴──────┐
                    │   Cronjob   │
                    └─────────────┘
```

## 7. 도메인 설정

Railway 대시보드 → Frontend 서비스 → Settings → Domains:
- Railway 제공 도메인: `*.up.railway.app`
- 커스텀 도메인: CNAME 설정 필요

## 환경변수 요약

| 서비스 | 변수 | 설명 |
|--------|------|------|
| Backend | `DATABASE_URL` | PostgreSQL 연결 |
| Backend | `PG_CONN` | PostgreSQL 연결 (LangChain용) |
| Backend | `OPENAI_API_KEY` | OpenAI API 키 |
| Frontend | `BACKEND_URL` | Backend 서비스 URL |
| Cronjob | `DATABASE_URL` | PostgreSQL 연결 |
| Cronjob | `OPENAI_API_KEY` | 임베딩용 |
| Cronjob | `MALL_ID` | SweetOffer Mall ID |

## 트러블슈팅

### PostgreSQL 연결 오류
- `DATABASE_URL` 형식 확인: `postgresql://user:pass@host:5432/db`
- Railway 내부 통신 시 `railway.internal` 호스트 사용

### Backend 포트
- Railway는 `PORT` 환경변수 자동 주입
- Dockerfile에서 `$PORT` 사용 또는 8000 고정

### Cronjob 실행 안됨
- Cron 표현식 확인 (UTC 기준)
- 로그 확인: Railway 대시보드 → Deployments → View Logs
