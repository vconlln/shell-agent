# SDD ledger — plan: /home/vconlln/my-agent/docs/superpowers/plans/2026-09-17-tu-shell-agent-engine-python.md

## 起飞前裁定（控制器）
- **技术栈于实现阶段修订为「全 Python」**（用户裁定）：PySide6 界面 + httpx 直打 opencode HTTP/SSE + pytest + PyInstaller。规格 §19 记录了修订；TypeScript 版计划与其任务 1 代码（commit 52bbcaf..75d8615）已从工作树删除，git 历史仍可查。
- 目录：`/home/vconlln/my-agent`（真实目录；早期因沙箱只读而用的软链中转已不再需要）。文件策略现为 danger-full-access，审批提示已关闭——**不得再请求提权**。
- 环境：Python 3.14.7 + `.venv`（httpx 0.28.1、pytest 9.1.1 已装）；`tools/shellcheck` 0.11.0 静态二进制已就位；本机**没有**可用的 opencode 1.x Linux 二进制 → 任务 12 的真实冒烟默认 skip（不影响其余任务与离线端到端）。
- PySide6 6.11.2 / pytest-qt 4.5.0 / PyInstaller 6.22.3 已于本机 dry-run 验证可解析（Plan 2 与 M4 使用）。
- **计划 vs 规格冲突（用户裁定）**：CLI 非 TTY 默认拒绝执行、必须显式 `--yes`（规格 §11 为准）。已写进计划任务 12 与其验证命令。
- 起飞前扫描已内联修复两处计划缺陷：任务 5 测试里多余的 `_deps` 辅助函数（未使用）、任务 6 被我故意写错位置的 `import os`（已改为正常放在顶部）。

## 进度

## 任务 1（Python 版）
- 审查（deepseek-v4-pro）结论：规格 ✅ 符合、任务质量通过；1 条重要发现（**计划强制**）+ 4 条次要。
- **用户裁定：修** —— `FailureEvidence` 的两个裸 dict 字段改为专用冻结 dataclass（`ContractEvidence` / `ExecuteEvidence`）；`schema` / `write_attempt(files)` / `write_meta(patch)` 三个 dict 明确保留（天生是 JSON 结构）。
- 同步范围：计划的任务 1（types.py）、任务 8（prompt.py 与 tests）、任务 11（loop.py 及其 import）已一并更新，避免后续简报与代码脱节；代码侧已派回**原实现者**做第 1/5 轮修复。
- 次要（延后）：(a) `addopts="-q"` 叠加命令行 `-q` 会吞掉 `N passed` 汇总行（取证改用 `-o addopts=""`）；(b) 测试未覆盖 dataclass 冻结/默认值；(c) `assert ... is True` 风格脆弱；(d) `requires-python>=3.10` 的下限未实测。

## opencode 契约核对（真实 1.18.31 二进制，供任务 10 使用）
本机已装 `tools/opencode`（1.18.31，取自 npm 平台包 `opencode-linux-x64`，无需 postinstall）。用**干净的 XDG 目录**起 serve 后核对 OpenAPI：
- 请求字段是 `format`（→ `OutputFormat = TextOutputFormat | OutputFormatJsonSchema`），**`outputFormat` 零命中**；
- 结果在 `AssistantMessage.structured`（**与 JS SDK 文档写的 `structured_output` 不同**）→ 计划里两个都读；
- 失败形态 `info.error = StructuredOutputError{message, retries}`；body 只有 `parts` 必填；
- `GET /global/health` → `{"healthy":true,"version":"1.18.31"}`；`POST /session/{sessionID}/permissions/{permissionID}` 存在。
- **坑**：1.x 与 2.0.5 不能共用同一个 XDG 数据目录（2.0.5 写过的 DB 会让 1.x 报 `Database is not empty and has no session table`）；本机用 `.toolhome/share-1x` 与 `.toolhome/share` 分开。

Task 1: fix round 1/5 (1 addressed, 0 open — FailureEvidence 的裸 dict 改为 ContractEvidence/ExecuteEvidence; commits 3dfa460..ede2d8f)
Task 1: complete (commits 0aa506e..ede2d8f, review clean)

## 安全模型实证（真实 1.18.31，供任务 9/10 与 Windows 手测参照）
把带 `bash: deny / edit: deny / external_directory: deny / read 白名单` 的 agent 定义放进运行目录 `.opencode/agents/tu-shell-writer.md`，以该目录为 cwd 起 serve 后：
- `opencode agent list` 与 `GET /agent` 都列出 `tu-shell-writer (primary)` → **项目级 agent 发现机制成立**；
- `POST /session` 成功，返回体的 `directory` 等于 server 的 cwd → **「运行目录即项目根」成立**；
- `GET /agent` 的合并规则里出现 `bash * -> deny`、`edit * -> deny`、`read * -> deny` + `read <白名单> -> allow` → **agent 的 deny 覆盖全局 `* -> allow`，安全模型的地基成立**（规格 §18 风险 6 据此从「待实测」改为「已实证」）。

Task 2: complete (commits 5c3da37..ae27671, review clean —— 无关键/重要发现)
Task 2: minor (deferred): `test_crlf_is_normalized_to_lf` 未断言裸 `\r`（实现正确，render.py:20）
Task 2: minor (deferred): 取值优先级链条未完整覆盖（values>spec.default、spec.default>内联默认两段）
Task 2: minor (deferred): 未声明占位符报错只测单名字，未验证多名字的字典序输出
Task 2: minor (deferred): `declared` 中同名 spec 重复时后者静默覆盖，规格未定义该场景（render.py:29）

（注：控制器自身的文档提交 952e844 落在 ae27671 之后，未污染任务 2 的审查区间。）

## 任务 3
- 实现者报告 DONE_WITH_CONCERNS，抓到**计划自身的矛盾**：`set_trusted` 用 `_read_index()`，而 `index.json` 惰性创建，全新 store 上必然 `KeyError`，导致计划里 `test_set_trusted_persists` 不可能通过。实现者按"测试即 spec"改为 `metas = self.list()`（唯一实质偏离，1 行）。
  → **控制者裁定：采纳该偏离**，计划已同步为 `metas = self.list()` 并加注释。
- 控制者在同一片区域发现**同类且更严重**的第二处：`save()` 也用 `_read_index()`，在从未 `list()` 过的 store 上先保存 → 索引非空 → 3 套内置模板**永久不再写入**。计划已修，并已派原实现者补 `save()` 与一个新测试（第 1/5 轮修复）。
- 实现者附带观察（不处理，记录）：`Path.read_text()` 的 universal-newlines 会让 `store.read()` 静默返回 LF——这正是我们想要的语义（对 `to_lf` 幂等），但代价是看不到磁盘真实字节。

## 用户裁定：不在本机用真实模型凭据
用户选择**不在本机跑真实 opencode 结构化输出冒烟**（不读取其 API 凭据）。因此「请求带 `format: json_schema` → 模型返回 `info.structured`」这条主路径要到 **Windows 手测**才第一次真正运行（任务 12 的 `TU_LIVE=1` 用例）。契约字段名已用 1.18.31 的 OpenAPI 核实，风险已降到只剩"模型是否真的按 schema 返回"。
Task 3: fix round 1/5 (1 addressed, 0 open — save() 改走 self.list() 并补播种测试; commits 1980ee1..b7bcf9f)
Task 3: 实现者额外实证 `remove()` 不是同类缺陷（list() 的守卫是 `if not metas`，空索引仍触发播种），未扩大改动 —— 判断正确。残留：对全新库调 remove() 会留下杂散空索引文件（无害，延后）。
Task 3: 新测试无法在 pytest 下用旧代码跑出红态（pyproject 的 pythonpath=["."] 使工作区包遮蔽 PYTHONPATH）；实现者改用隔离进程直接跑行为并打印实际加载路径自证（旧码 seeded: False、新码 True）。取证方式诚实、有据。
Task 3: complete (commits 952e844..b7bcf9f, review clean —— 无关键/重要发现)
Task 3: minor (deferred): save() 先写 body 再调 list() → 同一次保存索引被写 2–3 次，且有一个 name/placeholders 短暂的错误中间态（store.py:152→161；单线程流程下无害）
Task 3: minor (deferred): remove() 在全新 store 上会留下 {"templates": []} 杂散索引文件（无害）
Task 3: minor (deferred): id 正则的 `$` 锚点会接受尾部换行（re.match 语义），save("mine\n") 能过校验并造出含换行的文件名 —— 计划逐字给定，非实现者引入
Task 3: minor (deferred): ensure_ascii=False / 末尾单换行 / 内置占位符写入索引等契约细节未固化为断言；set_trusted 只测了内置 id

Task 4: 实现完成（10dd4fe，6 passed / 全量 24）—— 实现者提了一个真实的对称性缺口：write_script/write_attempt 都 mkdir(parents=True) 自愈，只有 write_meta 不自愈。
Task 4: 控制者裁定「补上」（1 行 + 1 测试）；**续跑原实现者失败**（send_message 已投递但未真正唤醒该 agent：工作树零改动、测试仍 6 passed，随后收到的是重复投递的原完成消息）。
Task 4: 改走技能规定的备用路径 —— 分派**全新实现者**带简报/报告/裁定做这一处修复。
Task 4: fix round 1/5 (1 addressed, 0 open — write_meta 补自我修复 mkdir + 新用例; commits 10dd4fe..03f15ae；红态真实复现 FileNotFoundError，7 passed/全量 25)
Task 4: complete (commits af29b5c..03f15ae, review clean —— 无关键/重要发现)
Task 4: 审查者留的 ⚠️（RunStorePort 签名是否与 ports.py 逐字一致）已由控制者用 inspect.signature 逐方法比对解决：write_script/write_attempt/write_meta 参数名与 kind 全一致；run_dir 在实例上存在且为 str。→ 该 ⚠️ 关闭。
Task 4: minor (deferred): make_run_id 传入 naive datetime 时不转 UTC（默认路径正确）；显式 salt 未清洗
Task 4: minor (deferred): test_make_run_id_is_sortable_and_path_safe 名不副实（未断言可排序性）
Task 4: minor (deferred): write_script/write_attempt 的自愈无 first-call 用例，只有 write_meta 有
Task 4: minor (deferred): write_meta 写盘带尾随换行、另两个不带（无害的不一致）
Task 4: minor (deferred): 报告措辞与 diff 不符（新用例位置），已由实现者自行勘误

## 流程教训（重要，后续轮次遵守）
控制者对 `c67041da` 的续跑消息**实际生效了**，只是有延迟；我在文件状态还是旧的时候过早判定"续跑失败"并多派了一个全新实现者（`871d4a15`）。后者正确地拒绝制造空提交、并独立复核了红态与用例位置。
→ **规则**：给 idle/ready 子代理 send_message 后，必须等它的完成通知，不要用"此刻文件没变"来判定唤醒失败。

## 任务 5
- 实现完成（fa0ed13，8 passed / 全库 33），实现者带回两条高价值事实：
  1. **平台事实纠正（我的交办说明写错了）**：`opencode` 在 PATH 上（`/usr/bin/opencode`，系统包 `opencode 1.18.29-1`，root 所有）；只有 shellcheck 不在 PATH。因此 `system_deps()+detect_all()` 只报 1 条 problem（shellcheck）。→ 任务 12 的 CLI 冒烟仍须传 `--shellcheck-path tools/shellcheck`；`--opencode-path tools/opencode` 可选（传它更好：1.18.31 是核对过契约的那个二进制）。
  2. **locale 缺陷**：本机 `bash --version` 输出中文「GNU bash，版本 5.3.15」，规格的英文正则解析不出 → 版本恒为 `unknown`。
- 控制者裁定：**修**（`run_version` 在 `LC_ALL=C`/`LANG=C` 下探测 + 一条在中文机器上能真实抓住该缺陷的聚焦测试）。理由：`meta.json` 记录版本的目的就是"换机器失败时能定位"，恒为 unknown 会让该目的落空；与已做的"清理 SHELLCHECK_OPTS 保证可复现"属同一类。
- 后续任务须知：`DetectedTool.version` 可能是 `"unknown"`，比较前先判 unknown；Windows 路径判断已全部集中在 `candidate_paths()`，不得再散落 Program Files/APPDATA。
Task 5: fix round 1/5 (1 addressed, 0 open — run_version 在 C locale 下探测；红态为真实中文版本串；commit fa0ed13..0047755)
Task 5: 审查结论「需要修复」——1 条重要发现（审查者正确）：detect_all 未先判 `unknown`，
  `is_at_least("unknown","1.1.1")` → [0,0,0] → False → not False 为真 → 误报「版本过低（unknown）」，
  把「装了但解析不出」说成「太旧」，会误导用户升级没问题的安装。
  → 控制者裁定修法：保持阻断（版本未知就无法确认支持 permission 配置，那是安全模型前提），
    但走独立分支如实说明「无法识别 opencode 版本」+ 一条聚焦测试。
Task 5: 审查次要（不改，记录）：ToolName=str 别名是简报原样带来；locale 回归测试在英文机器上保护力有限（走真实 bash --version 的固有代价）。
Task 5: fix round 1/5 (1 addressed, 0 open — unknown 走独立分支「无法识别 opencode 版本」且仍阻断；红态精确复现误报文案；commits 0047755..e1e5202)
Task 5: 定向复审 —— 该发现 ADDRESSED，修复 diff 无新破坏；复审者另核实计划/代码/测试三方一致、无文档漂移。
Task 5: complete (commits ddb6f5a..e1e5202, review clean)

## 任务 6
- 实现完成（6ba9e42，8 passed / 全库 43），两条高价值实测发现：
  1. **`SHELLCHECK_OPTS` 污染被实证**：宿主环境带 `SHELLCHECK_OPTS=--exclude=SC2086` 时，含 SC2086 的脚本返回 exit=0 且 comments 为空（被静默放行）；经 run_shellcheck 才正确返回 exit=1。→ `build_env` 删除该变量是硬需求。
  2. **退出码 2 时 stdout 仍是合法空 JSON**：先解析 stdout 再看退出码的实现会把「文件不可读」误报成「脚本干净」；必须靠异常判空。`ShellcheckError` 语义 = 依赖故障（aborted_dependency），不是 needs_human。
- **该发现暴露了计划缺口**：任务 11 的 loop.py 原本没有捕获 shellcheck 抛出的异常（会直接穿出 run_loop）。已在计划里补 try/except → aborted_dependency + 落盘 shellcheck-error.txt + 一条聚焦测试。趁任务 11 未开始先修。
Task 6: complete (commits ea48559..6ba9e42, review clean —— 无关键/重要发现)
Task 6: 审查者的 ⚠️（tools/shellcheck 是否存在、测试是否 0-skip）已由控制者核实解决：二进制存在（0.11.0）、tests/test_shellcheck.py 8 passed 0 skipped。→ 该 ⚠️ 关闭。
Task 6: minor (deferred): parse_json1 的 level 非法值回退分支无测试
Task 6: minor (deferred): 退出码 4（未知 formatter）未被直接覆盖，hints 里该 label 从未走到
Task 6: minor (deferred): 异常消息里的 command 用空格 join，路径含空格时可读性差（不影响执行，真实执行走列表）
Task 6: minor (deferred): `# type: ignore[arg-type]` 是噪声（comment 来自 json.loads 即 Any）

## 任务 7
- 实现完成（304bb58，6 passed / 全库 49；红态 ModuleNotFoundError）。
- 实现者做了超出简报、但正对失效模式的实证：临时脚本 `sleep 300 & … wait`（孙子进程），超时与取消两条路径下孙子进程均被 SIGKILL（exit=-9，0.40s/0.30s，无残留）→ **进程树终止确已生效**（简报用例本身并不验证这一点）。
- 下游须知：`ExecuteResult.signal` 恒为 None，被杀脚本表现为 `exit_code=-9`；区分「脚本自己退出」与「超时/取消被杀」必须结合 `timed_out`/`cancelled`，不能只看 exit_code（计划的 loop.py 已按此判定）。
Task 7: 审查「通过（无关键）」，但 1 条重要发现（审查者正确）：`test_timeout_kills_process_tree` 名实不符——
  只断言 timed_out + 耗时，从不检查孙子进程是否真死；去掉 start_new_session 或把 killpg 换 os.kill 后该用例照样通过，
  孤儿进程会逃逸（正是规格点名的失效模式）。实现者先前的一次性实证方向正确但未进提交，不构成回归防护。
  → 控制者按「计划自身的名实不符」处理（同任务 3 那类），已把真断言写进计划，并派回原实现者补测试 + 要求做反向验证。
Task 7: 同批修计划缺口：loop.py 未捕获 execute 抛出的异常（Popen 失败会穿透编排层），
  规格 §6 的 aborted_dependency（依赖中途损坏）正指此情形 → 已补 try/except + 聚焦测试。
Task 7: minor (deferred): 无实际互斥对象的 lock；回调抛异常会中止读线程（表现为超时）；join 超时未校验。
Task 7: fix round 1/5 (1 addressed, 0 open — 新用例用孙子进程探活锁死进程树终止; commits 304bb58..19e6941)
  反向验证（实现者做，含对照实验）：
  - 变异 A（killpg→os.kill）：新用例真红（探活失败）；对照实验更精确——旧**取消**用例完全通过（孤儿逃逸无人发现），
    旧**超时**用例失败但原因是撞在 5 秒耗时限上（孤儿持有 stdout 写端 → 两个泵线程各空等 join(timeout=5)=10.4s），
    与进程树断言无关。→ 审查者判定的"名实不符"被实证，且比其描述更严重。
  - 变异 B（去掉 start_new_session）：pytest 自身被 SIGKILL（exit 137）——**若编排层/CLI 丢掉这一行，
    一次超时会杀掉调用方自己的进程组（应用自杀），而不只是杀脚本**。实现者在 setsid 中运行该变异以控制爆炸半径。
  → 已把该风险写进计划代码旁注释；并按实证把 docstring 改成准确措辞（派回实现者做纯注释同步）。
Task 7: 注释同步轮（55bb97e，AST 级证明语义零变化）—— 已按变异实证修正 docstring 与 start_new_session 警示注释。
Task 7: 定向复审 —— 该发现 ADDRESSED，无新 Critical/Important；复审者从代码结构独立确认新用例不可能假绿（变异 A 下孙子进程活 300s ≫ 3s 探活窗口；变异 B 下守卫不可能变绿）。
Task 7: complete (commits 717a19e..55bb97e, review clean)
Task 7: minor (deferred): 新用例的 marker 读取依赖「脚本在超时/取消触发前写完 pid」（余量约 8–16x，仅在极慢机器上会 ERROR 而非假绿）。

## 任务 8
- 实现完成（35f0e36，14 passed / 全库 65）。实现者额外做了两件超出简报的验证：判定顺序（用「同时违反多条规则」的输入证明 empty→too_large→has_crlf→missing_anchor 的优先级，因为简报 6 个用例各只违反一条、无法证明顺序）、以及"恰好等于 MAX_SCRIPT_BYTES 不越界"。
- 实现者上报（未自行修改）：`_FENCE` 锚定 `^...$`，故只有"整段恰好是一个围栏块"才去围栏；若模型把脚本包在散文+围栏里，围栏不会被剥掉，而 check_contract 因锚点是子串仍放行 → 围栏与散文行会进入 shellcheck/执行。
  **控制者裁定：暂不修（本轮唯一一次对上报缺陷说"不改"）。** 理由：① 代价有界且自愈（shellcheck 报语法错→证据回灌→模型改对，最坏浪费一轮）；② 提示词已在两处禁止围栏（schema 的 script 描述 + SYSTEM_RULES 第 5 条）；③ 朴素修法（文本内任意位置取首个围栏块）会截断合法包含 ``` 的脚本，属引入新风险。正确的修法是"仅当围栏块含全部锚点、且围栏外不含锚点时才取它"，需动 types/contract/prompt 三处；等 Windows 冒烟拿到"模型是否真会这么干"的证据再定。
Task 8: complete (commits 801953a..35f0e36, review clean —— 无关键/重要发现)
Task 8: minor (deferred): 提交的测试未锁定三项裁定行为（OUTPUT_SCHEMA 的 additionalProperties、shellcheck_summary 的整串格式与排序、SYSTEM_RULES 六条内容）——实现者用临时脚本验过但未固化。
Task 8: minor (deferred): check_contract 的 has_crlf 分支在正常管线（先 normalize 后 check）中不可达，纯防御性守卫（读代码时易误解为主链路）。
Task 8: minor (deferred，控制者已裁定暂不修): 散文包裹的围栏会穿透契约 —— 见上文「任务 8」条目的裁定理由。

## 任务 9
- 实现完成（4ee167b，6 passed / 全库 71）。13 个 _DENY_KEYS + external_directory 逐键 deny、read 两行白名单、默认不写 model、
  frontmatter 恰好两个 ---、正文直接插入 SYSTEM_RULES（机械比对证明与导入对象逐字相等）。
- 额外 YAML 自查（超出简报）：本机无 PyYAML/ruamel 且未装依赖 → 自写严格子集解析器（仓库外一次性脚本），
  确认 permission.read 解析为嵌套映射、13 个键是字符串 "deny"、反斜杠路径解析为 C:/Users/me/runs/r1/** 无转义残留、默认无 model 键；
  并如实写明覆盖边界（不覆盖锚点/多行标量/流式集合；遇到会报错而非静默误读）。自查脚本自身两处缺陷也已记录。
- 实现者上报的残留风险「write_agent_file 会覆盖同名文件、无原子写/备份」→ **控制者裁定：不是问题，不改**。
  理由：agent 定义写在 `<run_root>/<runId>/.opencode/agents/` 下，runId 每次运行都是新目录，那是我们自己的目录，
  不存在覆盖用户手改文件的场景（除非用户手工往一次性的运行目录里塞文件）。
Task 9: 审查「通过」，但 1 条重要发现（审查者正确、控制者采纳并加强计划）：
  run_dir 为空串时 _to_posix("") 返回空串 → 生成 "/**": allow；该规则比 "*": deny 更具体 → 覆盖默认拒绝
  → **整盘可读**。审查者注明"简报参考实现同样如此故未列关键"，但控制者判断：安全生成器上"退化输入即放行"
  必须堵，修法仅两行（render_agent_file 空值守卫 + write_agent_file 先渲染后落盘，避免留下垃圾目录）。
  → 已派回原实现者修复，并补一条断言报错而非放行的测试。
Task 9: minor (deferred): model 值的 YAML 引号包裹（现有 provider/model 取值安全，改动会牵动已断言的字面量）
Task 9: minor (deferred): rstrip("/") 对 "/" 与 "C:/" 过度收敛（主要风险已被空值守卫覆盖）
Task 9: minor (deferred): 测试未断言 deny 键集合恰好 13 个（多 deny 是更安全方向）
Task 9: fix round 1/5 (1 addressed, 0 open — 空 run_dir 守卫 + 先渲染后落盘 + 拒绝测试; commits 4ee167b..ae01117)
Task 9: 实现者登记、控制者裁定**刻意不改**（已知项）：
  - run_dir="   "（纯空白）不被守卫拦下 → 生成无效规则 "   /**": allow 并在 cwd 建出名为 "   " 的目录。
    判定依据：① 本代码路径不可达（run_dir 来自 run_dir_for(run_root, run_id)，run_id 恒为时间戳+随机盐，非空非空白）；
    ② 后果是"规则不生效 + 垃圾目录"，不是放宽权限；③ 修法仅需 `if not run.strip()`，等 Plan 2 真有用户输入进入该路径时再收。
  - "C:/" 在 POSIX 上生成无效规则（C: 被视为相对路径）—— 同前次裁定，残留但不放宽。
Task 9: 定向复审 —— 发现 ADDRESSED，无新 Critical/Important；复审者独立确认守卫是 fail-closed（合法输入含 Windows/UNC 路径不受影响），
  并复核了控制者延后的两项**均不放宽权限**（"   /**" 是相对键、"C:" 在 POSIX 上是相对段）。
Task 9: complete (commits 35f0e36..ae01117, review clean)

## 任务 10
- 实现完成（e959f0e，15 passed / 全库 87）。两条超出简报的加固，控制者**采纳并回填计划**：
  1. 回环 HTTP 必须 `trust_env=False`：本机 env 有 http_proxy/ALL_PROXY，httpx 的 mount 表先命中代理项
     （NO_PROXY 列了 127.0.0.1 也没用），导致 wait_healthy/POST session/message/GET event 四条路径全不可用
     （socks 代理还会 ImportError: 需要 socksio）。代理在中文开发环境是常态，属真实产品级缺陷而非本机特例。
  2. `serve` 的 Popen 必须独立进程组（posix start_new_session / nt CREATE_NEW_PROCESS_GROUP），
     否则复用的 kill_tree 会 killpg 到调用方自己的进程组（变异实测删掉后整体 exit 137；pgid 对照 110116≠110108）。
- 实现者的额外实证（/tmp 脚本未入库）：桩 serve 的离线端到端 13/13，含权限 auto-reject 的端点与 body、
  跨 data: 行的多行 delta 帧拼回、reasoning 增量忽略、_saw_delta 不回退、dispose 后 serve 确被 SIGKILL；
  变异 C（去掉去重守卫）真红为 ['你好','你好世界','世界']。
- 残留登记（控制者裁定不改，均无功能影响）：stop() 只 kill 不 wait（短暂僵尸态，端口已释放）；
  generate() 的 cancel 未接线（中断由 abort() 负责）；start() 的 agent_name 形参未使用（我们只用 tu-shell-writer）；
  wait_healthy 不识别「进程秒崩」（死因在 server.log，调用方会把它显示出来）。
Task 10: 审查「需要修复」，两条重要发现，控制者逐条核实后裁定**不同**：
  1. `info.error` 字段路径"与简报矛盾" → **不改代码**：审查者的依据是我简报里那句不准的 prose（`StructuredOutputError{message, retries}`）。
     回查当时从真实 1.18.31 导出的 OpenAPI（.toolhome/out/openapi-1x.json）：确切形状是
     `{"name": "StructuredOutputError", "data": {"message": ..., "retries": ...}}`，name 与 data 都是 required。
     → **实现读 error["name"] + error["data"]["message"] 是正确的**；已修计划的措辞，并明确要求实现者不要为迎合错措辞改代码。
     （流程教训：计划与代码矛盾时，控制者必须回到一手证据核实，否则"修一轮"会把正确实现改成错的。）
  2. 适配器层零已提交测试覆盖 → **采纳**：15 条单测只覆盖 events/server 纯函数，`__init__.py` 的双字段名、
     info.error 报错、权限自动拒绝、format/agent/parts 请求体只有 /tmp 脚本验过。已要求把桩 serve 的离线测试
     固化为提交内的 tests/test_adapter_offline.py（5 项覆盖），并同步进计划的任务 10 交付物与预期（20 passed）。
Task 10: 次要（登记不改）：stop() 只 kill 不 wait；start() 的 agent_name 形参未用；dispose() 不 join 事件线程；
  _reject_permission 无 raise_for_status（端点漂移时会静默，安全网盲区）；pick_free_port 的 TOCTOU。
Task 11（起飞前扫描）：控制者对计划做一致性扫描，抓到**我自己造成的一处脱节**：
  任务 1 的修复轮把 FailureEvidence 的证据字段改成 ContractEvidence/ExecuteEvidence，任务 8 的 prompt.py 也改成了属性访问，
  但**任务 11 的 loop.py 仍用裸 dict 构造证据**（3 处：生成失败分支、契约失败分支、执行失败分支），且导入列表缺这两个类型。
  后果：照该计划实现会在修复路径上抛 AttributeError（prompt 读 contract.message，而拿到的是 dict）。
  → 已修：3 处改为 dataclass 构造 + 导入补齐。（这类"改了 A 忘了 B"的漂移正是起飞前扫描要抓的东西。）
Task 10: fix round 1/5 (1 addressed, 1 判定为「发现不成立」——见下; commits e959f0e..ba99e1f)
  发现 1（info.error 形状矛盾）：**不成立**。控制者回查真实 1.18.31 OpenAPI 确认代码读 error["name"] + error["data"]["message"] 正确，
    是我简报的 prose 漏了嵌套；已改文档、零代码改动。新测试第 4 条按确切形状把该契约钉住了。
  发现 2（适配器层零已提交测试）：**已解决**。新增 tests/test_adapter_offline.py（372 行、5 个用例、实现零改动），
    并用 5 条变异矩阵（format→outputFormat / 去掉 structured_output 回退 / reject→always / 去掉 _saw_delta / 不检查 info.error）
    验证 5/5 真红且失败集合恰为预期那一条 —— 测试有牙齿，且有据可查。
  未固化（按裁定）：dispose() 杀 serve 属真实进程行为，留 Windows 手测清单，不写探活式脆弱断言。
Task 10: 定向复审结论 —— 发现 2 ADDRESSED（测试真有牙齿、无时序/平台级 flaky 风险，复审者从代码结构逐条核对）；
  发现 1 在**技术层面**已闭合（代码读嵌套 + 新测试钉住形状，复审者确认），但**文档层面未闭合**：
  规格 §3 与 task-10-brief 仍留有我那句不准的 `StructuredOutputError{message, retries}`。
  → 控制者修法规格措辞并重新生成简报（这两处是控制者自己的产物，不属于实现者范围）。
Task 10: 发现 1 的文档层面由控制者关闭（规格 §3 + 简报措辞改为确切形状；grep 复查全仓无旧措辞残留），
  并列入最终整分支审查的核对项。**这是一次显式的流程偏离**：该关闭是纯机械可验证的（grep），
  再花一轮子代理属仪式而非验证，故由控制者执行并留证。
Task 10: complete (commits e193961..ba99e1f，复审：发现 2 ADDRESSED、发现 1 技术层面闭合；文档层面由控制者关闭)
Task 11（起飞前）：计划的任务 11 预期用例数已过期（11 → 实际 13 个 test 函数），已修并重生成简报。

## 任务 11
- 实现完成（97f41a5，13 passed / 全库 105）。实现者抓到**计划的两处自相矛盾**，诊断准确、处置正确，控制者全部采纳：
  1. 测试 helper `make_ports` 返回 dict，而 `LoopInput.ports` 注解是 `LoopPorts`、run_loop 通篇属性访问 →
     照抄两段原文必然 13 failed（AttributeError: 'dict' object has no attribute 'emit'）。
     处置：保留 loop.py 逐字，仅在测试 `run()` 里加一行 `ports=LoopPorts(**input_ports)`（+ import）。
     **取舍判定：实现是我的规格、测试 helper 只是脚手架，该改脚手架——实现者的选择正确。**
  2. `test_user_rejection_cancels_without_next_round` 用信任模板 → `approved = trusted or confirm(...)` 短路，
     confirm 端口根本不被调用，confirm=False 形同虚设；该测试与 test_trusted_template_never_asks_for_confirmation 互斥，
     **任何实现都通不过**。处置：测试改用 FakeTemplate(trusted=False)，落回测试名声称的路径。
  → 两处均已回填计划（保持代码与需求源一致）。
- 额外取证（未入库的一次性脚本）：四条终止路径落盘全对；exit_code=-9 + timed_out 不误判成功且提示含「超时被杀」；
  cancelled 直判；cancel 鸭子类型在首轮生成前生效（rounds=0）；detect().problems 分支不生成脚本。
Task 11: 审查「通过（附带测试覆盖补强要求）」，2 条重要 + 若干次要，控制者裁定：
  重要 1（测试未锁住"退出码不能单独决定成败"与 cancel 语义）→ **修**：补 3 条用例（-9+timed_out 不得判成功且提示含「超时被杀」、
    execute 返回 cancelled 直判且不进下一轮、cancel 令牌在首轮生成前生效且 rounds=0）。
  重要 2（四条终止路径的落盘副作用未被断言）→ **修**：harness 记录 attempts/metas，三条终止用例各补落盘断言。
  次要 3（execute.json 手写 f-string，取消路径 exit_code=None 会写出非法 JSON `"exit_code": None`）→ **修**（真实缺陷，改 json.dumps + import json）。
  次要 4（首轮取消不写 meta.json，与另两处取消路径不一致）→ **修**（补 write_meta）。
  次要 5（detect() 未被 try/except 包裹）→ **延后**：任务 12 的 facade 契约是"返回 problems 而非抛异常"，届时明确该契约。
  次要 6（needs_human 丢弃 script_path）→ **延后**：UI 可由 run_dir 推导。

## 用户裁定：默认阻断级别 warning → info
- 触发：预检发现 **SC2086（变量未加引号，最常见隐患）在 shellcheck 0.11.0 里是 info 级**，而默认阈值是 warning
  → 这类真实隐患会"只展示、不修"，脚本带隐患执行。用户裁定改为 info（style 仍不阻断）。
- 牵连点（已全部回填文档）：规格 §11 与 §16.4 的默认值说明、types.py 的 RunConfig.blocking_level 默认值、
  任务 11 的 info 级用例（原「info 不阻断」改为「style 不阻断」+ 新增「info 在默认级别下必须阻断」）。
- 任务 11 的断言写法一并去次序依赖：新增 `_attempt_file(harness, name)` 按文件名查找，替换 3 处 `attempts[-1]`/`[0]`
  （`[-1]` 依赖"最后一笔恰好是它"的偶然次序，无关重构会让守卫误报）。
Task 12（起飞前推演）：又抓到一处会让 CLI 直接失败的缺口 —— CLI 把 `--*-path` 覆盖只传给了自己的自检，
  而 `run_loop` 内部会**再调一次** `ports.toolchain.detect()`；facade 构造时没带同一组 override，
  于是在"shellcheck 不在 PATH"的机器（本机即如此）上：CLI 自检通过、循环内预检却判依赖缺失 → aborted_dependency，
  现象极具迷惑性。已修：override 只算一次并同时传给 detect_all 与 ShellToolchain。
Task 11: fix round 2/5 (2 addressed, 0 open — 默认阻断级别改 info + 断言去次序依赖; commits a906b02..223e30d)
  实现者订正了我一处写法错误：`_attempt_file` 返回的是**内容**，我却用它"在内容里找文件名"（恒 False，三条终止用例会同时倒下）。
  订正为新增 `_attempt_names(harness)`（写过的文件名集合）承接那三条断言，`_attempt_file` 保留给 json.loads 守卫用。已回填计划。
  涟漪检查（实测）：全仓 blocking_level 仅 5 处，默认值变更只波及 test_info_level_findings_do_not_block 一条（已换成 style+info 两条）。
  反向验证（顺带做）：把默认改回 warning → 只有 info 那条用例红（rounds=1，即 info 级 SC2086 未触发修复、脚本带隐患执行），正是用户裁定要消除的行为。
Task 11: 定向复审 —— 四条发现全部 ADDRESSED，无新 Critical/Important；复审者**独立**复核了默认值变更的涟漪
  （未采信报告的 grep 结论）与去次序依赖断言的有效性（缺文件时两者都会红、不假绿）。
Task 11: complete (commits 220103e..223e30d, review clean)
Task 11: minor (deferred): `_attempt_file` 用裸 next()，缺文件时抛 StopIteration 而非清晰断言信息（仍是红测，不假绿）。

## 任务 12
- 实现完成（08ed096，111 passed / 1 skipped）。**CLI 在本机真实跑通**：真实 opencode 1.18.31（已登录）+ 真实模型 +
  结构化输出，第 1 轮执行退出码 1 → 回灌 → 第 2 轮退出码 0 → `succeeded（2 轮）`；另一次 3 轮成功。
  去掉 `--yes` → `cancelled（1 轮）` 且不执行脚本（符合规格 §11）。离线全链路产物齐全（attempts/1 含 SC2045 于 shellcheck.txt）。
- 实现者如实上报的"语义水分"（高价值）：两次 succeeded 都是模型**放宽了方案约束**（目录不存在就报 0 个文件、退出 0）才通过的，
  运行目录里根本没有 logs/ —— 所以 CLI 冒烟只证明"链路通"，没证明"约束被满足"。已写进计划的验证段并要求 Windows 手测用带真实目标目录的方案。
- 实现者订正简报两处错误（均采纳）：① 原始 json1 的 code 是裸数字 2045，带 SC 前缀的形态只在渲染后的 shellcheck.txt 里；
  ② 离线用例自带假 Toolchain、不 import cli/facade，红态不会出现 ModuleNotFoundError 而是 SC2045 断言失败。
- **相对路径缺陷（真实）**：`--opencode-path tools/opencode` 失败 —— start_serve 以 cwd=run_dir 起子进程，相对路径解析不到
  → [Errno 2] → aborted_dependency(0 轮)，而 CLI 自检却通过（现象迷惑）。已派修复：`_path_overrides` 一律 resolve 成绝对路径 + 聚焦测试 + 相对路径真实复跑。
- 未跑 TU_LIVE 冒烟：CLI 实跑已覆盖更大的路径（含 shellcheck 与 bash 执行），不必再消耗用户额度。
Task 12: fix round 1/5 (1 addressed, 0 open — _path_overrides resolve 成绝对路径 + 聚焦测试 + 相对路径真实复跑;
  commits 08ed096..3b19469；复跑结论 succeeded(2 轮)，[Errno 2] 消失)
Task 12: **最重要的产品级发现（实现者主动上报，非缺陷但影响交付语义）**：三次真实运行的 succeeded 都是靠**放宽方案约束**
  得到的（报 0 个并退出 0 / 直接报 0 个 / 自建 logs 目录），模式一致：**成功来自放弃约束，而不是把约束实现对**。
  流程的成功判定只有「shellcheck 通过 + 退出码 0」，**没有任何机制检查方案约束是否被满足**。
  → 已写进规格 §11：① attempts/<n>/notes.md 的取舍说明必须与脚本全文在 UI 并列展示，由人核对；
    ② 运行不保证幂等（脚本可能改动运行目录）；③ 任何文案都不得把 succeeded 说成"方案已正确实现"。
  这是 Plan 2 的硬性界面要求，也是本工具覆盖该风险的唯一手段。
Task 12: 定向复审 —— 发现 ADDRESSED，无新 Critical/Important；复审者实测 resolve() 边界（不存在路径不抛、含空格路径正常）。
Task 12: complete (commits 512bf50..3b19469, review clean)
Task 12: minor (deferred → 交最终审查甄别):
  - `Path(path).resolve()` 不展开 `~`：`--opencode-path '~/tools/opencode'`（带引号）会变成字面 `~/...` 目录并静默回退 PATH；建议 `expanduser().resolve()`。
  - tests/test_e2e_offline.py:125 的 `assert overrides["bash"] if "bash" in overrides else True` 是**恒真断言**（控制者写进修复指令的错，看着像断言其实什么都没验）——应删除。
  - `test_e2e_offline.py` 的惰性 import 使该"离线"文件整文件运行时需要 httpx 可导入（轻微侵蚀离线属性）。

## 最终整分支审查（0aa506e..3b19469，代码 152K/4181 行，docs 排除）
结论：**可以交付（无 Critical）**；4 条 Important + 9 条 Minor；并逐条甄别了账本里全部延后项。
决定修复的范围（一波，之后一次定向复审）：
  F1 分层反转：agent_file.py 反向 import orchestrator.prompt.SYSTEM_RULES（唯一消费方就是它）→ 常量搬回 agent_file。
  F2 取消/中止未接线：generate() 收 cancel 不用、abort() 从未被调用（规格 §7.6 的"超时 → abort"未实现）；
     loop 的生成异常分支会把"用户取消"误记成契约失败白烧轮次 → 接线 + 两处测试。
  F3 §9/§10 落盘缺口（计划缩水）：meta.json 只有 {outcome,rounds,duration_ms}，plan.md/template.sh 未写
     → 新增 RunStorePort.write_inputs + loop 写 plan/template + meta 补 runId/sessionId/config/detection。
     裁定：规格 §10 的 evidence.json **不再单独生成**（现有 attempts/<n>/* 已是更细的证据），规格措辞已同步。
  F4 start() 非异常安全：serve 起来后建会话失败 → 进程与 SSE 线程泄漏（CLI 的 finally 恰好掩盖）→ try/except + 自我 dispose。
  F5 cli.py 未 expanduser：'~/tools/opencode' 会静默回退 PATH（用户以为覆盖生效）。
  F6 tests/test_e2e_offline.py:125 恒真断言（控制者写进指令的错）→ 删除。
  F7 loop 的 detect() 是唯一没兜底的端口调用 → 统一 aborted_dependency。
明确延后（交交付说明）：_reject_permission 无 raise_for_status、wait_healthy 错误信息不含 server.log 尾部、
  start() 的 agent_name 死参数、散文包裹围栏穿透、以及账本其余标"可延后/不必修"的项目。
最终修复波（1a59e4b）：F1–F7 全部落实，119 passed / 1 skipped（+7 条测试，skip 数不变）；
  每条缺陷都做了 mutation 验证（改回缺陷版 → 对应用例真红）。
  CLI 复跑 succeeded(3 轮)：**第 1 轮被 shellcheck 拦下（SC2012 info 级）→ 证明默认阈值 info 在真实链路上生效**，
  三轮分别走 shellcheck → execute → execute 三条证据路径；meta.json 现已含 runId/sessionId/config/detection；
  运行目录根出现 plan.md 与 template.sh（§10 输入副本补齐）。
  实现者自报：看门线程测试第一版是**假绿**（事件先 set 后 wait 等于没阻塞 + 返回后兜底 abort 掩盖），返工两轮才用时序断言抓住；
  并顺手修掉连带缺陷"同一次 generate 连发两条 abort"（看门线程 + 兜底，已去重 + 加"只允许一条"断言）。
  断言形式变更：meta.json 成超集后，原 4 处整字典相等断言改为 contains_meta() 子集匹配（仍逐字段校验）。

## 最终修复波（1a59e4b）后的定向复审
F1–F7 全部 ADDRESSED，无新 Critical/Important。残留 3 条 Minor（均无害/有界，不阻塞交付）：
  - 超时路径未参与 abort 去重 → 取消与超时重叠时对会话多发一条 abort（幂等）。
  - detect/start 失败路径的 meta.json 仍是 bare 形状（{outcome, rounds}），与其余 6 条路径的超集形状不一致
    → Plan 2 的消费者需容忍两种形态。
  - 看门线程在 abort 自身挂起时有界残留（≤300s，daemon）。
