# 发票盒子 (InvoiceBox) — Claude Code 项目上下文

## 项目概述

- **名称**：发票盒子 V6.1 (InvoiceBox)
- **类型**：桌面端应用（PyInstaller 打包的 .exe）
- **架构**：本地 Flask 服务器 + 浏览器前端（Vue 3 + Element Plus）
- **功能**：发票文件管理、OCR 识别、自动分类、OFD 转 PDF、合并预览、专票打印 2 份

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3 + Flask |
| 前端 | Vue.js 3 (vue.global.js, Options API) |
| UI 组件 | Element Plus |
| PDF 处理 | PyMuPDF (fitz) |
| OFD 处理 | easyofd + 手动 XML 解析 |
| 图像处理 | PIL (Pillow) |
| 数据存储 | JSON 文件 (database.json) |
| 打包 | PyInstaller |

## 关键路径规则（重要！）

项目支持**开发模式**和**打包模式**两种运行环境，所有路径必须兼容两者：

```python
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后的运行环境
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))  # .exe 所在目录
    INTERNAL_DIR = sys._MEIPASS  # PyInstaller 临时解压目录（内含 templates、static）
    appdata_path = os.getenv('LOCALAPPDATA')
    if not appdata_path: appdata_path = os.path.expanduser("~")
    DATA_DIR = os.path.join(appdata_path, 'InvoiceBox_V5')  # 用户数据目录
else:
    # 开发模式直接运行 main.py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    INTERNAL_DIR = BASE_DIR
    DATA_DIR = BASE_DIR
```

**路径常量：**
- `TEMPLATE_DIR = os.path.join(INTERNAL_DIR, 'templates')` — HTML 模板
- `STATIC_DIR = os.path.join(INTERNAL_DIR, 'static')` — JS/CSS 静态资源
- `UPLOAD_DIR = os.path.join(DATA_DIR, 'invoices')` — 上传的发票文件
- `DB_FILE = os.path.join(DATA_DIR, 'database.json')` — 数据库

**规则：读取代码/模板/静态资源用 `INTERNAL_DIR`，读写用户数据用 `DATA_DIR`。**

## 代码规范

1. **单文件后端**：所有 Flask 路由和业务逻辑在 `main.py` 中，不要拆多文件
2. **数据库操作**：始终通过 `load_db()` 和 `save_db()` 读写，格式为 `{"invoices": [...]}`
3. **文件命名**：上传文件以 UUID 前 8 位命名，MD5 用于去重
4. **异常处理**：关键操作（OCR、OFD 转换）用 try/except 包裹，不要暴露内部错误
5. **编码**：所有文件读写使用 `utf-8`，JSON 用 `ensure_ascii=False`
6. **前端**：Vue 3 使用 Options API（setup 函数），Element Plus 直接使用

## 数据库结构

```json
{
  "invoices": [
    {
      "id": "uuid",
      "name": "存储文件名.pdf",
      "display_name": "原始文件名.pdf",
      "amount": 123.45,
      "category": "机票|火车票|住宿|交通|行程单|餐饮|其他",
      "date": "2026-05-03",
      "date_start": "2026-04-02",
      "date_end": "2026-04-03",
      "route": "北京 → 乌鲁木齐",
      "md5": "文件MD5哈希"
    }
  ]
}
```

## 发票分类规则

OCR 提取文本后，按关键词自动分类（优先级从高到低）：
- **机票**：含"行程单"+"机票/航空/航班/民航/旅客"，或"航空运输/客票"
- **火车票**：含"火车/铁路"，或车次号格式 "G1..."
- **住宿**：含"酒店/住宿"
- **交通**：含"客运/运输服务/通行费"
- **餐饮**：含"餐饮/美食"
- **行程单**：含"行程单"但不匹配机票关键词
- **其他**：无法识别的归为其他

## OCR 识别逻辑 (smart_ocr)

### 日期提取
- **住宿发票**：优先从备注栏提取入住/退房日期（如"订单日期:4-2至4-3"），用开票日期的年份补全
- **其他类型**：提取所有日期，排序后取最早和最晚作为 date_start/date_end

### 路线提取
- **火车票**：匹配"XX站"格式的中文站名；GBK 编码 PDF fallback 用拉丁站名映射（LATIN_STATION）
- **机票**：双策略——IATA 机场代码（PEK→北京）优先，不足时用航班号位置定位中文城市名

### 金额提取
- 火车票：匹配"票价"后的数字
- 机票/行程单：匹配"合计/共计/Total"后的数字
- 通用 fallback：匹配"小写/合计/￥"后的数字，或所有数字取最大值

## 打印布局逻辑 (_build_layout)

发票分为两类，布局不同：

| 类型 | 包含分类 | 每页布局 | 打印份数 |
|------|---------|---------|---------|
| 专票 | 机票、火车票、住宿 | 1 张发票居中，40mm 边距 | 用户可选（专票×2） |
| 普票 | 交通、餐饮、行程单、其他 | 2 张发票上下拼一页，虚线分隔 | 1 份 |

- 专票：竖版 A4（595×842），`fitz.Rect(40, 40, 555, 802)`
- 普票：竖版 A4，上半 `fitz.Rect(20, 20, 575, 411)`，下半 `fitz.Rect(20, 421, 575, 822)`
- `duplicate_special=True` 时，每张专票生成 2 页
- 预览和下载使用同一布局函数，保证一致性

## API 路由

| 路由 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 返回 index.html |
| `/api/init` | GET | 返回数据库全部数据 |
| `/api/upload` | POST | 上传发票文件，OCR 识别，返回发票信息 |
| `/api/update` | POST | 更新发票信息 |
| `/api/delete` | POST | 删除发票及其文件 |
| `/api/clear` | POST | 清空全部发票及文件 |
| `/api/render_pages` | POST | 返回预览图片数组和页码映射 |
| `/api/download_pdf` | POST | 生成合并 PDF，返回下载链接 |
| `/api/preview` | POST | 返回 merged_preview.pdf 链接 |
| `/file/<name>` | GET | 返回发票文件 |

## 前端架构

- 单文件 SPA：`templates/index.html` 包含模板、样式、脚本
- 状态管理：Vue 3 `ref` / `computed`
- 持久化设置：`localStorage` 存储 `ib_dupSpecial`（专票打印 2 份）
- 视图切换：preview（预览）、report（报销单）
- 拖拽排序：原生 HTML5 drag 事件

## 打包发布

- 使用 `发票盒子_V6.1.spec` 配置
- 打包命令：`pyinstaller 发票盒子_V6.1.spec`
- 打包后输出在 `dist/` 目录
- `datas` 配置已包含 `templates` 和 `static` 目录

## 注意事项

1. 不要引入新的数据库依赖，保持 JSON 文件存储
2. 前端资源不依赖外部 CDN，全部本地打包
3. OFD 解析依赖 `easyofd`，注意处理其日志输出（已禁用 loguru）
4. 用户数据目录在 `LOCALAPPDATA\InvoiceBox_V5`，不要硬编码到其他位置
5. 打包后的 .exe 是窗口模式（无控制台），调试直接运行 `main.py`
