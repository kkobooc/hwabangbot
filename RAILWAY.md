# Railway 배포 가이드

## 1. Railway 프로젝트 생성

```bash
# Railway CLI 설치
npm install -g @railway/cli

# 로그인
railway login
```

## 2. PostgreSQL 설정 (커스텀 Docker + pgvector)

`database/` 디렉토리에 pgvector 포함 PostgreSQL Docker 이미지가 정의되어 있다.

```bash
cd database
railway link        # 프로젝트 선택 → 새 서비스 생성 (이름: database)
railway up          # Docker 빌드 & 배포
```

**환경변수 설정**:
```bash
railway variable set POSTGRES_PASSWORD=<strong-password>
```

**볼륨 연결** (데이터 영속성):
```bash
railway volume add --mount-path /var/lib/postgresql/data
```

**내부 DATABASE_URL 설정** (다른 서비스에서 참조용):
```bash
railway variable set DATABASE_URL="postgresql://hwabang:<password>@database.railway.internal:5432/hwabangbot"
```

> 초기 배포 시 `database/init/01_schema.sql`이 자동 실행되어 pgvector extension + 전체 테이블이 생성된다.

### 데이터 마이그레이션

기존 DB에서 새 DB로 데이터를 마이그레이션할 때:

```bash
cd database
pip install -r requirements.txt

# 마이그레이션 실행 (TCP 프록시 활성화 필요)
export OLD_DATABASE_URL="postgresql://user:pass@old-host:5432/db"
export NEW_DATABASE_URL="postgresql://hwabang:<pw>@<proxy-host>:<port>/hwabangbot"
python migrate.py
```

## 3. Backend 배포

```bash
cd backend
railway link  # 프로젝트 선택 → backend 서비스
railway up
```

**환경변수 설정** (CLI):
```bash
railway variable set 'DATABASE_URL=${{database.DATABASE_URL}}'
railway variable set 'PG_CONN=${{database.DATABASE_URL}}'
railway variable set OPENAI_API_KEY=sk-...
railway variable set LLM_MODEL=gpt-4o-mini
railway variable set EMBEDDING_MODEL=text-embedding-3-small
railway variable set EMBEDDING_DIM=1536
```

## 4. Frontend 배포

```bash
cd frontend
railway link
railway up
```

**환경변수 설정**:
```bash
railway variable set BACKEND_URL=https://backend-production-xxxx.up.railway.app
```

> Railway 내부 통신 시: `http://backend.railway.internal:8000`

## 5. Cronjob 배포

```bash
cd cronjob
railway link
railway up
```

**환경변수 설정**:
```bash
railway variable set 'DATABASE_URL=${{database.DATABASE_URL}}'
railway variable set OPENAI_API_KEY=sk-...
railway variable set MALL_ID=kangkd78910
railway variable set ACCESS_KEY=...
railway variable set SECRET_KEY=...
railway variable set REFRESH_KEY=...
```

**Cron 스케줄 설정** (Railway 대시보드 → cronjob 서비스 → Settings → Cron):
```
# 매일 오전 3시 (KST 기준 UTC+9 → UTC 18시)
0 18 * * *
```

## 6. 서비스 간 연결

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│  Frontend   │────▶│   Backend   │────▶│  database         │
└─────────────┘     └─────────────┘     │  (Docker pgvector)│
                           ▲            └──────────────────┘
                    ┌──────┴──────┐            ▲
                    │   Cronjob   │────────────┘
                    └─────────────┘
```

## 7. 도메인 설정

Railway 대시보드 → Frontend 서비스 → Settings → Domains:
- Railway 제공 도메인: `*.up.railway.app`
- 커스텀 도메인: CNAME 설정 필요

## 환경변수 요약

| 서비스 | 변수 | 설명 |
|--------|------|------|
| database | `POSTGRES_PASSWORD` | PostgreSQL 비밀번호 |
| database | `DATABASE_URL` | 내부 연결 URL (다른 서비스 참조용) |
| Backend | `DATABASE_URL` | `${{database.DATABASE_URL}}` |
| Backend | `PG_CONN` | `${{database.DATABASE_URL}}` |
| Backend | `OPENAI_API_KEY` | OpenAI API 키 |
| Frontend | `BACKEND_URL` | Backend 서비스 URL |
| Cronjob | `DATABASE_URL` | `${{database.DATABASE_URL}}` |
| Cronjob | `OPENAI_API_KEY` | 임베딩용 |
| Cronjob | `MALL_ID` | SweetOffer Mall ID |

## 트러블슈팅

### PostgreSQL 연결 오류
- `DATABASE_URL` 형식 확인: `postgresql://user:pass@host:5432/db`
- Railway 내부 통신 시 `database.railway.internal` 호스트 사용

### Backend 포트
- Railway는 `PORT` 환경변수 자동 주입
- Dockerfile에서 `$PORT` 사용 또는 8000 고정

### Cronjob 실행 안됨
- Cron 표현식 확인 (UTC 기준)
- 로그 확인: Railway 대시보드 → Deployments → View Logs

### pgvector extension 오류
- `database/init/01_schema.sql`에서 자동 생성됨
- 수동 확인: `psql $DATABASE_URL -c "SELECT extname FROM pg_extension;"`
