# Hermes 项目上下文隔离 — 追踪文档

> 创建于 2026-05-18 | 最后更新 2026-06-03

---

## 一、目标

### 核心目标

**让 Hermes 支持「项目级上下文隔离」：每个项目拥有独立的聊天窗口、记忆、文档和技能，互不污染。**

### 目标达成情况

| # | 目标 | 衡量标准 | 状态 |
|---|------|---------|------|
| G1 | 隔离记忆 | 切到 alpha，看不到 beta 记忆 | ✅ 已完成 |
| G2 | 隔离技能 | 每个项目只加载自己的 skill | ✅ 已完成 |
| G3 | Web 主页替代微信作为主交互界面 | 用户打开 /chat/ 直接开始聊天 | ✅ 已完成 |
| G4 | 主聊天窗口 + 项目窗口 | 主窗口/项目/话题三层独立 | ✅ 已完成 |
| G5 | 话题隔离 | 多轮讨论自动生成话题，有独立上下文 | ✅ 已完成 |
| G6 | 按项目/话题过滤搜索 | 搜索只返回对应项目的会话 | ✅ 已完成 |
| G7 | 快速创建项目 | Web 端一键创建项目 | ✅ 已完成 |
| G8 | 模型隔离（远期） | 不同项目用不同模型 | 🔮 远期 |

---

## 二、架构

### 三层独立架构（2026-05-31 完成）

```
Web Chat
├── 主窗口 (main)        — 默认入口，全局记忆
├── 项目窗口 (project)   — 独立 SOUL/MEMORY/workspace
└── 话题窗口 (topic)     — 临时讨论，独立上下文
```

三层平级独立，话题不归属项目。Session key 格式：
- 主窗口: `main`
- 项目: `{project_key}`
- 话题: `topic-{topic_id}`

### 目录结构

```
~/.hermes/projects/
├── main/
│   ├── SOUL.md           # 主窗口 persona
│   └── MEMORY.md         # 全局记忆
├── alpha/
│   ├── SOUL.md           # 项目 persona
│   ├── MEMORY.md         # 项目记忆
│   └── workspace → /path # 软链接到实际代码
└── _topics/
    ├── topics.json                    # 话题列表
    ├── topic-{id}_chat.json           # 话题消息
    ├── topic-{id}_files.json          # 话题关联文件（持久化）
    └── topic-{id}_archive/            # 话题归档
```

---

## 三、已完成功能

### 核心功能

- [x] 文件级项目隔离（SOUL.md + MEMORY.md）
- [x] API 层 system_message 注入（不泄露其他项目内容）
- [x] 三层架构：主窗口 / 项目 / 话题
- [x] 话题自动创建 + 独立上下文
- [x] 白盒记忆 CRUD（查看/添加/编辑/删除/置顶）
- [x] 关联文件面板（右侧栏，含下载按钮）
- [x] 会话持久化（chat_history.json + archive）
- [x] session_files 持久化（重启不丢文件关联）
- [x] 文件下载支持中文文件名（RFC 5987 编码）
- [x] Vision 支持（HEIF→JPEG 转换 + mimo-v2.5 图片描述）
- [x] SSE 流式响应
- [x] 消息搜索（会话 + 记忆全文检索）
- [x] 项目创建/删除/切换
- [x] 双入口：Web Chat + 微信（iLink Bot）

### 运维相关

- [x] API key 从 .env 加载（不硬编码）
- [x] nginx 反向代理配置
- [x] bridge 进程管理（systemd user service）
- [x] session archive 防膨胀（定期清理）

---

## 四、已知陷阱

详见 `docs/` 目录下的 ADR 和组件文档。关键陷阱：

1. 切换项目时必须重置 `currentTopicId = null`
2. `exitTopic()` 的 header 不能写死"主窗口"，要动态读当前项目
3. `_load_projects` 需跳过 `_topics` 目录
4. session_files 存盘使用 `.tmp` + `os.rename` 保证原子性
5. 中文文件名下载需 RFC 5987 编码（Python http.server latin-1 限制）
6. 向 /var/www/html/ 写文件后必须 `chown -R admin:admin`

---

## 五、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| HTTP Server | Python http.server (stdlib) | 零依赖，1.6GB RAM 环境友好 |
| 前端 | 原生 HTML/JS/CSS | 无构建步骤，单文件部署 |
| 持久化 | JSON 文件 | 简单可靠，无数据库依赖 |
| LLM API | MiMo (mimo-v2.5-pro) | 低延迟，中文优化 |
| Vision | MiMo (mimo-v2.5) | 支持图片输入 |
| 反向代理 | nginx | 成熟稳定 |

---

## 六、参考

- [Context Isolation Levels](https://arxiv.org/abs/2504.19954) — HoYu Fu, 2026-05-10
- [OpenBMB/PilotDeck](https://github.com/OpenBMB/PilotDeck) — 白盒记忆设计灵感
- [ADR-001: Three-Layer Isolation](docs/adr/001-three-layer-isolation.md)

---

*本文档随项目进展持续更新*
