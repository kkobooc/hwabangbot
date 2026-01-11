# --- 커스텀 PG retriever: 임베딩 테이블 + 본문 테이블 JOIN ---
from sqlalchemy import create_engine, text
from langchain_core.documents import Document

class PGRawRetriever:
    def __init__(
        self,
        engine,
        embeddings,
        k=5,
        threshold=None,                 # distance(코사인 거리) 상한. 작을수록 유사. 예: 0.35
        max_chars=None,                 # 본문 너무 길면 자르기. 예: 1200
        content_column="content",       # processed_content의 본문 컬럼명 (예: "content" / "body" / "text")
        embedding_table="processed_content_embeddings",
        content_table="processed_content",
    ):
        self.eng = engine
        self.emb = embeddings
        self.k = k
        self.threshold = threshold
        self.max_chars = max_chars
        self.content_column = content_column
        self.embedding_table = embedding_table
        self.content_table = content_table

        # 안전한 SQL(컬럼/테이블명은 f-string으로, 값은 바인딩)
        self.sql = text(f"""
        SELECT
          e.id,
          e.content_id,
          e.embedding <=> (:qvec)::vector AS distance,
          e.title,
          e.detail_url,
          e.image_url,
          e.products,
          pc.{self.content_column} AS body
        FROM {self.embedding_table} AS e
        JOIN {self.content_table}   AS pc
          ON pc.id = e.content_id
        ORDER BY e.embedding <=> (:qvec)::vector ASC
        LIMIT :k
        """)

    def invoke(self, query: str):
        qvec = self.emb.embed_query(query)  # list[float], len=1536
        with self.eng.begin() as c:
            rows = c.execute(self.sql, {"qvec": qvec, "k": self.k}).fetchall()

        docs = []
        for r in rows:
            # threshold 처리 (distance는 작을수록 유사)
            if self.threshold is not None and float(r.distance) > self.threshold:
                continue

            body = r.body or ""
            if self.max_chars is not None and len(body) > self.max_chars:
                body = body[: self.max_chars] + "..."

            md = {
                "id": r.id,
                "content_id": r.content_id,
                "distance": float(r.distance),
                "title": r.title,
                "detail_url": r.detail_url,
                "image_url": r.image_url,
                "products": r.products,
                "source_table": self.embedding_table,
            }
            docs.append(Document(page_content=body, metadata=md))
        return docs
