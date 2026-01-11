-- processed_content 테이블 DDL
-- Storybook 콘텐츠 전처리 결과를 저장하는 테이블

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
    hashtags TEXT[],  -- 배열로 저장
    text_content TEXT,  -- 텍스트 내용
    text_length INTEGER,  -- 텍스트 길이
    image_url TEXT,  -- 이미지 URL
    products TEXT[],  -- 상품 번호 배열
    detail_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(content_pk, mall_id, board_no)
);

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_processed_content_mall_id ON processed_content(mall_id);
CREATE INDEX IF NOT EXISTS idx_processed_content_board_no ON processed_content(board_no);
CREATE INDEX IF NOT EXISTS idx_processed_content_likes ON processed_content(likes DESC);
CREATE INDEX IF NOT EXISTS idx_processed_content_views ON processed_content(views DESC);
CREATE INDEX IF NOT EXISTS idx_processed_content_text_length ON processed_content(text_length);
CREATE INDEX IF NOT EXISTS idx_processed_content_modified_at ON processed_content(modified_at DESC);

-- 해시태그 검색을 위한 GIN 인덱스
CREATE INDEX IF NOT EXISTS idx_processed_content_hashtags ON processed_content USING GIN(hashtags);

-- 이미지 URL 검색을 위한 GIN 인덱스
CREATE INDEX IF NOT EXISTS idx_processed_content_image_url ON processed_content(image_url);

-- 상품 번호 검색을 위한 GIN 인덱스
CREATE INDEX IF NOT EXISTS idx_processed_content_products ON processed_content USING GIN(products);

-- 텍스트 검색을 위한 인덱스
CREATE INDEX IF NOT EXISTS idx_processed_content_text_content ON processed_content USING GIN(to_tsvector('korean', text_content));

-- 테이블 코멘트
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
