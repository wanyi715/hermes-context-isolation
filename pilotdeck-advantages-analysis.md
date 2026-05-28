# PilotDeck 优势分析与 Hermes 借鉴方案

> 分析日期：2026-05-28  
> 来源项目：[OpenBMB/PilotDeck](https://github.com/OpenBMB/PilotDeck)（AGPL-3.0）  
> ⚠️ 本文档将随 Hermes 开源发布。任何借鉴均需明确标注出处。

---

## PilotDeck 的三大核心创新

PilotDeck 不是"又一个 React 聊天 UI"。它的真正创新在三个架构级设计上：

| 创新 | 性质 | 行业首创？ |
|------|------|-----------|
| 白盒记忆（White-box Memory） | 架构创新 | ✅ 是 |
| 智能路由（Smart Routing） | 算法创新 | 🟡 部分（Anthropic 有类似概念） |
| 始终在线（Always-on Agent） | 模式创新 | ✅ 是 |

其余亮点（工具渲染器、流式平滑、WorkSpace 隔离、React 技术栈）属于**优秀工程实践**，技术上可借鉴但非 PilotDeck 独创。

---

## 一、白盒记忆 — PilotDeck 最大的创新

### 他们做了什么

当前几乎所有 AI Agent（包括 Hermes）的记忆是**黑盒**：

```
Agent 推理 → 自动生成记忆 → 写入文件 → 注入下轮 prompt
                ↑
           你完全看不到中间过程
```

PilotDeck 把它变成**白盒**：

```
生成 → 提取 → 存储 → 检索 → 注入
  │      │      │      │
  └──────┴──────┴──────┴── 全程可见、可编辑、可回滚
```

每条记忆有完整的审计追踪：哪次对话生成的、什么时候、属于哪个项目。错了能精确定位并修正。Dream Mode 在空闲时自动压缩记忆，支持一键回滚。

### Hermes 当前状态

```python
# 唯一的记忆机制：全局 MEMORY.md
GLOBAL_MEMORY = os.path.expanduser("~/.hermes/memories/MEMORY.md")
```

- Agent 调用 `memory` 工具写入
- 下轮自动注入 prompt
- 没有 UI 查看内容
- 没有编辑/删除能力
- 没有项目隔离
- 不知道哪条记忆从哪次对话来

### 可以借鉴到什么程度

| 功能 | 难度 | 对 Hermes 的价值 | 借鉴来源 |
|------|------|-----------------|----------|
| 记忆按项目隔离文件 | 🟢 低 | 高。解决项目间记忆污染 | 思路受 PilotDeck WorkSpace 记忆隔离启发 |
| 上下文面板加编辑/删除 | 🟢 低 | 高。能改能删，不再只读 | 思路受 PilotDeck Memory Dashboard UI 启发 |
| 记忆带时间戳和来源 | 🟡 中 | 中。追溯"这条记忆哪来的" | 思路受 PilotDeck 审计追踪设计启发 |
| 完整 Memory Dashboard | 🔴 高 | 高。独立 iframe 面板 | 需注明"架构受 PilotDeck 白盒记忆设计启发" |
| Dream Mode 自动压缩+回滚 | 🔴 高 | 待评估 | 如实现需注明"灵感来自 PilotDeck Dream Mode" |

**建议路线：**
1. 短期：拆 `memories/{project}/MEMORY.md`（项目隔离）
2. 短期：上下文面板加编辑/删除按钮
3. 中期：每条记忆加 `## source: 2026-05-28 / Synopsys Q2 对话` 来源标记
4. 长期：独立 Memory Dashboard（iframe 嵌入）

---

## 二、智能路由 — 省钱且质量不降

### 他们做了什么

```
用户输入 → 任务分类器 → 简单任务 → 便宜模型（如 MiniMax/Sonnet）
                        → 复杂任务 → 主力模型（如 Opus 4.5）
```

实测数据：主力模型 + 便宜子模型，成本降到 1/6，质量从 69.1 反超到 70.6。

### Hermes 当前状态

全部任务用 deepseek-v4-pro，包括「帮我翻译这句话」「今天天气怎么样」。

### 可以借鉴到什么程度

| 方案 | 难度 | 说明 | 借鉴来源 |
|------|------|------|----------|
| 关键词规则路由 | 🟢 低 | `if "翻译" in msg → deepseek-chat` | 通用工程实践 |
| LLM 分类路由 | 🟡 中 | 先让 light model 判断任务复杂度 | 思路受 PilotDeck Smart Routing 启发 |
| 动态阈值调整 | 🔴 高 | 根据历史数据优化分类阈值 | 思路受 PilotDeck 路由算法设计启发 |

**建议路线：** 先用关键词规则做 MVP（翻译/摘要/格式化 → chat，其他 → v4-pro），验证省钱效果后再上 LLM 分类。

---

## 三、始终在线 Agent — 从被动到主动

### 他们做了什么

Agent 不只是「你问它答」。后台持续运行：
- 发现待办任务
- 执行长时间监控
- 产出文件 + 摘要报告
- 用户回来后看到的是结果

### Hermes 当前状态

- `cronjob` 可以定时触发（早报等）
- `terminal(background=true)` 可以后台跑命令
- 但 Agent 不会**自己发现任务**

### 可以借鉴到什么程度

| 方案 | 难度 | 说明 | 借鉴来源 |
|------|------|------|----------|
| cronjob 加「发现模式」 | 🟡 中 | 定时扫描工作目录，发现新文件/新数据自动触发分析 | 思路受 PilotDeck Always-on 设计启发 |
| 任务队列 + 优先级 | 🔴 高 | Agent 维护待办清单，空闲时自动取任务执行 | 思路受 PilotDeck 后台执行模式启发 |

**建议路线：** 短期内不强求。Hermes 的 cronjob 已经覆盖了主要的后台场景（早报、周报、备份）。「主动发现」这个能力等前两项落地后再评估。

---

## 四、工具渲染器 — 优秀工程实践（非 PilotDeck 独创）

### 他们做了什么

每种工具调用有专属渲染组件：
- 终端 → 绿色左边框，折叠/展开
- 文件编辑 → 琥珀色，diff 视图
- 搜索 → 灰色
- 子代理 → 紫色，进度追踪

类似设计在 Claude Code、Cursor 中也有体现，属于行业最佳实践的趋同演化。

### Hermes 当前状态

所有工具输出都是纯文本 markdown，混在聊天消息里。

### 可借鉴的部分

| 功能 | 难度 | 说明 |
|------|------|------|
| 工具调用折叠展示 | 🟡 中 | 长输出默认折叠，点击展开 |
| 按工具类型着色 | 🟢 低 | terminal=绿，search=灰，file=蓝 |
| Diff 对比视图 | 🟡 中 | patch 结果用 +- 行展示 |

这些无需特别标注 PilotDeck，属于通用 UI 模式。

---

## 五、流式平滑渲染 — 具体算法可参考

### 他们做了什么

`streamSmoother.ts` 实现帧率适应的文本流式渲染：
- 33ms 目标帧率
- 360ms 目标滞后（给大脑处理时间）
- EMA 平滑字符速率
- 优先在标点处断句

### Hermes 当前状态

```javascript
// 简单追加，收到多少渲染多少
text += chunk;
renderMessages();
```

### 可借鉴的部分

如果实现帧节流 + 标点断句，可在代码注释中标注：

```javascript
/**
 * Stream smoother — renders streaming text at a controlled frame rate
 * to reduce visual jitter. Algorithm inspired by PilotDeck's streamSmoother.
 * @see https://github.com/OpenBMB/PilotDeck
 */
```

---

## 六、应该标注出处的汇总

以下是如果实现需要明确标注「受 PilotDeck 启发」的功能：

| 功能 | 标注方式 |
|------|----------|
| 白盒记忆架构（可见/可编辑/可追溯） | `ARCHITECTURE.md` 注明：记忆白盒化设计受 OpenBMB/PilotDeck 启发 |
| Memory Dashboard（iframe 嵌入） | 页面底部注：Memory Dashboard UI 模式借鉴自 OpenBMB/PilotDeck |
| Dream Mode 自动压缩+回滚 | 代码注释 + 文档标注 |
| 智能路由（LLM 分类器） | 路由模块注释：智能路由思路受 OpenBMB/PilotDeck 启发 |
| 流式平滑算法 | 函数注释：算法受 PilotDeck streamSmoother 启发 |

**不需要标注的（通用工程实践）：**
- React 技术栈
- WebSocket 通信
- 工具折叠/展开 UI
- 项目级文件隔离
- 深色主题
- Tailwind CSS

---

## 七、优先级路径图

```
Week 1-2（低投入高回报）
  ├── 流式消息帧节流（~50行JS）
  ├── 工具调用折叠展示（~200行JS）
  └── 上下文面板加编辑/删除（~100行）

Week 3-4（中等投入）
  ├── 记忆按项目隔离（拆MEMORY.md）
  └── 关键词规则路由（deepseek-chat vs v4-pro）

Month 2-3（大工程）
  ├── 独立Memory Dashboard（iframe）
  └── LLM分类智能路由

长期（待评估）
  ├── Always-on Agent模式
  └── 记忆自动压缩+回滚
```
