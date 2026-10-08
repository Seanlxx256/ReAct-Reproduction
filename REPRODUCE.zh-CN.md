# HotpotQA 复现实验

默认模型：`gpt-4.1-mini-2025-04-14`。保留原 Notebook 的六示例提示词、随机种子 233、最多 7 轮和 temperature=0。当前版本输出上限改为 256 token，避免 v3 中出现的动作截断；可用 `--max-tokens 100` 保留旧上限。
使用 Chat Completions 替换已停用的 text-davinci-002，并增加简短系统指令控制续写格式。这是模型替换后的方法复现，不能视为原论文分数的精确复现。
Wikipedia 使用实时网页，检索内容会随日期变化。模型不使用内置搜索或 function calling，而是输出原仓库的文本动作，由 WikiEnv 执行。

## Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-hotpotqa.txt
.\.venv\Scripts\python.exe run_hotpotqa.py --check
```

在本机设置 `OPENAI_API_KEY` 环境变量，然后运行；不要把密钥写进代码或提交到 Git。

```powershell
# 20 条检查
.\.venv\Scripts\python.exe run_hotpotqa.py --samples 20 --output results/hotpotqa-mini-20
# 同配置断点续跑
.\.venv\Scripts\python.exe run_hotpotqa.py --samples 20 --output results/hotpotqa-mini-20 --resume
# 原 Notebook 的 500 条抽样
.\.venv\Scripts\python.exe run_hotpotqa.py --samples 500 --output results/hotpotqa-mini-500
```

20 条和 500 条使用相同打乱顺序，20 条是 500 条的前缀。每个输出目录保存 `config.json`（模型、索引、参数与文件哈希）、`episodes.jsonl`（逐条轨迹、答案、EM/F1、API 输出和 token 用量）、`summary.json`（已完成样本汇总）。
异常时停止，不把网络/API 故障记为模型答错。续跑跳过已保存的完整样本；被中断的当前样本从头重跑，可能产生额外调用费用。
若文件最后一行因系统崩溃损坏，先备份并修复该行再续跑；脚本不会静默忽略损坏记录。
100 token 保留原设置；若输出频繁截断，可用 `--max-tokens 256` 和新的输出目录评估，并报告该设置变化。

## 模型对照

```powershell
.\.venv\Scripts\python.exe run_hotpotqa.py --model gpt-4.1-2025-04-14 --samples 500 --output results/hotpotqa-gpt41-500
# 本地 vLLM 等提供 OpenAI 兼容 Chat Completions 时：
.\.venv\Scripts\python.exe run_hotpotqa.py --model Qwen/Qwen3-30B-A3B-Instruct-2507 --base-url http://localhost:8000/v1 --samples 20 --output results/hotpotqa-qwen-20
```

本地服务同样需要 `OPENAI_API_KEY`，无鉴权服务可设置为任意非空占位值。托管服务的模型名以服务商实际提供的名称为准；需支持 stop、temperature 和 max_tokens 参数。
`run_hotpotqa.py` 仅运行 ReAct。七种 QA 对照方法使用下面的 `run_qa.py`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest test_hotpotqa -v
```

离线验证不调用模型或 Wikipedia，不产生实验成绩。正式评估前应确认英文 Wikipedia 和模型服务可访问。

## Wikipedia 403 检查

请求已添加可识别的 User-Agent，符合 Wikimedia 对客户端标识的建议。可通过 `WIKIPEDIA_USER_AGENT` 设置自己的项目标识和联系方式。
先单独检查 Wikipedia，无需 API key，也不会调用模型：

```powershell
.\.venv\Scripts\python.exe run_hotpotqa.py --check-wiki
```

若仍返回 403，需检查本机代理或网络出口是否被 Wikipedia 拒绝，不能把该故障当作模型答错。修复代码会改变实验配置哈希，已有实验应保留并使用新的输出目录，不能混合新旧配置续跑。

## HotpotQA / FEVER 七种方法对照

`run_qa.py` 支持 `standard`、`cot`、`act`、`react`、`cot_sc`、`cot_sc_to_react`、`react_to_cot_sc`。
默认跑前四种；`--methods all` 跑七种。全部采用 question-only 设置，标准答案只用于评分。Standard/CoT/CoT-SC 不检索 Wikipedia。
提示示例来自仓库对应 JSON：HotpotQA 六例，FEVER 三例。为了适配聊天模型，增加模式相关系统指令；这与原 PaLM completion 接口不同。
ReAct/Act 每次输出上限 256，CoT 上限 512；这是显式记录的实现选择，不声称原论文采用相同 token 上限。
CoT-SC 默认 21 条采样、temperature=0.7，以标准化答案投票；无效/空答案不参与投票，但计入全部采样次数，平票选最先采样的答案。
CoT-SC→ReAct 在最多票数严格少于 n/2 时切换；ReAct→CoT-SC 仅在步数耗尽且未提交答案时切换。
各组合方法独立生成自己的轨迹，不复用其他方法的付费结果。
HotpotQA 默认 7 步；FEVER 默认 5 步，并从仓库全部 9,999 条 dev 抽样，修正 Notebook 的固定 range(7405)。

```powershell
# 离线检查七种方法和抽样索引，不调用 API
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods all --check
.\.venv\Scripts\python.exe run_qa.py --dataset fever --methods all --check
# 推荐先跑四组各 20 条
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods standard cot act react --samples 20 --output results/qa-v4-20
# 正式四组各 500 条，单独输出目录
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods standard cot act react --samples 500 --output results/qa-v4-500
# 七种方法各 500 条：包含大量 CoT-SC 采样，费用高于单组 ReAct
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods all --samples 500 --output results/qa-all-500
# FEVER 七种方法
.\.venv\Scripts\python.exe run_qa.py --dataset fever --methods all --samples 500 --output results/qa-all-500
# 中断后，对原命令追加 --resume；例如
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods all --samples 500 --output results/qa-all-500 --resume
```

结果目录为 `<output>/<dataset>/<method>/`。每组保存配置、轨迹及汇总；`<output>/<dataset>/comparison.json` 汇总本次请求的方法。
`summary.json` 包含 prompt/completion/cached/total token 数、截断次数、格式补救次数和步数耗尽次数。
未完成样本的已返回模型调用保存在 `interruptions.jsonl`，不计入完成样本成绩；计算总费用时还需计入这些调用。
增加方法时可指定新增方法并沿用输出根目录；改变样本数、预算或代码后需使用新目录。不得在同一实验中混合配置。
v3 目录保留原结果，不会自动重新评分或合并进新实验。

```powershell
.\.venv\Scripts\python.exe -m unittest test_hotpotqa test_qa -v
```

此实现覆盖论文 Table 1 的七种提示方法，不包含 PaLM 原模型、微调/模型规模实验、图 2 的完整采样数扫描、表 2 的人工标注研究或监督学习 SoTA 训练。
500 条是本仓库的小规模复现方案，不能声称评估规模与论文主表完全相同。
