#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
content_processor.py
- Storybook 콘텐츠 전처리 및 분석
- Likes/Views 상위 콘텐츠 추출 및 JSON 파싱
"""
import json
import logging
import re
from typing import List, Dict, Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = "postgresql://bdlab:bdlab25!!@postgresql.blendedlabs.xyz:5432/hwabang"

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

def create_processed_content_table():
    """전처리된 콘텐츠를 저장할 테이블 생성"""
    sql = """
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
        image_url TEXT,  -- 단일 이미지 URL
        products TEXT[],  -- 상품 번호 배열
        detail_url TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        UNIQUE(content_pk, mall_id, board_no)
    )
    """
    qe(sql)
    logging.info("processed_content 테이블 생성 완료")

def clean_html_tags(text):
    """HTML 태그 제거 및 텍스트 정리"""
    if not text:
        return ""
    
    # HTML 태그 제거
    text = re.sub(r'<[^>]+>', '', text)
    
    # HTML 엔티티 디코딩
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    
    # 연속된 공백 제거
    text = re.sub(r'\s+', ' ', text)
    
    # 앞뒤 공백 제거
    text = text.strip()
    
    return text

def parse_content_document(raw_json):
    """JSON에서 hashtags, text, image_url, products 추출"""
    try:
        data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        content_doc = data.get('content_document', {})
        
        # 1. 해시태그는 최상위 필드에서만 추출
        hashtags = data.get('hashtags', [])
        
        text_content = []
        best_image_url = None
        
        # 2. 이미지 URL 선택 로직
        # cover_image가 있으면 사용, 없으면 aspect_ratio가 1에 가장 가까운 이미지 사용
        cover_image = data.get('cover_image')
        if cover_image:
            best_image_url = cover_image
        else:
            # lines에서 이미지들 중 aspect_ratio가 1에 가장 가까운 것 찾기
            lines = content_doc.get('lines', [])
            best_aspect_ratio_diff = float('inf')
            
            for line in lines:
                if line.get('type') == 'image':
                    image_url = line.get('image_url')
                    aspect_ratio = line.get('aspect_ratio')
                    if image_url and aspect_ratio:
                        aspect_ratio_diff = abs(aspect_ratio - 1.0)
                        if aspect_ratio_diff < best_aspect_ratio_diff:
                            best_aspect_ratio_diff = aspect_ratio_diff
                            best_image_url = image_url
        
        # lines에서 텍스트 추출
        lines = content_doc.get('lines', [])
        for line in lines:
            line_type = line.get('type', '')
            
            if line_type == 'paragraph':
                context = line.get('context', '').strip()
                if context and context != '':
                    # HTML 태그 제거
                    cleaned_context = clean_html_tags(context)
                    if cleaned_context:
                        text_content.append(cleaned_context)
        
        # products 배열 추출 (최상위 레벨)
        products = data.get('products', [])
        
        # 텍스트 결합 및 최종 정리
        final_text = ' '.join(text_content)
        final_text = clean_html_tags(final_text)  # 한 번 더 정리
        
        return {
            'hashtags': list(set(hashtags)),  # 중복 제거
            'text_content': final_text,
            'image_url': best_image_url,  # 단일 이미지 URL
            'products': products,  # 상품 번호 배열
            'detail_url': data.get('detail_url', '')
        }
    except Exception as e:
        logging.error(f"JSON 파싱 오류: {e}")
        return {
            'hashtags': [],
            'text_content': '',
            'image_url': None,
            'products': [],
            'detail_url': ''
        }

def get_top_content_intersection(mall_id=None, board_no=None, percentile=3):
    """Likes와 Views 상위 N% 교집합 조회 (percentile=3이면 상위 33%)"""
    sql = """
    SELECT 
        content_pk,
        mall_id,
        board_no,
        title,
        likes,
        views,
        published,
        modified_at,
        raw_json
    FROM content_details cd
    WHERE 
        EXISTS (
            SELECT 1 FROM (
                SELECT content_pk
                FROM (
                    SELECT 
                        content_pk,
                        NTILE(%s) OVER (ORDER BY likes DESC NULLS LAST) as percentile_group
                    FROM content_details 
                    WHERE likes IS NOT NULL
                    """ + (f"AND mall_id = '{mall_id}'" if mall_id else "") + """
                    """ + (f"AND board_no = {board_no}" if board_no else "") + """
                ) ranked_likes
                WHERE percentile_group = 1
            ) likes_top WHERE likes_top.content_pk = cd.content_pk
        )
        AND
        EXISTS (
            SELECT 1 FROM (
                SELECT content_pk
                FROM (
                    SELECT 
                        content_pk,
                        NTILE(%s) OVER (ORDER BY views DESC NULLS LAST) as percentile_group
                    FROM content_details 
                    WHERE views IS NOT NULL
                    """ + (f"AND mall_id = '{mall_id}'" if mall_id else "") + """
                    """ + (f"AND board_no = {board_no}" if board_no else "") + """
                ) ranked_views
                WHERE percentile_group = 1
            ) views_top WHERE views_top.content_pk = cd.content_pk
        )
    ORDER BY likes DESC, views DESC
    """
    return qa(sql, (percentile, percentile))

def upsert_processed_content(content_data):
    """전처리된 콘텐츠 데이터를 processed_content 테이블에 저장"""
    sql = """
    INSERT INTO processed_content (
        content_pk, mall_id, board_no, title, likes, views, 
        published, modified_at, hashtags, text_content, text_length,
        image_url, products, detail_url
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (content_pk, mall_id, board_no) DO UPDATE SET
        title = EXCLUDED.title,
        likes = EXCLUDED.likes,
        views = EXCLUDED.views,
        published = EXCLUDED.published,
        modified_at = EXCLUDED.modified_at,
        hashtags = EXCLUDED.hashtags,
        text_content = EXCLUDED.text_content,
        text_length = EXCLUDED.text_length,
        image_url = EXCLUDED.image_url,
        products = EXCLUDED.products,
        detail_url = EXCLUDED.detail_url,
        created_at = NOW()
    """
    qe(sql, content_data)
    logging.info(f"Processed content saved: content_pk={content_data[0]}")

def process_top_content(mall_id=None, board_no=None, percentile=3, min_text_length=500):
    """상위 콘텐츠를 전처리하여 새로운 테이블에 저장"""
    # 테이블 생성
    create_processed_content_table()
    
    # 상위 콘텐츠 조회
    top_contents = get_top_content_intersection(mall_id, board_no, percentile)
    logging.info(f"Found {len(top_contents)} top content items (top {100//percentile}%)")
    
    processed_count = 0
    skipped_count = 0
    
    for content in top_contents:
        try:
            # JSON 파싱
            parsed = parse_content_document(content['raw_json'])
            
            # 텍스트 길이 체크
            text_length = len(parsed['text_content'])
            if text_length < min_text_length:
                logging.info(f"Skipping content_pk {content['content_pk']}: text_length={text_length} < {min_text_length}")
                skipped_count += 1
                continue
            
            # 데이터 준비
            content_data = (
                content['content_pk'],
                content['mall_id'],
                content['board_no'],
                content['title'],
                content['likes'],
                content['views'],
                content['published'],
                content['modified_at'],
                parsed['hashtags'],
                parsed['text_content'],
                text_length,  # text_length
                parsed['image_url'],  # 단일 이미지 URL
                parsed['products'],  # 상품 번호 배열
                parsed['detail_url']
            )
            
            # DB에 저장
            upsert_processed_content(content_data)
            processed_count += 1
            
        except Exception as e:
            logging.error(f"Error processing content_pk {content['content_pk']}: {e}")
    
    logging.info(f"Successfully processed {processed_count} content items, skipped {skipped_count} items (text_length < {min_text_length})")
    return processed_count

def get_processed_content_stats():
    """전처리된 콘텐츠 통계 조회"""
    sql = """
    SELECT 
        COUNT(*) as total_count,
        AVG(likes) as avg_likes,
        AVG(views) as avg_views,
        MAX(likes) as max_likes,
        MAX(views) as max_views,
        COUNT(*) FILTER (WHERE array_length(hashtags, 1) > 0) as content_with_hashtags,
        COUNT(*) FILTER (WHERE image_url IS NOT NULL) as content_with_images
    FROM processed_content
    """
    return q1(sql)

def get_popular_hashtags(limit=20):
    """인기 해시태그 조회"""
    sql = """
    SELECT 
        unnest(hashtags) as hashtag,
        COUNT(*) as count
    FROM processed_content
    WHERE array_length(hashtags, 1) > 0
    GROUP BY hashtag
    ORDER BY count DESC
    LIMIT %s
    """
    return qa(sql, (limit,))

if __name__ == "__main__":
    import argparse
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    parser = argparse.ArgumentParser(description="Storybook 콘텐츠 전처리")
    parser.add_argument("--mall-id", help="Mall ID")
    parser.add_argument("--board-no", type=int, help="Board number")
    parser.add_argument("--percentile", type=int, default=3, help="상위 N% (기본값: 3 = 상위 33%)")
    parser.add_argument("--min-text-length", type=int, default=500, help="최소 텍스트 길이 (기본값: 500)")
    parser.add_argument("--stats", action="store_true", help="통계 조회")
    parser.add_argument("--hashtags", action="store_true", help="인기 해시태그 조회")
    
    args = parser.parse_args()
    
    if args.stats:
        stats = get_processed_content_stats()
        print("=== 전처리된 콘텐츠 통계 ===")
        print(f"총 콘텐츠 수: {stats['total_count']}")
        print(f"평균 좋아요: {stats['avg_likes']:.1f}")
        print(f"평균 조회수: {stats['avg_views']:.1f}")
        print(f"최대 좋아요: {stats['max_likes']}")
        print(f"최대 조회수: {stats['max_views']}")
        print(f"해시태그 있는 콘텐츠: {stats['content_with_hashtags']}")
        print(f"이미지 있는 콘텐츠: {stats['content_with_images']}")
    
    elif args.hashtags:
        hashtags = get_popular_hashtags()
        print("=== 인기 해시태그 ===")
        for tag in hashtags:
            print(f"{tag['hashtag']}: {tag['count']}회")
    
    else:
        # 전처리 실행
        processed_count = process_top_content(args.mall_id, args.board_no, args.percentile, args.min_text_length)
        print(f"전처리 완료: {processed_count}개 콘텐츠 처리됨")
