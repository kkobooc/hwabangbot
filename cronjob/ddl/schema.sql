-- =====================================================
-- hwabangbot DDL
-- Storybook 콘텐츠 수집 및 전처리 테이블
-- =====================================================

-- -----------------------------------------------------
-- 1. current_keys: API 인증 키 관리
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS current_keys (
    mall_id VARCHAR(50) PRIMARY KEY,
    access_key VARCHAR(255) NOT NULL,
    secret_key VARCHAR(255) NOT NULL,
    secret_key_expired_at TIMESTAMP WITH TIME ZONE,
    refresh_key VARCHAR(255),
    refresh_key_expired_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMENT ON TABLE current_keys IS 'SweetOffer API 인증 키 저장';

-- -----------------------------------------------------
-- 2. key_refresh_log: 키 갱신 로그
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS key_refresh_log (
    id SERIAL PRIMARY KEY,
    mall_id VARCHAR(50) NOT NULL,
    access_key VARCHAR(255),
    secret_key VARCHAR(255),
    secret_key_expired_at TIMESTAMP WITH TIME ZONE,
    refresh_key VARCHAR(255),
    refresh_key_expired_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) NOT NULL,
    detail TEXT,
    raw_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_key_refresh_log_mall_id ON key_refresh_log(mall_id);
CREATE INDEX IF NOT EXISTS idx_key_refresh_log_created_at ON key_refresh_log(created_at DESC);

COMMENT ON TABLE key_refresh_log IS 'API 키 갱신 이력';

-- -----------------------------------------------------
-- 3. boards: 게시판 목록
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS boards (
    id SERIAL PRIMARY KEY,
    mall_id VARCHAR(50) NOT NULL,
    board_no INTEGER NOT NULL,
    board_name VARCHAR(255),
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    raw_json JSONB,
    UNIQUE(mall_id, board_no)
);

CREATE INDEX IF NOT EXISTS idx_boards_mall_id ON boards(mall_id);

COMMENT ON TABLE boards IS 'Storybook 게시판 목록';

-- -----------------------------------------------------
-- 4. board_contents: 게시판 콘텐츠 목록
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS board_contents (
    id SERIAL PRIMARY KEY,
    mall_id VARCHAR(50) NOT NULL,
    board_no INTEGER NOT NULL,
    content_pk INTEGER NOT NULL,
    provider VARCHAR(50),
    shop_no INTEGER,
    content_no INTEGER,
    title TEXT,
    published BOOLEAN,
    modified_at TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    raw_json JSONB,
    UNIQUE(mall_id, board_no, content_pk)
);

CREATE INDEX IF NOT EXISTS idx_board_contents_mall_id ON board_contents(mall_id);
CREATE INDEX IF NOT EXISTS idx_board_contents_board_no ON board_contents(board_no);
CREATE INDEX IF NOT EXISTS idx_board_contents_content_pk ON board_contents(content_pk);

COMMENT ON TABLE board_contents IS '게시판별 콘텐츠 목록';

-- -----------------------------------------------------
-- 5. content_details: 콘텐츠 상세 정보
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS content_details (
    id SERIAL PRIMARY KEY,
    mall_id VARCHAR(50) NOT NULL,
    board_no INTEGER NOT NULL,
    content_pk INTEGER NOT NULL,
    content_no INTEGER,
    title TEXT,
    detail_url TEXT,
    member_id VARCHAR(100),
    likes INTEGER,
    views INTEGER,
    scraps INTEGER,
    comments INTEGER,
    shares INTEGER,
    published BOOLEAN,
    modified_at TIMESTAMP WITH TIME ZONE,
    issued_at TIMESTAMP WITH TIME ZONE,
    published_at TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    raw_json JSONB,
    UNIQUE(mall_id, board_no, content_pk)
);

CREATE INDEX IF NOT EXISTS idx_content_details_mall_id ON content_details(mall_id);
CREATE INDEX IF NOT EXISTS idx_content_details_board_no ON content_details(board_no);
CREATE INDEX IF NOT EXISTS idx_content_details_content_pk ON content_details(content_pk);
CREATE INDEX IF NOT EXISTS idx_content_details_likes ON content_details(likes DESC);
CREATE INDEX IF NOT EXISTS idx_content_details_views ON content_details(views DESC);

COMMENT ON TABLE content_details IS '콘텐츠 상세 정보 (좋아요, 조회수 등)';

-- -----------------------------------------------------
-- 6. crawl_checkpoint: 크롤링 체크포인트
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS crawl_checkpoint (
    id SERIAL PRIMARY KEY,
    mall_id VARCHAR(50) NOT NULL,
    board_no INTEGER NOT NULL,
    last_content_pk INTEGER,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(mall_id, board_no)
);

COMMENT ON TABLE crawl_checkpoint IS '증분 크롤링용 체크포인트';

-- -----------------------------------------------------
-- 7. processed_content: 전처리된 콘텐츠
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS processed_content (
    id SERIAL PRIMARY KEY,
    content_pk INTEGER NOT NULL,
    mall_id VARCHAR(50) NOT NULL,
    board_no INTEGER NOT NULL,
    title TEXT,
    likes INTEGER,
    views INTEGER,
    published BOOLEAN,
    modified_at TIMESTAMP WITH TIME ZONE,
    hashtags TEXT[],
    text_content TEXT,
    text_length INTEGER,
    image_url TEXT,
    products TEXT[],
    detail_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(content_pk, mall_id, board_no)
);

CREATE INDEX IF NOT EXISTS idx_processed_content_mall_id ON processed_content(mall_id);
CREATE INDEX IF NOT EXISTS idx_processed_content_board_no ON processed_content(board_no);
CREATE INDEX IF NOT EXISTS idx_processed_content_likes ON processed_content(likes DESC);
CREATE INDEX IF NOT EXISTS idx_processed_content_views ON processed_content(views DESC);
CREATE INDEX IF NOT EXISTS idx_processed_content_text_length ON processed_content(text_length);
CREATE INDEX IF NOT EXISTS idx_processed_content_modified_at ON processed_content(modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_processed_content_hashtags ON processed_content USING GIN(hashtags);
CREATE INDEX IF NOT EXISTS idx_processed_content_image_url ON processed_content(image_url);
CREATE INDEX IF NOT EXISTS idx_processed_content_products ON processed_content USING GIN(products);
CREATE INDEX IF NOT EXISTS idx_processed_content_text_content ON processed_content USING GIN(to_tsvector('simple', text_content));

COMMENT ON TABLE processed_content IS 'Storybook 콘텐츠 전처리 결과 테이블';
COMMENT ON COLUMN processed_content.content_pk IS '콘텐츠 고유 ID';
COMMENT ON COLUMN processed_content.mall_id IS '몰 ID';
COMMENT ON COLUMN processed_content.board_no IS '게시판 번호';
COMMENT ON COLUMN processed_content.title IS '콘텐츠 제목';
COMMENT ON COLUMN processed_content.likes IS '좋아요 수';
COMMENT ON COLUMN processed_content.views IS '조회수';
COMMENT ON COLUMN processed_content.published IS '발행 여부';
COMMENT ON COLUMN processed_content.modified_at IS '수정일시';
COMMENT ON COLUMN processed_content.hashtags IS '해시태그 배열';
COMMENT ON COLUMN processed_content.text_content IS '추출된 텍스트 내용';
COMMENT ON COLUMN processed_content.text_length IS '텍스트 길이 (문자 수)';
COMMENT ON COLUMN processed_content.image_url IS '이미지 URL';
COMMENT ON COLUMN processed_content.products IS '상품 번호 배열';
COMMENT ON COLUMN processed_content.detail_url IS '상세 페이지 URL';
COMMENT ON COLUMN processed_content.created_at IS '처리 생성일시';
