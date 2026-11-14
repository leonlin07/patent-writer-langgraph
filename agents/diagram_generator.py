"""
图表生成器模块
生成附图说明和图表描述
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from agents.input_parser import TechnicalInfo
from utils.llm_client import get_llm_client
from utils.json_utils import extract_json
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class Diagram:
    """附图数据结构"""
    figure_number: int
    title: str
    description: str
    components: List[Dict[str, str]]  # 标号和名称的列表
    diagram_type: str  # "flowchart", "system", "structure", etc.


@dataclass
class DiagramDocument:
    """附图文档数据结构"""
    invention_title: str
    diagrams: List[Diagram]
    brief_description: str  # 附图简要说明


class DiagramGenerator:
    """图表生成器类"""
    
    def __init__(self):
        """初始化图表生成器"""
        self.llm_client = get_llm_client()
    
    def generate(
        self,
        technical_info: TechnicalInfo,
        num_diagrams: int = 3
    ) -> DiagramDocument:
        """
        生成附图说明
        
        Args:
            technical_info: 技术信息
            num_diagrams: 附图数量
            
        Returns:
            DiagramDocument对象
        """
        diagrams = []
        
        # 确定需要的附图类型
        diagram_types = self._determine_diagram_types(technical_info, num_diagrams)
        
        for i, diag_type in enumerate(diagram_types, 1):
            diagram = self._generate_diagram(technical_info, i, diag_type)
            diagrams.append(diagram)
        
        # 生成附图简要说明
        brief_description = self._generate_brief_description(diagrams)
        
        return DiagramDocument(
            invention_title=technical_info.invention_name,
            diagrams=diagrams,
            brief_description=brief_description
        )
    
    def _determine_diagram_types(
        self,
        technical_info: TechnicalInfo,
        num_diagrams: int
    ) -> List[str]:
        """
        确定附图类型
        
        Args:
            technical_info: 技术信息
            num_diagrams: 附图数量
            
        Returns:
            附图类型列表
        """
        solution = technical_info.technical_solution.lower()
        
        types = []
        
        # 判断是否需要系统架构图
        if any(kw in solution for kw in ['系统', '装置', '设备', '模块', '组件']):
            types.append('system')
        
        # 判断是否需要流程图
        if any(kw in solution for kw in ['方法', '步骤', '流程', '过程']):
            types.append('flowchart')
        
        # 判断是否需要结构图
        if any(kw in solution for kw in ['结构', '部件', '组成']):
            types.append('structure')
        
        # 补充默认类型
        default_types = ['system', 'flowchart', 'structure']
        while len(types) < num_diagrams:
            for dt in default_types:
                if dt not in types:
                    types.append(dt)
                    break
                if len(types) >= num_diagrams:
                    break
        
        return types[:num_diagrams]
    
    def _generate_diagram(
        self,
        technical_info: TechnicalInfo,
        figure_num: int,
        diagram_type: str
    ) -> Diagram:
        """
        生成单个附图说明
        
        Args:
            technical_info: 技术信息
            figure_num: 图号
            diagram_type: 附图类型
            
        Returns:
            Diagram对象
        """
        type_names = {
            'system': '系统架构图',
            'flowchart': '方法流程图',
            'structure': '结构示意图',
            'schematic': '原理示意图',
            'block': '模块框图',
        }
        
        title = type_names.get(diagram_type, f'图{figure_num}')
        
        prompt = f"""请为以下发明生成图{figure_num}（{title}）的详细说明。

发明名称: {technical_info.invention_name}
技术方案: {technical_info.technical_solution[:800]}

要求：
1. 说明该图展示的内容
2. 列出图中各标号（1, 2, 3...）对应的部件/步骤名称
3. 简要说明各部件/步骤的功能或作用

以JSON格式返回：
{{
  "description": "图的详细说明",
  "components": [
    {{"number": "1", "name": "部件名称", "function": "功能描述"}},
    {{"number": "2", "name": "部件名称", "function": "功能描述"}}
  ]
}}"""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=1000
            )
            
            import json
            import re
            
            # 提取JSON（稳健解析：容忍裸换行、围栏、尾逗号）
            data = extract_json(response)
            if data and isinstance(data, dict):
                return Diagram(
                    figure_number=figure_num,
                    title=title,
                    description=data.get('description', ''),
                    components=data.get('components', []),
                    diagram_type=diagram_type
                )
        except Exception as e:
            logger.warning(f"图{figure_num}生成失败: {e}")
        
        # 返回默认图说明
        return self._get_default_diagram(technical_info, figure_num, title, diagram_type)
    
    def _get_default_diagram(
        self,
        technical_info: TechnicalInfo,
        figure_num: int,
        title: str,
        diagram_type: str
    ) -> Diagram:
        """获取默认图说明"""
        if diagram_type == 'system':
            components = [
                {"number": "1", "name": "输入模块", "function": "接收输入数据"},
                {"number": "2", "name": "处理模块", "function": "执行核心处理"},
                {"number": "3", "name": "输出模块", "function": "输出处理结果"},
            ]
            description = f"图{figure_num}为本发明的系统架构图，展示了{technical_info.invention_name}的整体结构和各模块之间的连接关系。"
        
        elif diagram_type == 'flowchart':
            components = [
                {"number": "1", "name": "步骤S1", "function": "初始化/输入"},
                {"number": "2", "name": "步骤S2", "function": "核心处理步骤"},
                {"number": "3", "name": "步骤S3", "function": "输出结果"},
            ]
            description = f"图{figure_num}为本发明的方法流程图，展示了{technical_info.invention_name}的主要执行步骤。"
        
        else:
            components = [
                {"number": "1", "name": "主体部件", "function": "核心组成部分"},
                {"number": "2", "name": "辅助部件", "function": "辅助功能实现"},
            ]
            description = f"图{figure_num}为本发明的结构示意图，展示了{technical_info.invention_name}的主要结构组成。"
        
        return Diagram(
            figure_number=figure_num,
            title=title,
            description=description,
            components=components,
            diagram_type=diagram_type
        )
    
    def _generate_brief_description(self, diagrams: List[Diagram]) -> str:
        """生成附图简要说明"""
        lines = []
        
        for diagram in diagrams:
            line = f"图{diagram.figure_number}为{diagram.title}；"
            lines.append(line)
        
        return '\n'.join(lines)
    
    def to_text(self, document: DiagramDocument) -> str:
        """
        将附图说明转换为文本格式
        
        Args:
            document: 附图文档
            
        Returns:
            文本格式的附图说明
        """
        lines = ["附图说明", ""]
        
        # 简要说明
        lines.append(document.brief_description)
        lines.append("")
        
        # 详细说明
        for diagram in document.diagrams:
            lines.append(f"如图{diagram.figure_number}所示，{diagram.description}")
            
            if diagram.components:
                lines.append("")
                lines.append(f"图{diagram.figure_number}中：")
                for comp in diagram.components:
                    func = comp.get('function', '')
                    if func:
                        lines.append(f"{comp['number']}-{comp['name']}，{func}；")
                    else:
                        lines.append(f"{comp['number']}-{comp['name']}；")
            
            lines.append("")
        
        return '\n'.join(lines)
    
    def to_markdown(self, document: DiagramDocument) -> str:
        """
        将附图说明转换为Markdown格式
        
        Args:
            document: 附图文档
            
        Returns:
            Markdown格式的附图说明
        """
        lines = [
            f"# 附图说明 - {document.invention_title}",
            ""
        ]
        
        # 简要说明
        lines.append("## 附图简要说明")
        lines.append("")
        lines.append(document.brief_description)
        lines.append("")
        
        # 详细说明
        lines.append("## 附图详细说明")
        lines.append("")
        
        for diagram in document.diagrams:
            lines.append(f"### 图{diagram.figure_number} - {diagram.title}")
            lines.append("")
            lines.append(diagram.description)
            
            if diagram.components:
                lines.append("")
                lines.append("**图中标号说明：**")
                lines.append("")
                lines.append("| 标号 | 名称 | 功能 |")
                lines.append("|------|------|------|")
                for comp in diagram.components:
                    func = comp.get('function', '-')
                    lines.append(f"| {comp['number']} | {comp['name']} | {func} |")
            
            lines.append("")
        
        return '\n'.join(lines)
    
    def generate_mermaid_diagram(self, diagram: Diagram) -> str:
        """
        生成Mermaid图表代码
        
        Args:
            diagram: 附图对象
            
        Returns:
            Mermaid图表代码
        """
        if diagram.diagram_type == 'flowchart':
            lines = ["```mermaid", "flowchart TD"]
            
            for comp in diagram.components:
                node_id = f"S{comp['number']}"
                name = comp['name']
                lines.append(f"    {node_id}[{name}]")
            
            # 添加连接
            for i in range(len(diagram.components) - 1):
                from_node = f"S{diagram.components[i]['number']}"
                to_node = f"S{diagram.components[i+1]['number']}"
                lines.append(f"    {from_node} --> {to_node}")
            
            lines.append("```")
            return '\n'.join(lines)
        
        elif diagram.diagram_type == 'system':
            lines = ["```mermaid", "graph LR"]
            
            for comp in diagram.components:
                node_id = f"M{comp['number']}"
                name = comp['name']
                lines.append(f"    {node_id}[{name}]")
            
            # 添加连接
            for i in range(len(diagram.components) - 1):
                from_node = f"M{diagram.components[i]['number']}"
                to_node = f"M{diagram.components[i+1]['number']}"
                lines.append(f"    {from_node} --> {to_node}")
            
            lines.append("```")
            return '\n'.join(lines)
        
        else:
            return f"<!-- 图{diagram.figure_number}: {diagram.title} - 请使用专业绘图工具绘制 -->"
