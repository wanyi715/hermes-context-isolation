# MEMORY — Hermes 上下文隔离系统

- Hermes Agent 项目目录在 ~/.hermes/projects/hermes-context-isolation/
- GitHub 仓库：wanyi715/hermes_workflow（公开，MIT License）
- 项目模型：deepseek-v4-pro（推理模型，用于设计和规划）
- SOUL.md 和 MEMORY.md 是 Hermes 项目上下文隔离的核心文件——其他项目（emperor-game, earnings, podcast）都有，但本项目之前缺失
- 环境变量 $HERMES_PROJECT、$HERMES_MEMORY、$HERMES_SOUL 未设置——意味着项目目录存在但隔离并未真正生效
- 参考框架：CIL (Context Isolation Levels 0-4)，Context Injection + Scoped Task Mode
- 当前项目需要先完成自身初始化（SOUL.md + MEMORY.md），再产出可发布的文档方法论
- GitHub token（fine-grained, repo + workflow 权限）已配置到 gh CLI，但 push 时需要确认 content: write 权限已开启
