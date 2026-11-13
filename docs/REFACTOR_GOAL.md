# 经期管理模块 v2 重写 Goal（REFACTOR_GOAL）

> 目标：对情侣日程 app（GitHub `UniPeacher/couple_schedule`，生产站点
> `schedule.unipeacher.email`，部署于 uni-vps `~/sched-share`，容器
> `sched-share` :8795）的经期管理模块做**领域级重写**，达到成熟服务
> （Clue / 美柚 / Flo 路线）的直观度与科学度。
> 本文件是唯一权威规格与进度记录。每个工作会话开场只需：
> 「读 `docs/REFACTOR_GOAL.md`，推进当前未完成的里程碑，完成其验收门前不要切换到其它功能」。

## 环境与铁律

- **开发机**：local-osaka（168.138.53.198，SSH 别名 `local-osaka`，ARM64，
  Python 3.13 / Node 20 / Docker 可用）。开发目录 `~/Projects/couple_schedule-refactor`。
  测试命令约定：`TMPDIR=/tmp/couple-sched-tmp python3 -m unittest discover -s tests`（先 `mkdir -p`）。
- **生产机**：uni-vps（217.142.245.70，别名 `uni-vps`）。生产目录 `~/sched-share`
  在 M5 部署前**只读**，任何会话不得直接修改。
- 浏览器验收必须在隔离数据 + 备用端口上进行，严禁指向正式端口/正式库。
- 批量删除禁止；删除仅限本任务创建的单个明确文件。
- 新表一律 `cycle_` 前缀——`remote_backup.py`/`cycle.py:prepare_remote_backup` 依赖
  该前缀做脱敏清空，脱敏是上线硬验收项。
- 回滚三件套（M5 前备齐）：旧镜像 tag、正式库一致性快照、旧 cycle 数据归档导出。

## 已敲定决策（grill 结论，勿重开）

1. **边界**：经期域内彻底重来，`cycle_*` 表升级 v2 模型；双人协作、代记确认、
   隐私授权、通知去重的既有语义作为需求规格继承；课表、旅行清单、纪念日、
   AI 简报、账号体系不动。
2. **数据输入**：经期开始/结束日期唯一必填；新增**可选** BBT（每日一体温点）
   与症状趋势校准层。有 BBT → 反推个体化黄体期长度、事后确认排卵；
   无 BBT → 日历推算（固定黄体期假设 14 天）。不强迫每日打卡。
3. **科学边界**：输出四相分期（行经/卵泡/排卵/黄体）+ 各期典型症状科普 +
   周期规律性、黄体期长度趋势等健康洞察。排卵日可作为分期推导结果标注，
   **不做避孕/安全期指导**。免责声明改为「不用于避孕、诊断；未记录不代表推迟」。
4. **算法**：指数加权（新周期权重高）周期长/经期长，异常间隔降权而非整体拒判；
   输出「最可能日 ± 置信窗口」，样本越多窗口越窄；纯标准库实现，无外部依赖。
   样本不足时诚实显示「记录 N 个周期后形成预估」，不假装有预测。
5. **UI**：新四相**周期环**作模块首屏（环心 = 当前相位 + 第几天，预测窗口 =
   半透明弧段，四相着色）；现有月历保留，每格按相位上色；点日期进入记录不变。
6. **共享与提醒**：相位详情随现有 `share_prediction`/`share_details` 授权整体披露，
   不加新细粒度开关；**新增「经前/PMS 临近」伴侣提醒**一类，纳入现有提前天数
   设置与通知去重机制；提醒仍只走本地消息中心 + Android 轮询。
7. **数据策略**：新库从零 + 旧库只读归档。上线前把旧 `cycle_*` 全量导出为带时间戳
   归档 JSON 留在服务器；v1 已确认的经期日期记录**迁移**入 v2（日期是客观事实，
   必须保留，否则冷启动太久）；v1 的预估/授权/待确认代记不迁（授权语义在新模型
   中重建，用户重设一次）。迁移脚本幂等，先在正式库副本上演练。

## 里程碑

### M1 引擎 + 数据层（纯 Python，先于任何 UI/API）
- v2 表结构（幂等 `CREATE IF NOT EXISTS`）：
  - `cycle2_periods`（uid,start,end,confirmed 语义沿用 v1 代记规则）
  - `cycle2_bbt`（uid,date,temp_c 可选校准数据）
  - `cycle2_day_notes`（uid,date,flow,pain,symptoms,mood,note…沿用 v1 字段集）
  - `cycle2_settings`（uid,typical_cycle,typical_duration,prediction_enabled,share_* 沿用 v1）
  - 保留 v1 表与数据（授权、代记、通知键等协作表 v2 先复用 v1 结构，M2 接线）
- 新文件 `cycle_engine.py`：加权预测 + 四相推导 + BBT 校准 + 置信窗口 + 健康洞察，
  纯函数、可单测；`cycle.py` v1 逻辑保持不动（只读参考）。
- 验收门：新增单测全绿——加权均值、窗口随样本收窄、异常间隔降权、
  BBT 反推黄体期（±2 天样本）、无 BBT 回退 14 天假设、样本不足拒判但给进度提示、
  四相边界（行经期属于周期第 1 天起）。基线 83 项不破坏。

### M2 API + 协作语义接线
- `app.py` 新增 `/api/cycle2/*`（记录、日笔记、BBT、快照、相位视图、授权复用）；
  旧 `/api/cycle*` 冻结只读，v2 UI 全部走新接口。
- 代记确认流在新模型上回归；PMS/经前提醒接入 `reminder_pass` 与既有
  notice-key 去重；撤回授权即刻生效语义沿用。
- 验收门：`test_cycle2*.py` 通过真实 SQLite/HTTP 断言；伴侣视图授权矩阵测试；
  通知每周期一次不重复推送。

### M3 UI：周期环 + 月历相位上色
- `cycle2.js` / `cycle2.css`（新文件，不改 v1 文件）：环首屏、四相图例、
  「黄体期 · 第 19 天」环心文案、预测窗口弧、健康洞察卡、BBT 可选记录表单、
  月历格子按相位上色。隐私默认中性文案沿用 v1 规则。
- 验收门：`node --check`；浏览器冒烟（隔离数据、备用端口）通过：环显示、
  月历上色、记录/补录、代记确认、深色模式。

### M4 迁移演练 + 备份脱敏
- `migrate_cycle_v1_to_v2.py`：幂等；迁移已确认经期日期 + 日笔记；导出旧库全量
  归档 JSON（带时间戳）；v1 表保留不动。
- `remote_backup.py` / `prepare_remote_backup` 适配：确认清空覆盖全部 `cycle2_*`
  表；脱敏副本断言为空。
- 验收门：在**生产库快照副本**上演练迁移，核对统计一致；脱敏副本复查无
  cycle 数据；重复执行迁移不产生重复行。

### M5 部署（人工确认后执行）
- uni-vps：快照 → 归档导出 → `docker compose up -d --build` → 生产冒烟
  （双账号真实数据、环正常显示、提醒注册）→ 旧镜像/旧库三件套确认可回滚。
- **M5 必须等用户晨间人工确认，未确认不得执行。**

## 进度表（每个会话过门后更新）

| 里程碑 | 状态 | 完成时间 | 备注 |
|---|---|---|---|
| M1 引擎+数据层 | ✅ 完成 | 2026-11-13 | `cycle_engine.py` + `cycle2.py`；34 项新单测 |
| M2 API+协作接线 | ✅ 完成 | 2026-11-13 | `cycle2_collaboration.py` + `/api/cycle2/*`；26 项新测 |
| M3 UI 周期环 | ✅ 完成 | 2026-11-13 | `cycle2.js/css`；11 项浏览器验收全过 |
| M4 迁移+脱敏 | ✅ 完成 | 2026-11-13 | `migrate_cycle_v2.py`；脱敏已覆盖 `cycle2_*`；生产快照演练通过 |
| M5 部署 | ⬜ 待人工确认 | | 步骤见下 |

**全量测试**：`TMPDIR=/tmp/couple-sched-tmp python3 -m unittest discover -s tests` → Ran 154, OK（local-osaka，2026-11-13）。
**浏览器验收**：`node tests/cycle2_browser.cjs`（fixture 端口 18799）→ ALL CYCLE2 BROWSER CHECKS PASSED。

### 生产数据实况（2026-11-13 快照核对）

- `cycle_periods`：仅 **1 条**（uid=b，2026-10-24～10-30）；`cycle_days` 1 条。
- `cycle_settings` / `cycle_collab` / `cycle_preferences` / `cycle_proposals` **全为空** —— 说明现有授权与提醒偏好从未配置过。
- 结论：迁移后 v2 只有 1 个周期，预测会显示"已记录 1 次经期，再补充 1 次即可形成预估"。这是设计内的冷启动行为，不是缺陷。

## M5 上线 Runbook（需用户明确确认后执行）

```bash
# 0) 确认当前基线（只读）
ssh uni-vps 'cd ~/sched-share && git log --oneline -1 && git status --porcelain | head'

# 1) 部署前三件套
ssh uni-vps 'cd ~/sched-share && \
  docker tag sched-share:latest sched-share:pre-v2-$(date +%Y%m%d) && \
  sqlite3 data/sched.db "VACUUM INTO \"data/backups/pre-v2-$(date +%Y%m%d).db\"" || \
  python3 -c "import sqlite3;c=sqlite3.connect(\"data/sched.db\");c.execute(\"VACUUM INTO \\\"data/backups/pre-v2-$(date +%Y%m%d).db\\\"\")"'
# 并记录旧 cycle 行数，供回滚核对

# 2) 拉取新代码（在开发分支合并到 main 并经用户同意后）
ssh uni-vps 'cd ~/sched-share && git pull --ff-only'

# 3) 迁移（幂等；先 --dry-run 再正式）
ssh uni-vps 'cd ~/sched-share && python3 migrate_cycle_v2.py --db data/sched.db --archive-dir data/cycle-v1-archive --dry-run'
ssh uni-vps 'cd ~/sched-share && python3 migrate_cycle_v2.py --db data/sched.db --archive-dir data/cycle-v1-archive'

# 4) 构建重启
ssh uni-vps 'cd ~/sched-share && docker compose up -d --build'

# 5) 冒烟（双账号真实登录，只看不改）
#    - 首页出现「🌷 周期」入口，点开显示环与"再补充 1 次即可形成预估"
#    - 月历无相位着色（数据不足，符合预期）
#    - 记录一次经期开始 → 环出现"行经期 第 1 天"
#    - 伴侣账号：默认看不到任何东西（生产原本就没授权）

# 6) 脱敏复查（重要）
ssh uni-vps 'cd ~/sched-share && python3 -c "
import sqlite3,sys; sys.path.insert(0,\".\")
from cycle import prepare_remote_backup
prepare_remote_backup(\"data/sched.db\",\"/tmp/x.db\")
d=sqlite3.connect(\"/tmp/x.db\")
ts=[r[0] for r in d.execute(\"select name from sqlite_master where type=\x27table\x27 and (name GLOB \x27cycle_*\x27 or name GLOB \x27cycle2_*\x27)\")]
bad=[(t,d.execute(f\x27select count(*) from \"{t}\"\x27).fetchone()[0]) for t in ts]
print(\"non-empty:\",[x for x in bad if x[1]])
"'

# 回滚
ssh uni-vps 'cd ~/sched-share && docker tag sched-share:pre-v2-YYYYMMDD sched-share:latest && docker compose up -d'
# 数据回滚：旧 v1 表从未被删除或修改，v2 表可整表清空（cycle2_*）
```
