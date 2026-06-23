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
APP_VERSION = "6.5"
SCHEMA_VERSION = 2

NEW_INVOICE_DEFAULTS = {
    "source_type": "pdf",
    "invoice_number": "",
    "batch_id": "",
    "status": "pending",
    "sort_order": 0,
    "invoice_date": "",
    "notes": "",
    "duplicate_status": "none",
    "created_at": "",
    "updated_at": "",
    "last_exported_at": "",
}

BATCH_STATUSES = ("draft", "exported", "reimbursed", "archived")
INVOICE_STATUSES = ("pending", "confirmed", "exported", "reimbursed", "archived")

DEFAULT_SETTINGS = {
    "company_name": "",
    "report_title": "费用报销单",
    "currency_symbol": "¥",
    "categories": ["交通", "公共交通", "打车费", "行程单", "住宿", "餐饮", "火车票", "机票", "专票", "办公", "邮寄费", "其他"],
    "special_categories": ["机票", "火车票", "住宿", "公共交通"],
    "excluded_report_categories": ["行程单"],
    "duplicate_special": False,
    "inbox_dir": "",
}

if not os.path.exists(UPLOAD_DIR): os.makedirs(UPLOAD_DIR)

app = Flask(__name__, template_folder=TEMPLATE_DIR)
db_lock = threading.RLock()

# ── 收件箱监听 ───────────────────────────────────────────────────
ALLOWED_IMPORT_EXT = {'pdf', 'ofd', 'jpg', 'jpeg', 'png'}
_inbox_seen = set()  # 已处理的文件路径集合

def _import_single_file(fpath, display_name):
    """导入单个文件，返回 item dict 或 None"""
    ext = fpath.rsplit('.', 1)[-1].lower() if '.' in fpath else ''
    if ext not in ALLOWED_IMPORT_EXT:
        return None
    new_name = f"{uuid.uuid4().hex[:8]}.{ext}"
    dest = os.path.join(UPLOAD_DIR, new_name)
    shutil.copy2(fpath, dest)
    cur_md5 = calculate_md5(dest)
    with db_lock:
        db = load_db()
        if any(i.get('md5') == cur_md5 for i in db['invoices']):
            try: os.remove(dest)
            except: pass
            return None
    # 图片转 PDF
    if ext in ('jpg', 'jpeg', 'png'):
        try:
            img = Image.open(dest)
            if img.mode == 'RGBA': img = img.convert('RGB')
            pdf_path = dest.rsplit('.', 1)[0] + '.pdf'
            a4_w, a4_h = 595, 842
            scale = min(a4_w / img.size[0], a4_h / img.size[1])
            new_w, new_h = int(img.size[0] * scale), int(img.size[1] * scale)
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            a4_img = Image.new('RGB', (a4_w, a4_h), (255, 255, 255))
            a4_img.paste(img_resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))
            a4_img.save(pdf_path, 'PDF')
            os.remove(dest)
            new_name = new_name.rsplit('.', 1)[0] + '.pdf'
            dest = pdf_path
            ext = 'pdf'
        except:
            return None
    if ext == 'ofd':
        pdf_path = ofd_to_pdf(dest)
        if pdf_path:
            new_name = new_name.replace('.ofd', '.pdf')
            try: os.remove(dest)
            except: pass
            dest = pdf_path
        else:
            try: os.remove(dest)
            except: pass
            return None
    info = smart_ocr(dest)
    current_settings = load_settings()
    if info.get("category") not in current_settings.get("categories", []):
        info["category"] = "其他"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    item = {"id": str(uuid.uuid4()), "name": new_name, "display_name": display_name,
            "amount": info['amount'], "category": info['category'],
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "date_start": info.get('date_start', ''), "date_end": info.get('date_end', ''),
            "route": info.get('route', ''), "md5": cur_md5,
            "document_type": info.get("document_type", ""),
            "confidence": info.get("confidence", 0),
            "matched_evidence": info.get("matched_evidence", []),
            "risk_points": info.get("risk_points", []),
            "needs_manual_review": info.get("needs_manual_review", False),
            "seller_name": info.get("seller_name", ""),
            "invoice_number": info.get("invoice_number", ""),
            "source_type": ext,
            "batch_id": "", "status": "pending", "sort_order": 0,
            "invoice_date": "", "notes": "", "duplicate_status": "none",
            "created_at": now_str, "updated_at": now_str, "last_exported_at": ""}
    with db_lock:
        db = load_db()
        item["sort_order"] = len(db['invoices'])
        item["duplicate_status"] = check_duplicate(item, db['invoices'])
        if not item["needs_manual_review"] and item["confidence"] >= 0.70:
            if item["amount"] > 0 and item["category"] not in ("其他", ""):
                if item["date_start"] or item["invoice_date"]:
                    item["status"] = "confirmed"
        db['invoices'].append(item)
        save_db(db)
    return item

def _inbox_scan():
    """扫描收件箱目录，导入新文件"""
    settings = load_settings()
    inbox = settings.get("inbox_dir", "").strip()
    if not inbox or not os.path.isdir(inbox):
        return []
    imported = []
    for fname in os.listdir(inbox):
        fpath = os.path.join(inbox, fname)
        if not os.path.isfile(fpath): continue
        if fpath in _inbox_seen: continue
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        if ext not in ALLOWED_IMPORT_EXT: continue
        _inbox_seen.add(fpath)
        item = _import_single_file(fpath, fname)
        if item:
            imported.append(item)
    return imported

def _inbox_watcher():
    """后台线程：每 30 秒扫描一次收件箱"""
    while True:
        try:
            _inbox_scan()
        except Exception as e:
            print(f"收件箱扫描异常: {e}")
        time.sleep(30)

# 启动监听线程（延迟到 __main__ 中启动，确保所有函数已定义）
# _watcher_thread = threading.Thread(target=_inbox_watcher, daemon=True)
# _watcher_thread.start()

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

def _compute_invoice_status(inv):
    """自动计算发票状态"""
    if inv.get("needs_manual_review"):
        return "pending"
    if inv.get("confidence", 0) < 0.70:
        return "pending"
    # 信息缺失检查
    if not inv.get("amount") or float(inv.get("amount", 0)) <= 0:
        return "pending"
    if inv.get("category") in ("其他", ""):
        return "pending"
    if not inv.get("date_start") and not inv.get("invoice_date"):
        return "pending"
    return "confirmed"

def _migrate_v1_to_v2(data):
    """将 v1 数据迁移到 v2 格式"""
    # 备份旧数据
    backup_path = f"{DB_FILE}.v1.backup-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    if os.path.exists(DB_FILE):
        try:
            import shutil
            shutil.copy2(DB_FILE, backup_path)
            print(f"V6.2 数据已备份到: {backup_path}")
        except Exception as e:
            print(f"备份旧数据失败: {e}")

    invoices = data.get("invoices", [])
    for i, inv in enumerate(invoices):
        for key, default in NEW_INVOICE_DEFAULTS.items():
            if key not in inv:
                inv[key] = default
        inv["sort_order"] = i
        if not inv.get("status"):
            inv["status"] = _compute_invoice_status(inv)
        if not inv.get("source_type"):
            name = inv.get("name", "")
            if name.lower().endswith(".ofd"):
                inv["source_type"] = "ofd"
            else:
                inv["source_type"] = "pdf"
        if not inv.get("invoice_number"):
            inv["invoice_number"] = ""
        if not inv.get("seller_name"):
            inv["seller_name"] = ""
        if not inv.get("created_at"):
            inv["created_at"] = ""
        if not inv.get("updated_at"):
            inv["updated_at"] = ""

    data["schema_version"] = 2
    data["batches"] = data.get("batches", [])
    return data

def load_db():
    with db_lock:
        if not os.path.exists(DB_FILE):
            return {"schema_version": SCHEMA_VERSION, "invoices": [], "batches": []}
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("invoices"), list):
                raise ValueError("database.json 格式不正确")
            # 自动迁移
            if data.get("schema_version", 1) < SCHEMA_VERSION:
                data = _migrate_v1_to_v2(data)
                _save_json_atomic(DB_FILE, data)
            data.setdefault("batches", [])
            data.setdefault("schema_version", SCHEMA_VERSION)
            return data
        except Exception as e:
            backup = f"{DB_FILE}.corrupt-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                os.replace(DB_FILE, backup)
                print(f"数据库读取失败，已备份到 {backup}: {e}")
            except Exception as backup_err:
                print(f"数据库读取失败，备份也失败: {e}; {backup_err}")
            return {"schema_version": SCHEMA_VERSION, "invoices": [], "batches": []}

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

def check_duplicate(invoice, all_invoices):
    """检查业务疑似重复：多维度交叉匹配"""
    inv_num = (invoice.get("invoice_number") or "").strip()
    seller = (invoice.get("seller_name") or "").strip()
    amount = round(float(invoice.get("amount", 0)), 2)
    inv_date = invoice.get("invoice_date") or invoice.get("date_start") or ""
    inv_md5 = invoice.get("md5", "")
    inv_id = invoice.get("id", "")

    for other in all_invoices:
        if other.get("id") == inv_id:
            continue
        if other.get("duplicate_status") == "ignored":
            continue

        other_num = (other.get("invoice_number") or "").strip()
        other_seller = (other.get("seller_name") or "").strip()
        other_amount = round(float(other.get("amount", 0)), 2)
        other_date = other.get("invoice_date") or other.get("date_start") or ""
        other_md5 = other.get("md5", "")

        # 规则1：MD5 完全相同 → 疑似重复（文件内容一模一样）
        if inv_md5 and other_md5 and inv_md5 == other_md5:
            return "suspected"

        # 规则2：发票号码完全相同且非空 → 疑似重复
        if inv_num and other_num and inv_num == other_num:
            return "suspected"

        # 规则3：销售方+金额+日期 三条件全部满足 → 疑似重复
        num_match = inv_num and other_num and inv_num == other_num
        seller_exact = seller and other_seller and seller == other_seller
        # 模糊销售方匹配：一个包含另一个（处理"有限公司" vs "有限责任公司"等变体）
        seller_fuzzy = (seller and other_seller and len(seller) >= 4 and len(other_seller) >= 4
                        and (seller in other_seller or other_seller in seller))
        amount_match = amount > 0 and amount == other_amount
        # 金额容差匹配：差额在 1% 以内
        amount_fuzzy = (amount > 0 and other_amount > 0
                        and abs(amount - other_amount) / max(amount, other_amount) < 0.01)
        date_match = inv_date and other_date and inv_date == other_date
        # 日期接近：3天以内
        date_close = False
        if inv_date and other_date:
            try:
                d1 = datetime.datetime.strptime(inv_date[:10], "%Y-%m-%d")
                d2 = datetime.datetime.strptime(other_date[:10], "%Y-%m-%d")
                date_close = abs((d1 - d2).days) <= 3
            except: pass

        # 精确三条件
        if (seller_exact or seller_fuzzy) and amount_match and date_match:
            return "suspected"
        # 销售方+金额精确+日期接近
        if seller_exact and amount_match and date_close:
            return "suspected"
        # 销售方模糊+金额容差+日期精确
        if seller_fuzzy and amount_fuzzy and date_match:
            return "suspected"

    return "none"

def write_backup_archive(target):
    db = load_db()
    settings = load_settings()
    manifest = {"product": "InvoiceBox", "version": APP_VERSION, "exported_at": datetime.datetime.now().isoformat(timespec="seconds"), "invoice_count": len(db["invoices"])}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("database.json", json.dumps(db, ensure_ascii=False, indent=2))
        z.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
        # 包含用户分类学习规则
        rules_path = os.path.join(DATA_DIR, "user_classifications.json")
        if os.path.exists(rules_path):
            z.write(rules_path, "user_classifications.json")
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
        info['invoice_number'] = fields.get('invoice_number', '')

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
        # 用原始文本（保留空格和换行）提取金额，避免数字粘连产生幻觉
        raw_for_amt = text.replace("¥", "￥").replace("CNY", "￥")
        # 针对特定类型的精准提取
        if info['category'] == '火车票':
            m = re.search(r'票价[^0-9]*([0-9]+\.[0-9]{2})', raw_for_amt)
            if m: amount_str = m.group(1)
        elif info['category'] in ['机票', '行程单']:
             m = re.search(r'(合计|共计|Total)[^0-9]*([0-9]+\.[0-9]{2})', raw_for_amt)
             if m: amount_str = m.group(2)

        # 贪婪模式：金额提取
        if not amount_str:
            candidates = []
            # Pattern A：优先从 ￥/小写/合计 后提取
            matches_A = re.findall(r'(?:小写|合计|￥|¥)\s*([0-9,]+\.[0-9]{2})', raw_for_amt)
            for val in matches_A:
                try: candidates.append(float(val.replace(',', '')))
                except: pass
            if not candidates:
                # Pattern B：全文匹配，但限制合理范围（≤100万）
                matches_B = re.findall(r'(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})(?![0-9])', raw_for_amt)
                for val in matches_B:
                    try:
                        v = float(val.replace(',', ''))
                        if 0 < v <= 1000000:
                            candidates.append(v)
                    except: pass
            if candidates:
                info['amount'] = max(candidates)

        if amount_str: info['amount'] = float(amount_str)
        return info
    except: return info

def _build_layout(ids, duplicate_special=False):
    """构建页面布局：专票独占一页，发票+行程单配对拼页，普票2张拼一页。"""
    db = load_db()
    settings = load_settings()
    fmap = {i['id']: i['name'] for i in db['invoices']}
    cat_map = {i['id']: i.get('category', '') for i in db['invoices']}
    doc_type_map = {i['id']: i.get('document_type', '') for i in db['invoices']}
    special_cats = set(settings.get("special_categories", []))
    W, H = 595, 842
    MARGIN = 40
    special_rect = fitz.Rect(MARGIN, MARGIN, W - MARGIN, H - MARGIN)
    normal_rects = [fitz.Rect(20, 20, W-20, H/2 - 10), fitz.Rect(20, H/2 + 10, W-20, H-20)]

    # ── 配对：发票+行程单按类型配对 ─────────────────────────────
    # ── 发票 ↔ 行程单 智能配对 ────────────────────────────────────
    PAIR_HINTS = {'打车费': '打车', '公共交通': '地铁'}
    # 构建发票详情索引
    inv_details = {}
    for inv in db['invoices']:
        inv_details[inv['id']] = {
            'amount': round(float(inv.get('amount', 0)), 2),
            'date': inv.get('date_start') or inv.get('invoice_date') or '',
            'seller': inv.get('seller_name', ''),
            'route': inv.get('route', ''),
        }

    pair_of = {}
    for inv_id in ids:
        cat = cat_map.get(inv_id, '')
        if cat not in PAIR_HINTS or inv_id in pair_of:
            continue
        hint = PAIR_HINTS[cat]
        inv_info = inv_details.get(inv_id, {})
        best_score = -1
        best_nid = None
        for nid in ids:
            if nid == inv_id or nid in pair_of: continue
            if cat_map.get(nid) != '行程单' or hint not in doc_type_map.get(nid, ''):
                continue
            trip_info = inv_details.get(nid, {})
            score = 0
            # 金额完全匹配 +50
            if inv_info.get('amount') > 0 and inv_info['amount'] == trip_info.get('amount'):
                score += 50
            # 日期匹配 +30
            if inv_info.get('date') and inv_info['date'] == trip_info.get('date'):
                score += 30
            # 路线/城市匹配 +20
            if inv_info.get('route') and trip_info.get('route'):
                inv_route = inv_info['route']
                trip_route = trip_info['route']
                if inv_route[:2] == trip_route[:2]:  # 同城市前缀
                    score += 20
            if score > best_score:
                best_score = score
                best_nid = nid
        if best_nid and best_score >= 50:  # 至少金额匹配
            pair_of[inv_id] = best_nid
            pair_of[best_nid] = inv_id

    # ── 生成页面 ───────────────────────────────────────────────
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
        if ext not in ('pdf', 'ofd', 'jpg', 'jpeg', 'png'):
            return jsonify({"error": f"不支持的文件格式: .{ext}"}), 400
        name = f"{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        f.save(path)

        # 图片转 PDF
        if ext in ('jpg', 'jpeg', 'png'):
            try:
                img = Image.open(path)
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                pdf_path = path.rsplit('.', 1)[0] + '.pdf'
                # A4 尺寸：595 x 842 点
                a4_w, a4_h = 595, 842
                img_w, img_h = img.size
                scale = min(a4_w / img_w, a4_h / img_h)
                new_w, new_h = int(img_w * scale), int(img_h * scale)
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                # 居中放到 A4 白底
                a4_img = Image.new('RGB', (a4_w, a4_h), (255, 255, 255))
                offset_x = (a4_w - new_w) // 2
                offset_y = (a4_h - new_h) // 2
                a4_img.paste(img_resized, (offset_x, offset_y))
                a4_img.save(pdf_path, 'PDF')
                os.remove(path)
                name = name.rsplit('.', 1)[0] + '.pdf'
                path = pdf_path
                ext = 'pdf'
            except Exception as img_err:
                try: os.remove(path)
                except: pass
                return jsonify({"error": f"图片转换失败: {str(img_err)}"}), 400

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
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        item = {"id": str(uuid.uuid4()), "name": final, "display_name": disp, "amount": info['amount'],
                "category": info['category'], "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "date_start": info.get('date_start', ''), "date_end": info.get('date_end', ''),
                "route": info.get('route', ''), "md5": cur_md5,
                "document_type": info.get("document_type", ""),
                "confidence": info.get("confidence", 0),
                "matched_evidence": info.get("matched_evidence", []),
                "risk_points": info.get("risk_points", []),
                "needs_manual_review": info.get("needs_manual_review", False),
                "seller_name": info.get("seller_name", ""),
                "invoice_number": info.get("invoice_number", ""),
                "source_type": "ofd" if ext == "ofd" else ("image" if ext in ("jpg","jpeg","png") else "pdf"),
                "batch_id": "", "status": "pending", "sort_order": len(db['invoices']),
                "invoice_date": "", "notes": "", "duplicate_status": "none",
                "created_at": now_str, "updated_at": now_str, "last_exported_at": ""}
        # 疑似重复检测
        item["duplicate_status"] = check_duplicate(item, db['invoices'])
        # 自动确认：高置信度且信息完整
        if not item["needs_manual_review"] and item["confidence"] >= 0.70:
            if item["amount"] > 0 and item["category"] not in ("其他", ""):
                if item["date_start"] or item["invoice_date"]:
                    item["status"] = "confirmed"
        db['invoices'].append(item)
        save_db(db)
        return jsonify(item)
    except Exception as e:
        import traceback
        print(f"上传异常: {e}")
        traceback.print_exc()
        return jsonify({"error": f"上传处理异常: {str(e)}"}), 500

@app.route('/api/import/zip', methods=['POST'])
def import_zip():
    """从 ZIP 文件批量导入发票"""
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({"error": "未选择文件"}), 400
    if not f.filename.lower().endswith('.zip'):
        return jsonify({"error": "请上传 ZIP 文件"}), 400

    import io
    try:
        content = f.read()
        zf = zipfile.ZipFile(io.BytesIO(content))
    except Exception:
        return jsonify({"error": "ZIP 文件损坏"}), 400

    # 安全检查
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            return jsonify({"error": "ZIP 包含不安全路径"}), 400

    ALLOWED_EXT = {'pdf', 'ofd', 'jpg', 'jpeg', 'png'}
    results = {"success": 0, "skipped": 0, "errors": []}

    for zip_name in zf.namelist():
        if zip_name.endswith('/'):
            continue
        ext = zip_name.rsplit('.', 1)[-1].lower() if '.' in zip_name else ''
        if ext not in ALLOWED_EXT:
            continue

        try:
            file_data = zf.read(zip_name)
            display_name = os.path.basename(zip_name)
            fname = f"{uuid.uuid4().hex[:8]}.{ext}"
            path = os.path.join(UPLOAD_DIR, fname)
            with open(path, 'wb') as out:
                out.write(file_data)

            cur_md5 = calculate_md5(path)

            with db_lock:
                db = load_db()
                duplicate = any(i.get('md5') == cur_md5 for i in db['invoices'])

            if duplicate:
                try: os.remove(path)
                except: pass
                results["skipped"] += 1
                continue

            # 图片转 PDF
            if ext in ('jpg', 'jpeg', 'png'):
                try:
                    img = Image.open(path)
                    if img.mode == 'RGBA': img = img.convert('RGB')
                    pdf_path = path.rsplit('.', 1)[0] + '.pdf'
                    a4_w, a4_h = 595, 842
                    scale = min(a4_w / img.size[0], a4_h / img.size[1])
                    new_w, new_h = int(img.size[0] * scale), int(img.size[1] * scale)
                    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                    a4_img = Image.new('RGB', (a4_w, a4_h), (255, 255, 255))
                    a4_img.paste(img_resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))
                    a4_img.save(pdf_path, 'PDF')
                    os.remove(path)
                    fname = fname.rsplit('.', 1)[0] + '.pdf'
                    path = pdf_path
                    ext = 'pdf'
                except Exception as e:
                    results["errors"].append(f"{display_name}: 图片转换失败")
                    continue

            # OFD 转 PDF
            if ext == 'ofd':
                pdf_path = ofd_to_pdf(path)
                if pdf_path:
                    fname = fname.replace('.ofd', '.pdf')
                    display_name = display_name.replace('.ofd', '.pdf')
                    try: os.remove(path)
                    except: pass
                    path = pdf_path
                    ext = 'pdf'
                else:
                    try: os.remove(path)
                    except: pass
                    results["errors"].append(f"{display_name}: OFD转换失败")
                    continue

            info = smart_ocr(path)
            current_settings = load_settings()
            if info.get("category") not in current_settings.get("categories", []):
                info["category"] = "其他"

            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item = {"id": str(uuid.uuid4()), "name": fname, "display_name": display_name,
                    "amount": info['amount'], "category": info['category'],
                    "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                    "date_start": info.get('date_start', ''), "date_end": info.get('date_end', ''),
                    "route": info.get('route', ''), "md5": cur_md5,
                    "document_type": info.get("document_type", ""),
                    "confidence": info.get("confidence", 0),
                    "matched_evidence": info.get("matched_evidence", []),
                    "risk_points": info.get("risk_points", []),
                    "needs_manual_review": info.get("needs_manual_review", False),
                    "seller_name": info.get("seller_name", ""),
                    "invoice_number": info.get("invoice_number", ""),
                    "source_type": ext if ext in ('ofd',) else ("image" if ext in ('jpg','jpeg','png') else "pdf"),
                    "batch_id": "", "status": "pending", "sort_order": 0,
                    "invoice_date": "", "notes": "", "duplicate_status": "none",
                    "created_at": now_str, "updated_at": now_str, "last_exported_at": ""}

            with db_lock:
                db = load_db()
                item["sort_order"] = len(db['invoices'])
                item["duplicate_status"] = check_duplicate(item, db['invoices'])
                if not item["needs_manual_review"] and item["confidence"] >= 0.70:
                    if item["amount"] > 0 and item["category"] not in ("其他", ""):
                        if item["date_start"] or item["invoice_date"]:
                            item["status"] = "confirmed"
                db['invoices'].append(item)
                save_db(db)

            results["success"] += 1
        except Exception as e:
            results["errors"].append(f"{zip_name}: {str(e)[:50]}")

    zf.close()
    return jsonify({"ok": True, **results})

@app.route('/api/import/folder', methods=['POST'])
def import_folder():
    """从本地文件夹批量导入发票"""
    d = request.get_json(silent=True) or {}
    folder = d.get("path", "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "无效的文件夹路径"}), 400

    ALLOWED_EXT = {'pdf', 'ofd', 'jpg', 'jpeg', 'png'}
    results = {"success": 0, "skipped": 0, "errors": [], "files": []}

    for fname in sorted(os.listdir(folder)):
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        if ext not in ALLOWED_EXT:
            continue
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue
        results["files"].append(fname)

    return jsonify({"ok": True, "count": len(results["files"]), "files": results["files"]})

@app.route('/api/import/folder/confirm', methods=['POST'])
def import_folder_confirm():
    """确认导入文件夹中的文件"""
    d = request.get_json(silent=True) or {}
    folder = d.get("path", "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "无效的文件夹路径"}), 400

    ALLOWED_EXT = {'pdf', 'ofd', 'jpg', 'jpeg', 'png'}
    results = {"success": 0, "skipped": 0, "errors": []}

    for fname in sorted(os.listdir(folder)):
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        if ext not in ALLOWED_EXT:
            continue
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue

        try:
            # 复制到 UPLOAD_DIR
            new_name = f"{uuid.uuid4().hex[:8]}.{ext}"
            dest = os.path.join(UPLOAD_DIR, new_name)
            shutil.copy2(fpath, dest)

            cur_md5 = calculate_md5(dest)
            with db_lock:
                db = load_db()
                duplicate = any(i.get('md5') == cur_md5 for i in db['invoices'])

            if duplicate:
                try: os.remove(dest)
                except: pass
                results["skipped"] += 1
                continue

            # 图片转 PDF
            if ext in ('jpg', 'jpeg', 'png'):
                try:
                    img = Image.open(dest)
                    if img.mode == 'RGBA': img = img.convert('RGB')
                    pdf_path = dest.rsplit('.', 1)[0] + '.pdf'
                    a4_w, a4_h = 595, 842
                    scale = min(a4_w / img.size[0], a4_h / img.size[1])
                    new_w, new_h = int(img.size[0] * scale), int(img.size[1] * scale)
                    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                    a4_img = Image.new('RGB', (a4_w, a4_h), (255, 255, 255))
                    a4_img.paste(img_resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))
                    a4_img.save(pdf_path, 'PDF')
                    os.remove(dest)
                    new_name = new_name.rsplit('.', 1)[0] + '.pdf'
                    dest = pdf_path
                    ext = 'pdf'
                except:
                    results["errors"].append(f"{fname}: 图片转换失败")
                    continue

            if ext == 'ofd':
                pdf_path = ofd_to_pdf(dest)
                if pdf_path:
                    new_name = new_name.replace('.ofd', '.pdf')
                    try: os.remove(dest)
                    except: pass
                    dest = pdf_path
                    ext = 'pdf'
                else:
                    try: os.remove(dest)
                    except: pass
                    results["errors"].append(f"{fname}: OFD转换失败")
                    continue

            info = smart_ocr(dest)
            current_settings = load_settings()
            if info.get("category") not in current_settings.get("categories", []):
                info["category"] = "其他"

            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item = {"id": str(uuid.uuid4()), "name": new_name, "display_name": fname,
                    "amount": info['amount'], "category": info['category'],
                    "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                    "date_start": info.get('date_start', ''), "date_end": info.get('date_end', ''),
                    "route": info.get('route', ''), "md5": cur_md5,
                    "document_type": info.get("document_type", ""),
                    "confidence": info.get("confidence", 0),
                    "matched_evidence": info.get("matched_evidence", []),
                    "risk_points": info.get("risk_points", []),
                    "needs_manual_review": info.get("needs_manual_review", False),
                    "seller_name": info.get("seller_name", ""),
                    "invoice_number": info.get("invoice_number", ""),
                    "source_type": ext if ext in ('ofd',) else ("image" if ext in ('jpg','jpeg','png') else "pdf"),
                    "batch_id": "", "status": "pending", "sort_order": 0,
                    "invoice_date": "", "notes": "", "duplicate_status": "none",
                    "created_at": now_str, "updated_at": now_str, "last_exported_at": ""}

            with db_lock:
                db = load_db()
                item["sort_order"] = len(db['invoices'])
                item["duplicate_status"] = check_duplicate(item, db['invoices'])
                if not item["needs_manual_review"] and item["confidence"] >= 0.70:
                    if item["amount"] > 0 and item["category"] not in ("其他", ""):
                        if item["date_start"] or item["invoice_date"]:
                            item["status"] = "confirmed"
                db['invoices'].append(item)
                save_db(db)

            results["success"] += 1
        except Exception as e:
            results["errors"].append(f"{fname}: {str(e)[:50]}")

    return jsonify({"ok": True, **results})

@app.route('/api/update', methods=['POST'])
def update():
    d = request.json
    if 'category' in d:
        valid_cats = load_settings().get('categories', [])
        if d['category'] not in valid_cats:
            return jsonify({"error": "无效的发票分类"}), 400
    # 只允许修改这些字段，防止覆盖 id/name/md5/confidence 等内部字段
    allowed = {"display_name", "amount", "category", "date_start", "date_end", "route",
               "invoice_number", "seller_name", "invoice_date", "notes", "batch_id", "status"}
    changes = {k: v for k, v in d.items() if k in allowed}
    if "amount" in changes:
        try: changes["amount"] = round(max(0.0, float(changes["amount"])), 2)
        except: changes["amount"] = 0.0
    db = load_db()
    for i in db['invoices']:
        if i['id'] == d.get('id'):
            if 'category' in changes and changes['category'] != i.get('category'):
                record_user_correction(
                    DATA_DIR,
                    seller_name=i.get('seller_name', ''),
                    title=i.get('document_type', ''),
                    original_category=i.get('category', ''),
                    correct_category=changes['category'],
                )
                i['needs_manual_review'] = False
                i['confidence'] = 1.0
            if 'status' in changes and changes['status'] in INVOICE_STATUSES:
                i['status'] = changes.pop('status')
            i.update(changes)
            i['updated_at'] = datetime.datetime.now().isoformat(timespec="seconds")
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
    db['invoices'] = []
    save_db(db)
    return jsonify("ok")

@app.route('/api/preview', methods=['POST'])
def preview():
    return jsonify({"url": f"/file/merged_preview.pdf?t={time.time()}"})

@app.route('/file/<name>')
def file(name):
    fname = os.path.basename(name)
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在"}), 404
    resp = make_response(send_file(path))
    resp.headers['Content-Disposition'] = 'inline'
    return resp

@app.route('/download/<name>')
def download_file(name):
    from urllib.parse import unquote
    fname = os.path.basename(unquote(name))
    path = os.path.join(BACKUP_DIR, fname)
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在"}), 404
    resp = make_response(send_file(path, as_attachment=True, download_name=fname))
    return resp

# ═══════════════════════════════════════════════════════════════════
# V6.3 批次管理 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/batches/create', methods=['POST'])
def batch_create():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()[:50]
    if not name:
        return jsonify({"ok": False, "code": "NAME_REQUIRED", "message": "批次名称不能为空"}), 400
    batch = {
        "id": str(uuid.uuid4()),
        "name": name,
        "applicant": str(d.get("applicant", "")).strip()[:20],
        "department": str(d.get("department", "")).strip()[:30],
        "project": str(d.get("project", "")).strip()[:30],
        "description": str(d.get("description", "")).strip()[:200],
        "status": "draft",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with db_lock:
        db = load_db()
        db["batches"].append(batch)
        save_db(db)
    return jsonify({"ok": True, "batch": batch})

@app.route('/api/batches/update', methods=['POST'])
def batch_update():
    d = request.get_json(silent=True) or {}
    bid = d.get("id", "")
    if not bid:
        return jsonify({"ok": False, "code": "MISSING_ID", "message": "缺少批次ID"}), 400
    allowed = {"name", "applicant", "department", "project", "description"}
    changes = {k: str(v).strip()[:50] for k, v in d.items() if k in allowed}
    with db_lock:
        db = load_db()
        batch = next((b for b in db["batches"] if b["id"] == bid), None)
        if not batch:
            return jsonify({"ok": False, "code": "BATCH_NOT_FOUND", "message": "批次不存在"}), 404
        batch.update(changes)
        batch["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_db(db)
    return jsonify({"ok": True, "batch": batch})

@app.route('/api/batches/delete', methods=['POST'])
def batch_delete():
    d = request.get_json(silent=True) or {}
    bid = d.get("id", "")
    force = d.get("force", False)
    with db_lock:
        db = load_db()
        batch = next((b for b in db["batches"] if b["id"] == bid), None)
        if not batch:
            return jsonify({"ok": False, "code": "BATCH_NOT_FOUND", "message": "批次不存在"}), 404
        inv_count = sum(1 for i in db["invoices"] if i.get("batch_id") == bid)
        if inv_count > 0 and not force:
            return jsonify({"ok": False, "code": "BATCH_NOT_EMPTY", "message": f"批次内有{inv_count}张发票", "count": inv_count}), 400
        if inv_count > 0 and force:
            for inv in db["invoices"]:
                if inv.get("batch_id") == bid:
                    inv["batch_id"] = ""
        db["batches"] = [b for b in db["batches"] if b["id"] != bid]
        save_db(db)
    return jsonify({"ok": True})

@app.route('/api/batches/status', methods=['POST'])
def batch_status():
    d = request.get_json(silent=True) or {}
    bid = d.get("id", "")
    new_status = d.get("status", "")
    if new_status not in BATCH_STATUSES:
        return jsonify({"ok": False, "code": "INVALID_STATUS", "message": "无效状态"}), 400
    with db_lock:
        db = load_db()
        batch = next((b for b in db["batches"] if b["id"] == bid), None)
        if not batch:
            return jsonify({"ok": False, "code": "BATCH_NOT_FOUND", "message": "批次不存在"}), 404
        batch["status"] = new_status
        batch["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_db(db)
    return jsonify({"ok": True, "batch": batch})

# ═══════════════════════════════════════════════════════════════════
# V6.3 批量操作 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/invoices/batch_update', methods=['POST'])
def invoices_batch_update():
    d = request.get_json(silent=True) or {}
    ids = d.get("ids", [])
    if not ids:
        return jsonify({"ok": False, "code": "MISSING_IDS", "message": "缺少发票ID列表"}), 400
    allowed = {"category", "batch_id", "status"}
    changes = {k: v for k, v in d.items() if k in allowed and v is not None}
    if not changes:
        return jsonify({"ok": False, "code": "NO_CHANGES", "message": "无有效修改"}), 400
    if "status" in changes and changes["status"] not in INVOICE_STATUSES:
        return jsonify({"ok": False, "code": "INVALID_STATUS", "message": "无效状态"}), 400
    success, fail = 0, 0
    with db_lock:
        db = load_db()
        for inv in db["invoices"]:
            if inv["id"] in ids:
                if "category" in changes and changes["category"] != inv.get("category"):
                    record_user_correction(DATA_DIR, seller_name=inv.get("seller_name",""),
                        title=inv.get("document_type",""), original_category=inv.get("category",""),
                        correct_category=changes["category"])
                inv.update(changes)
                inv["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                success += 1
        save_db(db)
    return jsonify({"ok": True, "success": success, "fail": fail})

@app.route('/api/invoices/batch_confirm', methods=['POST'])
def invoices_batch_confirm():
    d = request.get_json(silent=True) or {}
    ids = d.get("ids", [])
    warnings = []
    with db_lock:
        db = load_db()
        for inv in db["invoices"]:
            if inv["id"] in ids:
                issues = []
                if not inv.get("amount") or float(inv.get("amount", 0)) <= 0:
                    issues.append("金额缺失")
                if inv.get("category") in ("其他", ""):
                    issues.append("分类未确定")
                if issues:
                    warnings.append({"id": inv["id"], "name": inv.get("display_name",""), "issues": issues})
                inv["status"] = "confirmed"
                inv["needs_manual_review"] = False
                inv["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_db(db)
    return jsonify({"ok": True, "confirmed": len(ids), "warnings": warnings})

@app.route('/api/invoices/batch_delete', methods=['POST'])
def invoices_batch_delete():
    d = request.get_json(silent=True) or {}
    ids = d.get("ids", [])
    if not ids:
        return jsonify({"ok": False, "code": "MISSING_IDS"}), 400
    deleted = 0
    with db_lock:
        db = load_db()
        remaining = []
        for inv in db["invoices"]:
            if inv["id"] in ids:
                fpath = os.path.join(UPLOAD_DIR, inv["name"])
                if os.path.exists(fpath):
                    try: os.remove(fpath)
                    except: pass
                deleted += 1
            else:
                remaining.append(inv)
        db["invoices"] = remaining
        save_db(db)
    return jsonify({"ok": True, "deleted": deleted})

# ═══════════════════════════════════════════════════════════════════
# V6.3 报销材料导出 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/export/package', methods=['POST'])
def export_package():
    d = request.get_json(silent=True) or {}
    batch_id = d.get("batch_id", "")
    dup_special = d.get("duplicate_special", False)
    force = d.get("force_export", False)

    db = load_db()
    settings = load_settings()
    excluded = set(settings.get("excluded_report_categories", []))

    # 获取批次发票
    if batch_id:
        batch = next((b for b in db["batches"] if b["id"] == batch_id), None)
        if not batch:
            return jsonify({"ok": False, "code": "BATCH_NOT_FOUND"}), 404
        invoices = [i for i in db["invoices"] if i.get("batch_id") == batch_id]
        batch_name = batch["name"]
    else:
        invoices = db["invoices"]
        batch_name = "全部发票"

    if not invoices:
        return jsonify({"ok": False, "code": "NO_INVOICES", "message": "无发票可导出"}), 400

    # 异常检查
    pending = [i for i in invoices if i.get("status") == "pending"]
    missing_info = [i for i in invoices if not i.get("amount") or float(i.get("amount", 0)) <= 0 or i.get("category") in ("其他", "")]
    suspected = [i for i in invoices if i.get("duplicate_status") == "suspected"]

    anomalies = pending + missing_info + suspected
    if anomalies and not force:
        return jsonify({
            "ok": False, "code": "HAS_ANOMALIES",
            "pending": len(pending), "missing_info": len(missing_info),
            "suspected": len(suspected), "total": len(invoices)
        }), 409

    # 金额计算
    total_amount = 0.0
    for inv in invoices:
        if inv.get("category") not in excluded:
            total_amount += float(inv.get("amount", 0))

    now = datetime.datetime.now()
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', batch_name)

    try:
        import io
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 01_费用报销单.pdf
            report_pdf = _generate_report_pdf(batch, invoices, settings, excluded, total_amount)
            zf.writestr("01_费用报销单.pdf", report_pdf)

            # 02_发票拼版.pdf
            ids = [i["id"] for i in invoices if os.path.exists(os.path.join(UPLOAD_DIR, i["name"]))]
            pages, _ = _build_layout(ids, duplicate_special=dup_special)
            doc = fitz.open()
            W, H = 595, 842
            for items in pages:
                page = doc.new_page(width=W, height=H)
                for fpath, rect in items:
                    insert_to_page(page, fpath, rect)
                if len(items) == 2:
                    page.draw_line((20, H/2), (W-20, H/2), color=(0.7,0.7,0.7), dashes=[2])
            layout_buf = io.BytesIO()
            doc.save(layout_buf)
            doc.close()
            zf.writestr("02_发票拼版.pdf", layout_buf.getvalue())

            # 发票文件重命名并加入ZIP
            # 格式: 序号-发票类型-金额-开票单位-文件类型.pdf
            inv_seq = 0
            for inv in invoices:
                fpath = os.path.join(UPLOAD_DIR, inv["name"])
                if not os.path.exists(fpath):
                    continue
                inv_seq += 1
                cat = inv.get("category", "其他")
                amt = float(inv.get("amount", 0))
                amt_str = f"{amt:.2f}" if amt != int(amt) else str(int(amt))
                seller = re.sub(r'[<>:"/\\|?*\s]', '', inv.get("seller_name", ""))[:30]
                doc_type = inv.get("document_type", "")
                # 行程单 → 附单01，发票 → 发票
                if cat == "行程单" or "行程单" in doc_type:
                    role = "附单01"
                else:
                    role = "发票"
                if seller:
                    new_name = f"{inv_seq}-{cat}-{amt_str}元-{seller}-{role}.pdf"
                else:
                    new_name = f"{inv_seq}-{cat}-{amt_str}元-{role}.pdf"
                # 清理文件名中的非法字符
                new_name = re.sub(r'[<>:"/\\|?*]', '_', new_name)
                with open(fpath, 'rb') as f:
                    zf.writestr(f"发票/{new_name}", f.read())

            # 03_发票明细.csv
            csv_content = "﻿序号,原始文件名,发票号码,销售方,单据类型,费用分类,金额,开票日期,费用开始日期,费用结束日期,路线,状态,备注\n"
            for idx, inv in enumerate(invoices, 1):
                row = [
                    str(idx), inv.get("display_name",""), inv.get("invoice_number",""),
                    inv.get("seller_name",""), inv.get("document_type",""), inv.get("category",""),
                    f"{float(inv.get('amount',0)):.2f}", inv.get("invoice_date",""),
                    inv.get("date_start",""), inv.get("date_end",""), inv.get("route",""),
                    inv.get("status",""), inv.get("notes","")
                ]
                csv_content += ",".join(f'"{r}"' for r in row) + "\n"
            zf.writestr("03_发票明细.csv", csv_content.encode('utf-8'))

            # 04_异常清单.txt
            anomaly_lines = [f"报销批次：{batch_name}", f"导出时间：{now.strftime('%Y-%m-%d %H:%M:%S')}", ""]
            if pending:
                anomaly_lines.append(f"【待确认发票】共{len(pending)}张")
                for inv in pending:
                    anomaly_lines.append(f"  - {inv.get('display_name','')} ({inv.get('category','')})")
                anomaly_lines.append("")
            if missing_info:
                anomaly_lines.append(f"【信息缺失发票】共{len(missing_info)}张")
                for inv in missing_info:
                    anomaly_lines.append(f"  - {inv.get('display_name','')} 金额:{inv.get('amount',0)} 分类:{inv.get('category','')}")
                anomaly_lines.append("")
            if suspected:
                anomaly_lines.append(f"【疑似重复发票】共{len(suspected)}张")
                for inv in suspected:
                    anomaly_lines.append(f"  - {inv.get('display_name','')} 发票号:{inv.get('invoice_number','')}")
                anomaly_lines.append("")
            if not pending and not missing_info and not suspected:
                anomaly_lines.append("无异常")
            zf.writestr("04_异常清单.txt", "\n".join(anomaly_lines).encode('utf-8'))

        zip_buf.seek(0)
        zip_path = os.path.join(BACKUP_DIR, f"{safe_name}_报销材料_{now.strftime('%Y%m%d_%H%M%S')}.zip")
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(zip_path, 'wb') as f:
            f.write(zip_buf.getvalue())

        # 更新状态
        if batch_id:
            with db_lock:
                db = load_db()
                batch = next((b for b in db["batches"] if b["id"] == batch_id), None)
                if batch:
                    batch["status"] = "exported"
                    batch["updated_at"] = now.isoformat(timespec="seconds")
                for inv in db["invoices"]:
                    if inv.get("batch_id") == batch_id and inv.get("status") == "confirmed":
                        inv["status"] = "exported"
                        inv["last_exported_at"] = now.isoformat(timespec="seconds")
                save_db(db)

        return jsonify({"ok": True, "file": os.path.basename(zip_path),
                        "total_amount": round(total_amount, 2), "count": len(invoices)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "code": "EXPORT_ERROR", "message": str(e)}), 500

def _generate_report_pdf(batch, invoices, settings, excluded, total_amount):
    """生成报销单 PDF"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    W, H = 595, 842
    FONT = "china-s"

    title = settings.get("report_title", "费用报销单")
    applicant = batch.get("applicant", "") if batch else ""
    department = batch.get("department", "") if batch else ""
    batch_name = batch.get("name", "") if batch else ""

    # 标题居中
    title_size = 18
    title_w = fitz.get_text_length(title, fontname=FONT, fontsize=title_size)
    page.insert_text(((W - title_w) / 2, 50), title, fontsize=title_size, fontname=FONT)

    # 信息行
    y = 80
    page.insert_text((50, y), f"申请日期：{datetime.datetime.now().strftime('%Y-%m-%d')}", fontsize=11, fontname=FONT)
    page.insert_text((250, y), f"申请人：{applicant}", fontsize=11, fontname=FONT)
    page.insert_text((430, y), f"部门：{department}", fontsize=11, fontname=FONT)
    y += 20
    if batch_name:
        page.insert_text((50, y), f"报销批次：{batch_name}", fontsize=11, fontname=FONT)
    page.insert_text((300, y), f"附件张数：{len(invoices)} 张", fontsize=11, fontname=FONT)
    y += 30

    # 分类汇总表
    from collections import defaultdict
    cat_summary = defaultdict(lambda: {"count": 0, "total": 0.0})
    for inv in invoices:
        cat = inv.get("category", "其他")
        cat_summary[cat]["count"] += 1
        if cat not in excluded:
            cat_summary[cat]["total"] += float(inv.get("amount", 0))

    # 表格列定义
    COL1 = 50   # 费用类别
    COL2 = 280  # 单据数量
    COL3 = 450  # 金额
    TABLE_W = W - 100  # 表格宽度

    # 表头
    page.draw_line((50, y), (50 + TABLE_W, y), color=(0,0,0), width=1)
    y += 18
    page.insert_text((COL1, y), "费用类别", fontsize=11, fontname=FONT)
    page.insert_text((COL2, y), "单据数量", fontsize=11, fontname=FONT)
    page.insert_text((COL3, y), "金额 (RMB)", fontsize=11, fontname=FONT)
    y += 8
    page.draw_line((50, y), (50 + TABLE_W, y), color=(0,0,0), width=0.5)

    for cat, info in cat_summary.items():
        y += 22
        page.insert_text((COL1, y), cat, fontsize=11, fontname=FONT)
        # 数量居中
        count_str = str(info["count"])
        count_w = fitz.get_text_length(count_str, fontname=FONT, fontsize=11)
        page.insert_text((COL2 + (60 - count_w) / 2, y), count_str, fontsize=11, fontname=FONT)
        # 金额右对齐
        amt_str = f"¥{info['total']:.2f}"
        amt_w = fitz.get_text_length(amt_str, fontname=FONT, fontsize=11)
        page.insert_text((50 + TABLE_W - amt_w - 10, y), amt_str, fontsize=11, fontname=FONT)

    y += 8
    page.draw_line((50, y), (50 + TABLE_W, y), color=(0,0,0), width=1)
    y += 22
    page.insert_text((COL1, y), "合计", fontsize=12, fontname=FONT)
    total_str = f"¥{total_amount:.2f}"
    total_w = fitz.get_text_length(total_str, fontname=FONT, fontsize=12)
    page.insert_text((50 + TABLE_W - total_w - 10, y), total_str, fontsize=12, fontname=FONT)

    y += 30
    # 金额大写
    upper_str = f"金额大写：{_digit_upper(total_amount)}"
    page.insert_text((50, y), upper_str, fontsize=11, fontname=FONT)

    # 签字区
    y += 70
    sign_labels = ["报销申请人", "部门负责人", "财务审核"]
    sign_spacing = TABLE_W // 3
    for i, label in enumerate(sign_labels):
        x = 50 + i * sign_spacing + sign_spacing // 2
        lw = fitz.get_text_length(label, fontname=FONT, fontsize=11)
        page.insert_text((x - lw // 2, y), label, fontsize=11, fontname=FONT)
        page.draw_line((x - 60, y + 5), (x + 60, y + 5), color=(0,0,0), width=0.5)

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()

def _digit_upper(n):
    """金额大写转换"""
    n = float(n)
    if n == 0: return "零元整"
    digits = "零壹贰叁肆伍陆柒捌玖"
    int_part = int(n)
    frac_str = f"{n:.2f}".split('.')[1]
    jiao, fen = int(frac_str[0]), int(frac_str[1])
    result = ""
    if int_part > 0:
        units = ['','','','亿','','','万','','','']
        s = str(int_part)
        off = 10 - len(s)
        for i, c in enumerate(s):
            d = int(c)
            result += digits[d] + (units[off+i] if d else '')
        result = re.sub(r'零+', '零', result)
        result = re.sub(r'零(万|亿)', r'\1', result)
        result = result.replace('亿万', '亿')
        result += '元'
    if jiao == 0 and fen == 0:
        result += '整'
    else:
        result += (digits[jiao] + '角' if jiao else '') + (digits[fen] + '分' if fen else '')
    return re.sub(r'零元', '元', result).lstrip('元').replace('元整', '元整')

# ═══════════════════════════════════════════════════════════════════
# V6.3 备份恢复 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/backup/create', methods=['POST'])
def backup_create():
    now = datetime.datetime.now()
    fname = f"InvoiceBox_Backup_{now.strftime('%Y%m%d_%H%M%S')}.zip"
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, fname)
    try:
        write_backup_archive(target)
        return jsonify({"ok": True, "file": fname})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route('/api/backup/inspect', methods=['POST'])
def backup_inspect():
    f = request.files.get('file')
    if not f:
        return jsonify({"ok": False, "message": "未选择文件"}), 400
    try:
        import io
        zf = zipfile.ZipFile(io.BytesIO(f.read()))
        names = set(zf.namelist())
        if "database.json" not in names:
            return jsonify({"ok": False, "message": "备份包缺少 database.json"}), 400
        manifest = {}
        if "manifest.json" in names:
            manifest = json.loads(zf.read("manifest.json"))
        db = json.loads(zf.read("database.json"))
        return jsonify({
            "ok": True,
            "version": manifest.get("version", "未知"),
            "exported_at": manifest.get("exported_at", "未知"),
            "invoice_count": len(db.get("invoices", [])),
            "batch_count": len(db.get("batches", [])),
        })
    except Exception as e:
        return jsonify({"ok": False, "message": f"备份文件无效: {e}"}), 400

@app.route('/api/backup/restore', methods=['POST'])
def backup_restore():
    f = request.files.get('file')
    if not f:
        return jsonify({"ok": False, "message": "未选择文件"}), 400
    try:
        import io, tempfile as tf
        content = f.read()
        zf = zipfile.ZipFile(io.BytesIO(content))

        # 安全校验
        for name in zf.namelist():
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                return jsonify({"ok": False, "message": "备份包包含不安全路径"}), 400

        names = set(zf.namelist())
        if "database.json" not in names:
            return jsonify({"ok": False, "message": "备份包缺少 database.json"}), 400

        db_data = json.loads(zf.read("database.json"))
        if not isinstance(db_data, dict) or not isinstance(db_data.get("invoices"), list):
            return jsonify({"ok": False, "message": "数据库格式不正确"}), 400

        # 自动备份当前数据
        now = datetime.datetime.now()
        auto_backup = os.path.join(BACKUP_DIR, f"pre_restore_{now.strftime('%Y%m%d_%H%M%S')}.zip")
        os.makedirs(BACKUP_DIR, exist_ok=True)
        write_backup_archive(auto_backup)

        # 解压到临时目录验证
        with tf.TemporaryDirectory() as tmpdir:
            zf.extractall(tmpdir)
            # 验证发票文件存在
            for inv in db_data.get("invoices", []):
                fname = os.path.basename(str(inv.get("name", "")))
                fpath = os.path.join(tmpdir, "invoices", fname)
                if not os.path.exists(fpath):
                    return jsonify({"ok": False, "message": f"备份缺少发票文件: {fname}"}), 400

            # 替换数据
            import shutil
            # 复制发票文件
            src_invoices = os.path.join(tmpdir, "invoices")
            if os.path.exists(src_invoices):
                if os.path.exists(UPLOAD_DIR):
                    shutil.rmtree(UPLOAD_DIR)
                shutil.copytree(src_invoices, UPLOAD_DIR)

        # 写入数据库
        save_db(db_data)
        if "settings.json" in names:
            settings_data = json.loads(zf.read("settings.json"))
            save_settings(settings_data)
        # 恢复用户分类学习规则
        if "user_classifications.json" in names:
            rules_path = os.path.join(DATA_DIR, "user_classifications.json")
            try:
                with open(rules_path, 'wb') as rf:
                    rf.write(zf.read("user_classifications.json"))
            except Exception as e:
                print(f"恢复用户规则失败: {e}")

        return jsonify({"ok": True, "invoice_count": len(db_data.get("invoices", [])),
                        "auto_backup": os.path.basename(auto_backup)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "message": f"恢复失败: {e}"}), 500

@app.route('/api/data/open_folder', methods=['POST'])
def open_folder():
    d = request.get_json(silent=True) or {}
    target = d.get("target", "data")
    folder = DATA_DIR if target == "data" else BACKUP_DIR
    try:
        os.startfile(folder)
    except Exception:
        pass
    return jsonify({"ok": True, "path": folder})

# ═══════════════════════════════════════════════════════════════════
# V6.3 分类规则管理 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/rules', methods=['GET'])
def rules_list():
    rules = load_user_rules(DATA_DIR)
    return jsonify({"ok": True, "rules": rules})

@app.route('/api/rules/delete', methods=['POST'])
def rules_delete():
    d = request.get_json(silent=True) or {}
    idx = d.get("index", -1)
    rules = load_user_rules(DATA_DIR)
    if 0 <= idx < len(rules):
        rules.pop(idx)
        path = os.path.join(DATA_DIR, "user_classifications.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(rules, f, indent=2, ensure_ascii=False)
        except: pass
    return jsonify({"ok": True, "rules": rules})

@app.route('/api/rules/clear', methods=['POST'])
def rules_clear():
    path = os.path.join(DATA_DIR, "user_classifications.json")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([], f)
    except: pass
    return jsonify({"ok": True})

# ═══════════════════════════════════════════════════════════════════
# V6.5 收件箱 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/inbox/scan', methods=['POST'])
def inbox_scan():
    """手动触发收件箱扫描"""
    imported = _inbox_scan()
    return jsonify({"ok": True, "imported": len(imported)})

@app.route('/api/inbox/configure', methods=['POST'])
def inbox_configure():
    """设置收件箱路径"""
    d = request.get_json(silent=True) or {}
    path = d.get("path", "").strip()
    if path and not os.path.isdir(path):
        return jsonify({"ok": False, "error": "无效的文件夹路径"}), 400
    settings = load_settings()
    settings["inbox_dir"] = path
    save_settings(settings)
    return jsonify({"ok": True, "inbox_dir": path})

# ═══════════════════════════════════════════════════════════════════
# V6.5 统计 API
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/stats', methods=['GET'])
def stats():
    """返回发票统计数据"""
    db = load_db()
    invoices = db.get("invoices", [])
    settings = load_settings()
    excluded = set(settings.get("excluded_report_categories", []))

    total_count = len(invoices)
    total_amount = sum(float(i.get("amount", 0)) for i in invoices if i.get("category") not in excluded)
    status_counts = {}
    cat_summary = {}
    month_summary = {}
    seller_summary = {}

    for inv in invoices:
        # 状态统计
        st = inv.get("status", "pending")
        status_counts[st] = status_counts.get(st, 0) + 1

        cat = inv.get("category", "其他")
        amt = float(inv.get("amount", 0))

        # 分类统计
        if cat not in cat_summary:
            cat_summary[cat] = {"count": 0, "total": 0.0}
        cat_summary[cat]["count"] += 1
        if cat not in excluded:
            cat_summary[cat]["total"] += amt

        # 月度统计
        date = inv.get("date_start") or inv.get("invoice_date") or inv.get("date", "")
        if date and len(date) >= 7:
            month = date[:7]  # YYYY-MM
            if month not in month_summary:
                month_summary[month] = {"count": 0, "total": 0.0}
            month_summary[month]["count"] += 1
            if cat not in excluded:
                month_summary[month]["total"] += amt

        # 销售方统计
        seller = inv.get("seller_name", "").strip()
        if seller:
            if seller not in seller_summary:
                seller_summary[seller] = {"count": 0, "total": 0.0}
            seller_summary[seller]["count"] += 1
            if cat not in excluded:
                seller_summary[seller]["total"] += amt

    # 排序
    top_sellers = sorted(seller_summary.items(), key=lambda x: x[1]["total"], reverse=True)[:20]
    top_months = sorted(month_summary.items(), key=lambda x: x[0], reverse=True)[:12]

    return jsonify({
        "total_count": total_count,
        "total_amount": round(total_amount, 2),
        "status_counts": status_counts,
        "categories": cat_summary,
        "months": dict(top_months),
        "top_sellers": [{"name": k, **v, "total": round(v["total"], 2)} for k, v in top_sellers],
    })

if __name__ == '__main__':
    # 启动收件箱监听
    threading.Thread(target=_inbox_watcher, daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open('http://127.0.0.1:5000'))).start()
    print(">>> 启动成功！请在浏览器访问 http://127.0.0.1:5000 <<<")
    app.run(port=5000, debug=False)
