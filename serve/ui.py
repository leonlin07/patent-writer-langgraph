"""Gradio 交互前端：粘贴/上传交底书 → 生成 → 进度条 → 展示四件套。

设计：直接复用 service.run_pipeline（带 progress_callback 驱动 gr.Progress），
独立运行，无需先起 FastAPI 服务。

用法：
    python serve/ui.py          # 默认 127.0.0.1:7860
"""
from __future__ import annotations

import os
import sys
import tempfile

# 保证从任意目录运行都能导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import gradio as gr
except ImportError:  # pragma: no cover
    raise ImportError(
        "运行 Gradio 前端需先安装: pip install gradio"
    )

from service import run_pipeline  # noqa: E402

# 节点名 → 中文展示名（进度条提示）
NODE_LABELS = {
    "disclosure": "生成交底书",
    "parse": "解析交底书",
    "search": "专利检索",
    "outline": "生成大纲",
    "claims": "撰写权利要求",
    "description": "撰写说明书",
    "abstract": "撰写摘要",
    "diagram": "生成附图说明",
    "merge": "合并文档",
}
TOTAL_NODES = len(NODE_LABELS)


def _generate(
    input_text: str,
    input_file,
    do_search: bool,
    save_individual: bool,
    progress=gr.Progress(),
):
    """执行生成，进度条实时反映节点完成情况。"""
    if not input_text.strip() and input_file is None:
        return "请粘贴交底书文本或上传文件。", "", {}

    # 准备输入文件
    tmp_dir = tempfile.mkdtemp(prefix="patent_ui_")
    if input_file is not None:
        input_path = input_file.name
    else:
        input_path = os.path.join(tmp_dir, "交底书.txt")
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(input_text)

    output_dir = os.path.join(tmp_dir, "output")

    done = []

    def on_progress(node_name: str) -> None:
        if node_name not in done:
            done.append(node_name)
        ratio = min(1.0, len(done) / TOTAL_NODES)
        label = NODE_LABELS.get(node_name, node_name)
        progress(ratio, desc=f"已完成：{label}")

    try:
        result = run_pipeline(
            input_path=input_path,
            output_dir=output_dir,
            do_search=do_search,
            save_individual=save_individual,
            progress_callback=on_progress,
        )
    except Exception as e:  # noqa: BLE001
        return f"生成失败：{e}", "", {}

    if not result.get("success"):
        return "生成未完全成功：" + "；".join(result.get("errors", [])), "", {}

    files = result.get("files", {})
    summary = "\n".join(f"- {k}: {v}" for k, v in files.items())
    main_file = files.get("full_document", "")
    return summary, main_file, files


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="专利写作 Agent") as demo:
        gr.Markdown("# 专利写作 Agent（LangGraph）\n输入技术交底书，自动生成完整专利四件套。")

        with gr.Row():
            with gr.Column():
                input_text = gr.Textbox(
                    label="技术交底书（粘贴文本）",
                    lines=12,
                    placeholder="发明名称、技术领域、背景技术、技术问题、技术方案、有益效果…",
                )
                input_file = gr.File(label="或上传交底书文件（.txt/.docx/.pdf）")
                with gr.Row():
                    do_search = gr.Checkbox(label="专利检索（需配置 SERPAPI/EXA Key）", value=False)
                    save_individual = gr.Checkbox(label="分别保存各部分文档", value=True)
                btn = gr.Button("生成专利", variant="primary")

            with gr.Column():
                summary = gr.Textbox(label="产物清单", lines=8)
                download = gr.File(label="完整专利文档（下载）")

        btn.click(
            fn=_generate,
            inputs=[input_text, input_file, do_search, save_individual],
            outputs=[summary, download],
        )

    return demo


if __name__ == "__main__":
    build_ui().launch(
        server_name=os.getenv("UI_HOST", "127.0.0.1"),
        server_port=int(os.getenv("UI_PORT", "7860")),
    )
