-- ============================================================
-- init.sql
-- 线上知识库系统 - 数据库初始化脚本
-- 首次启动 PostgreSQL 容器时自动执行
-- ============================================================

-- 1. 启用必要扩展
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector 向量类型
CREATE EXTENSION IF NOT EXISTS citext;      -- 大小写不敏感文本
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid()

-- ============================================================
-- 2. users 用户表
-- ============================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           CITEXT UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    nickname        VARCHAR(100),
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON TABLE users IS '用户表：保存登录、权限隔离和用户数据归属';
COMMENT ON COLUMN users.email IS '登录邮箱，CITEXT 类型自动大小写不敏感';
COMMENT ON COLUMN users.password_hash IS 'bcrypt 或 argon2 哈希';
COMMENT ON COLUMN users.status IS 'active / disabled';

-- ============================================================
-- 3. knowledge_bases 知识库表（与用户 1:1）
-- ============================================================
CREATE TABLE knowledge_bases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    document_count  INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (user_id)   -- 用户与知识库 1:1
);

COMMENT ON TABLE knowledge_bases IS '知识库表：用户与知识库 1:1';
COMMENT ON COLUMN knowledge_bases.document_count IS '冗余统计字段，文档上传/删除时更新';

CREATE INDEX idx_knowledge_bases_user_id ON knowledge_bases(user_id);

-- ============================================================
-- 4. documents 文档表
-- ============================================================
CREATE TABLE documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id   UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename            VARCHAR(500) NOT NULL,
    file_type           VARCHAR(20) NOT NULL,
    file_size           BIGINT NOT NULL,
    storage_path        TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'uploaded',
    domain_label        VARCHAR(50) NOT NULL DEFAULT 'general',
    parse_error         TEXT,
    uploaded_at         TIMESTAMP NOT NULL DEFAULT now(),
    processed_at        TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT chk_documents_status CHECK (
        status IN ('uploaded', 'parsing', 'indexing', 'ready', 'failed')
    ),
    CONSTRAINT chk_documents_file_type CHECK (
        file_type IN ('md', 'pdf', 'doc', 'docx', 'txt')
    )
);

COMMENT ON TABLE documents IS '文档表：元信息、处理状态和自动识别的领域标签';
COMMENT ON COLUMN documents.domain_label IS '统一小写，MVP 阶段固定 general';
COMMENT ON COLUMN documents.status IS 'uploaded / parsing / indexing / ready / failed';

CREATE INDEX idx_documents_kb_id ON documents(knowledge_base_id);
CREATE INDEX idx_documents_user_id ON documents(user_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_domain ON documents(domain_label);

-- ============================================================
-- 5. document_chunks 文档切片表（含向量列）
-- ============================================================
CREATE TABLE document_chunks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    knowledge_base_id   UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain_label        VARCHAR(50) NOT NULL DEFAULT 'general',
    chunk_index         INTEGER NOT NULL,
    content             TEXT NOT NULL,
    parent_content      TEXT,
    token_count         INTEGER,
    embedding           VECTOR(1024),
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON TABLE document_chunks IS '文档切片表：RAG 检索的核心表';
COMMENT ON COLUMN document_chunks.embedding IS 'qwen3.7-text-embedding 1024 维向量';
COMMENT ON COLUMN document_chunks.parent_content IS '父级上下文，缓解语义漂移';

CREATE INDEX idx_chunks_document_id ON document_chunks(document_id);
CREATE INDEX idx_chunks_kb_id ON document_chunks(knowledge_base_id);
CREATE INDEX idx_chunks_user_id ON document_chunks(user_id);
CREATE INDEX idx_chunks_domain ON document_chunks(domain_label);

-- 向量索引（HNSW）
CREATE INDEX idx_chunks_embedding_hnsw ON document_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- ============================================================
-- 6. qa_sessions 问答会话表（V2）
-- ============================================================
CREATE TABLE qa_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    knowledge_base_id   UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    title               VARCHAR(300),
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON TABLE qa_sessions IS '问答会话表（V2）：对话历史和多轮追问';

CREATE INDEX idx_qa_sessions_user_id ON qa_sessions(user_id);

-- ============================================================
-- 7. qa_messages 问答记录表
-- ============================================================
CREATE TABLE qa_messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID REFERENCES qa_sessions(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    knowledge_base_id   UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    question            TEXT NOT NULL,
    answer              TEXT,
    intent_type         VARCHAR(20),
    routed_domain       VARCHAR(50),
    confidence_score    NUMERIC(5,2),
    answer_source       VARCHAR(20),
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT chk_qa_messages_intent CHECK (
        intent_type IS NULL OR intent_type IN ('fact', 'summary', 'compare', 'metadata', 'other')
    ),
    CONSTRAINT chk_qa_messages_source CHECK (
        answer_source IS NULL OR answer_source IN ('knowledge_base', 'web_search', 'mixed', 'no_answer')
    )
);

COMMENT ON TABLE qa_messages IS '问答记录表：问题、回答、意图、路由和置信度';
COMMENT ON COLUMN qa_messages.session_id IS 'V1 可为空，V2 建 qa_sessions 后启用';

CREATE INDEX idx_qa_messages_user_id ON qa_messages(user_id);
CREATE INDEX idx_qa_messages_session_id ON qa_messages(session_id);
CREATE INDEX idx_qa_messages_created_at ON qa_messages(created_at DESC);

-- ============================================================
-- 8. qa_references 回答引用表
-- ============================================================
CREATE TABLE qa_references (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    qa_message_id       UUID NOT NULL REFERENCES qa_messages(id) ON DELETE CASCADE,
    source_type         VARCHAR(20) NOT NULL,
    document_id         UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id            UUID REFERENCES document_chunks(id) ON DELETE CASCADE,
    web_title           VARCHAR(500),
    web_url             TEXT,
    snippet             TEXT,
    relevance_score     NUMERIC(5,2),
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT chk_qa_references_source CHECK (
        source_type IN ('document_chunk', 'web_search')
    ),
    CONSTRAINT chk_qa_references_fields CHECK (
        (source_type = 'document_chunk' AND document_id IS NOT NULL AND chunk_id IS NOT NULL)
        OR
        (source_type = 'web_search' AND web_url IS NOT NULL)
    )
);

COMMENT ON TABLE qa_references IS '回答引用表：知识库片段或在线搜索结果';
COMMENT ON COLUMN qa_references.source_type IS 'document_chunk / web_search';

CREATE INDEX idx_qa_references_message_id ON qa_references(qa_message_id);

-- ============================================================
-- 9. processing_jobs 文档处理任务表
-- ============================================================
CREATE TABLE processing_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    job_type        VARCHAR(20) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT chk_processing_jobs_type CHECK (
        job_type IN ('parse', 'classify', 'chunk', 'embedding', 'index')
    ),
    CONSTRAINT chk_processing_jobs_status CHECK (
        status IN ('pending', 'running', 'success', 'failed')
    )
);

COMMENT ON TABLE processing_jobs IS '文档处理任务表：展示处理状态和失败原因';
COMMENT ON COLUMN processing_jobs.status IS '任一 job 失败 → documents.status = failed';

CREATE INDEX idx_processing_jobs_document_id ON processing_jobs(document_id);
CREATE INDEX idx_processing_jobs_status ON processing_jobs(status);