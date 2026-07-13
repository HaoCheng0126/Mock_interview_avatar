# Interview Agent — 开发文档

数字人模拟面试的 Python 实现：`interview/` 是面试 agent 本体，`hub/` 是配置控制台。产品级说明与快速开始见[根 README](../README.md)。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `interview/agent.py` | HTTP 服务与装配：每次 `/api/start-session` 重读 YAML、重建控制器；档案接入 API |
| `interview/controller.py` | 工作流核心：17 状态的面试状态机（提问/听答/思考提醒/超时跳题/追问/收尾） |
| `interview/interview_manager.py` | 加载 `config/interview.yaml`；persona 上下文与知识库块构建；系统提示词/开场白渲染 |
| `interview/prompts.py` | 全部 LLM 提示词与口播话术的内置默认值 + `{placeholder}` 渲染器 |
| `interview/question_planner.py` | 按题库顺序出下一题 |
| `interview/answer_evaluator.py` | 回答评估（LLM JSON，失败降级启发式） |
| `interview/follow_up_decider.py` | 追问判定（LLM 判定 + 规则兜底） |
| `interview/follow_up_planner.py` | 追问话术生成（纯模板） |
| `interview/report_generator.py` | 终局多维报告（LLM + 统计兜底） |
| `interview/profile.py` | 面试准备档案：简历文件解析（pypdf/python-docx）+ 岗位/JD/简历定向写入 YAML |
| `interview/listener.py` / `asr_manager.py` | LiveAvatar 事件桥 + Qwen ASR 实时转写 |
| `interview/session_store.py` | 面试状态 JSON 落盘（`/tmp/liveavatar-interviews`） |
| `hub/hub.py` | 控制台：凭证 API、agent 子进程启停与日志、面试配置/预览 API |
| `hub/config_store.py` | 凭证与端口存储（`config/hub_settings.json`，掩码/600 权限） |
| `hub/interview_config.py` | interview.yaml 表单化读写：默认值填充、保存前真实解析器校验、默认值剔除 |
| `llm_client.py` | OpenAI 兼容异步 LLM 客户端（共享系统提示词与上下文） |

## 面试工作流

```
开场白 → ┌─ 提问 → 听答（可配思考提醒 N 次，硬超时跳题）
         │      → 答后：衔接语 + 后台 LLM 评估
         │      → LLM 判定是否追问（每题上限）→ 追问 或 下一题 ─┐
         └──────────────────────────────────────────────────┘
题库耗尽 → 汇总报告 → 结束语        异常轨：累计/连续跳题超限 → 提前终止
```

节奏由平台 `session.state=IDLE` 驱动（说完一句才发下一句），30s 看门狗防死锁；评估在后台并行，不阻塞对话。

## interview.yaml 配置段

| 段 | 内容 | 缺省行为 |
| --- | --- | --- |
| `interview` | 标题/语言/时长/难度/每题追问上限 | 内置默认 |
| `interviewer` | 人设：姓名/风格/规则 — 注入全部 LLM 提示词 | 通用人设 |
| `candidate` | 目标岗位/背景 — 面试页「面试准备」可写 | — |
| `rubric` + `question_sets` | 评分维度与题库（题面/考察点/期望信号/红旗） | 必填 |
| `knowledge` | 资料条目（JD/简历/领域文档）+ 注入字数预算 | 无注入 |
| `speech` | 开场白模板、转场/跳题/结束语、衔接语、思考提醒 | prompts.py 默认 |
| `workflow` | 各超时秒数与跳题阈值 | prompts.py 默认 |
| `prompts` | 4 个 LLM 模板（system/evaluator/follow_up_decider/report） | prompts.py 默认 |

占位符（模板内可用）：`{interviewer_name} {interviewer_style} {interviewer_rules} {target_role} {candidate_background} {title} {duration_minutes} {knowledge} {knowledge_block}` + 各模板运行时字段。未知占位符原样保留。

## API

面试 agent（默认 :8083）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/start-session` | 创建 LiveAvatar 会话（每次重读配置） |
| `POST` | `/api/stop-session` | 结束会话 |
| `POST` | `/api/interview/start` / `stop` | 面试流程启停 |
| `GET` | `/api/interview/status` | 状态/进度/字幕/终局报告 |
| `POST` | `/api/interview/audio-input` | 麦克风开关 |
| `GET/POST` | `/api/interview/profile` | 面试准备档案；POST 为 multipart（`target_role`/`jd_text`/`resume_text`/`resume_file`，10MB） |

Hub（默认 :8000，仅 127.0.0.1）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET/POST` | `/api/config` | 平台/LLM/ASR 凭证与端口（响应掩码，磁盘 600） |
| `GET` | `/api/agents` | agent 状态（running/external/stopped） |
| `POST` | `/api/agents/start` / `stop` | 以保存配置为环境变量拉起/终止子进程 |
| `GET` | `/api/agents/logs?name=` | 子进程日志（内存环形缓冲） |
| `GET/POST` | `/api/interview-config` | 面试配置表单读写（保存前真实解析器校验） |
| `POST` | `/api/interview-config/preview` | 渲染最终系统提示词/开场白（不落盘） |

## 测试

```bash
pytest tests/ -q        # hub + interview + llm_client + 分发安全
```
