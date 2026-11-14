"""
文档合并器模块
整合所有部分生成完整专利文档
"""
import os
from typing import Optional
from datetime import datetime
from agents.claims_writer import ClaimsDocument
from agents.description_writer import DescriptionDocument
from agents.abstract_writer import AbstractDocument
from agents.diagram_generator import DiagramDocument


class DocumentMerger:
    """文档合并器类"""
    
    def __init__(self, output_dir: str = "output"):
        """
        初始化文档合并器
        
        Args:
            output_dir: 输出目录
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def merge(
        self,
        invention_title: str,
        claims_doc: ClaimsDocument,
        description_doc: DescriptionDocument,
        abstract_doc: AbstractDocument,
        diagram_doc: DiagramDocument,
        format: str = "markdown"
    ) -> str:
        """
        合并所有文档
        
        Args:
            invention_title: 发明名称
            claims_doc: 权利要求书
            description_doc: 说明书
            abstract_doc: 摘要
            diagram_doc: 附图说明
            format: 输出格式（markdown, text, docx）
            
        Returns:
            输出文件路径
        """
        if format == "markdown":
            return self._merge_to_markdown(
                invention_title, claims_doc, description_doc,
                abstract_doc, diagram_doc
            )
        elif format == "text":
            return self._merge_to_text(
                invention_title, claims_doc, description_doc,
                abstract_doc, diagram_doc
            )
        elif format == "docx":
            return self._merge_to_docx(
                invention_title, claims_doc, description_doc,
                abstract_doc, diagram_doc
            )
        else:
            raise ValueError(f"不支持的格式: {format}")
    
    def _merge_to_markdown(
        self,
        invention_title: str,
        claims_doc: ClaimsDocument,
        description_doc: DescriptionDocument,
        abstract_doc: AbstractDocument,
        diagram_doc: DiagramDocument
    ) -> str:
        """合并为Markdown格式"""
        from agents.claims_writer import ClaimsWriter
        from agents.description_writer import DescriptionWriter
        from agents.abstract_writer import AbstractWriter
        from agents.diagram_generator import DiagramGenerator
        
        lines = [
            f"# 专利申请文件 - {invention_title}",
            "",
            f"**生成时间**: {datetime.now().strftime('%Y年%m月%d日')}",
            "",
            "---",
            "",
        ]
        
        # 摘要
        lines.append("# 摘要")
        lines.append("")
        lines.append(abstract_doc.abstract_text)
        lines.append("")
        lines.append(f"**摘要附图**: 图1")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 权利要求书
        lines.append("# 权利要求书")
        lines.append("")
        lines.append(f"**权利要求总数**: {claims_doc.total_count}项")
        lines.append("")
        
        for claim in claims_doc.claims:
            lines.append(claim.full_text)
            lines.append("")
        
        lines.append("---")
        lines.append("")
        
        # 说明书
        lines.append("# 说明书")
        lines.append("")
        
        # 技术领域
        tech_field_sec = next(
            (s for s in description_doc.sections if "技术领域" in s.title), None
        )
        if tech_field_sec:
            lines.append(f"## {tech_field_sec.title}")
            lines.append("")
            lines.append(tech_field_sec.content)
            lines.append("")
        
        # 背景技术
        bg_art_sec = next(
            (s for s in description_doc.sections if "背景技术" in s.title), None
        )
        if bg_art_sec:
            lines.append(f"## {bg_art_sec.title}")
            lines.append("")
            lines.append(bg_art_sec.content)
            lines.append("")
        
        # 发明内容
        inv_content_sec = next(
            (s for s in description_doc.sections if "发明内容" in s.title), None
        )
        if inv_content_sec:
            lines.append(f"## {inv_content_sec.title}")
            lines.append("")
            lines.append(inv_content_sec.content)
            lines.append("")
        
        # 附图说明
        lines.append("## 附图说明")
        lines.append("")
        lines.append(diagram_doc.brief_description)
        lines.append("")
        
        for diagram in diagram_doc.diagrams:
            lines.append(f"**图{diagram.figure_number}** - {diagram.title}")
            lines.append("")
            lines.append(diagram.description)
            lines.append("")
            
            if diagram.components:
                lines.append("图中标号说明：")
                for comp in diagram.components:
                    func = comp.get('function', '')
                    if func:
                        lines.append(f"- **{comp['number']}** - {comp['name']}：{func}")
                    else:
                        lines.append(f"- **{comp['number']}** - {comp['name']}")
            lines.append("")
        
        # 具体实施方式
        impl_sec = next(
            (s for s in description_doc.sections if "实施方式" in s.title or "实施例" in s.title), None
        )
        if impl_sec:
            lines.append(f"## {impl_sec.title}")
            lines.append("")
            lines.append(impl_sec.content)
            lines.append("")
        
        lines.append("---")
        lines.append("")
        
        # 写入文件
        safe_title = "".join(c for c in invention_title if c.isalnum() or c in (' ', '-', '_')).strip()
        filename = f"{safe_title}_专利文档.md"
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        return filepath
    
    def _merge_to_text(
        self,
        invention_title: str,
        claims_doc: ClaimsDocument,
        description_doc: DescriptionDocument,
        abstract_doc: AbstractDocument,
        diagram_doc: DiagramDocument
    ) -> str:
        """合并为纯文本格式"""
        lines = [
            f"专利申请文件 - {invention_title}",
            "",
            f"生成时间: {datetime.now().strftime('%Y年%m月%d日')}",
            "",
            "=" * 60,
            "",
        ]
        
        # 摘要
        lines.append("摘  要")
        lines.append("")
        lines.append(abstract_doc.abstract_text)
        lines.append("")
        lines.append("摘要附图：图1")
        lines.append("")
        lines.append("=" * 60)
        lines.append("")
        
        # 权利要求书
        lines.append("权利要求书")
        lines.append("")
        lines.append(f"权利要求总数: {claims_doc.total_count}项")
        lines.append("")
        
        for claim in claims_doc.claims:
            lines.append(claim.full_text)
            lines.append("")
        
        lines.append("=" * 60)
        lines.append("")
        
        # 说明书
        lines.append("说 明 书")
        lines.append("")
        lines.append(invention_title)
        lines.append("")
        
        for section in sorted(description_doc.sections, key=lambda s: s.order):
            lines.append(section.title)
            lines.append("")
            lines.append(section.content)
            lines.append("")
        
        # 附图说明
        lines.append("附图说明")
        lines.append("")
        lines.append(diagram_doc.brief_description)
        lines.append("")
        
        for diagram in diagram_doc.diagrams:
            lines.append(f"图{diagram.figure_number}：{diagram.title}")
            lines.append(diagram.description)
            lines.append("")
            
            if diagram.components:
                for comp in diagram.components:
                    func = comp.get('function', '')
                    if func:
                        lines.append(f"  {comp['number']}-{comp['name']}，{func}")
                    else:
                        lines.append(f"  {comp['number']}-{comp['name']}")
            lines.append("")
        
        lines.append("=" * 60)
        lines.append("")
        
        # 写入文件
        safe_title = "".join(c for c in invention_title if c.isalnum() or c in (' ', '-', '_')).strip()
        filename = f"{safe_title}_专利文档.txt"
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        return filepath
    
    def _merge_to_docx(
        self,
        invention_title: str,
        claims_doc: ClaimsDocument,
        description_doc: DescriptionDocument,
        abstract_doc: AbstractDocument,
        diagram_doc: DiagramDocument
    ) -> str:
        """合并为Word文档格式"""
        try:
            from docx import Document
            from docx.shared import Pt, Inches
            from docx.enum.text import WD_ALIGN_PARAGRAPH
        except ImportError:
            raise ImportError("请安装 python-docx: pip install python-docx")
        
        doc = Document()
        
        # 标题
        title = doc.add_heading(f'专利申请文件 - {invention_title}', 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_paragraph(f'生成时间: {datetime.now().strftime("%Y年%m月%d日")}')
        doc.add_page_break()
        
        # 摘要
        doc.add_heading('摘要', 1)
        doc.add_paragraph(abstract_doc.abstract_text)
        doc.add_paragraph(f'摘要附图：图1')
        doc.add_page_break()
        
        # 权利要求书
        doc.add_heading('权利要求书', 1)
        doc.add_paragraph(f'权利要求总数: {claims_doc.total_count}项')
        
        for claim in claims_doc.claims:
            p = doc.add_paragraph(claim.full_text)
            p.paragraph_format.space_after = Pt(12)
        
        doc.add_page_break()
        
        # 说明书
        doc.add_heading('说明书', 1)
        doc.add_heading(invention_title, 2)
        
        for section in sorted(description_doc.sections, key=lambda s: s.order):
            doc.add_heading(section.title, 2)
            doc.add_paragraph(section.content)
        
        # 附图说明
        doc.add_heading('附图说明', 2)
        doc.add_paragraph(diagram_doc.brief_description)
        
        for diagram in diagram_doc.diagrams:
            doc.add_heading(f'图{diagram.figure_number} - {diagram.title}', 3)
            doc.add_paragraph(diagram.description)
            
            if diagram.components:
                table = doc.add_table(rows=1, cols=3)
                table.style = 'Light Grid Accent 1'
                
                hdr_cells = table.rows[0].cells
                hdr_cells[0].text = '标号'
                hdr_cells[1].text = '名称'
                hdr_cells[2].text = '功能'
                
                for comp in diagram.components:
                    row_cells = table.add_row().cells
                    row_cells[0].text = comp['number']
                    row_cells[1].text = comp['name']
                    row_cells[2].text = comp.get('function', '')
        
        # 写入文件
        safe_title = "".join(c for c in invention_title if c.isalnum() or c in (' ', '-', '_')).strip()
        filename = f"{safe_title}_专利文档.docx"
        filepath = os.path.join(self.output_dir, filename)
        
        doc.save(filepath)
        return filepath
    
    def merge_individual_files(
        self,
        invention_title: str,
        claims_doc: ClaimsDocument,
        description_doc: DescriptionDocument,
        abstract_doc: AbstractDocument,
        diagram_doc: DiagramDocument
    ) -> dict:
        """
        分别保存各个文档
        
        Returns:
            文件路径字典
        """
        from agents.claims_writer import ClaimsWriter
        from agents.description_writer import DescriptionWriter
        from agents.abstract_writer import AbstractWriter
        from agents.diagram_generator import DiagramGenerator
        
        safe_title = "".join(c for c in invention_title if c.isalnum() or c in (' ', '-', '_')).strip()
        
        files = {}
        
        # 权利要求书
        claims_writer = ClaimsWriter()
        claims_path = os.path.join(self.output_dir, f"{safe_title}_权利要求书.md")
        with open(claims_path, 'w', encoding='utf-8') as f:
            f.write(claims_writer.to_markdown(claims_doc))
        files['claims'] = claims_path
        
        # 说明书
        desc_writer = DescriptionWriter()
        desc_path = os.path.join(self.output_dir, f"{safe_title}_说明书.md")
        with open(desc_path, 'w', encoding='utf-8') as f:
            f.write(desc_writer.to_markdown(description_doc))
        files['description'] = desc_path
        
        # 摘要
        abs_writer = AbstractWriter()
        abs_path = os.path.join(self.output_dir, f"{safe_title}_摘要.md")
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(abs_writer.to_markdown(abstract_doc))
        files['abstract'] = abs_path
        
        # 附图说明
        diag_writer = DiagramGenerator()
        diag_path = os.path.join(self.output_dir, f"{safe_title}_附图说明.md")
        with open(diag_path, 'w', encoding='utf-8') as f:
            f.write(diag_writer.to_markdown(diagram_doc))
        files['diagrams'] = diag_path
        
        return files
