# Patent Writer（LangGraph 版）

基于 LangGraph 状态图编排的专利写作智能体：从《技术交底书》或一个技术项目目录，
自动生成完整的专利申请文件（摘要 / 权利要求书 / 说明书 / 附图说明）。

## 一、架构与功能边界

项目分三层，边界清晰、单向依赖：

```
┌─────────────────────────────────────────────────────────┐
│ 入口层：main.py（CLI） / service.py（服务化 API）          │
│   负责：参数解析、结果汇总、断点续跑调度                    │
└───────────────────────┬─────────────────────────────────┘
                        │ 调用
┌───────────────────────▼─────────────────────────────────┐
│ 编排层：workflow/（LangGraph 状态图）                      │
│   state.py  共享状态 PatentState（TypedDict + reducer）   │
│   nodes.py  9 个节点（取 state 入参 → 写回 state）        │
│   graph.py  build_graph() 组装有向图                       │
│   负责：流程编排、并行扇出、条件分支、断点 checkpointer     │
└───────────────────────┬─────────────────────────────────┘
                        │ 调用
┌───────────────────────▼─────────────────────────────────┐
│ 业务层：agents/（8+1 个生成模块）                          │
│   input_parser / patent_searcher / outline_generator      │
│   claims_writer / description_writer / abstract_writer    │
│   diagram_generator / document_merger / project_to_disclosure
│   负责：每个步骤的具体实现（LLM 提示词、结构化解析、落盘）  │
│                                                          │
│ 基础设施：utils/（llm_client / search_client / json_utils）│
│ 配置：config.py（环境变量驱动，显式加载项目根 .env）        │
└─────────────────────────────────────────────────────────┘
```

**交互关系**：编排层 `workflow/nodes.py` 只做「从 state 取入参 → 调用 `agents/*` → 把结果写回 state」，
不重复实现业务逻辑；业务层 `agents/*` 不感知 LangGraph，可被串行（`sequential.py`）或图编排（`workflow/`）复用。

## 二、目录结构

```
patent-writer-langgraph/
├── agents/                  # 业务逻辑（解析/检索/大纲/权项/说明书/摘要/附图/合并/项目→交底书）
│   └── project_to_disclosure.py
├── utils/                   # 基础设施（llm_client / search_client / json_utils）
├── workflow/                # LangGraph 编排层（state / nodes / graph）
├── config.py                # 统一配置（环境变量驱动）
├── main.py                  # 统一 CLI 入口（LangGraph 版，兼容两套旧参数）
├── sequential.py            # 串行兼容层（原 PatentWriterAgent 类）
├── service.py               # 服务化 API 封装（预留扩展点）
├── logging_config.py        # 统一日志/可观测性（预留扩展点）
├── test_graph.py            # 离线测试（MockLLM，无需 API key）
├── scripts/                 # 交付辅助脚本（docx 生成、附图绘制、查新报告等）
├── data/                    # 输入样例（技术交底书）
├── output/                  # 输出目录（gitignore）
├── .env.example             # 配置样例
├── .gitignore
├── requirements.txt         # 合并依赖
└── pyproject.toml           # 项目元数据 + 可选依赖分组 + CLI 入口点
```

## 三、快速开始

```bash
# 1. 安装（建议使用原 conda 环境 guiji_agent_patent，py3.10）
D:/SoftWare/Conda/envs/guiji_agent_patent/python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. 配置：复制 .env.example 为 .env，填入 SILICONFLOW_API_KEY
cp .env.example .env

# 3. 离线测试（不调真实 LLM，验证图拓扑 + 数据流 + 断点续跑）
D:/SoftWare/Conda/envs/guiji_agent_patent/python.exe test_graph.py
```

## 四、使用方式

### 命令行（统一入口 main.py）

```bash
# 阶段B：已有交底书 → 完整专利
python main.py --input data/技术交底书.txt --output output/我的专利 --no-search

# 阶段A+B：项目目录一键到底
python main.py --project ../my_project --hint "基于视觉语言模型的多模态红队测试" --output output/rededit

# 只出交底书（阶段A）
python main.py --project ../my_project --hint "..." --stage disclosure

# 断点续跑
python main.py --input data/技术交底书.txt --output output --checkpoint ckpt.sqlite --thread-id job-1
```

### Python API（service.py，服务化入口）

```python
from service import run_pipeline, generate_patent, generate_disclosure

# 一键流水线
result = run_pipeline(input_path="data/技术交底书.txt", output_dir="output", do_search=False)
print(result["success"], result["files"], result["errors"])

# 仅阶段A / 仅阶段B
generate_disclosure(project_dir="../my_project", output_path="data/交底书.txt", hint="...")
generate_patent(input_path="data/交底书.txt", output_dir="output")
```

### 串行兼容层（sequential.py）

```python
from sequential import PatentWriterAgent

agent = PatentWriterAgent(output_dir="output")
result = agent.write_patent("data/技术交底书.txt", do_search=False)
```

## 五、图拓扑

```
START ─(route_entry)─► disclosure（可选：项目→交底书）─► parse ─(route_after_parse)─► search ─► outline
                                                                                          │
                             ┌────────────────────────────────────────────────────────────┤
                             ▼              ▼              ▼              ▼
                         claims       description     abstract       diagram      （并行扇出）
                             │              │              │              │
                             └──────► verify（合规校验）◄──┘              │
                                          │ (条件边)                       │
                                          ▼                               │
                                        merge ◄───────────────────────────┘ ─► END
```

相比原串行实现：权项/说明书/摘要/附图 4 次 LLM 调用改为同一超步并行执行；状态外置到 `PatentState`，
配合 `--checkpoint`/`--thread-id` 支持断点续跑。claims/description 生成后先经 `verify` 节点做合规校验
（权项编号/术语一致/说明书支持三项，规则实现 0 token，默认仅标记不阻断）。

## 六、服务化与评测（已落地）

### 6.1 HTTP 服务（`serve/`）

| 接口 | 说明 |
|---|---|
| `POST /generate` | 提交生成任务，立即返回 `job_id`（后台异步执行） |
| `GET /status/{job_id}` | 查询进度（状态 + 已完成节点） |
| `GET /result/{job_id}` | 查询结果（四件套文件路径） |
| `GET /health` | 健康检查（容器探活） |

```bash
python serve/run.py                       # 启动 HTTP 服务（默认 0.0.0.0:8000）
python serve/ui.py                        # 启动 Gradio 交互界面
uvicorn serve.app:app --port 8000         # 标准 uvicorn 方式
docker build -t patent-writer-langgraph . && docker run -p 8000:8000 patent-writer-langgraph
```

### 6.2 Agentic RAG 与质量门禁

- `agents/retrieval_agent.py`：查询改写 + 迭代检索 + 相关性评估（`RAG_ENABLED` 开关，默认关闭）
- `agents/verifier.py`：verify 节点三项合规校验（规则 0 token，可选 LLM-as-judge，`VERIFY_USE_LLM`/`VERIFY_ENFORCE` 控制）
- `utils/llm_client.py`：token usage 采集 + 成本估算（`get_usage()` / `estimate_cost()`）

### 6.3 评测与可观测

- `eval/metrics.py`：自建测试集，产出成功率/耗时/合规率/成本 → `eval/report.md`
- 实测（4 题，Qwen3.5-122B-A10B）：成功率 100%、单次成本 ¥0.067、文件完整 100%

### 6.4 扩展点一览

| 扩展点 | 位置 | 说明 |
|---|---|---|
| 接口封装 | `serve/app.py` | FastAPI 三接口 + 健康检查 + CORS |
| 异步任务 | `serve/tasks.py` | 线程池 + 每 job 独立 checkpoint + 断点续跑 |
| 配置外置 | `config.py` | 全环境变量驱动；`.env` 显式加载项目根，不依赖 CWD |
| 日志与可观测 | `logging_config.py` | 统一 logger，可切换 JSON 结构化输出 / 接 OTel |
| 测试注入 | `workflow/graph.py` | `build_graph(node_overrides=...)` 支持替换节点注入故障 |
| CLI 入口点 | `pyproject.toml` | `patent-writer = "main:main"` |

## 七、兼容性风险与处理

1. **msgpack 序列化告警**：checkpoint 落盘/读回时 `TechnicalInfo` 等 dataclass 会被 LangGraph 标记为
   「未注册类型」，当前版本（1.2.x）仅警告、功能正常；未来版本若默认拦截，需在 checkpointer 上注册
   `allowed_msgpack_modules` 或改用 dict 存状态（见 `workflow/state.py`）。

2. **`scripts/` 下的顶层 import**：辅助脚本移入子目录后，直接 `python scripts/xxx.py` 时项目根不在
   `sys.path`。已对依赖项目模块的 `generate_from_project.py` 加 `sys.path` 引导；其余 docx/matplotlib 脚本
   只依赖第三方库，不受影响。

3. **`agents/*` 顶层平铺导入**：`agents/`、`utils/`、`config.py` 保持原「顶层平铺」导入风格
   （`from agents.xxx import`），要求项目根目录在 `sys.path` 上（CLI 直接运行即满足）。若后续打包为
   独立库，需统一改为 `patent_writer_agent.*` 前缀（见 `pyproject.toml` 的 packages 声明）。

4. **`.env` 与 CWD**：`config.py` 已改为 `load_dotenv(BASE_DIR / ".env")`，从任意目录运行/部署不再依赖 CWD。

5. **`data/` 历史遗留**：原 `data/` 含少量 GBK 误编码的乱码文件名样例，合并时未随迁，需补充样例可直接
   放置于 `data/`。

## 八、已知注意事项

- 自动生成的初稿必须经专利代理师/发明人逐项复核，尤其权利要求书的保护范围与说明书的支持关系。
- 专利检索需在 `.env` 配置 `SERPAPI_API_KEY` / `EXA_API_KEY`，未配置时检索节点实际不执行
  （会提示「勿据此判新颖性」）。
- 默认模型 `Qwen/Qwen3.5-122B-A10B` 为思考模型，`config.py` 已默认关闭 thinking，避免推理 token
  挤占输出预算导致内容为空。
