# 发票盒子 (InvoiceBox)

**Windows 桌面版 6.6.0 已提供便携包：到 [Releases 下载](https://github.com/dayesann/InvoiceBox-/releases/latest)，解压后双击 `InvoiceBox.exe`。无需安装 Python。**

桌面版更新了发票/行程单自动匹配、拖拽手动关联与排序保存、多页 PDF 拼版，以及 OFD 矢量转换。详见[桌面版使用与构建说明](docs/桌面版使用说明.md)。

本仓库包含两套不同实现：

- **网站版**：位于 [`web/`](web/README.md)，以线上公开发布包为基线，新增可维护的发票与行程单关联逻辑。原网站工程源码目前未找回。
- **桌面版**：根目录 `main.py`、`templates/` 和 `static/`，采用 Flask + Vue，数据保存到本机。本次已单独更新至 6.6.0；与 Cloudflare 网站版各自构建和发布。

这是一个基于 Flask (Python) 和 Vue.js 开发的发票处理工具。

## 功能
1. **智能识别**：自动识别 PDF/OFD 发票金额、类别。
2. **报销单生成**：自动生成带分类统计的 A4 报销单。
3. **发票拼图**：自动将发票拼接为 A4 大小，方便打印。
4. **防重复**：自动检测重复上传的发票。

## 如何运行
1. 安装依赖：`pip install -r requirements-desktop.txt`
2. 运行：`python main.py`
