# Hermes Web Chat vs PilotDeck 详细对比

> 对比日期：2026-05-28  
> Hermes 版本：bridge.py v1 + chat/index.html  
> PilotDeck 版本：开源初版（2026-05-28）

---

## 一、总览

| 维度 | Hermes Web Chat | PilotDeck |
|------|----------------|-----------|
| **定位** | 个人 AI Agent 聊天面板 | 任务型 AI Agent 生产力平台 |
| **开源协议** | 私有 | AGPL-3.0 |
| **前端框架** | 单 HTML 文件，Vanilla JS + CSS | React 18 + TypeScript + Tailwind |
| **构建工具** | 无 | Vite（HMR 热更新） |
| **后端中间层** | Python `http.server` (bridge.py) | Express (Node.js) |
| **实时通信** | HTTP fetch + SSE 流式 | WebSocket |
| **前端状态管理** | 全局变量 (`messages`, `currentProjectKey`) | React Context + Zustand-like store |
| **桌面应用** | 无 | Electron（macOS/Windows 安装包） |
| **代码规模** | bridge.py 1238 行 + index.html 1142 行 | 数百个 TS/TSX 文件，React 应用 |

---

## 二、架构对比

### 2.1 整体架构

```
Hermes Web Chat:
  浏览器 ──HTTP──▶ bridge.py ──本地调用──▶ Hermes Agent (AIAgent)
                    │
                    ├── ProjectManager (内存 Map)
                    ├── 文件存储 (chat_history.json)
                    └── 记忆注入 (MEMORY.md)

PilotDeck:
  浏览器 ──WebSocket──▶ Express Bridge ──▶ Gateway
            REST API        │
                            ├── SessionManager (内存 Map + JSONL)
                            ├── Memory Service (独立仪表盘)
                            ├── Cron Daemon (后台任务)
                            └── Plugin Process Manager
```

**关键差异：**  
- Hermes 直接调用 AIAgent 类（同进程），PilotDeck 通过 Gateway 抽象（可远程）
- Hermes 用文件存储会话，PilotDeck 用 JSONL + 后端数据库
- PilotDeck 有独立的 WebSocket 通道用于实时推送（流式消息、通知、任务状态）

### 2.2 项目/会话隔离机制

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **隔离单位** | `project` + `topic` 双层 | `WorkSpace`（项目级） |
| **Session Key** | `"projectKey|topicId"` | `sessionId`（UUID） |
| **存储结构** | `self.sessions = {key: {agent, messages}}` | `Map<sessionId, NormalizedMessage[]>` |
| **文件隔离** | `session_files[key]` | 每个 WorkSpace 独立文件系统 |
| **记忆隔离** | 全局 MEMORY.md，非项目级 | 每个 WorkSpace 独立记忆存储 |
| **技能隔离** | 全局 `~/.hermes/skills/` | 每个 WorkSpace 独立技能集 |
| **上下文注入** | 切换项目时重新加载所有上下文 | 按需注入，限定检索范围 |

**Hermes 的优势：**
- `topic` 子层级提供了比 WorkSpace 更细粒度的隔离（项目下的多话题并行）
- 会话文件路径清晰：`topic_{id}_chat.json`

**PilotDeck 的优势：**
- 记忆和技能真正做到项目级别隔离（不同项目的记忆互不可见）
- 后端 JSONL 是唯一真相源，不依赖前端 localStorage
- `useSessionStore` 的 Map 结构切换 session 时无需清空再加载

**⚠️ Hermes 当前已知问题（用户已记录）：**
- 话题切换时前端复制主窗口内容，不是桥级别的真隔离
- 记忆和技能是全局的，不同项目会互相污染
- PilotDeck 的方案更接近用户想要的「话题层真隔离」

---

## 三、前端 UI 对比

### 3.1 布局

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **侧边栏** | 240px 固定宽度，项目列表 + 话题列表 | 可折叠应用壳，左侧导航 |
| **主区域** | 聊天窗口 + 底部输入框 | 聊天窗口 + 多面板切换（文件/终端/Git/MCP） |
| **上下文面板** | 右侧滑出面板（记忆/技能/当前文件） | 独立 Tab 或内嵌 iframe |
| **响应式** | CSS media query（700px 断点） | Tailwind 响应式 + 移动端适配 |
| **主题** | 固定深色 (#0d1117) | 深色/亮色切换 |

### 3.2 消息渲染

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **Markdown** | marked.js CDN | 自定义 Markdown 组件 |
| **代码高亮** | 无（纯文本） | 无（使用 markdown 代码块样式） |
| **图片** | `MEDIA:` 协议显示为链接 | ImageLightbox 弹窗查看 |
| **文件附件** | 文本链接 | 结构化附件卡片 |
| **Diff 展示** | 无 | ToolDiffViewer 组件（+-行着色） |
| **消息动画** | CSS fadeIn 0.2s | React 过渡动画 |

### 3.3 工具调用渲染 — 🔥 最大差距

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **工具调用展示** | 混在纯文本 markdown 中 | 独立 ToolRenderer 组件 |
| **折叠/展开** | 无 | CollapsibleDisplay（可折叠，带分类颜色） |
| **工具分类着色** | 无 | edit=琥珀、search=灰、bash=绿、todo=紫、agent=紫 |
| **子代理任务** | 无结构化展示 | SubagentContainer（子代理进度追踪） |
| **任务列表** | markdown 列表 | TodoListContent（复选框+状态） |
| **计划审批** | 无 | PlanApprovedCard（交互式卡片） |
| **向用户提问** | 无 | AskUserQuestionPanel（交互面板） |
| **Diff 对比** | 无 | ToolDiffViewer（行级 diff） |
| **文件列表** | 无 | FileListContent（可点击打开） |

**Hermes 的差距：** PilotDeck 为每种工具类型注册了专用渲染组件。Hermes 当前所有工具输出都是纯文本 markdown，用户需要自己解析大段 JSON 或终端输出。这是提升聊天体验最立竿见影的方向。

---

## 四、流式消息处理

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **传输方式** | HTTP fetch + ReadableStream | WebSocket 帧推送 |
| **渲染平滑** | 简单 `text += chunk` 追加 | `streamSmoother`（帧率适应算法） |
| **帧率控制** | 无（收到多少渲染多少） | 33ms 目标帧，360ms 目标滞后 |
| **边界断句** | 无 | 按标点/空格自然断句 |
| **字符速率平滑** | 无 | EMA 平滑（alpha=0.22） |
| **Session Dedup** | 无 | 同 turn streaming 去重保护 |
| **历史消息分页** | 一次性加载全部 | 分页加载（20 条/页，初始 100 条可见） |

**PilotDeck `streamSmoother` 的核心算法：**
- 维护一个字符缓冲区
- 每帧（~33ms）从缓冲区取出适量字符渲染
- 根据历史速率动态调整每帧字符数（1-36 个）
- 优先在标点处断句，避免单词被截断

**Hermes 可以做的最小改进：** 加一个简单的帧节流（`requestAnimationFrame` + 字符缓冲），避免高频 DOM 更新造成抖动。

**PilotDeck session dedup bug fix（值得注意）：**
他们发现一个 bug——新 turn 的 `__streaming_<sid>` 消息会错误地 splice 掉**上一轮**的 assistant 回复尾部，导致前一条回复丢失。修复方案是在 `computeMerged` 中加了同 turn 去重守卫。

---

## 五、记忆管理 — 🔥 第二大差距

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **存储位置** | `~/.hermes/memories/MEMORY.md` | 项目级 JSONL + 数据库 |
| **UI 面板** | 右侧上下文面板（只读列表） | 独立 iframe Memory Dashboard |
| **查看** | 可看当前项目相关记忆 | 完整审计追踪（时间戳、来源、项目） |
| **编辑** | ❌ 不可编辑 | ✅ 可编辑/删除/置顶 |
| **隔离** | ❌ 全局，项目间共享 | ✅ 按 WorkSpace 隔离 |
| **回滚** | ❌ | ✅ Dream Mode 一键回滚 |
| **压缩** | ❌ | ✅ 空闲时自动压缩记忆 |
| **生成追溯** | ❌ 不知道哪条记忆从哪来 | ✅ 端到端可见：生成→提取→存储→检索 |
| **幻觉修正** | ❌ 记错了无法定位 | ✅ 精确定位并修正错误条目 |

**Hermes 的差距：** 用户现在的记忆是黑盒——只知道 prompt 里注入了记忆，但不知道具体内容、来源、是否正确。PilotDeck 的「白盒记忆」是他们对 AI Agent 最大的创新点。

**对 Hermes 的建议：**
1. 短期：给上下文面板加编辑/删除按钮
2. 中期：按项目拆记忆文件，不再全局共享
3. 长期：参考 PilotDeck 做独立 Memory Dashboard（iframe 嵌入）

---

## 六、模型路由与成本

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **当前模型** | deepseek-v4-pro（全部任务） | Opus 4.5（主力）+ Sonnet 4.5（子任务） |
| **路由策略** | 无（单一模型） | 自动检测任务复杂度，分派不同模型 |
| **成本** | 全量 deepseek-v4-pro | 实测节省 ~70% token 成本 |
| **质量** | — | 路由模式质量反超纯主力模型（70.6 vs 69.1） |

**PilotDeck 实测数据（7 个复杂任务基准）：**

| 方案 | 评分 | 成本 |
|------|------|------|
| MiniMax-M2.7 单 Agent | 37.1 | $1.90 |
| Sonnet 4.6 单 Agent | 69.1 | $18.36 |
| **Sonnet 4.6 主力 + MiniMax 子任务** | **70.6** | **$3.15** ← 成本 1/6，质量反超 |

**对 Hermes 的建议：**
- 简单任务（翻译、格式化、摘要）→ deepseek-chat（便宜 10x+）
- 复杂分析（财报、代码、多步推理）→ deepseek-v4-pro
- cron 早报等固定工作流可以用规则的轻量模型

---

## 七、后台执行与定时任务

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **定时任务** | cronjob 工具（基于系统 cron） | 内置 Cron Daemon |
| **后台执行** | `terminal(background=true)` | Always-on 模式 |
| **任务发现** | 无（被动响应） | Agent 主动发现待办任务 |
| **通知** | 微信推送 | Web Push（VAPID）+ 浏览器通知 |
| **运行日志** | cron 输出文件 | 结构化运行历史 + 日志 |

**PilotDeck 「Always-on」模式的设计思路（值得学习）：**
- Agent 不只是「你问它答」
- 后台持续监控、发现任务、执行并产出结果
- 用户回来后看到的是完成的文件 + 摘要报告

Hermes 的 cron 早报已有雏形，但 PilotDeck 把这个模式泛化了——任何项目都可以有后台任务。

---

## 八、技术栈与可维护性

| 方面 | Hermes | PilotDeck |
|------|--------|-----------|
| **前端语言** | Vanilla JS（无类型） | TypeScript（类型安全） |
| **样式方案** | 内联 CSS（全局作用域） | Tailwind（工具类） |
| **组件化** | 无（单文件脚本） | React 组件树 |
| **热更新** | 无（改完刷新浏览器） | Vite HMR（秒级热更新） |
| **国际化** | 无 | i18n 多语言支持 |
| **PWA** | 无 | ✅ manifest + service worker |
| **测试** | 无 | Jest 单元测试 |
| **代码检查** | 无 | ESLint + TypeScript strict |
| **桌面打包** | 无 | Electron（DMG/EXE 安装包） |
| **Docker 部署** | 无 | ✅ docker-compose |

---

## 九、Hermes 独有的优势

对比不是为了贬低自己的项目。Hermes 在某些方面其实做得更好：

| 优势 | 说明 |
|------|------|
| **部署简单** | 两个文件（bridge.py + index.html），Python 标准库即可跑，零 npm |
| **话题子层级** | `project → topic` 两层隔离，比 PilotDeck 单层 WorkSpace 更灵活 |
| **多平台接入** | 微信、飞书、Web Chat 统一 Agent 后端 |
| **与 Hermes Agent 深度集成** | 直接调用 AIAgent 类，共享工具链和配置 |
| **内存占用小** | 单进程 Python，不像 Node.js 前端工具链吃资源 |
| **上下文面板** | 右下角下载按钮、记忆/技能/当前文件一览——简洁直观 |
| **项目搜索（新增）** | 侧边栏项目搜索框，快速定位 |

---

## 十、优先级建议

基于以上对比，按投入产出比排序：

### 🔴 高优先级（投入小、收益大）

1. **流式消息帧节流** — 参考 `streamSmoother`，加 `requestAnimationFrame` 缓冲，消除抖动（~50 行 JS）
2. **工具调用折叠展示** — 为 terminal/web_search 等工具输出加折叠组件（~200 行 JS）
3. **记忆面板加编辑/删除** — 上下文面板已有数据，加上操作按钮即可（~100 行）

### 🟡 中优先级（需要一定重构）

4. **话题层真隔离** — bridge.py session key 已支持 `project|topic` 格式，前端需要做对应的消息加载和切换逻辑
5. **记忆按项目隔离** — 拆 MEMORY.md 为 `memories/{project}/MEMORY.md`
6. **工具调用分类着色** — 参考 PilotDeck 的 `borderColorMap`，按工具类型显示不同左边框颜色

### 🟢 低优先级（大工程，长期规划）

7. **独立 Memory Dashboard** — 参考 PilotDeck 的 iframe 嵌入方案
8. **模型智能路由** — 简单任务用 deepseek-chat，复杂用 v4-pro
9. **前端框架升级** — Vanilla JS → React/TypeScript，获得组件化和类型安全
10. **桌面应用** — Electron 打包，独立窗口运行

---

## 附录：PilotDeck 相关文件速查

| 想参考什么 | 文件路径 |
|-----------|---------|
| Session 隔离 Store | `ui/src/stores/useSessionStore.ts` |
| 流式平滑渲染 | `ui/src/components/chat/hooks/streamSmoother.ts` |
| 工具渲染器 | `ui/src/components/chat/tools/ToolRenderer.tsx` |
| 工具配置注册表 | `ui/src/components/chat/tools/configs/toolConfigs.ts` |
| 可折叠展示组件 | `ui/src/components/chat/tools/components/CollapsibleDisplay.tsx` |
| 会话状态管理 Hook | `ui/src/components/chat/hooks/useChatSessionState.ts` |
| 记忆面板 | `ui/src/components/main-content/view/memory/MemoryPanel.tsx` |
| 后端会话管理器 | `ui/server/sessionManager.js` |
| 后台任务管理 | `ui/server/services/always-on-events.js` |

> 仓库地址：https://github.com/OpenBMB/PilotDeck
