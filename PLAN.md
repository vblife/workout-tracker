# Workout Tracking Bot 设计方案（用户：Charles）

> 版本 v0.1 · 2026-10-11 · 基于 `/workspace/mcp-timer-probe`（Python 标准库、Streamable HTTP、无状态、`hiit_timer` 工具 + `ui://hiit-timer/timer.html` 资源，已验证桌面端可渲染 MCP App）。

## 1. 目标与范围
| # | 需求 | 实现位置 |
|---|------|---------|
| 1 | 自然语言快速记录（"10 push ups"、"bench press, 8 reps, 120lb"、"深蹲 8 次 100 磅"） | LLM 解析（Skill）→ `log_sets` 工具写库 |
| 2 | 中英文/别名归一到标准动作名 | `aliases` 表 + `resolve_exercise` 工具；未命中时 LLM 建议并经确认后 `add_alias` |
| 3 | 每日训练计划（4–5 天/周，有氧+力量+徒步） | `get_context` 提供数据，LLM 按 Skill 规则编排；Routine 每早推送 |
| 4 | 历史分析与建议 | `query_stats` 聚合 + LLM 解读 |
| 5 | 渐进超负荷建议（次数/组数/重量） | 服务端确定性算法 `suggest_next`（见 §5） |
| 6 | 自定义 HIIT 计时器（MCP App），完成后自动记录 | `hiit_timer` 工具 + `ui://workout/hiit.html`；App 回调 `log_hiit` |
| 7 | 进度图表（MCP App） | `show_progress` + `ui://workout/progress.html` |
| 8 | 周/月/季/年/全部报告 | `report` 工具（结构化数据 + 可选 UI）+ LLM 写点评 |
| 9 | 首次引导：单位、每周天数、目标 | Skill 检测 `settings` 为空 → 问答 → `update_settings` |

## 2. 架构

```
Grok Bot (桌面/移动)
 ├─ Skill: workout/SKILL.md      解析意图、引导、计划编排、报告文案
 ├─ Routines                     每日计划 07:00 PT、周报 周日 20:00、月/季/年报
 └─ MCP: workout-mcp (Python stdlib, 单文件 server.py)
        ├─ tools/*  确定性读写 + 计算
        ├─ resources: ui://workout/hiit.html, ui://workout/progress.html, ui://workout/report.html
        └─ SQLite /home/box/workout/workout.db   (WAL 模式；每日备份到 /home/box/workout/backups/)
```

**分工原则**：LLM 负责"理解与表达"（自然语言解析、别名猜测、计划措辞、报告点评）；MCP 服务器负责"事实与计算"（存储、归一、聚合、e1RM、超负荷建议、图表数据）。数字一律来自工具，LLM 不自行计算历史数据。

### 2.1 工具清单
| 工具 | 输入 | 输出 | 备注 |
|------|------|------|------|
| `get_context` | `{days?:14}` | settings、最近训练、本周完成天数、各肌群/有氧负荷、待恢复肌群 | 计划/建议前必调；settings 为空时返回 `onboarding_required:true` |
| `update_settings` | `{key:value...}` | 更新后的 settings | 引导与修改偏好 |
| `resolve_exercise` | `{names:[str]}` | 每个名字 → `{exercise_id, canonical, confidence, candidates[]}` | 见 §4 |
| `add_exercise` / `add_alias` | `{canonical, category, muscle_groups, equipment, is_bodyweight}` / `{exercise_id, alias, lang}` | 新记录 | 需用户确认后调用 |
| `log_sets` | `{performed_at?, session_hint?, sets:[{exercise, reps?, weight?, unit?, duration_s?, distance?, rpe?, note?}]}` | 写入的 set id、所属 session、是否 PR、e1RM | 自动归一；未知动作返回 `needs_confirmation` 不写库；30 分钟内同日归入同一 session |
| `edit_set` / `delete_set` | `{set_id, ...}` | 结果 | 纠错（"上一组其实是 110lb"）|
| `log_activity` | `{type:'hike'|'run'|..., duration_min, distance?, elevation_gain?, notes?}` | session id | 徒步/有氧 |
| `suggest_next` | `{exercise, target_rep_range?}` | 下次推荐 `{sets, reps, weight, rationale}` | §5 算法 |
| `query_stats` | `{exercise?, metric:'e1rm'|'volume'|'frequency'|'best_set', from, to, group_by:'day'|'week'|'month'}` | 时间序列 | 分析与图表数据源 |
| `report` | `{period:'week'|'month'|'quarter'|'year'|'all', anchor_date?}` | 结构化报告 JSON + `fallback_url` | §6；带 `_meta.ui.resourceUri=ui://workout/report.html` |
| `show_progress` | `{exercise?, metric, range}` | 序列数据 + `fallback_url` | UI: progress.html |
| `hiit_timer` | `{name?, rounds, segments:[{exercise?, kind:'work'|'rest', seconds}], warmup_s?, cooldown_s?}` | 计时器配置 + `hiit_id`（状态 planned）+ `fallback_url` | UI: hiit.html |
| `log_hiit` | `{hiit_id, completed_rounds, total_seconds, status:'completed'|'aborted'}` | 已记录 | 由 App 内 `tools/call` 调用（visibility 含 `app`）；移动端由用户口头告知后 LLM 调用 |
| `export_data` | `{format:'csv'|'json'}` | 文件路径 | 备份/迁移 |

所有带 UI 的工具：`_meta.ui.resourceUri` + `structuredContent`（App 读取）+ 文本 `content`（含 `fallback_url`，供移动端与不渲染 UI 的客户端）。

## 3. 数据库 Schema（SQLite）
```sql
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
-- 键：unit('lb'|'kg'), days_per_week(4-5), goals(JSON: ['strength','cardio','hiking','fat_loss']),
--     preferred_days(JSON), session_minutes, experience('beginner'|..), equipment(JSON),
--     timezone('America/Los_Angeles'), bodyweight, injuries, onboarded_at, rep_range_default('8-12')

CREATE TABLE exercises (
  id INTEGER PRIMARY KEY, canonical_name TEXT UNIQUE NOT NULL,  -- 英文 snake/Title，如 'Bench Press'
  name_zh TEXT, category TEXT NOT NULL,          -- strength|cardio|hiit|mobility|hike
  muscle_groups TEXT,                            -- JSON ['chest','triceps']
  equipment TEXT,                                -- barbell|dumbbell|bodyweight|machine|none
  is_bodyweight INTEGER DEFAULT 0, tracking TEXT DEFAULT 'reps_weight', -- reps_weight|reps|duration|distance
  increment REAL,                                -- 推荐加重步长（按默认单位）
  created_at TEXT NOT NULL, archived INTEGER DEFAULT 0);

CREATE TABLE aliases (
  alias_norm TEXT PRIMARY KEY,                   -- 归一化后的别名（小写/去空格/去标点/繁转简）
  alias_raw TEXT NOT NULL, exercise_id INTEGER NOT NULL REFERENCES exercises(id),
  lang TEXT, source TEXT DEFAULT 'seed',         -- seed|user|llm
  created_at TEXT NOT NULL);

CREATE TABLE sessions (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
  type TEXT,                                     -- strength|cardio|hiit|hike|mixed
  title TEXT, duration_min REAL, distance_km REAL, elevation_gain_m REAL,
  rpe REAL, notes TEXT, source TEXT DEFAULT 'chat');

CREATE TABLE sets (
  id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
  exercise_id INTEGER NOT NULL REFERENCES exercises(id),
  set_index INTEGER, performed_at TEXT NOT NULL,
  reps INTEGER, weight_kg REAL,                  -- 统一以 kg 存储
  input_weight REAL, input_unit TEXT,            -- 保留原始输入
  duration_s INTEGER, distance_m REAL, rpe REAL, is_warmup INTEGER DEFAULT 0,
  e1rm_kg REAL,                                  -- 写入时计算（Epley）
  raw_text TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE INDEX idx_sets_ex_time ON sets(exercise_id, performed_at);

CREATE TABLE hiit_workouts (
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id),
  name TEXT, config_json TEXT NOT NULL,          -- {rounds, segments[], warmup_s, cooldown_s}
  planned_seconds INTEGER, completed_rounds INTEGER, total_seconds INTEGER,
  status TEXT NOT NULL DEFAULT 'planned',        -- planned|completed|aborted
  created_at TEXT NOT NULL, completed_at TEXT);

-- 可选（Phase 3）：plans(id, date, json, status) 记录推荐计划与执行情况，用于依从率分析
CREATE TABLE schema_version (version INTEGER);
```
时间统一存 ISO-8601 UTC，展示时转 PT。体重动作（俯卧撑）weight_kg 为空，进度以次数/总量计。

## 4. 别名归一
1. **归一化字符串**：小写、去空格/连字符/标点、全角转半角、繁转简、去复数 s（"push ups"→"pushup"，"俯卧撑"）。
2. **精确匹配** `aliases.alias_norm` → 命中即用（confidence 1.0）。
3. **模糊匹配**：stdlib `difflib` 对英文别名评分 ≥0.85 自动采用；0.6–0.85 返回候选。
4. **LLM 兜底**：未命中时工具返回 `needs_confirmation` + 候选，LLM 用常识判断（如"卧推"=Bench Press、"杠铃深蹲"=Back Squat）并向用户确认一次，确认后 `add_alias(source='llm')`，以后直接命中。
5. **种子数据**：预置约 80 个常见动作及中英别名（卧推/bench/BP、深蹲/squat、硬拉/deadlift、引体向上/pull up/chin up(独立)、俯卧撑/push up、平板支撑/plank、跑步/run、徒步/hike …）。
6. 变体策略：设备不同视为不同动作（Dumbbell vs Barbell Bench Press），握法等差异作为 notes。

## 5. 渐进超负荷算法（`suggest_next`）
- **e1RM（Epley）**：`e1RM = w × (1 + reps/30)`，reps≤12 时有效；每组写入时计算，用于 PR 与趋势。
- **双重进阶（主算法）**：设目标区间 [r_min, r_max]（默认 8–12，可按动作/目标覆盖），组数默认 3。
  - 上次所有工作组都 ≥ r_max（且 RPE≤8 若有）→ 加重：上肢 +5 lb/2.5 kg，下肢 +10 lb/5 kg（或 `exercises.increment`），次数回到 r_min。
  - 否则保持重量，目标每组 +1 次。
  - 连续 2 次未能达到 r_min 或 e1RM 下降 >5% → 降重 10%（deload）。
  - 距上次 >14 天 → 回退 10% 重量重新开始。
- **体重动作**：按次数进阶（每次 +1–2 次），总次数到阈值后建议更难变式或加组。
- **有氧/徒步**：每周总时长/距离增幅 ≤10%；徒步按爬升量进阶。
- **周量控制**：每肌群每周 10–20 工作组为参考，`get_context` 返回现状供 LLM 排计划。
- 输出带 `rationale`（"上次 3×12@120lb 全部完成 → 今天 3×8@125lb"），LLM 原样解释。

## 6. 报告内容（`report`）
| 周期 | 内容 |
|------|------|
| 周 | 训练天数 vs 目标(4–5)、各 session 列表、总量(kg·reps)、有氧/徒步时长与距离、HIIT 次数、新 PR、肌群分布、下周建议 |
| 月 | 上述汇总 + 周对比趋势、主要动作 e1RM 变化、连续打卡最长天数、依从率 |
| 季 | 月度趋势、力量增长百分比、平台期动作识别（e1RM 4 周无增长）、计划调整建议 |
| 年 | 全年总天数/总量/总距离/总爬升、PR 时间线、最好月份、年度目标回顾 |
| 全部 | 历史最佳、累计里程碑（如 100 次训练、累计 100 km 徒步）、长期曲线 |
数字由工具算出（JSON），LLM 只写 3–5 条点评与建议。Routine：周日 20:00 PT 周报；每月 1 日月报；季度/年度首日相应报告。

## 7. MCP App UI
- **hiit.html**：读取 `structuredContent` 配置；显示当前段（动作名/work/rest）、大号倒计时、轮次 x/N、下一段预告、进度条、开始/暂停/跳过/结束；最后 3 秒提示音 + 震动（若支持）；Wake Lock 防息屏；完成/中止时通过 App bridge `tools/call log_hiit` 回写。由原型 timer.html 扩展。
- **progress.html**：纯内联 SVG/Canvas 折线图（无外部 CDN，CSP 为空），切换指标 e1RM/总量/频率，动作下拉；数据来自 `structuredContent`（必要时 App 调 `query_stats`）。
- **report.html**：卡片式汇总 + 小图表（周热力图、肌群柱状图）。
- **移动端回退**：每个 UI 工具返回 `fallback_url`。推荐做法：把同一份 HTML 作为**静态页**托管（如 GitHub Pages），配置/数据经 URL `#fragment` 传入（base64 JSON），无需访问盒子；移动端 HIIT 完成后由用户在聊天中说"完成了"，LLM 调 `log_hiit`。

## 8. 托管决策
| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. stdio 本地命令（AddMcpServer command 模式，在盒子上运行）** | 无公网暴露、无 URL 变动、无需 443 入站/隧道、最稳定；直接访问 SQLite | 需确认 Grok Bot 客户端对 stdio 服务器同样执行 `resources/read` 并渲染 UI（协议层面应支持，待验证）；无法给移动端提供可用回退链接 |
| B. ngrok 免费静态域名 + Streamable HTTP | 已验证可渲染；可同时提供 fallback 网页 | ngrok 进程需守护；公网暴露需加鉴权 token；免费额度/警告页（浏览器访问有插页，影响移动回退体验） |

**决定**：主选 **A（stdio）**，同一 `server.py` 同时支持 `--stdio` 与 `--http` 两种传输（共享 `handle()`）。Phase 0 先验证 stdio 下 MCP App 渲染；若不渲染则切 B（ngrok 静态域名 + Bearer token + systemd/守护脚本）。移动回退链接与托管解耦：用静态页 + URL fragment（§7），因此即使选 A 也有回退。

## 9. 分阶段计划
| 阶段 | 内容 | 里程碑/验收 |
|------|------|------------|
| P0 验证（0.5 天） | 将原型改为 stdio，AddMcpServer 注册，确认桌面渲染 hiit UI 与 App→`tools/call` 回调 | 决定 A/B |
| P1 核心记录（1–2 天） | Schema+迁移、种子别名、`resolve_exercise`/`log_sets`/`edit_set`/`log_activity`/`get_context`/`update_settings`；SKILL.md 含解析规则与引导流程 | 3 条示例句正确入库；中英别名归一；首次引导写入 settings |
| P2 HIIT（1 天） | 可配置 `hiit_timer` + `log_hiit` + 静态回退页 | 自定义 "40s 波比/20s 休 ×8" 运行并自动记录 |
| P3 建议与计划（1–2 天） | `suggest_next`（双重进阶+Epley）、每日计划 Skill 规则、每日 Routine | 连续模拟数据下建议符合规则；早上推送当日计划 |
| P4 进度与报告（2 天） | `query_stats`、`show_progress`、`report` + UI、周/月/季/年 Routines | 五种周期报告可生成，图表在桌面渲染、移动端有链接 |
| P5 加固 | 每日备份、`export_data`、单元测试、错误处理 | 备份可恢复；测试通过 |

## 10. 待确认问题
1. Grok Bot 对 stdio 服务器是否渲染 MCP App、是否支持 App 内 `tools/call`（P0 验证）？
2. 移动端回退：是否可以用 GitHub Pages 等公共静态托管（数据会出现在 URL 中，仅含计时配置/汇总数字）？
3. Charles 的具体目标权重：增肌/力量/减脂/徒步耐力？是否有伤病、可用器械（健身房还是家里）？
4. 默认次数区间与加重步长（如家用哑铃只有固定重量时如何进阶）？
5. 是否记录体重/身体数据、心率或接入可穿戴设备（Apple Health/Strava）用于有氧与徒步？
6. 每日计划推送时间与渠道（App 内通知 / Slack / 邮件）？
7. 盒子上其他 bot 是否需要读取 workout.db（共享盒子，权限/隐私）？
8. 多用户：是否仅 Charles 一人（当前 schema 不含 user_id）？
