import os, sys, json, uuid, datetime, re, threading, webbrowser, time, base64, zipfile, hashlib
import mimetypes
import fitz  # PyMuPDF
from flask import Flask, request, jsonify, send_file, Response, make_response

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
MERGED_PDF = os.path.join(UPLOAD_DIR, 'merged_preview.pdf')

if not os.path.exists(UPLOAD_DIR): os.makedirs(UPLOAD_DIR)

app = Flask(__name__, template_folder=TEMPLATE_DIR)

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
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {"invoices": []}

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

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

def smart_ocr(path):
    info = {"amount": 0.0, "category": "其他"}
    text = ""
    try:
        if path.lower().endswith('.ofd'): text = read_ofd_source(path)
        if not text.strip():
            with fitz.open(path) as doc:
                for p in doc: text += p.get_text()
        
        # 预处理：保留关键符号，去除多余空白
        clean_txt = text.replace(" ", "").replace("¥", "￥").replace("CNY", "￥").replace("\n", "")
        
        if '行程单' in clean_txt: info['category'] = '行程单'
        elif '火车' in clean_txt or '铁路' in clean_txt or 'G1' in clean_txt: info['category'] = '火车票'
        elif '航空运输' in clean_txt or '客票' in clean_txt: info['category'] = '机票'
        elif '客运' in clean_txt or '运输服务' in clean_txt or '通行费' in clean_txt: info['category'] = '交通'
        elif '餐饮' in clean_txt or '美食' in clean_txt: info['category'] = '餐饮'
        elif '酒店' in clean_txt or '住宿' in clean_txt: info['category'] = '住宿'

        amount_str = ""
        # 针对特定类型的精准提取
        if info['category'] == '火车票':
            m = re.search(r'票价[^0-9]*([0-9]+\.[0-9]{2})', clean_txt)
            if m: amount_str = m.group(1)
        elif info['category'] in ['机票', '行程单']:
             # 针对机票行程单的合计
             m = re.search(r'(合计|共计|Total)[^0-9]*([0-9]+\.[0-9]{2})', clean_txt)
             if m: amount_str = m.group(2)
        
        # 如果上面没找到，或者不是特殊类型，使用“贪婪模式”
        if not amount_str:
            candidates = []
            
            # 策略A：寻找 "小写" 或 "合计" 后面紧跟着的数字（允许中间有 ￥ 或 () 等干扰字符）
            # [^0-9.\n]* 表示：只要不是数字和换行，中间夹什么符号我都忍了
            matches_A = re.findall(r'(?:小写|合计|￥|¥)[^0-9.\n]*([0-9,]+\.[0-9]{2})', clean_txt)
            for val in matches_A:
                try: candidates.append(float(val.replace(',', '')))
                except: pass
            
            # 策略B：如果策略A失败，寻找全文中最后出现的带小数点的数字（通常发票最大的数字在最后）
            if not candidates:
                matches_B = re.findall(r'([0-9,]+\.[0-9]{2})', clean_txt)
                for val in matches_B:
                    try: candidates.append(float(val.replace(',', '')))
                    except: pass
            
            if candidates: 
                # 取最大值通常比较稳妥
                info['amount'] = max(candidates)

        if amount_str: info['amount'] = float(amount_str)
        return info
    except: return info

@app.route('/api/render_pages', methods=['POST'])
def render_pages():
    ids = request.json.get('ids', [])
    db = load_db()
    fmap = {i['id']: i['name'] for i in db['invoices']}
    files = [os.path.join(UPLOAD_DIR, fmap[i]) for i in ids if i in fmap]
    images = []
    for fpath in files:
        try:
            with fitz.open(fpath) as doc:
                page = doc[0]
                rot = -90 if page.rect.height > page.rect.width else 0
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5).prerotate(rot))
                b64 = base64.b64encode(pix.tobytes("png")).decode('ascii')
                images.append(f"data:image/png;base64,{b64}")
        except: images.append("")
    return jsonify({"images": images})

def insert_to_page(page, fpath, rect):
    try:
        src = fitz.open(fpath)
        src_page = src[0]
        rot = -90 if src_page.rect.height > src_page.rect.width else 0
        page.show_pdf_page(rect, src, 0, rotate=rot, keep_proportion=True)
    except: pass

@app.route('/api/download_pdf', methods=['POST'])
def download_pdf():
    ids = request.json.get('ids', [])
    db = load_db()
    fmap = {i['id']: i['name'] for i in db['invoices']}
    files = [os.path.join(UPLOAD_DIR, fmap[i]) for i in ids if i in fmap]
    doc = fitz.open()
    W, H = 595, 842
    for i in range(0, len(files), 2):
        page = doc.new_page(width=W, height=H)
        insert_to_page(page, files[i], fitz.Rect(20, 20, W-20, H/2 - 10))
        if i+1 < len(files):
            insert_to_page(page, files[i+1], fitz.Rect(20, H/2 + 10, W-20, H-20))
            page.draw_line((20, H/2), (W-20, H/2), color=(0.7,0.7,0.7), dashes=[2])
    doc.save(MERGED_PDF)
    return jsonify({"url": "/file/merged_preview.pdf"})

@app.route('/api/init')
def init(): return jsonify(load_db())

@app.route('/api/upload', methods=['POST'])
def upload():
    f = request.files['file']
    if f:
        ext = f.filename.split('.')[-1].lower()
        name = f"{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(UPLOAD_DIR, name)
        f.save(path)
        
        cur_md5 = calculate_md5(path)
        db = load_db()
        for i in db['invoices']:
            if i.get('md5') == cur_md5:
                os.remove(path)
                return jsonify({"error": "重复文件", "code": "DUPLICATE"}), 400

        final = name; disp = f.filename
        if ext == 'ofd':
            try:
                doc = fitz.open(path)
                pdf_bytes = doc.convert_to_pdf()
                final = name.replace('.ofd', '.pdf')
                with open(os.path.join(UPLOAD_DIR, final), "wb") as f_pdf: f_pdf.write(pdf_bytes)
                disp = f.filename.replace('.ofd', '.pdf')
            except: pass

        info = smart_ocr(path)
        item = {"id": str(uuid.uuid4()), "name": final, "display_name": disp, "amount": info['amount'], "category": info['category'], "date": datetime.datetime.now().strftime("%Y-%m-%d"), "md5": cur_md5}
        db['invoices'].append(item)
        save_db(db)
        return jsonify(item)

@app.route('/api/update', methods=['POST'])
def update():
    d = request.json
    db = load_db()
    for i in db['invoices']:
        if i['id'] == d['id']:
            i.update(d)
            break
    save_db(db)
    return jsonify("ok")

@app.route('/api/delete', methods=['POST'])
def delete():
    id = request.json.get('id')
    db = load_db()
    db['invoices'] = [i for i in db['invoices'] if i['id'] != id]
    save_db(db)
    return jsonify("ok")

@app.route('/api/clear', methods=['POST'])
def clear(): 
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

last_hb = time.time()
@app.before_request
def hb(): 
    global last_hb
    if request.endpoint == 'heartbeat': last_hb = time.time()

def start_watchdog():
    time.sleep(10)
    while True:
        time.sleep(1)

if __name__ == '__main__':
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open('http://127.0.0.1:5000'))).start()
    threading.Thread(target=start_watchdog, daemon=True).start()
    print(">>> 启动成功！请在浏览器访问 http://127.0.0.1:5000 <<<")
    app.run(port=5000, debug=False)