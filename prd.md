# 线上知识库应用 PRD

> 本文档用于确认产品方向和功能范围，暂不展开详细交互、技术实现和验收用例。

## 1. 产品概述

### 1.1 产品名称
线上知识库问答系统

### 1.2 产品目标
将个人或团队的多格式文档统一接入线上知识库，用户可以基于已有文档进行自然语言查询，并获得带引用和置信度的回答。

### 1.3 目标用户
- 需要管理个人资料、技术文档或业务资料的个人用户
- 需要基于内部文档进行查询和问答的小型团队

### 1.4 核心价值
- 统一管理多种格式的知识文档
- 提高文档检索效率
- 降低知识问答中的幻觉和无依据回答
- 知识库没有答案时，V2 提供在线搜索作为补充

## 2. 产品范围

### 2.1 V1 范围
- 文档上传、解析和知识库管理
- 后端隐式完成文档领域分类
- 基于知识库的问答检索
- 检索结果引用和置信度展示
- 基础用户和权限管理

### 2.2 V2 范围
- 用户提问时进行意图识别和领域路由
- 知识库无结果时的在线搜索
- 混合检索与重排
- 对话历史和多轮追问
- 检索效果评估

### 2.3 暂不包含
- 多人复杂协同编辑文档
- 自动生成完整知识体系
- 面向大规模企业的复杂组织权限
- 移动端原生应用
- V1 不做密码重置，用户忘记密码需联系管理员手动重置

## 3. 核心用户流程

> 以下流程描述产品最终形态，标注 V2 的步骤在 V2 版本实现。

1. 用户登录系统
2. 用户上传文档
3. 系统解析文档、隐式识别文档领域并建立索引
4. 用户输入问题
5. 用户第一次提问时，系统自动创建一个会话；V1 可使用隐式会话，不建 qa_sessions 表
6. 系统根据问题进行意图识别和领域判断（V2）
7. 系统在匹配的领域和文档范围内检索相关知识（V2）
8. 系统根据检索结果生成回答
9. 展示回答、引用来源和置信度
10. 若知识库无法提供有效依据，则询问或执行在线搜索（V2）

## 4. 功能需求

### 4.1 用户与知识库(1:1)

- 用户注册、登录和退出
- 创建、编辑、删除对应的文档
- 为知识库设置名称和描述
- 查看知识库中的文档数量和处理状态
- 文档领域分类由后端根据内容自动完成，不要求用户手动选择

**知识库**：用户创建的资源容器，一个用户只有一个知识库，一个知识库可以包含多个文档
**领域**：文档内容的语义分类，由后端自动打标签，一份文档一个领域标签

用户注册采用邮箱 + 密码直接注册，不做邮箱验证。密码使用 bcrypt 哈希存储。

### 4.2 文档管理

支持上传和解析以下格式：

- Markdown
- PDF
- DOC
- DOCX
- TXT

主要功能：

- 上传单个或多个文档
- 查看文档名称、格式、大小、上传时间
- 处理状态根据后端实时更新(uploaded → parsing → indexing → ready / failed)
- 删除或重新处理文档
- 文档解析失败时展示失败原因
- 文档内容切分并建立检索索引
- 系统根据文档内容自动生成领域标签或分类信息，分类失败或无法判断时，归入默认领域 general，不阻塞索引流程
- 文档上传成功后，由后台任务（FastAPI BackgroundTasks）依次触发 parse → classify → chunk → embedding → index 五个 job，前端轮询 documents.status 获取最新状态。

### 4.3 知识检索与问答

- 用户输入自然语言问题
- V1 阶段默认按 general 领域进行知识库检索
- V2 阶段判断问题意图，例如事实查询、摘要、对比或元数据查询
- V2 阶段根据问题意图判断应该检索的知识领域
- V2 阶段优先在系统自动匹配的领域和文档范围内检索
- 支持关键词检索与语义检索结合
- V2 阶段对检索结果进行排序和必要的重排
- 根据检索到的内容生成回答
- 意图识别与领域路由在 V2 实现，V1 阶段默认 intent_type = other、routed_domain = general。
- 意图识别与领域路由：使用一次轻量 LLM 调用同时判断意图类型和所属领域。分类失败或无法判断时，默认 intent_type = other、routed_domain = general，不阻塞检索流程。

### 4.4 语义漂移处理

- 文档切分时保留父级上下文
- 检索子片段时关联其所属文档和父级内容
- 尽量避免不同领域、不同文档之间的内容混淆
- 回答中标明使用了哪些文档作为依据

### 4.5 在线搜索降级

在线搜索降级在 V2 实现，V1 阶段检索为空时直接返回“未找到依据”。

当知识库中没有找到足够依据时：

- 明确提示当前知识库缺少相关内容
- 根据产品策略执行在线搜索
- 区分“知识库回答”和“在线搜索回答”
- 展示在线搜索结果的来源链接
- 在线搜索失败时，返回无法确认的结果，不强行生成答案
- 在线搜索的触发条件为：检索结果为空，或最高置信度低于预设阈值。
- V2 阶段在线搜索触发阈值暂定为 60%，即最高置信度低于 60% 时触发在线搜索。阈值可在配置中调整。

### 4.6 置信度与引用

- 每次回答展示置信度百分比分数
- 展示回答对应的文档名称、片段和定位信息
- 支持点击引用查看原文上下文
- 当检索结果不足时，降低置信度并明确提示
- 没有可靠依据时，系统应优先返回“未找到依据”
- 置信度计算方式：取检索结果中最高相似度分数，经过归一化映射为 0-100 的百分比。具体映射规则在 SPEC 阶段确定。

## 5. 页面结构

### 5.1 登录页

注册 + 登录，进入系统的唯一入口。登录成功后跳转问答页。

### 5.2 文档管理页

上传（拖拽/点击）、文档列表（名称、格式、大小、时间、状态）、删除、重新处理。状态实时更新，失败时展示原因。

### 5.3 问答页

登录后默认进入。左侧栏展示知识库名称和文档数量，主区域为消息列表（问题、回答、置信度、引用卡片），底部输入框 Enter 发送。V1 检索为空时返回“未找到依据”；V2 检索为空或置信度低于阈值时触发在线搜索，结果与知识库回答明确区分。

### 5.4 问答历史页（V2）

会话列表和会话详情。

### 5.5 系统设置页

账号信息（只读）、修改密码、退出登录。V1 保持极简。

## 6. 非功能需求

- 支持线上部署
- 第一版按个人开发、自己和朋友使用的产品形态设计
- 首期预计支持约 20 名用户使用
- 单个上传文档文件大小上限为 20MB（指原始文件大小）
- 文档处理过程可查看状态
- 问答过程有明确的加载和失败提示
- 问答响应时间（P95）< 15 秒（含检索 + 生成）
- 文档索引时间 < 60 秒/文档（10 页以内）
- 用户只能访问自己有权限的知识库
- 上传文件类型白名单校验（仅允许 md / pdf / doc / docx / txt）
- API Key 通过环境变量注入，不硬编码在代码中
- 重要操作和回答结果具备基本日志
- 每次问答记录：请求 ID、检索命中数、耗时、置信度、是否触发在线搜索。
- 敏感配置和用户数据不能直接暴露

## 7. 关键业务规则

1. 文档未成功解析和索引前，不参与问答检索。
2. V1 阶段默认使用 general 领域检索；V2 阶段问题优先匹配明确的知识领域，避免全库无差别搜索。
3. V1 阶段检索不到有效依据时返回“未找到依据”；V2 阶段触发在线搜索。
4. 所有基于知识库生成的回答都应尽量提供引用。
5. V2 阶段在线搜索结果必须与知识库结果明确区分。

## 8. 已确认决策

- 第一版需要支持线上多人使用，但主要面向自己和朋友使用，按个人开发可控范围设计。
- 置信度采用百分比分数展示。
- 不支持用户手动指定检索领域，V1 默认使用 general，V2 由后端隐式判断。
- 在线搜索降级放在 V2 实现，V1 检索不到有效依据时返回“未找到依据”。
- 第一版不做文档版本管理，用户可通过删除后重新上传实现更新。
- 对话历史和多轮追问需要支持，但放在 V2 实现。
- 首期部署规模按约 20 名用户考虑，单个文档大小上限为 20MB。

## 9. 技术约束

以下技术选型为强制约束，开发过程中不得随意替换。

- 前端：html + css + js
- 后端：Python + FastAPI
- 文档解析：Docling
- RAG框架：LlamaIndex
- 向量数据库：pgvector 
- 数据库：PostgreSQL
- 语言模型：使用 openai 的 gpt-5.5 模型
- 向量模型：使用 qween 的 qwen3.7-text-embedding 模型(1024维)
- 在线搜索服务：Tavily（V2 引入）
- 部署：Docker Compose

## 10. 实体设计(数据库)

> 第一版以 PostgreSQL + pgvector 为主，实体设计围绕用户、知识库、文档、文档切片、问答记录和引用来源展开。

### 10.1 users 用户表

用于保存系统用户信息。第一版主要用于登录、权限隔离和用户数据归属。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 用户唯一 ID |
| email | VARCHAR / CITEXT | 登录邮箱，唯一；存储时统一转小写，或直接使用 CITEXT 类型 |
| password_hash | VARCHAR | 密码哈希，使用 bcrypt 或 argon2 哈希 |
| nickname | VARCHAR | 用户昵称 |
| status | VARCHAR | 用户状态：active / disabled |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

关系：

- 一个用户只有一个知识库。
- 用户只能访问自己名下的知识库、文档和问答记录。

### 10.2 knowledge_bases 知识库表

用于保存用户的知识库基础信息。当前设计为用户与知识库 1:1。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 知识库唯一 ID |
| user_id | UUID / BIGINT | 所属用户 ID |
| name | VARCHAR | 知识库名称 |
| description | TEXT | 知识库描述 |
| document_count | INTEGER | 文档数量，可作为冗余统计字段 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

关系：

- 一个知识库属于一个用户。
- 一个知识库可以包含多个文档。

### 10.3 documents 文档表

用于保存用户上传文档的元信息、解析状态和后端自动识别出的领域标签。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 文档唯一 ID |
| knowledge_base_id | UUID / BIGINT | 所属知识库 ID |
| user_id | UUID / BIGINT | 所属用户 ID，便于权限过滤 |
| filename | VARCHAR | 原始文件名 |
| file_type | VARCHAR | 文件类型：md / pdf / doc / docx / txt |
| file_size | BIGINT | 文件大小，单位 byte，第一版上限 20MB |
| storage_path | TEXT | 文件存储路径 |
| status | VARCHAR | 处理状态：uploaded / parsing / indexing / ready / failed |
| domain_label | VARCHAR | 后端自动生成的领域标签，无法判断时为 general |
| parse_error | TEXT | 解析或索引失败原因 |
| uploaded_at | TIMESTAMP | 上传时间 |
| processed_at | TIMESTAMP | 处理完成时间 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

关系：

- 一个文档属于一个知识库。
- 一个文档会被切分为多个文档片段。
- 只有 status = ready 的文档才参与问答检索。
- domain_label 统一小写；MVP 阶段只使用 general，后续由分类器扩展。
- domain_label 的允许值由后端分类器定义，MVP 阶段固定为 general，V2 引入分类器后扩展为预定义的领域列表（如 backend / frontend / product / general）。

### 10.4 document_chunks 文档切片表

用于保存文档切分后的文本片段、父级上下文和向量数据，是 RAG 检索的核心表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 切片唯一 ID |
| document_id | UUID / BIGINT | 所属文档 ID |
| knowledge_base_id | UUID / BIGINT | 所属知识库 ID |
| user_id | UUID / BIGINT | 所属用户 ID，便于权限过滤 |
| domain_label | VARCHAR | 继承文档领域标签 |
| chunk_index | INTEGER | 当前文档内的切片序号 |
| content | TEXT | 切片文本内容 |
| parent_content | TEXT | 父级上下文内容，用于缓解语义漂移 |
| token_count | INTEGER | 切片 token 数 |
| embedding | VECTOR(1024) | 向量表示，对应 qwen embedding 维度 |
| created_at | TIMESTAMP | 创建时间 |

关系：

- 一个文档包含多个切片。
- 检索时先根据 user_id、knowledge_base_id、domain_label 过滤，再进行向量或关键词检索。
- embedding 保持 VECTOR(1024)；更换 Embedding 模型时需要重建此列并重新索引。
- 文档重新处理时，先删除该文档的所有旧 chunk，再写入新 chunk。
- domain_label 统一小写；MVP 阶段只使用 general，后续由分类器扩展。
- domain_label 的允许值由后端分类器定义，MVP 阶段固定为 general，V2 引入分类器后扩展为预定义的领域列表（如 backend / frontend / product / general）。

### 10.5 qa_sessions 问答会话表（V2）

用于保存对话会话。V1 可以暂不实现，V2 用于对话历史和多轮追问。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 会话唯一 ID |
| user_id | UUID / BIGINT | 所属用户 ID |
| knowledge_base_id | UUID / BIGINT | 所属知识库 ID |
| title | VARCHAR | 会话标题，可由首个问题生成 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

关系：

- 一个用户可以有多个问答会话。
- 一个会话可以包含多条问答消息。

### 10.6 qa_messages 问答记录表

用于保存用户问题、系统回答、意图识别结果、领域路由结果和置信度。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 问答记录唯一 ID |
| session_id | UUID / BIGINT | 所属会话 ID；V1 不建 qa_sessions 表，session_id 暂时为空；V2 引入会话后补上外键约束 |
| user_id | UUID / BIGINT | 所属用户 ID |
| knowledge_base_id | UUID / BIGINT | 所属知识库 ID |
| question | TEXT | 用户问题 |
| answer | TEXT | 系统回答 |
| intent_type | VARCHAR | 意图类型：fact / summary / compare / metadata / other |
| routed_domain | VARCHAR | 系统判断出的检索领域 |
| confidence_score | NUMERIC(5,2) | 百分比置信度，例如 86.50 |
| answer_source | VARCHAR | 回答来源：knowledge_base / web_search / mixed / no_answer |
| created_at | TIMESTAMP | 创建时间 |

关系：

- 一条问答记录可以关联多个引用来源。
- V1 阶段字段可先使用默认值或留空；V2 引入意图路由和在线搜索后再填充完整。
- 当知识库检索结果为空或置信度低于阈值时，answer_source 可为 web_search。

### 10.7 qa_references 回答引用表

用于保存每次回答引用了哪些文档片段或在线搜索结果。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 引用唯一 ID |
| qa_message_id | UUID / BIGINT | 所属问答记录 ID |
| source_type | VARCHAR | 来源类型：document_chunk / web_search |
| document_id | UUID / BIGINT | 引用的文档 ID，在线搜索时为空 |
| chunk_id | UUID / BIGINT | 引用的文档切片 ID，在线搜索时为空 |
| web_title | VARCHAR | 在线搜索结果标题 |
| web_url | TEXT | 在线搜索结果链接 |
| snippet | TEXT | 引用片段或搜索摘要 |
| relevance_score | NUMERIC(5,2) | 相关性分数 |
| created_at | TIMESTAMP | 创建时间 |

关系：

- 一条问答记录可以有多个引用。
- 知识库引用关联 document_chunks。
- 在线搜索引用保存标题、链接和摘要。
- 校验规则：source_type = document_chunk 时，document_id 和 chunk_id 不能为空；source_type = web_search 时，web_url 不能为空。

### 10.8 processing_jobs 文档处理任务表

用于记录文档解析、领域分类、切片和索引过程，方便前端展示处理状态和失败原因。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID / BIGSERIAL | 任务唯一 ID |
| document_id | UUID / BIGINT | 所属文档 ID |
| job_type | VARCHAR | 任务类型：parse / classify / chunk / embedding / index |
| status | VARCHAR | 任务状态：pending / running / success / failed |
| error_message | TEXT | 失败原因 |
| started_at | TIMESTAMP | 开始时间 |
| finished_at | TIMESTAMP | 结束时间 |
| created_at | TIMESTAMP | 创建时间 |

关系：

- 一个文档可以对应多个处理任务。
- documents.status 可以由 processing_jobs 的执行结果汇总更新。
- 状态汇总规则：任一 job 失败，则 documents.status = failed；全部 job 成功，则 documents.status = ready；有 job 运行中，则 documents.status 更新为对应的中间状态。

### 10.9 实体关系概览

- users 1:1 knowledge_bases
- knowledge_bases 1:N documents
- documents 1:N document_chunks
- users 1:N qa_messages
- qa_sessions 1:N qa_messages（V2）
- qa_messages 1:N qa_references
- documents 1:N processing_jobs
- 所有父子关系默认 ON DELETE CASCADE，删除用户时级联清理其知识库、文档、chunk、问答记录和引用。

## 11. API 分组

- 认证：注册、登录、退出
- 文档：上传、列表、详情、删除、重新处理
- 问答：提问、获取引用
- 会话（V2）：列表、详情
- 健康检查：/health

## 12. 版本规划

### V1：知识库问答基础能力

- 用户登录
- 多格式文档上传与解析
- 后端隐式文档领域分类
- 基础检索问答
- 引用和百分比置信度展示
- 支持线上部署和多人使用

### V2：检索质量与问答体验增强

- 意图识别与领域路由
- 在线搜索降级
- 混合检索与重排
- 对话历史
- 多轮追问
- 检索效果评估