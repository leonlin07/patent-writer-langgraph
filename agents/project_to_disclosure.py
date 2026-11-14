"""
项目材料 → 技术交底书 生成器

用途：
    当你手上只有一份"项目"（代码仓库 / 论文 PDF / 技术报告 / 学位论文），
    而没有符合专利撰写习惯的《技术交底书》时，用本模块先把项目材料
    逆向提炼成标准交底书，再交给 PatentWriterAgent 走正常流水线。

设计要点：
    1. 智能选材：优先 README/论文/文档，其次核心源码，自动跳过依赖目录与二进制。
    2. Map-Reduce：项目材料通常远超 LLM 单次上下文，先分片摘要再综合成稿，
       避免 InputParser 中 `content[:3000]` 硬截断导致的信息丢失。
    3. 格式对齐：输出严格遵循 InputParser 的正则字段顺序
       （发明名称→技术领域→背景技术→技术问题→技术方案→有益效果→具体实施方式），
       保证下游零改造即可解析。
"""
import os
import re
import sys
import time
from typing import List, Dict, Optional, Tuple

# Windows 控制台可能为 GBK，强制 UTF-8 输出，避免中文打印崩溃/写入乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from utils.llm_client import get_llm_client
from logging_config import get_logger
logger = get_logger(__name__)

# 可读取的文本类扩展名
SUPPORTED_EXT = {
    ".md", ".txt", ".rst", ".tex",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
    ".sh", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
}

# 明确的"高价值文档"文件名关键词（优先读取，且给更大配额）
DOC_HINTS = ("readme", "架构", "设计", "说明", "report", "论文", "paper",
             "thesis", "技术方案", "design", "doc", "guide")

# 需要跳过的目录
# 注意：output/data/results 等是"产出物"而非"源头材料"，混入会污染交底书
# （实测本项目就曾把 output/ 里已生成的专利文档当成技术材料提炼）
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".pytest_cache",
    "venv", ".venv", "env", ".env", "dist", "build", "target", "out",
    ".idea", ".vscode", ".workbuddy", ".gradle", ".mypy_cache", "vendor",
    "site-packages", "packages", ".next", ".nuxt", "coverage", "logs",
    "output", "outputs", "results", "runs", "checkpoints", "ckpt",
    "assets", "static", "examples", "tests", "test", "__tests__",
}

# 文件名层面的噪声过滤：下游专利产出物、迁移产物、配置样板等
NOISE_FNAME = (
    "权利要求", "说明书", "摘要", "交底书", "专利", "patent",
    "migration", ".min.", "package-lock", "yarn.lock", "poetry.lock",
)

# 跳过的文件后缀（二进制/大资源）
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".so", ".dll", ".exe", ".bin",
    ".pkl", ".pt", ".pth", ".ckpt", ".onnx", ".npy", ".npz", ".h5",
    ".mp4", ".avi", ".mp3", ".wav", ".ttf", ".otf", ".woff", ".woff2",
    ".pyc", ".pyo", ".lock", ".min.js", ".map",
}

# 交底书字段顺序（必须与 InputParser 的正则顺序一致）
DISCLOSURE_FIELDS = [
    "发明名称", "技术领域", "背景技术", "技术问题", "技术方案", "有益效果", "具体实施方式",
]


class ProjectToDisclosure:
    """把项目材料逆向提炼为技术交底书"""

    def __init__(self, provider: Optional[str] = None):
        self.llm = get_llm_client(provider)

    # ------------------------------------------------------------------ 选材

    def collect(self, root: str, max_files: int = 40,
                max_chars_each: int = 8000) -> List[Dict[str, str]]:
        """
        扫描项目目录，收集可供提炼的文本材料

        Args:
            root: 项目根目录
            max_files: 最多收集文件数
            max_chars_each: 单文件最多读取字符数

        Returns:
            [{"path": 相对路径, "content": 文本, "priority": 优先级}, ...]
        """
        items: List[Dict[str, str]] = []

        for dirpath, dirnames, filenames in os.walk(root):
            # 原地过滤跳过目录
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext in SKIP_EXT or ext not in SUPPORTED_EXT:
                    continue
                if any(n in fn.lower() for n in NOISE_FNAME):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(full) > 2 * 1024 * 1024:  # 跳过 >2MB
                        continue
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except Exception:
                    continue

                text = text.strip()
                if len(text) < 50:  # 过短无信息量
                    continue

                rel = os.path.relpath(full, root)
                # 高价值文档优先级 0，源码 1
                priority = 0 if any(h in fn.lower() for h in DOC_HINTS) else 1

                items.append({
                    "path": rel.replace("\\", "/"),
                    "content": text[:max_chars_each],
                    "priority": priority,
                })

        # 文档优先，其次按信息量降序
        items.sort(key=lambda x: (x["priority"], -len(x["content"])))
        return items[:max_files]

    @staticmethod
    def _pack(items: List[Dict[str, str]], chunk_chars: int = 12000) -> List[str]:
        """把材料打包成若干不超过 chunk_chars 的文本块（尽量不切断单个文件）"""
        chunks, cur = [], ""
        for it in items:
            block = f"\n===== 文件: {it['path']} =====\n{it['content']}\n"
            if cur and len(cur) + len(block) > chunk_chars:
                chunks.append(cur)
                cur = ""
            if len(block) > chunk_chars:  # 单文件超大则硬切
                block = block[:chunk_chars]
            cur += block
        if cur.strip():
            chunks.append(cur)
        return chunks

    # ------------------------------------------------------------- Map 阶段

    def _summarize_chunk(self, chunk: str, hint: str, idx: int, total: int) -> str:
        """对单个材料块做技术要点抽取"""
        system_prompt = (
            "你是资深专利代理师兼技术专家，擅长从工程材料中提炼可申请专利的技术要点。"
            "只输出技术事实，不要客套话。"
        )
        prompt = f"""以下是某技术项目材料的一部分（第{idx}/{total}部分）。
{f'项目方向提示：{hint}' if hint else ''}

{chunk}

请从中提炼**可用于申请专利**的技术要点，严格按以下小标题输出（缺失的写"未提及"）：

【技术主题】一句话说明这部分在做什么
【现有做法及其不足】这部分材料提到/暗示的现有技术方案，以及它的问题
【关键技术手段】具体实现手段、算法、结构、流程步骤（尽量具体，含参数、模块名、步骤编号）
【创新点】与常规做法相比的区别性技术特征
【达到的效果】性能、精度、效率、成本等方面的改善（有数字必须保留数字）

不要输出除上述小标题以外的内容。"""

        return self.llm.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=2000,
        )

    # ----------------------------------------------------------- Reduce 阶段

    def _synthesize(self, summaries: List[str], hint: str) -> str:
        """把各块要点综合成完整技术交底书"""
        joined = "\n\n".join(f"--- 材料片段{i}要点 ---\n{s}" for i, s in enumerate(summaries, 1))

        system_prompt = """你是资深专利代理师，正在为发明人撰写《技术交底书》。
要求：
1. 只依据给定材料，严禁编造未提及的技术细节、参数或效果。
2. 材料未提及的内容，写"待补充"，不要臆造。
3. 使用规范专利术语，技术方案要写成"手段-功能"对应的完整链条。
4. 严格使用下列字段标题，顺序不得改变，每个字段标题以中文顿号结尾的冒号收尾。
5. 全文不要出现"附图"二字（避免干扰下游解析器）。"""

        prompt = f"""请基于以下技术要点，撰写一份标准《技术交底书》。
{f'项目方向提示：{hint}' if hint else ''}

=== 技术要点汇总 ===
{joined}
=== 汇总结束 ===

请严格按以下格式输出（字段顺序不可更改，标题必须完全一致）：

发明名称：
（格式为"一种基于/用于……的……方法及系统"，25字以内）

技术领域：
（本发明所属的技术领域，2-3句）

背景技术：
（现有技术方案及其缺陷，200-400字，分点说明现有做法的问题）

技术问题：
（本发明要解决的技术问题，100-200字，以"如何……"或"……的问题"表述）

技术方案：
（详细描述技术方案：先写方法的完整步骤S1/S2/S3……，再写对应的系统模块组成；
每步写清楚"做什么"和"怎么做"；包含关键参数、算法、数据流向；600-1200字）

有益效果：
（与现有技术相比的优势，分3-6条，有量化数据必须保留）

具体实施方式：
（至少一个完整实施例，描述具体实现过程、配置、流程，400-800字）

请直接输出交底书正文，不要包含任何解释性文字。"""

        return self.llm.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.4,
            max_tokens=4000,
        )

    # ---------------------------------------------------------------- 清洗

    @staticmethod
    def _clean(text: str) -> str:
        """清洗 LLM 输出，保证字段标题干净、格式统一"""
        # 去掉包裹代码块
        text = re.sub(r"^```[a-zA-Z]*\n", "", text.strip())
        text = re.sub(r"\n```$", "", text.strip())

        # 统一字段标题：允许 ## / ** 装饰，归一为"字段名："
        for f in DISCLOSURE_FIELDS:
            text = re.sub(
                rf"^[\s\-#*_]*{f}[\s*_]*[：:]\s*",
                f"{f}：\n",
                text,
                flags=re.MULTILINE,
            )
            text = re.sub(
                rf"^[\s\-#*_]*\*\*{f}\*\*[\s*_]*[：:]\s*",
                f"{f}：\n",
                text,
                flags=re.MULTILINE,
            )

        # 删除多余空行（保留段落分隔）
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"

    @staticmethod
    def validate(text: str) -> Tuple[bool, List[str]]:
        """
        校验交底书是否可被下游 InputParser 正确解析

        Returns:
            (是否合格, 问题列表)
        """
        problems: List[str] = []
        for f in DISCLOSURE_FIELDS:
            if not re.search(rf"{f}\s*[：:]", text):
                problems.append(f"缺少字段「{f}」")

        # 检查是否有实质内容（防止空壳）
        for f in ("技术方案", "技术问题"):
            m = re.search(rf"{f}\s*[：:]\s*([\s\S]+?)(?=\n[^\n]*[：:]|$)", text)
            if m and len(m.group(1).strip()) < 30:
                problems.append(f"字段「{f}」内容过短（<30字），可能未生成实质内容")

        return (len(problems) == 0), problems

    # ---------------------------------------------------------------- 主流程

    def generate(
        self,
        project_dir: str,
        output_path: str,
        hint: str = "",
        max_files: int = 40,
        verbose: bool = True,
    ) -> Dict:
        """
        从项目目录生成技术交底书

        Args:
            project_dir: 项目根目录
            output_path: 交底书输出路径（.txt）
            hint: 项目方向提示（例如"基于视觉语言模型的红队测试 Agent"）
            max_files: 最多读取文件数
            verbose: 是否打印进度

        Returns:
            {"success": bool, "path": str, "files_used": int, "problems": [...]}
        """
        result = {"success": False, "path": "", "files_used": 0, "problems": []}

        if verbose:
            logger.info(f"\n{'='*60}")
            logger.info("项目材料 → 技术交底书")
            logger.info(f"{'='*60}\n")
            logger.info(f"[1/4] 扫描项目材料: {project_dir}")

        items = self.collect(project_dir, max_files=max_files)
        if not items:
            result["problems"].append("未从项目目录收集到任何可读的文本材料")
            return result

        total_chars = sum(len(i["content"]) for i in items)
        if verbose:
            logger.info(f"      [OK] 收集 {len(items)} 个文件，共约 {total_chars} 字")
            for it in items[:5]:
                logger.info(f"           - {it['path']} ({len(it['content'])}字)")
            if len(items) > 5:
                logger.info(f"           ... 等共 {len(items)} 个")

        chunks = self._pack(items)
        if verbose:
            logger.info(f"\n[2/4] 分块提炼技术要点（{len(chunks)} 块）...")

        summaries: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            try:
                s = self._summarize_chunk(chunk, hint, i, len(chunks))
                summaries.append(s)
                if verbose:
                    logger.info(f"      [OK] 第 {i}/{len(chunks)} 块完成（{len(s)}字）")
            except Exception as e:
                # 单块失败不致命，跳过即可
                logger.warning(f"      [WARN] 第 {i}/{len(chunks)} 块失败: {e}")

        if not summaries:
            result["problems"].append("所有材料块提炼失败，请检查 API 配置或网络")
            return result

        if verbose:
            logger.info(f"\n[3/4] 综合生成技术交底书...")

        raw = self._synthesize(summaries, hint)
        disclosure = self._clean(raw)

        if verbose:
            logger.info(f"      [OK] 交底书生成完成（{len(disclosure)}字）")

        ok, problems = self.validate(disclosure)
        result["problems"] = problems

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(disclosure)

        result["path"] = output_path
        result["files_used"] = len(items)
        result["success"] = ok or len(problems) <= 1  # 容忍 1 个次要字段缺失

        if verbose:
            logger.info(f"\n[4/4] 校验与落盘")
            if problems:
                for p in problems:
                    logger.warning(f"      [WARN] {p}")
            logger.info(f"      [OK] 已保存: {output_path}")

        return result


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="从项目材料（代码仓库/论文/文档）逆向生成技术交底书"
    )
    parser.add_argument("--project", "-p", required=True, help="项目根目录路径")
    parser.add_argument("--output", "-o", default="data/技术交底书.txt",
                        help="交底书输出路径（默认: data/技术交底书.txt）")
    parser.add_argument("--hint", default="",
                        help="项目方向提示，例如 '基于视觉语言模型的红队测试Agent'")
    parser.add_argument("--max-files", type=int, default=40, help="最多读取文件数")
    parser.add_argument("--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()

    if not os.path.isdir(args.project):
        logger.error(f"错误: 项目目录不存在: {args.project}")
        sys.exit(1)

    gen = ProjectToDisclosure()
    r = gen.generate(
        project_dir=args.project,
        output_path=args.output,
        hint=args.hint,
        max_files=args.max_files,
        verbose=not args.quiet,
    )

    if r["success"]:
        logger.info(f"\n完成！交底书: {r['path']}")
        logger.info(f"下一步: python main.py --input \"{r['path']}\" --output output/你的专利名")
        sys.exit(0)
    else:
        logger.warning("\n失败:")
        for p in r["problems"]:
            logger.info(f"  - {p}")
        sys.exit(1)


if __name__ == "__main__":
    main()
