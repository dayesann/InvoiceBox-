import os, sys, json, uuid, datetime, re, threading, webbrowser, time, base64, zipfile, hashlib
import mimetypes
import io
import shutil
import tempfile
import fitz  # PyMuPDF
from PIL import Image
from easyofd import OFD
from flask import Flask, request, jsonify, send_file, Response, make_response
from classifier import classify as classify_invoice, load_user_rules, record_user_correction, extract_fields

# 1. 基础配置
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    INTERNAL_DIR = sys._MEIPASS
    appdata_path = os.getenv('LOCALAPPDATA')
    if not appdata_path: appdata_path = os.path.expanduser("~")
    DATA_DIR = os.path.join(appdata_path, 'InvoiceBox_V5')
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    INTERNAL_DIR = BASE_DIR
    DATA_DIR = BASE_DIR

TEMPLATE_DIR = os.path.join(INTERNAL_DIR, 'templates')
STATIC_DIR = os.path.join(INTERNAL_DIR, 'static')
UPLOAD_DIR = os.path.join(DATA_DIR, 'invoices')
DB_FILE = os.path.join(DATA_DIR, 'database.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
MERGED_PDF = os.path.join(UPLOAD_DIR, 'merged_preview.pdf')
APP_VERSION = "6.2"

DEFAULT_SETTINGS = {
    "company_name": "",
    "report_title": "费用报销单",
    "currency_symbol": "¥",
    "categories": ["交通", "公共交通", "打车费", "行程单", "住宿", "餐饮", "火车票", "机票", "专票", "办公", "邮寄费", "其他"],
    "special_categories": ["机票", "火车票", "住宿", "公共交通"],
    "excluded_report_categories": ["行程单"],
    "duplicate_special": False,
}

if not os.path.exists(UPLOAD_DIR): os.makedirs(UPLOAD_DIR)

app = Flask(__name__, template_folder=TEMPLATE_DIR)
db_lock = threading.RLock()

@app.route('/static/<path:filename>')
def serve_static(filename):
    file_path = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(file_path): return "Not Found", 404
    with open(file_path, 'rb') as f: content = f.read()
    mime = 'text/css' if filename.endswith('.css') else 'application/javascript' if filename.endswith('.js') else 'application/octet-stream'
    return Response(content, mimetype=mime)

@app.route('/')
def index(): return send_file(os.path.join(TEMPLATE_DIR, 'index.html'))

@app.route('/api/heartbeat')
def heartbeat(): return "ok"

def load_db():
    with db_lock:
        if not os.path.exists(DB_FILE):
            return {"invoices": []}
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("invoices"), list):
                raise ValueError("database.json 格式不正确")
            return data
        except Exception as e:
            backup = f"{DB_FILE}.corrupt-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                os.replace(DB_FILE, backup)
                print(f"数据库读取失败，已备份到 {backup}: {e}")
            except Exception as backup_err:
                print(f"数据库读取失败，备份也失败: {e}; {backup_err}")
            return {"invoices": []}

def save_db(data):
    with db_lock: _save_json_atomic(DB_FILE, data)

def _save_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_file = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_file, path)
    finally:
        if os.path.exists(tmp_file):
            try: os.remove(tmp_file)
            except: pass

def normalize_settings(data):
    raw = data if isinstance(data, dict) else {}
    settings = dict(DEFAULT_SETTINGS)
    settings["company_name"] = str(raw.get("company_name", "")).strip()[:80]
    settings["report_title"] = str(raw.get("report_title", DEFAULT_SETTINGS["report_title"])).strip()[:40] or DEFAULT_SETTINGS["report_title"]
    settings["currency_symbol"] = str(raw.get("currency_symbol", DEFAULT_SETTINGS["currency_symbol"])).strip()[:4] or DEFAULT_SETTINGS["currency_symbol"]
    categories = []
    for value in raw.get("categories", DEFAULT_SETTINGS["categories"]):
        name = str(value).strip()[:20]
        if name and name not in categories: categories.append(name)
        if len(categories) >= 30: break
    if not categories: categories = list(DEFAULT_SETTINGS["categories"])
    if "其他" not in categories: categories.append("其他")
    settings["categories"] = categories
    special = raw.get("special_categories", DEFAULT_SETTINGS["special_categories"])
    excluded = raw.get("excluded_report_categories", DEFAULT_SETTINGS["excluded_report_categories"])
    settings["special_categories"] = [c for c in categories if c in special]
    settings["excluded_report_categories"] = [c for c in categories if c in excluded]
    settings["duplicate_special"] = bool(raw.get("duplicate_special", DEFAULT_SETTINGS["duplicate_special"]))
    return settings

def load_settings():
    with db_lock:
        if not os.path.exists(SETTINGS_FILE): return dict(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return normalize_settings(json.load(f))
        except Exception as e:
            backup = f"{SETTINGS_FILE}.corrupt-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                os.replace(SETTINGS_FILE, backup)
                print(f"设置读取失败，已备份到 {backup}: {e}")
            except Exception as backup_err:
                print(f"设置读取失败，备份也失败: {e}; {backup_err}")
            return dict(DEFAULT_SETTINGS)

def save_settings(data):
    settings = normalize_settings(data)
    with db_lock: _save_json_atomic(SETTINGS_FILE, settings)
    return settings

def write_backup_archive(target):
    db = load_db()
    settings = load_settings()
    manifest = {"product": "InvoiceBox", "version": APP_VERSION, "exported_at": datetime.datetime.now().isoformat(timespec="seconds"), "invoice_count": len(db["invoices"])}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("database.json", json.dumps(db, ensure_ascii=False, indent=2))
        z.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
        for item in db["invoices"]:
            name = os.path.basename(str(item.get("name", "")))
            path = os.path.join(UPLOAD_DIR, name)
            if name and os.path.isfile(path): z.write(path, f"invoices/{name}")

def validate_restore_archive(archive):
    names = set(archive.namelist())
    if "database.json" not in names: raise ValueError("备份包缺少 database.json")
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"): raise ValueError("备份包包含不安全路径")
    db = json.loads(archive.read("database.json").decode("utf-8"))
    if not isinstance(db, dict) or not isinstance(db.get("invoices"), list): raise ValueError("备份中的数据库格式不正确")
    settings = dict(DEFAULT_SETTINGS)
    if "settings.json" in names: settings = normalize_settings(json.loads(archive.read("settings.json").decode("utf-8")))
    for item in db["invoices"]:
        if not isinstance(item, dict): raise ValueError("备份中的发票记录格式不正确")
        name = str(item.get("name", ""))
        if not name or name != os.path.basename(name): raise ValueError("备份中存在无效文件名")
        if f"invoices/{name}" not in names: raise ValueError(f"备份包缺少发票文件: {name}")
    return db, settings

def calculate_md5(file_path):
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""): hash_md5.update(chunk)
    return hash_md5.hexdigest()

# 2. OCR 与转换
def read_ofd_source(ofd_path):
    full_text = ""
    try:
        with zipfile.ZipFile(ofd_path, 'r') as z:
            for filename in z.namelist():
                if filename.endswith('Content.xml'):
                    xml = z.read(filename).decode('utf-8', errors='ignore')
                    texts = re.findall(r'>([^<]+)<', xml)
                    full_text += " ".join(texts) + " "
    except: pass
    return full_text

def ofd_to_pdf(ofd_path):
    """将OFD文件转换为PDF，返回PDF文件路径"""
    import logging
    from loguru import logger as _loguru_logger
    logging.disable(logging.CRITICAL)
    _loguru_logger.disable("easyofd")
    try:
        with open(ofd_path, "rb") as f:
            ofd_data = f.read()
        ofd = OFD()
        ofd.read(ofd_data, fmt="binary")
        img_list = ofd.to_jpg()
        if not img_list:
            return None
        doc = fitz.open()
        for pil_img in img_list:
            img_w, img_h = pil_img.size
            page = doc.new_page(width=img_w, height=img_h)
            img_stream = io.BytesIO()
            pil_img.save(img_stream, format="JPEG", quality=85)
            page.insert_image(fitz.Rect(0, 0, img_w, img_h), stream=img_stream.getvalue())
        pdf_path = ofd_path.rsplit('.', 1)[0] + '.pdf'
        doc.save(pdf_path)
        doc.close()
        return pdf_path
    except Exception as e:
        print(f"OFD转换失败: {e}")
        return None
    finally:
        logging.disable(logging.NOTSET)
        _loguru_logger.enable("easyofd")

IATA_CITY = {
    'PEK': '北京', 'PKX': '北京', 'SHA': '上海', 'PVG': '上海', 'CAN': '广州',
    'SZX': '深圳', 'CTU': '成都', 'TFU': '成都', 'CKG': '重庆', 'HGH': '杭州',
    'WUH': '武汉', 'NKG': '南京', 'XIY': '西安', 'KMG': '昆明', 'CSX': '长沙',
    'URC': '乌鲁木齐', 'TAO': '青岛', 'DLC': '大连', 'TSN': '天津', 'SYX': '三亚',
    'HAK': '海口', 'XMN': '厦门', 'FOC': '福州', 'TNA': '济南', 'CGO': '郑州',
    'HRB': '哈尔滨', 'SHE': '沈阳', 'CGQ': '长春', 'WNZ': '温州', 'NNG': '南宁',
    'GUI': '贵阳', 'LHW': '兰州', 'XNN': '西宁', 'INC': '银川',
    'KHG': '喀什', 'HTN': '和田', 'AKU': '阿克苏', 'YIN': '伊宁', 'KRL': '库尔勒',
    'BPE': '秦皇岛', 'JDZ': '景德镇', 'DOY': '东营', 'JMU': '佳木斯', 'LJG': '丽江',
    'DIG': '迪庆', 'JHG': '西双版纳', 'ZAT': '昭通', 'LNJ': '临沧', 'SYM': '思茅',
    'ACX': '兴义', 'HZH': '黎平', 'LLB': '荔波', 'ZYI': '遵义', 'KWE': '贵阳',
    'NGB': '宁波', 'YIW': '义乌', 'JJN': '晋江',
    'XUZ': '徐州', 'YNZ': '盐城', 'LYG': '连云港', 'NTG': '南通', 'CZX': '常州',
    'YTY': '扬州', 'SZV': '苏州', 'WUX': '无锡', 'BFU': '蚌埠',
    'FUG': '阜阳', 'AQG': '安庆', 'TXN': '黄山', 'JUH': '池州',
}

# 火车票英文站名映射（GBK 编码 PDF 的 fallback）
LATIN_STATION = {
    'beijingnan': '北京南站', 'beijingxi': '北京西站', 'beijingdong': '北京东站',
    'beijing': '北京站', 'shanghaihongqiao': '上海虹桥站', 'shanghainan': '上海南站',
    'shanghai': '上海站', 'jinandong': '济南东站', 'jinanxi': '济南西站',
    'jinan': '济南站', 'nanjingnan': '南京南站', 'nanjing': '南京站',
    'hangzhoudong': '杭州东站', 'hangzhou': '杭州站', 'guangzhounan': '广州南站',
    'guangzhou': '广州站', 'shenzhenbei': '深圳北站', 'shenzhen': '深圳站',
    'wuhan': '武汉站', 'chengdudong': '成都东站', 'chengdu': '成都站',
    'chongqingxi': '重庆西站', 'chongqingbei': '重庆北站', 'chongqing': '重庆站',
    'xianbei': '西安北站', 'xian': '西安站', 'zhengzhoudong': '郑州东站',
    'zhengzhou': '郑州站', 'changchun': '长春站', 'harbinxi': '哈尔滨西站',
    'harbin': '哈尔滨站', 'shenyangbei': '沈阳北站', 'shenyang': '沈阳站',
    'dalian': '大连站', 'qingdao': '青岛站', 'kunmingnan': '昆明南站',
    'kunming': '昆明站', 'fuzhou': '福州站', 'xiamenbei': '厦门北站',
    'xiamen': '厦门站', 'changshanan': '长沙南站', 'changsha': '长沙站',
    'hefeinan': '合肥南站', 'hefei': '合肥站', 'guiyangbei': '贵阳北站',
    'guiyang': '贵阳站', 'urumqi': '乌鲁木齐站', 'lanzhouxi': '兰州西站',
    'lanzhou': '兰州站', 'xining': '西宁站', 'yinchuan': '银川站',
    'haikou': '海口站', 'sanya': '三亚站', 'lhasa': '拉萨站',
}

def smart_ocr(path):
    info = {"amount": 0.0, "category": "其他", "date_start": "", "date_end": "", "route": "",
            "document_type": "", "confidence": 0.0, "matched_evidence": [],
            "risk_points": [], "needs_manual_review": True, "seller_name": ""}
    text = ""
    try:
        if path.lower().endswith('.ofd'): text = read_ofd_source(path)
        if not text.strip():
            with fitz.open(path) as doc:
                for p in doc: text += p.get_text()

        # 预处理：去除空格用于关键词匹配和日期提取
        clean_txt = text.replace(" ", "").replace("¥", "￥").replace("CNY", "￥").replace("\n", "")

        # 多证据加权分类
        user_rules = load_user_rules(DATA_DIR)
        result = classify_invoice(clean_txt, text, user_rules)
        info['category'] = result['expense_category']
        info['document_type'] = result['document_type']
        info['confidence'] = result['confidence']
        info['matched_evidence'] = result['matched_evidence']
        info['risk_points'] = result['risk_points']
        info['needs_manual_review'] = result['needs_manual_review']
        fields = extract_fields(clean_txt, text)
        info['seller_name'] = fields.get('seller_name', '')

        # 日期提取
        if info['category'] == '住宿':
            # 住宿发票：优先从备注栏提取入住/退房日期（如 "订单日期:4-2至4-3"）
            stay = re.search(r'(\d{1,2})[月\-/](\d{1,2})[日至~]+(\d{1,2})[月\-/]?(\d{1,2})', clean_txt)
            if stay:
                m1, d1, m2, d2 = stay.groups()
                # 用开票日期的年份（匹配 "2026年" 格式）
                year = re.search(r'(20\d{2})年', clean_txt)
                y = year.group(1) if year else datetime.datetime.now().strftime('%Y')
                info['date_start'] = f"{y}-{int(m1):02d}-{int(d1):02d}"
                info['date_end'] = f"{y}-{int(m2):02d}-{int(d2):02d}"
            else:
                # 备选：用开票日期
                all_dates = re.findall(r'(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})[日号]?', clean_txt)
                if all_dates:
                    y, m, d = all_dates[0]
                    info['date_start'] = info['date_end'] = f"{y}-{int(m):02d}-{int(d):02d}"
        else:
            # 其他类型：提取所有日期
            all_dates = re.findall(r'(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})[日号]?', clean_txt)
            if all_dates:
                parsed = sorted(set(f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in all_dates))
                info['date_start'] = parsed[0]
                info['date_end'] = parsed[-1] if len(parsed) > 1 else parsed[0]

        # 路线提取
        if info['category'] == '火车票':
            # 火车票：匹配 "XX站" 格式
            stations = re.findall(r'([一-鿿]{2,6}(?:站|南站|西站|东站|北站))', clean_txt)
            if len(stations) >= 2:
                info['route'] = f"{stations[0]} → {stations[-1]}"
            else:
                # GBK 编码 PDF fallback：匹配英文站名
                latins = re.findall(r'([A-Z][a-z]+(?:nan|xi|dong|bei)?)\b', text)
                latin_cities = []
                for lm in latins:
                    key = lm.lower()
                    if key in LATIN_STATION and LATIN_STATION[key] not in latin_cities:
                        latin_cities.append(LATIN_STATION[key])
                if len(latin_cities) >= 2:
                    info['route'] = f"{latin_cities[0]} → {latin_cities[-1]}"
                elif len(latin_cities) == 1:
                    info['route'] = latin_cities[0]
        elif info['category'] == '机票':
            dep_city = ''
            arr_city = ''
            # 方法1：IATA 机场代码（PEK184 等格式）
            iata = re.findall(r'([A-Z]{3})(?=\d|[A-Z]|\s|$)', text)
            iata_cities = []
            for code in iata:
                if code in IATA_CITY and IATA_CITY[code] not in iata_cities:
                    iata_cities.append(IATA_CITY[code])
            # 方法2：按航班号位置定位中文城市名
            exclude = {'国内', '合计', '共计', '旅客', '行程', '航班', '航空', '客票', '电子', '国航', '南航', '东航', '海航'}
            flight_m = re.search(r'(?<![A-Z])([A-Z]{2}\d{3,4})', text)
            if flight_m:
                flight_pos = flight_m.start()
                before = text[:flight_pos]
                after = text[flight_pos:]
                dep_cities = re.findall(r'([一-鿿]{2,4})(?:\s+[一-鿿]+)*\s*(?:[A-Z]\d+)?', before)
                arr_cities = re.findall(r'([一-鿿]{2,4})(?:\s+[一-鿿]+)*\s*(?:[A-Z]\d+)?', after)
                dep = [c for c in dep_cities if c not in exclude]
                arr = [c for c in arr_cities if c not in exclude]
                if dep: dep_city = dep[-1]
                if arr: arr_city = arr[0]
            # IATA 结果优先，不足时用中文匹配补充
            if len(iata_cities) >= 2:
                dep_city, arr_city = iata_cities[0], iata_cities[1]
            elif len(iata_cities) == 1 and not dep_city:
                dep_city = iata_cities[0]
            if dep_city and arr_city:
                info['route'] = f"{dep_city} → {arr_city}"
            elif dep_city:
                info['route'] = dep_city
            elif arr_city:
                info['route'] = arr_city

        amount_str = ""
        # 针对特定类型的精准提取
        if info['category'] == '火车票':
            m = re.search(r'票价[^0-9]*([0-9]+\.[0-9]{2})', clean_txt)
            if m: amount_str = m.group(1)
        elif info['category'] in ['机票', '行程单']:
             m = re.search(r'(合计|共计|Total)[^0-9]*([0-9]+\.[0-9]{2})', clean_txt)
             if m: amount_str = m.group(2)

        # 贪婪模式：金额提取
        if not amount_str:
            candidates = []
            matches_A = re.findall(r'(?:小写|合计|￥|¥)[^0-9.\n]*([0-9,]+\.[0-9]{2})', clean_txt)
            for val in matches_A:
                try: candidates.append(float(val.replace(',', '')))
                except: pass
            if not candidates:
                matches_B = re.findall(r'([0-9,]+\.[0-9]{2})', clean_txt)
                for val in matches_B:
                    try: candidates.append(float(val.replace(',', '')))
                    except: pass
            if candidates:
                info['amount'] = max(candidates)

        if amount_str: info['amount'] = float(amount_str)
        return info
    except: return info

def _build_layout(ids, duplicate_special=False):
    """构建页面布局：专票竖版A4独占一页，普票2张拼一页；
    公共交通与行程单金额一致时配对拼页。"""
    db = load_db()
    settings = load_settings()
    fmap = {i['id']: i['name'] for i in db['invoices']}
    cat_map = {i['id']: i.get('category', '') for i in db['invoices']}
    amt_map = {i['id']: round(float(i.get('amount', 0)), 2) for i in db['invoices']}
    doc_type_map = {i['id']: i.get('document_type', '') for i in db['invoices']}
    special_cats = set(settings.get("special_categories", []))
    W, H = 595, 842
    MARGIN = 40
    special_rect = fitz.Rect(MARGIN, MARGIN, W - MARGIN, H - MARGIN)
    normal_rects = [fitz.Rect(20, 20, W-20, H/2 - 10), fitz.Rect(20, H/2 + 10, W-20, H-20)]

    # 发票 ↔ 行程单 金额+类型配对
    # 打车费 ↔ 打车行程单，公共交通 ↔ 地铁行程单
    PAIR_RULES = {'打车费': '打车', '公共交通': '地铁'}
    pair_of = {}
    for inv_id in ids:
        cat = cat_map.get(inv_id, '')
        if cat in PAIR_RULES and inv_id not in pair_of:
            target_amt = amt_map.get(inv_id, -1)
            hint = PAIR_RULES[cat]
            for nid in ids:
                if nid == inv_id or nid in pair_of: continue
                if cat_map.get(nid) == '行程单' and amt_map.get(nid, -2) == target_amt:
                    if hint in doc_type_map.get(nid, ''):
                        pair_of[inv_id] = nid
                        pair_of[nid] = inv_id
                        break

    pages = []
    page_map = {}
    normal_buf = []
    normal_buf_ids = []
    paired_done = set()

    def flush_normals(buf, buf_ids):
        for i in range(0, len(buf), 2):
            items = [(buf[i], normal_rects[0])]
            page_map[buf_ids[i]] = len(pages)
            if i+1 < len(buf):
                items.append((buf[i+1], normal_rects[1]))
                page_map[buf_ids[i+1]] = len(pages)
            pages.append(items)

    for inv_id in ids:
        if inv_id not in fmap: continue
        if inv_id in paired_done: continue
        fpath = os.path.join(UPLOAD_DIR, fmap[inv_id])
        partner_id = pair_of.get(inv_id)

        if partner_id:
            if normal_buf:
                flush_normals(normal_buf, normal_buf_ids)
                normal_buf = []; normal_buf_ids = []
            partner_fpath = os.path.join(UPLOAD_DIR, fmap[partner_id])
            page_map[inv_id] = len(pages)
            page_map[partner_id] = len(pages)
            pages.append([(fpath, normal_rects[0]), (partner_fpath, normal_rects[1])])
            paired_done.add(inv_id)
            paired_done.add(partner_id)
        elif cat_map.get(inv_id) in special_cats:
            if normal_buf:
                flush_normals(normal_buf, normal_buf_ids)
                normal_buf = []; normal_buf_ids = []
            page_map[inv_id] = len(pages)
            pages.append([(fpath, special_rect)])
            if duplicate_special:
                page_map[inv_id] = len(pages) - 1
                pages.append([(fpath, special_rect)])
        else:
            normal_buf.append(fpath)
            normal_buf_ids.append(inv_id)
            if len(normal_buf) == 2:
                flush_normals(normal_buf, normal_buf_ids)
                normal_buf = []; normal_buf_ids = []

    if normal_buf:
        flush_normals(normal_buf, normal_buf_ids)

    return pages, page_map

@app.route('/api/render_pages', methods=['POST'])
def render_pages():
    try:
        ids = request.json.get('ids', [])
        dup = request.json.get('duplicate_special', False)
        pages, page_map = _build_layout(ids, duplicate_special=dup)
        images = []
        doc = fitz.open()
        W, H = 595, 842
        for items in pages:
            page = doc.new_page(width=W, height=H)
            for fpath, rect in items:
                insert_to_page(page, fpath, rect)
            if len(items) == 2:
                page.draw_line((20, H/2), (W-20, H/2), color=(0.7,0.7,0.7), dashes=[2])
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            b64 = base64.b64encode(pix.tobytes("png")).decode('ascii')
            images.append(f"data:image/png;base64,{b64}")
        doc.close()
        return jsonify({"images": images, "page_map": page_map})
    except Exception as e:
        print(f"render_pages error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def insert_to_page(page, fpath, rect):
    try:
        src = fitz.open(fpath)
        src_page = src[0]
        rot = -90 if src_page.rect.height > src_page.rect.width else 0
        page.show_pdf_page(rect, src, 0, rotate=rot, keep_proportion=True)
    except Exception as e:
        print(f"insert_to_page error for {fpath}: {e}")
    finally:
        try: src.close()
        except: pass

@app.route('/api/download_pdf', methods=['POST'])
def download_pdf():
    ids = request.json.get('ids', [])
    dup = request.json.get('duplicate_special', False)
    pages, _ = _build_layout(ids, duplicate_special=dup)
    doc = fitz.open()
    W, H = 595, 842
    for items in pages:
        page = doc.new_page(width=W, height=H)
        for fpath, rect in items:
            insert_to_page(page, fpath, rect)
        if len(items) == 2:
            page.draw_line((20, H/2), (W-20, H/2), color=(0.7,0.7,0.7), dashes=[2])
    doc.save(MERGED_PDF)
    return jsonify({"url": "/file/merged_preview.pdf"})

@app.route('/api/init')
def init():
    data = load_db()
    data["settings"] = load_settings()
    return jsonify(data)

@app.route('/api/settings', methods=['GET', 'POST'])
def settings_api():
    if request.method == 'GET':
        return jsonify(load_settings())
    payload = request.get_json(silent=True) or {}
    result = save_settings(payload)
    return jsonify(result)

@app.route('/api/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({"error": "未选择文件"}), 400
    try:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ('pdf', 'ofd'):
            return jsonify({"error": f"不支持的文件格式: .{ext}"}), 400
        name = f"{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        f.save(path)

        cur_md5 = calculate_md5(path)
        db = load_db()
        for i in db['invoices']:
            if i.get('md5') == cur_md5:
                os.remove(path)
                return jsonify({"error": "重复文件", "code": "DUPLICATE"}), 400

        info = smart_ocr(path)
        current_settings = load_settings()
        if info.get("category") not in current_settings.get("categories", []):
            info["category"] = "其他"

        final = name; disp = f.filename
        if ext == 'ofd':
            pdf_path = ofd_to_pdf(path)
            if pdf_path:
                final = name.replace('.ofd', '.pdf')
                disp = f.filename.replace('.ofd', '.pdf')
                try:
                    if os.path.exists(path) and os.path.abspath(path) != os.path.abspath(pdf_path):
                        os.remove(path)
                except Exception as cleanup_err:
                    print(f"OFD源文件清理失败: {cleanup_err}")
            else:
                os.remove(path)
                return jsonify({"error": "OFD文件转换失败，请检查文件格式"}), 400
        item = {"id": str(uuid.uuid4()), "name": final, "display_name": disp, "amount": info['amount'],
                "category": info['category'], "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "date_start": info.get('date_start', ''), "date_end": info.get('date_end', ''),
                "route": info.get('route', ''), "md5": cur_md5,
                "document_type": info.get("document_type", ""),
                "confidence": info.get("confidence", 0),
                "matched_evidence": info.get("matched_evidence", []),
                "risk_points": info.get("risk_points", []),
                "needs_manual_review": info.get("needs_manual_review", False),
                "seller_name": info.get("seller_name", "")}
        db['invoices'].append(item)
        save_db(db)
        return jsonify(item)
    except Exception as e:
        import traceback
        print(f"上传异常: {e}")
        traceback.print_exc()
        return jsonify({"error": f"上传处理异常: {str(e)}"}), 500

@app.route('/api/update', methods=['POST'])
def update():
    d = request.json
    if 'category' in d:
        valid_cats = load_settings().get('categories', [])
        if d['category'] not in valid_cats:
            return jsonify({"error": "无效的发票分类"}), 400
    db = load_db()
    for i in db['invoices']:
        if i['id'] == d['id']:
            # Record user correction if category changed
            if 'category' in d and d['category'] != i.get('category'):
                record_user_correction(
                    DATA_DIR,
                    seller_name=i.get('seller_name', ''),
                    title=i.get('document_type', ''),
                    original_category=i.get('category', ''),
                    correct_category=d['category'],
                )
                i['needs_manual_review'] = False
                i['confidence'] = 1.0
            i.update(d)
            break
    save_db(db)
    return jsonify("ok")

@app.route('/api/learn_category', methods=['POST'])
def learn_category():
    payload = request.get_json(silent=True) or {}
    if not payload.get("correct_category"):
        return jsonify({"error": "缺少 correct_category"}), 400
    record_user_correction(
        DATA_DIR,
        seller_name=payload.get("seller_name", ""),
        title=payload.get("title", ""),
        original_category=payload.get("original_category", ""),
        correct_category=payload["correct_category"],
    )
    return jsonify({"ok": True})

@app.route('/api/delete', methods=['POST'])
def delete():
    id = request.json.get('id')
    db = load_db()
    target = next((i for i in db['invoices'] if i['id'] == id), None)
    if target:
        fpath = os.path.join(UPLOAD_DIR, target['name'])
        if os.path.exists(fpath): os.remove(fpath)
    db['invoices'] = [i for i in db['invoices'] if i['id'] != id]
    save_db(db)
    return jsonify("ok")

@app.route('/api/clear', methods=['POST'])
def clear():
    db = load_db()
    for i in db['invoices']:
        fpath = os.path.join(UPLOAD_DIR, i['name'])
        if os.path.exists(fpath):
            try: os.remove(fpath)
            except: pass
    save_db({"invoices": []})
    return jsonify("ok")

@app.route('/api/preview', methods=['POST'])
def preview():
    return jsonify({"url": f"/file/merged_preview.pdf?t={time.time()}"})

@app.route('/file/<name>')
def file(name):
    path = os.path.join(UPLOAD_DIR, name)
    resp = make_response(send_file(path))
    resp.headers['Content-Disposition'] = 'inline'
    return resp

if __name__ == '__main__':
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open('http://127.0.0.1:5000'))).start()
    print(">>> 启动成功！请在浏览器访问 http://127.0.0.1:5000 <<<")
    app.run(port=5000, debug=False)
