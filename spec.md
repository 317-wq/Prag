```markdown
# 线上知识库系统开发规格

> 本文档是项目的开发执行清单，依据 PRD.md 编写。
> 每完成一项功能，将 `[ ]` 改为 `[x]`，并在第 10 节补充实现说明、测试结果和完成日期。

---

## 1. 项目目标与范围

### 1.1 项目目标

构建一个支持多格式文档导入、向量检索、基于文档问答和引用展示的线上知识库系统。

### 1.2 核心链路

```text
用户注册 / 登录
    ↓
上传文档
    ↓
文件校验与持久化
    ↓
文档解析 → 领域分类 → 切片 → 向量化 → 索引
    ↓
用户提问
    ↓
问题向量化 + 向量检索（限当前用户知识库）
    ↓
检索为空 → 返回“未找到依据”，不调用 LLM
    ↓
有结果 → LLM 生成带引用的回答
    ↓
前端展示回答、引用、置信度
```

### 1.3 V1 范围边界

- 面向个人及小范围使用，用户与知识库 1:1，不跨用户、不跨知识库检索
- V1 不做意图识别和领域路由，所有文档 `domain_label` 固定为 `general`
- V1 不做在线搜索，检索为空时直接返回“未找到依据”
- V1 不做对话历史和多轮追问

---

## 2. 版本路线图

### V1：知识库问答基础能力

- 用户注册、登录
- 多格式文档上传与解析
- 后端隐式文档领域分类（V1 固定 general）
- 基础向量检索问答
- 引用和百分比置信度展示
- 线上部署

### V2：检索质量与问答体验增强

- 意图识别与领域路由
- 在线搜索降级
- 混合检索与重排
- 对话历史与多轮追问
- 检索效果评估

---

## 3. 开发前环境与资源配置

### 3.1 本地基础环境

- [x] 安装 Python 3.11
- [x] 安装 Git，配置用户身份
- [x] 安装 Docker Desktop 和 Docker Compose
- [x] 创建项目虚拟环境
- [x] 创建 `.gitignore`，确认 `.env`、`data/` 不会被提交
- [x] 确认本机可访问阿里云百炼、OpenAI 兼容中转站、Tavily

### 3.2 Python 项目环境

- [x] 创建 `requirements.txt`
- [x] 安装 FastAPI、Uvicorn、Pydantic
- [x] 安装 SQLAlchemy、psycopg2-binary
- [x] 安装 LlamaIndex
- [x] 安装 `llama-index-readers-docling`、`llama-index-vector-stores-postgres`
- [x] 安装 `pgvector` Python 客户端
- [x] 安装 `openai`（用于调用 Qwen Embedding 和 GPT）
- [x] 安装 `tavily-python`（V2 引入）
- [x] 安装 `passlib[bcrypt]`、`python-jose`（认证）
- [x] 安装 `python-dotenv` 或 `pydantic-settings`
- [x] 安装 `pytest`、`pytest-asyncio`、`httpx`
- [x] 安装 `loguru`（日志）
- [x] 执行一次空项目启动测试，确认依赖可用

### 3.3 模型与外部服务

#### Embedding（阿里云百炼）

- [x] 创建百炼 API Key
- [x] 确认 `QWEN_BASE_URL` 可用
- [x] 确认 `qwen3.7-text-embedding` 模型名
- [x] 配置 `QWEN_EMBEDDING_MODEL_FLASH`，作为主向量模型不可用时的兜底模型
- [x] 确认 `qwen3.7-text-embedding` 与 `qwen3.7-text-embedding-flash` 均可用，返回向量维度均为 1024
- [x] 确认返回向量维度为 1024
- [x] 编写连通性检查脚本，验证中英文文本均可向量化

#### LLM（OpenAI 兼容中转站）

- [x] 配置 `GPT_API_KEY`、`GPT_BASE_URL`、`GPT_CHAT_MODEL`
- [x] 编写连通性检查
- [x] 验证 LLM 能严格依据给定上下文回答

#### 在线搜索（V2）

- [x] 创建 Tavily API Key
- [x] V1 阶段保持 `ONLINE_SEARCH_ENABLED=false`

### 3.4 数据库与存储

- [x] 确认 Docker Compose 使用 `pgvector/pgvector:pg16` 镜像
- [x] 编写 `init.sql`，包含 `CREATE EXTENSION vector / citext / pgcrypto`
- [x] 编写所有业务表建表语句
- [x] 挂载 `init.sql` 到 `/docker-entrypoint-initdb.d/`
- [x] 配置 PostgreSQL 数据卷持久化
- [x] 配置上传文件目录挂载
- [x] 验证容器重启后数据不丢失

### 3.5 环境变量清单

- [x] 创建 `.env.example`，包含全部配置项，密钥留空
- [x] 创建本地 `.env`，填入真实密钥，确认不提交 Git
- [x] 启动时校验必填配置
- [x] 敏感配置在日志中脱敏

---

## 4. 总体架构与目录

### 4.1 分层架构

```text
frontend/            原生 HTML / CSS / JS
    ↓ Fetch API
app/api/             HTTP 路由层
    ↓
app/services/        业务编排
    ↓
app/core/            解析、切片、索引、检索、生成
    ↓
app/repositories/    数据访问（PostgreSQL + pgvector）
    ↓
data/                上传文件、日志
```

### 4.2 推荐目录

```text
Prag/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   │   ├── health.py
│   │   ├── auth.py
│   │   ├── documents.py
│   │   └── chat.py
│   ├── core/
│   │   ├── loaders.py
│   │   ├── cleaner.py
│   │   ├── splitter.py
│   │   ├── embedder.py
│   │   ├── indexer.py
│   │   ├── retriever.py
│   │   ├── prompt.py
│   │   └── generator.py
│   ├── models/
│   ├── repositories/
│   └── services/
├── frontend/
│   ├── index.html
│   ├── documents.html
│   ├── settings.html
│   ├── css/
│   └── js/
├── data/
│   ├── uploads/
│   └── logs/
├── tests/
├── init.sql
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── .gitignore
```

- [x] 创建基础目录结构
- [ ] 确认各层职责边界
- [ ] 确认前端静态资源由 FastAPI 托管
- [x] 创建 `data/logs/` 目录，确认不会被提交到 Git

---

## 5. 公共数据模型与状态定义

### 5.1 文档状态

```text
uploaded → parsing → indexing → ready
                         └──────→ failed
```

- [x] 定义状态枚举
- [x] 定义状态转换规则
- [ ] 定义 `processing_jobs` 与 `documents.status` 的汇总关系

### 5.2 核心实体

按 PRD 第 10 节实现：

- [x] users
- [x] knowledge_bases
- [x] documents
- [x] document_chunks（含 `VECTOR(1024)`）
- [x] qa_messages
- [x] qa_references
- [x] processing_jobs
- [x] qa_sessions（V2）

### 5.3 字段约定

- [x] `email` 使用 CITEXT 或统一转小写
- [x] `password_hash` 使用 bcrypt
- [ ] `domain_label` 统一小写，V1 固定 `general`
- [x] `embedding` 保持 `VECTOR(1024)`，换模型时重建列
- [ ] 文档重新处理时，先删旧 chunk 再写新 chunk

### 5.4 日志约定

使用 Loguru 作为日志库，通过 `serialize=True` 输出 JSON 格式，文件写入 `data/logs/app.log`。

日志字段约定：

- `event`：事件类型，如 `document_upload`、`retrieval`、`llm_call`
- `request_id`：请求唯一标识，通过 `logger.bind()` 注入
- `user_id`：当前用户 ID
- `duration_ms`：耗时，单位毫秒
- `status`：success / failed

- [x] 配置 Loguru 输出到 `data/logs/app.log`
- [x] 用 InterceptHandler 桥接标准库日志（Uvicorn、SQLAlchemy）
- [x] 敏感信息（API Key、密码）不写入日志
- [x] 定义日志字段和格式
- [x] 定义日志级别使用规则（INFO 正常操作，WARNING 可恢复异常，ERROR 不可恢复失败）

---

## 6. V1 模块开发清单

### 6.1 基础配置与应用启动

- [x] 实现配置加载（`app/config.py`）
- [x] 实现必填环境变量校验
- [x] 实现 FastAPI 应用入口（`app/main.py`）
- [x] 配置统一异常处理
- [x] 配置请求 ID 和结构化日志
- [x] 实现 `/health`
- [x] 配置前端静态资源托管
- [x] 验证启动成功、API 文档可访问
- [x] 配置 Loguru 输出到 `data/logs/app.log`

### 6.2 用户注册与登录

- [x] 实现用户注册接口（邮箱 + 密码）
- [x] 密码 bcrypt 哈希存储
- [x] 注册时自动创建默认知识库
- [x] 实现登录接口，返回 JWT
- [x] 实现退出接口
- [x] 实现认证依赖，保护后续接口
- [x] 校验邮箱唯一
- [x] 编写注册、登录、鉴权测试
- [x] 记录注册、登录成功/失败日志，登录失败不记录密码

### 6.3 文件上传与文档管理

- [ ] 实现单文件上传
- [ ] 实现批量上传
- [ ] 校验扩展名白名单：md、pdf、doc、docx、txt
- [ ] 校验文件大小 ≤ 20MB
- [ ] 校验文件内容不为空
- [ ] 文件名安全处理
- [ ] 保存原始文件到 `UPLOAD_DIR`
- [ ] 创建 `documents` 记录，状态 `uploaded`
- [ ] 通过 BackgroundTasks 触发处理链
- [ ] 实现文档列表查询
- [ ] 实现文档详情查询
- [ ] 实现文档删除（含 chunk 和向量记录）
- [ ] 实现文档重新处理
- [ ] 单个文件失败不阻塞批量中的其他文件
- [ ] 编写上传、校验、删除测试
- [ ] 记录上传文件名、大小、校验结果；删除和重新处理记录文档 ID

### 6.4 多格式文档解析

- [ ] 封装 DoclingReader
- [ ] 实现按扩展名路由的 Loader
- [ ] 支持 md、pdf、doc、docx、txt
- [ ] 提取标题、页码、章节等位置信息
- [ ] 扫描版 PDF 明确返回“暂不支持 OCR”
- [ ] 解析失败写入 `parse_error`
- [ ] 编写各格式解析单元测试
- [ ] 记录解析耗时、chunk 数量；失败记录 parse_error

### 6.5 文本清洗与分块

- [ ] 清理无意义空白和重复换行
- [ ] 保留标题、章节、页码边界
- [ ] 按段落优先切分
- [ ] 配置 chunk 最大长度和 overlap
- [ ] 为 chunk 添加 `document_id`、`chunk_index`、`parent_content`
- [ ] 编写分块单元测试
- [ ] 记录 chunk 数量、平均长度、耗时

### 6.6 Embedding 与向量索引

- [ ] 封装 Qwen Embedding 客户端
- [ ] 批量向量化，配置超时和重试
- [ ] 校验返回维度为 1024
- [ ] 写入 `document_chunks`，含向量和元数据
- [ ] 更新 `documents.status = ready`
- [ ] Embedding 失败标记 `failed`
- [ ] 验证重启后可加载已有向量
- [ ] 编写向量写入和维度校验测试
- [ ] 记录批量大小、向量维度、耗时、失败重试次数

### 6.7 基础向量检索

- [ ] 封装查询向量生成
- [ ] 实现 pgvector 余弦相似度检索
- [ ] 过滤条件：`user_id`、`knowledge_base_id`、`domain_label='general'`、`status='ready'`
- [ ] 配置 `top_k`
- [ ] 返回相似度分数
- [ ] 返回 chunk 文本和引用元数据
- [ ] 实现最小相关性阈值
- [ ] 检索为空时不调用 LLM
- [ ] 记录检索耗时和命中数
- [ ] 编写有命中、无命中、低分测试
- [ ] 记录 top_k、命中数、最高相似度、耗时

### 6.8 Prompt 与回答生成

- [ ] 定义系统 Prompt
- [ ] 定义上下文拼接格式
- [ ] 约束模型只能依据上下文回答
- [ ] 约束无法确认时返回“文档中没有足够依据”
- [ ] 约束模型不伪造引用
- [ ] 封装 GPT 调用客户端
- [ ] 配置超时和一次重试
- [ ] 处理调用失败，返回统一提示
- [ ] 编写 Prompt 和生成测试
- [ ] 记录上下文 token 数、LLM 耗时、是否重试

### 6.9 引用生成

- [ ] 定义引用编号规则
- [ ] 将检索结果转为引用对象
- [ ] 引用包含文档名、chunk 位置、原文片段
- [ ] 校验引用编号与实际结果关联
- [ ] 写入 `qa_references`
- [ ] 编写引用映射测试
- [ ] 记录引用数量、来源类型分布

### 6.10 问答接口

- [ ] 定义请求 / 响应 Schema
- [ ] 接收用户问题，校验非空
- [ ] 调用检索服务
- [ ] 处理空检索结果
- [ ] 调用生成服务
- [ ] 计算并返回置信度
- [ ] 写入 `qa_messages`
- [ ] 返回回答、引用、置信度、耗时
- [ ] 记录请求 ID、检索命中数
- [ ] 前端发送期间禁用重复提交
- [ ] 编写问答接口测试
- [ ] 记录完整链路耗时、检索命中数、置信度、是否返回“未找到依据”

### 6.11 原生 Web 前端

- [ ] 登录页：注册 / 登录
- [ ] 问答页：消息列表、输入框、引用卡片、置信度
- [ ] 文档管理页：上传、列表、状态、删除、重新处理
- [ ] 系统设置页：账号信息、修改密码、退出
- [ ] Enter 发送，Shift+Enter 换行
- [ ] 引用可点击展开原文
- [ ] 检索为空展示“未找到依据”
- [ ] 状态轮询更新
- [ ] 基础响应式布局
- [ ] 接口错误时在浏览器控制台输出请求 ID，方便和后端日志对应

### 6.12 V1 集成验收

- [ ] 上传 md / pdf / doc / docx / txt 各一份，均能索引成功
- [ ] 文档状态从 uploaded 变为 ready
- [ ] 用文档中的事实提问，获得带引用的回答
- [ ] 提问文档中不存在的内容，返回“未找到依据”，不调用 LLM
- [ ] 重启服务后索引仍可用
- [ ] Docker Compose 一键启动
- [ ] 完成一次完整演示

---

## 7. V2 模块开发清单

### 7.1 意图识别与领域路由

- [ ] 定义领域列表
- [ ] 用一次轻量 LLM 调用判断意图和领域
- [ ] 分类失败默认 `intent_type=other`、`routed_domain=general`
- [ ] 检索时按 `routed_domain` 过滤
- [ ] 记录分类结果和路由耗时
- [ ] 编写路由测试

### 7.2 在线搜索降级

- [ ] 接入 Tavily
- [ ] 触发条件：检索为空或最高置信度 < 60%
- [ ] 在线结果与知识库结果明确区分
- [ ] 保存引用到 `qa_references`（`source_type=web_search`）
- [ ] 在线搜索失败时返回无法确认
- [ ] 编写降级测试

### 7.3 混合检索与重排

- [ ] BM25 关键词检索
- [ ] 向量 + BM25 RRF 融合
- [ ] Cross-Encoder 重排序
- [ ] 对比启用前后的召回质量

### 7.4 对话历史与多轮追问

- [ ] 建 `qa_sessions` 表
- [ ] `qa_messages.session_id` 补外键
- [ ] 会话创建、列表、详情接口
- [ ] 前端问答历史页
- [ ] 多轮追问的上下文改写

### 7.5 检索效果评估

- [ ] 建立问题、标准答案、标准引用数据集
- [ ] 评估召回率、命中率、排序质量
- [ ] 评估引用正确率和空结果拒答准确性

---

## 8. 测试策略与验收标准

### 8.1 单元测试

- [x] 配置加载与校验
- [ ] 文件类型和大小校验
- [ ] 各格式文档解析
- [ ] 文本清洗与分块边界
- [ ] Embedding 维度校验
- [ ] 向量检索结果处理
- [ ] 空结果分支
- [ ] Prompt 拼接
- [ ] 引用映射
- [ ] 日志字段完整性校验（event、duration_ms 存在）
- [ ] 敏感信息不出现在日志中

### 8.2 接口测试

- [x] 健康检查
- [x] 注册 / 登录 / 鉴权
- [ ] 文档上传、列表、详情、删除、重新处理
- [ ] 问答接口
- [ ] 空问题
- [ ] 检索为空
- [ ] LLM 超时

### 8.3 端到端测试

- [ ] 上传 → 索引 → 提问 → 引用展示
- [ ] 无依据问题不调用 LLM
- [ ] 删除后无法检索
- [ ] 重新处理后内容更新
- [ ] 重启后数据可用
- [ ] Docker 环境完整链路

---

## 9. 开发执行顺序

1. [ ] 环境、密钥、目录配置
2. [x] 应用配置、健康检查、基础启动
3. [ ] 数据库表与 Repository
4. [x] 用户注册与登录
5. [ ] 文件上传与文档状态管理
6. [ ] 五种格式文档解析
7. [ ] 文本清洗和分块
8. [ ] Qwen Embedding 接入
9. [ ] pgvector 向量索引
10. [ ] 基础向量检索
11. [ ] Prompt 与 LLM 生成
12. [ ] 引用映射
13. [ ] 问答接口
14. [ ] 原生前端
15. [ ] V1 集成验收
16. [ ] V2 按模块迭代

---

## 10. 每次开发任务的完成记录

```markdown
### 核心实体数据库定义

- [√] `users`
- [√] `knowledge_bases`
- [√] `documents`
- [√] `document_chunks`，包含 `VECTOR(1024)`
- [√] `qa_messages`
- [√] `qa_references`
- [√] `processing_jobs`
- [√] `qa_sessions`（V2）

实现说明：
- 修改文件：`spec.md`
- 已有依据：`init.sql` 已包含上述 8 张表的建表语句、主键、外键和相关约束
- 说明：本次确认的是数据库实体定义，不代表应用层 CRUD 或业务流程已经实现
- 完成日期：2026-10-10

### 字段约定核对

- [√] `email` 使用 CITEXT 或统一转小写
- [√] `password_hash` 使用 bcrypt
- [ ] `domain_label` 统一小写，V1 固定 `general`
- [√] `embedding` 保持 `VECTOR(1024)`，换模型时重建列
- [ ] 文档重新处理时，先删旧 chunk 再写新 chunk

实现说明：
- 修改文件：`spec.md`
- 已有依据：`init.sql` 中 `users.email` 定义为 `CITEXT`，`document_chunks.embedding` 定义为 `VECTOR(1024)`
- 未勾选项：bcrypt、`domain_label` 统一小写和文档重新处理流程均需要应用层代码，目前处于环境配置阶段，暂不实现
- 完成日期：2026-10-11

### 日志字段和格式约定

- [√] 定义日志字段和格式
- [ ] 配置 Loguru 输出到 `data/logs/app.log`
- [ ] 用 InterceptHandler 桥接标准库日志（Uvicorn、SQLAlchemy）
- [ ] 敏感信息（API Key、密码）不写入日志
- [√] 定义日志级别使用规则

实现说明：
- 修改文件：`spec.md`
- 已有依据：第 5.4 节已定义 Loguru、JSON 格式、日志文件路径、日志字段及 INFO / WARNING / ERROR 的使用规则
- 未勾选项：日志输出、标准库桥接和敏感信息脱敏需要应用层代码，目前处于环境配置阶段，暂不实现
- 完成日期：2026-10-11

### 基础配置与应用启动

- [√] 实现配置加载（`app/config.py`）
- [√] 实现必填环境变量校验
- [√] 实现 FastAPI 应用入口（`app/main.py`）
- [√] 配置统一异常处理
- [√] 配置请求 ID 和结构化日志
- [√] 实现 `/health`
- [√] 配置前端静态资源托管
- [√] 验证启动成功、API 文档可访问
- [√] 配置 Loguru 输出到 `data/logs/app.log`

实现说明：
- 修改文件：`app/config.py`、`app/main.py`、`app/api/health.py`、`frontend/index.html`、`tests/test_app_startup.py`、`spec.md`
- 配置加载：使用 `pydantic-settings` 读取 `.env` 和环境变量，集中定义数据库、上传、模型、检索、日志配置
- 必填校验：启动前可校验 `DATABASE_URL`、`QWEN_API_KEY`、`GPT_API_KEY`，错误信息只输出变量名，不输出密钥值
- 应用入口：创建 FastAPI 实例，挂载健康检查路由和前端静态资源
- 日志：Loguru 同时输出终端和 `data/logs/app.log`，文件日志为 JSON，标准库日志通过 `InterceptHandler` 转发
- 测试：新增配置缺失、`/health` 和前端首页静态资源测试
- 验证：已运行 Pytest、compileall，并启动 Uvicorn 验证 `/health` 返回 `ok`、`/docs` 返回 200
- 完成日期：2026-10-11

### 用户注册与登录

- [√] 实现用户注册接口（邮箱 + 密码）
- [√] 密码 bcrypt 哈希存储
- [√] 注册时自动创建默认知识库
- [√] 实现登录接口，返回 JWT
- [√] 实现退出接口
- [√] 实现认证依赖，保护后续接口
- [√] 校验邮箱唯一
- [√] 编写注册、登录、鉴权测试
- [√] 记录注册、登录成功/失败日志，登录失败不记录密码

实现说明：
- 修改文件：`app/api/auth.py`、`app/database.py`、`app/models.py`、`app/config.py`、`app/main.py`、`tests/test_auth.py`、`tests/test_app_startup.py`、`.env.example`、`requirements.txt`、`spec.md`
- 注册接口：`POST /auth/register`，邮箱统一小写，密码使用 bcrypt 哈希，成功后创建“默认知识库”
- 登录接口：`POST /auth/login`，校验邮箱和密码，返回 Bearer JWT
- 退出接口：`POST /auth/logout`，要求认证，用于客户端丢弃无状态 JWT
- 认证依赖：`get_current_user` 解析 Bearer JWT 并加载 active 用户，`GET /auth/me` 用于验证认证状态
- 安全说明：响应和日志不输出明文密码；登录失败统一返回“邮箱或密码错误”
- 测试：覆盖注册、默认知识库、bcrypt 哈希、邮箱唯一、错误登录、JWT 登录、退出和认证依赖
- 完成日期：2026-10-11
```
