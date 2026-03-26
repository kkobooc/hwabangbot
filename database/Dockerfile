FROM pgvector/pgvector:0.8.0-pg16

ENV POSTGRES_DB=hwabangbot
ENV POSTGRES_USER=hwabang
ENV PGDATA=/var/lib/postgresql/data/pgdata

COPY init/ /docker-entrypoint-initdb.d/

EXPOSE 5432
