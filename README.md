# 发票盒子 V5.8 (InvoiceBox)

这是一个基于 Flask (Python) 和 Vue.js 开发的发票处理工具。

## 功能
1. **智能识别**：自动识别 PDF/OFD 发票金额、类别。
2. **报销单生成**：自动生成带分类统计的 A4 报销单。
3. **发票拼图**：自动将发票拼接为 A4 大小，方便打印。
4. **防重复**：自动检测重复上传的发票。

## 如何运行
1. 安装依赖：`pip install flask pymupdf`
2. 运行：`python main.py`
