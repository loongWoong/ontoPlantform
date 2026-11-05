# 标准适配插件 P0 补全执行计划（C0+C1）

> 依据：[`standards-plugin-completion-review-v1.md`](./standards-plugin-completion-review-v1.md)（缺口 G-01…G-25）。
> 本计划只做 **C0（立即对齐/低风险修复）+ C1（P0 合同补完）**；C2（治理/SHACL）与 C3（SPARQL/导入/ABox/类层次）留待下轮。
> 执行方式：Lead 冻结决策与文档/CI；A/B/C 三个开发工作流并行/串行推进；D 独立验证；最后 Lead 全量复核。

---

## 0. 已冻结决策（未收到反对即按此执行，写入文档）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 插件标识 | **保留短 ID `owl-rdf` / `skos` 为 canonical**；增加 `aliases=("onto-standards-owl",)` / `("onto-standards-skos",)` 兼容架构清单名；架构 §4 注明“`onto-standards-*` 为包名，插件 ID 为短名” | 已发布测试/CLI/README 使用短名；别名满足外部按架构名对接，零破坏 |
| D2 | IRI SSOT | 以实现的 `https://ontoplatform.local/ws/{workspace}/onto/{name}/v{version}#` 为准，更新架构 §2.4/§5 | 含 ontology name、避免 workspace 主机名限制；测试/golden 已锁定 |
| D3 | manifest 字段 | **以架构 §2.2 为准补齐**：`to_dict()` 增加 `formats:{"out","in"}`、`requires:{"ontoplatform","extras"}`、`config_schema`、`aliases`；原扁平 `formats_out/in` 保留为 dataclass 字段（兼容）；新增能力-方法一致性检查与 `requires` 兼容检查 | 插件“身份证”完整，第三方可就地声明平台版本/配置 |
| D4 | P0 范围 | **Turtle-only、TBox-only**；JSON-LD 与 ABox 移到 P1/P2；架构 §4/§7 更新；`scope/include_abox` 显式拒绝（非静默） | 诚实交付；避免“看起来支持” |
| D5 | API 动词 | 导出保留 `GET`（只读可缓存）；`POST /standards/export`、`validate`、`artifacts` 明确 P1，架构 §5 改口径 | GET 导出无副作用，HTTP 语义更合理 |
| D6 | `Platform.standards` | 移到 dataclass 可选字段（默认 `None`），API/CLI 显式报 `NotFoundError("standards not available")`；bootstrap 仍注入 | 降低库使用方直接构造 `Platform` 的 breaking 风险 |
| D7 | 插件异常 | 新增 `AdapterInvocationError(PlatformError)`，`errors.classify` → **502 / `plugin_failure` / 不可重试**；use case 捕获后审计 `standards.export.failed` 再抛 | 符合架构 §2.3/§8 DoD 5 |

---

## 1. 目标与验收

**目标**：把复核缺口中的 S1/S2 项（契约字段、能力校验、异常隔离、scope 拒绝、跨插件解耦、golden/数量断言、CI interop、文档同步、provenance/语言标签、跨进程确定性）全部补上。

**C0+C1 完成判定**：
1. `python -m pytest -q` 全绿（期望 ≥715 + 新增），**0 环境 skip**（本地装 `[interop]`）；
2. 覆盖率 ≥70%（CI 门禁命令可运行）；
3. 契约测试新增：manifest 新字段/别名/能力-方法/requires、scope 负例、异常隔离 502+审计、golden 逐字节、关键三元组数量、语言标签、provenance headers；
4. `git diff` 仅涉及本计划声明文件；`dependencies=[]`；Kernel 未动；
5. 文档与实现一致（架构 §2.2/§4/§5/§6/§7、plan-v2 O-3、审计 B2 注记、README 能力矩阵）。

---

## 2. 任务分解与写作用域

| ID | 任务 | 写作用域 | 依赖 |
|---|---|---|---|
| **task-10 / A** | SPI + Registry 契约：manifest 扩展（formats 嵌套/requires/config_schema/aliases）、能力-方法校验、requires 兼容、别名解析、`AdapterInvocationError`（502 映射） | `src/onto_platform/semantic/spi.py`、`semantic/registry.py`、`src/onto_platform/errors.py`、`tests/test_semantic_registry.py`、`tests/test_semantic_spi.py` | — |
| **task-11 / B** | 导出器：`formats.py` 共享格式/scope 校验（解耦 skos→owl）、scope/include_abox 显式拒绝、语言标签、golden 文件 + 数量断言、`skos` 独立 | `src/onto_platform/semantic/formats.py`、`semantic/turtle.py`、`semantic/owl.py`、`semantic/skos.py`、`tests/golden/standards/*.ttl`、`tests/test_standards_export.py`、`tests/test_standards_turtle.py` | — |
| **task-12 / C** | 接线：use case 异常隔离 + 失败审计 + provenance metadata、API 502/headers/charset、`Platform.standards` 可选守卫、CLI 摘要/别名兼容、测试 | `src/onto_platform/application/standards_usecases.py`、`interfaces/api.py`、`interfaces/cli.py`、`tests/test_standards_api.py`、`tests/test_standards_cli.py` | task-10, task-11 |
| **task-13 / D** | 独立验证：契约测试更新（新字段/别名/异常/scope/语言标签/headers）、跨进程确定性（PYTHONHASHSEED 子进程）、golden 独立核对、全量+覆盖率复跑、报告补注 | `tests/test_standards_contract.py`、`docs/standards-plugin-implementation-report.md` | task-12 |
| **Lead** | 计划/文档对齐、CI（`[dev,interop]` + pytest-cov）、`Platform.standards` 可选字段、最终集成与全量 | `docs/standards-plugin-completion-plan-v1.md`、`docs/standards-plugin-architecture.md`、`docs/plan-v2.md`、`docs/ontology-implementation-review-v1.md`、`README.md`、`.github/workflows/ci.yml`、`pyproject.toml`、`src/onto_platform/bootstrap.py` | — |

**禁止事项**：A/B/C 互不写对方文件；不得改 `tests/conftest.py`；不得新增运行时依赖；不得 commit；Lead 负责最终 `git diff` 复核。

---

## 3. 各任务验收细则

### A（SPI/Registry）
- `AdapterManifest` 增加 `requires: dict`、`config_schema: dict`、`aliases: tuple[str,...]`；`to_dict()` 输出架构字段并保留旧字段（旧消费方不炸）；
- `manifest_problems` 校验：requires 仅允许 `ontoplatform`（`>=x.y` 形式）+ `extras: [str]`；config_schema 为 dict；aliases 合法且不含自身；
- `registry`：能力-方法一致性（`export/validate/query/import_` 可调用）、`requires.ontoplatform` 与 `onto_platform.__version__` 兼容检查（不兼容 → `status="invalid"` + problems 含 "incompatible"，get 抛 `AdapterIncompatible`）、别名解析（`get("onto-standards-owl")` 可用，`names()` 只返回 canonical）、重复别名冲突报错；
- `errors.py`：新增 `AdapterInvocationError` → `("plugin_failure", 502, False)`；
- 单测：新字段 to_dict、requires 兼容/不兼容、能力缺失、别名解析、旧 manifest 兼容（不传新字段仍合法）。

### B（导出器）
- 新 `semantic/formats.py`：`SUPPORTED_FORMATS`、`check_export_format`、`check_scope`；`owl.py`/`skos.py` 只从 formats 导入（`owl.check_export_format` 保留再导出，兼容旧测试）；
- `check_scope`：仅接受 `""`/`"tbox"`；`abox`/`both`/`include_abox=True` → `AdapterError`；
- 语言标签：含 CJK 的 label/comment/altLabel 用 `lang="zh"`，纯 ASCII 不加（确定性）；
- golden：`tests/golden/standards/owl-rdf-v2.ttl`、`skos-v2.ttl`（固定 fixture 生成，逐字节断言）+ 数量断言（2 类/4 数据属性/2 对象属性/3 概念/altLabel 数）；
- 解耦验证：删除/隔离 owl 后 skos 仍可 import/export（测试用 `importlib` 或仅断言 skos 模块不再 import owl）。

### C（接线）
- use case `export`：`try/except PlatformError: raise`；`except Exception` → 审计 `standards.export.failed`（adapter/error 类型）→ 抛 `AdapterInvocationError`；
- 返回结果 metadata 合并：`workspace_id`、`ontology_version`、`schema_fingerprint`、`adapter_version`、`actor`；**不加时间戳**（保持确定性）；
- API：`_send_bytes` 支持额外 headers；导出响应加 `X-Adapter-Name/X-Schema-Fingerprint/X-Ontology-Version/X-Adapter-Version`；Turtle Content-Type 带 `charset=utf-8`；`platform.standards is None` → 404；
- CLI：`--out` 摘要含 provenance；别名 `--adapter onto-standards-owl` 可用；
- 测试：异常插件 → 502 + 审计、headers、metadata、别名、无 standards 的 404。

### D（独立验证）
- 契约测试按新合同更新，并新增：manifest 嵌套字段/别名、requires、能力-方法、scope 负例、异常 502+审计、headers/charset、语言标签、golden 独立比对、跨进程确定性；
- 运行：目标套件 + 全量 + 覆盖率；记录准确数字；报告追加“C0+C1 收尾”章节；
- 不修改实现代码。

---

## 4. 执行顺序

```text
Lead: 冻结决策 + 写本计划 + 文档/CI 准备（并行）
  ├─ A (task-10) ──┐
  ├─ B (task-11) ──┤ 并行
  │                └─ C (task-12) ── D (task-13) ── Lead 最终复核
  └─ Lead: docs/CI/pyproject/bootstrap（C 完成前完成）
```

- **Wave 1**：A + B 并行；Lead 同步做文档/CI/pyproject/bootstrap 可选字段。
- **Wave 2**：C 依赖 A/B 完成后启动（需 A 的错误类与 B 的 scope/语言标签）。
- **Wave 3**：D 依赖 C 完成后启动；Lead 在 D 期间准备最终 `git diff` 复核。
- **Wave 4**：Lead 跑全量+覆盖率、核对文档与实现、更新复核状态。

---

## 5. 风险与回退

| 风险 | 缓解 |
|---|---|
| manifest `to_dict` 新增字段破坏旧测试 | 保留旧键；测试只增不改语义；D 复核 |
| 语言标签改变 golden/字节确定性 | golden 同步生成；确定性测试锁死 |
| `Platform.standards` 默认 None 引入 API 属性错误 | 只在 API/CLI 入口访问并先判空；C 加 404 测试 |
| 别名与 canonical 名称冲突 | registry 注册时校验 aliases 唯一；测试覆盖 |
| 并发写作用域重叠 | 任务表声明 + 禁止跨文件；Lead 最终 diff 复核 |

---

## 6. 不在本计划（明确留待下轮）

- C2（P1）：workspace 启停/reload/health、SHACL 导出+校验、`validate` API/CLI、artifacts、前端按钮、插件示例；
- C3（P2/P3）：ABox 快照、SPARQL、导入、类层次/词表层级模型、语义检索、RDFS/OWL-RL 推理。

*本计划由 Lead 维护；执行结果追加到 `standards-plugin-completion-review-v1.md` 的“补全状态”与 QA 报告附录。*

---

## 7. 执行结果（已完成，2026-11）

| 任务 | 状态 | 结果 |
|---|---|---|
| task-10 / A | ✅ completed | manifest 新字段、能力-方法校验、requires 兼容、别名解析、`AdapterInvocationError`；新增 38 测试 |
| task-11 / B | ✅ completed | `formats.py` 解耦、scope 拒绝、CJK `@zh`、golden 2 份 + 数量断言；+16 测试 |
| task-12 / C | ✅ completed | 异常隔离 + `standards.export.failed` 审计 + 502；provenance 四响应头/charset/metadata；standards 可选守卫；+8 测试 |
| task-13 / D | ✅ completed | 独立契约 30 项（golden 反解析、跨进程 `PYTHONHASHSEED`、异常双层、别名字节一致）；报告追加 D-0…D-8 |
| Lead | ✅ completed | 计划/架构 §2.2/§2.4/§4/§5/§6/§7/§8、plan-v2 O-3、审计 B2 注记、README、CI `[dev,interop]`、pyproject pytest-cov、`Platform.standards` 可选、`semantic/__init__` 导出 |

**最终验证**：`python -m pytest -q` → **794 passed, 0 skipped, 65.60s**（Lead 复跑）；D 独立复跑 794/0/64.37s；覆盖率 **79.94%**（门禁 70% 通过）；`git status` 仅本轮声明文件，未 commit。

**决策落地**：D1 短名 canonical + 架构名别名；D2 实现 IRI 为 SSOT；D3 manifest 按架构补齐且保留旧扁平键；D4 P0 Turtle/TBox-only，JSON-LD/ABox 后移；D5 导出保持 GET；D6 `Platform.standards` 可选；D7 插件异常 502 + 审计。

**遗留**：C2（workspace 启停/reload/health、SHACL、validate/artifacts、前端、插件示例）与 C3（ABox/SPARQL/导入/类层次/词表层级/JSON-LD）未做；use case 返回的 `adapter` 字段在别名场景保留调用方名、metadata/响应头为 canonical（P1 统一）。
