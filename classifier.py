"""
Multi-evidence weighted invoice classifier.
"""
import re, os, json, datetime

STRONG, MEDIUM, WEAK, NEGATIVE = 40, 20, 8, -30

DOC_TYPE_PATTERNS = [
    ("航空运输电子客票行程单", r'航空运输电子客票行程单'),
    ("铁路电子客票", r'铁路电子客票'),
    ("电子客票行程单", r'电子客票行程单'),
    ("数电票", r'数电票|全电发票'),
    ("增值税电子普通发票", r'增值税电子普通发票'),
    ("增值税普通发票", r'增值税普通发票'),
    ("增值税专用发票", r'增值税专用发票'),
    ("地铁行程单", r'地铁.{0,6}行程单|轨道交通.{0,6}行程单'),
    ("公交行程单", r'公交.{0,6}行程单'),
    ("打车行程单", r'打车.{0,4}行程单|网约车行程单|出租车行程单|出行.{0,4}行程单|地图.{0,6}行程单'),
    ("出租车发票", r'出租车发票'),
]

CATEGORY_RULES = [
    {"category": "机票", "strong": [
        ("航空运输电子客票行程单", STRONG), ("电子客票行程单", STRONG),
        ("乘机人", STRONG), ("旅客姓名", STRONG), ("航班号", STRONG),
        ("中国国航", STRONG), ("国际航空", STRONG), ("东方航空", STRONG), ("东航", STRONG),
        ("南方航空", STRONG), ("南航", STRONG), ("海南航空", STRONG), ("海航", STRONG),
        ("厦门航空", STRONG), ("深圳航空", STRONG), ("春秋航空", STRONG), ("吉祥航空", STRONG),
        ("四川航空", STRONG), ("山东航空", STRONG), ("长龙航空", STRONG), ("首都航空", STRONG),
    ], "medium": [
        ("舱位", MEDIUM), ("民航发展基金", MEDIUM), ("燃油附加费", MEDIUM),
        ("机场建设费", MEDIUM), ("出发地", MEDIUM), ("目的地", MEDIUM), ("始发地", MEDIUM),
        ("承运人", MEDIUM),
    ], "weak": [("航空", WEAK), ("民航", WEAK), ("机场", WEAK)],
     "negative": [("运输服务", NEGATIVE), ("客运服务费", NEGATIVE), ("出行人", NEGATIVE), ("出行日期", NEGATIVE), ("交通工具类型", NEGATIVE)],
     "doc_type_hints": ["航空运输电子客票行程单", "电子客票行程单"]},

    {"category": "火车票", "strong": [
        ("铁路电子客票", STRONG), ("中国铁路", STRONG), ("12306", STRONG), ("国铁集团", STRONG),
        ("火车票", STRONG), ("出发站", STRONG), ("到达站", STRONG), ("席别", STRONG),
        ("二等座", STRONG), ("一等座", STRONG), ("商务座", STRONG),
        ("硬卧", STRONG), ("软卧", STRONG), ("无座", STRONG),
    ], "medium": [("动车", MEDIUM), ("高铁", MEDIUM), ("车次", MEDIUM)],
     "weak": [("G\\d{3,5}", WEAK), ("D\\d{3,5}", WEAK), ("C\\d{3,5}", WEAK),
              ("Z\\d{3,5}", WEAK), ("T\\d{3,5}", WEAK), ("K\\d{3,5}", WEAK)],
     "negative": [("运输服务", NEGATIVE), ("客运服务费", NEGATIVE)],
     "doc_type_hints": ["铁路电子客票"]},

    {"category": "打车费", "strong": [
        ("滴滴出行", STRONG), ("滴滴打车", STRONG), ("高德地图打车", STRONG), ("高德打车", STRONG),
        ("高德地图", STRONG), ("曹操出行", STRONG), ("T3出行", STRONG), ("首汽约车", STRONG),
        ("如祺出行", STRONG), ("享道出行", STRONG), ("出租汽车", STRONG),
        ("网络预约出租汽车", STRONG), ("打车行程单", STRONG), ("网约车行程单", STRONG),
        ("出租车发票", STRONG), ("花小猪", STRONG), ("打车", STRONG),
    ], "medium": [
        ("上车地点", MEDIUM), ("下车地点", MEDIUM), ("起点", MEDIUM), ("终点", MEDIUM),
        ("行程开始时间", MEDIUM), ("行程结束时间", MEDIUM), ("里程", MEDIUM),
        ("车牌号", MEDIUM), ("司机", MEDIUM), ("出行人", MEDIUM), ("出行日期", MEDIUM),
        ("客运服务费", MEDIUM), ("运输服务", MEDIUM),
    ], "weak": [],
     "negative": [("铁路", NEGATIVE), ("航空", NEGATIVE)],
     "doc_type_hints": ["网约车行程单", "打车行程单", "出租车发票"]},

    {"category": "公共交通", "strong": [
        ("地铁", STRONG), ("轨道交通", STRONG), ("公共交通", STRONG), ("公交", STRONG),
        ("城市通", STRONG), ("一卡通", STRONG), ("交通联合", STRONG),
        ("地铁行程单", STRONG), ("公交行程单", STRONG),
        ("轨道交通有限公司", STRONG), ("地铁运营有限公司", STRONG),
        ("公交集团", STRONG), ("公交有限公司", STRONG),
    ], "medium": [
        ("进站", MEDIUM), ("出站", MEDIUM), ("乘车码", MEDIUM),
        ("城市轨道交通", MEDIUM), ("公共交通乘车", MEDIUM),
    ], "weak": [], "negative": [], "doc_type_hints": ["地铁行程单", "公交行程单"]},

    {"category": "住宿", "strong": [
        ("住宿服务", STRONG), ("客房费", STRONG), ("房费", STRONG), ("酒店", STRONG),
        ("宾馆", STRONG), ("旅店", STRONG), ("民宿", STRONG), ("公寓酒店", STRONG),
        ("入住", STRONG), ("离店", STRONG), ("房晚", STRONG),
    ], "medium": [
        ("携程", MEDIUM), ("飞猪", MEDIUM), ("美团酒店", MEDIUM), ("去哪儿", MEDIUM),
        ("同程旅行", MEDIUM), ("华住", MEDIUM), ("锦江", MEDIUM), ("如家", MEDIUM),
        ("汉庭", MEDIUM), ("全季", MEDIUM), ("亚朵", MEDIUM), ("维也纳", MEDIUM),
        ("希尔顿", MEDIUM), ("万豪", MEDIUM), ("洲际", MEDIUM),
    ], "weak": [], "negative": [], "doc_type_hints": ["住宿发票", "酒店发票"]},

    {"category": "餐饮", "strong": [
        ("餐饮服务", STRONG), ("餐费", STRONG), ("食品", STRONG), ("饮品", STRONG),
        ("咖啡", STRONG), ("茶饮", STRONG), ("奶茶", STRONG), ("堂食", STRONG),
        ("外卖", STRONG), ("酒楼", STRONG), ("饭店", STRONG), ("餐厅", STRONG),
        ("咖啡馆", STRONG), ("茶馆", STRONG), ("火锅", STRONG), ("烧烤", STRONG),
        ("小吃", STRONG), ("快餐", STRONG),
    ], "medium": [
        ("美团", MEDIUM), ("饿了么", MEDIUM), ("星巴克", MEDIUM), ("瑞幸", MEDIUM),
        ("库迪", MEDIUM), ("奈雪", MEDIUM), ("喜茶", MEDIUM), ("麦当劳", MEDIUM),
        ("肯德基", MEDIUM), ("必胜客", MEDIUM), ("海底捞", MEDIUM),
    ], "weak": [], "negative": [], "doc_type_hints": ["餐饮发票"]},

    {"category": "邮寄费", "strong": [
        ("快递服务", STRONG), ("邮政服务", STRONG), ("物流服务", STRONG), ("运费", STRONG),
        ("寄递服务", STRONG), ("快递费", STRONG), ("邮寄费", STRONG), ("EMS", STRONG),
        ("中国邮政", STRONG), ("顺丰", STRONG), ("顺丰速运", STRONG), ("京东物流", STRONG),
        ("中通", STRONG), ("圆通", STRONG), ("申通", STRONG), ("韵达", STRONG),
        ("极兔", STRONG), ("德邦", STRONG), ("跨越速运", STRONG),
    ], "medium": [
        ("运单号", MEDIUM), ("寄件人", MEDIUM), ("收件人", MEDIUM), ("重量", MEDIUM),
        ("快递单号", MEDIUM), ("物流单号", MEDIUM), ("包裹", MEDIUM),
    ], "weak": [], "negative": [], "doc_type_hints": ["快递发票", "物流发票"]},

    {"category": "办公", "strong": [
        ("办公用品", STRONG), ("文具", STRONG), ("耗材", STRONG), ("打印", STRONG),
        ("复印", STRONG), ("纸张", STRONG), ("硒鼓", STRONG), ("墨盒", STRONG),
        ("办公设备", STRONG), ("电脑配件", STRONG), ("鼠标", STRONG), ("键盘", STRONG),
        ("显示器", STRONG),
    ], "medium": [], "weak": [], "negative": [], "doc_type_hints": []},
]

AMBIGUOUS_KEYWORDS = {"运输服务费", "运输服务", "服务费", "信息服务费", "技术服务费"}

def extract_fields(clean_txt, raw_text=""):
    fields = {}
    _STOP = r'(?=项目名称|纳税人识别号|金额|税额|发票号码|票据号码|日期|地址|开户|编号|$)'
    m = re.search(r'销售方\s*名?\s*称?\s*[：:]?\s*([一-鿿][一-鿿\w\(\)（）\-　]+?)' + _STOP, clean_txt)
    if m: fields["seller_name"] = m.group(1)
    else:
        parts = re.split(r'销售方', clean_txt)
        if len(parts) > 1:
            q = re.search(r'名\s*称\s*[：:]?\s*([一-鿿][一-鿿\w\(\)（）\-　]+?)' + _STOP, parts[1])
            if q: fields["seller_name"] = q.group(1)
    title_m = re.search(r'(航空运输电子客票行程单|铁路电子客票|电子客票行程单|增值税[电⼦]?[电子]?普通发票|增值税专用发票|数电票|全电发票|网约车行程单|打车行程单|地铁行程单|公交行程单|出租车发票)', clean_txt)
    if title_m: fields["title"] = title_m.group(1)
    items = re.findall(r'项目名称\s*([一-鿿\w\s/]+?)(?:规格|单位|数量|金额|税率|税额|\d+\.\d{2})', clean_txt)
    if items: fields["item_names"] = [i.strip() for i in items if i.strip()]
    pm = re.search(r'(?:乘机人|旅客姓名|乘客)\s*[：:]\s*([一-鿿]{2,4})', clean_txt)
    if pm: fields["passenger_name"] = pm.group(1)
    fm = re.search(r'(?:航班号?|航班)\s*[：:]?\s*([A-Z]{2}\d{3,5})', clean_txt)
    if not fm: fm = re.search(r'(?<![A-Z])([A-Z]{2}\d{3,5})', clean_txt)
    if fm: fields["flight_number"] = fm.group(fm.lastindex) if fm.lastindex else fm.group(1)
    tm = re.search(r'(?:车次|列车)\s*[：:]?\s*([GDZTCK]\d{3,5})', clean_txt)
    if not tm: tm = re.search(r'([GDZTCK]\d{3,5})', clean_txt)
    if tm: fields["train_number"] = tm.group(1)
    dep_m = re.search(r'(?:出发站|出发地|始发地?|起点|上车地点|从)\s*[：:]\s*([一-鿿]{2,10})', clean_txt)
    if dep_m: fields["departure"] = dep_m.group(1)
    arr_m = re.search(r'(?:到达站|到达地|目的地?|终点|下车地点|到)\s*[：:]\s*([一-鿿]{2,10})', clean_txt)
    if arr_m: fields["arrival"] = arr_m.group(1)
    ci_m = re.search(r'(?:入住|入住日期)\s*[：:]?\s*(\d{4}[\-/]\d{1,2}[\-/]\d{1,2})', clean_txt)
    if ci_m: fields["checkin_date"] = ci_m.group(1)
    co_m = re.search(r'(?:离店|退房|离店日期)\s*[：:]?\s*(\d{4}[\-/]\d{1,2}[\-/]\d{1,2})', clean_txt)
    if co_m: fields["checkout_date"] = co_m.group(1)
    tk_m = re.search(r'(?:运单号|快递单号|物流单号|单号)\s*[：:]\s*([A-Za-z0-9]{6,20})', clean_txt)
    if tk_m: fields["tracking_number"] = tk_m.group(1)
    inv_m = re.search(r'(?:发票号码|票据号码)\s*[：:]\s*(\d{8,20})', clean_txt)
    if inv_m: fields["invoice_number"] = inv_m.group(1)
    return fields

def detect_document_type(fields, clean_txt):
    title = fields.get("title", "")
    for doc_type, pattern in DOC_TYPE_PATTERNS:
        if re.search(pattern, title): return doc_type
    for doc_type, pattern in DOC_TYPE_PATTERNS:
        if re.search(pattern, clean_txt): return doc_type
    return ""

def _check(pattern, clean_txt, fields):
    if re.search(pattern, clean_txt): return True
    for key in ("seller_name", "title"):
        v = fields.get(key, "")
        if v and re.search(pattern, v): return True
    for item in fields.get("item_names", []):
        if re.search(pattern, item): return True
    return False

def score_categories(fields, clean_txt, user_rules=None):
    if user_rules:
        seller = fields.get("seller_name", "")
        items = fields.get("item_names", [])
        title = fields.get("title", "")
        for rule in user_rules:
            match = False
            if rule.get("seller_name") and seller and rule["seller_name"] in seller: match = True
            if rule.get("item_names") and items:
                for ri in rule["item_names"]:
                    if any(ri in it for it in items): match = True; break
            if rule.get("title") and title and rule["title"] in title: match = True
            if match:
                return [(rule["correct_category"], 100.0,
                         [f"用户规则: {rule.get('seller_name','') or rule.get('title','')}"], [])]
    results = []
    for rule in CATEGORY_RULES:
        cat, score, evidence, risk = rule["category"], 0.0, [], []
        for p, w in rule["strong"]:
            if _check(p, clean_txt, fields): score += w; evidence.append(p)
        for p, w in rule["medium"]:
            if _check(p, clean_txt, fields): score += w; evidence.append(p)
        for p, w in rule["weak"]:
            if _check(p, clean_txt, fields): score += w; evidence.append(p)
        for p, w in rule["negative"]:
            if _check(p, clean_txt, fields): score += w; evidence.append(f"负面:{p}")
        doc_type = detect_document_type(fields, clean_txt)
        if doc_type and doc_type in rule.get("doc_type_hints", []):
            score += 20; evidence.append(f"单据类型:{doc_type}")
        seller = fields.get("seller_name", "")
        if seller:
            for p, w in rule["strong"]:
                if re.search(p, seller): score += 15; evidence.append(f"销售方:{p}"); break
        has_strong = any(_check(p, clean_txt, fields) for p, _ in rule["strong"])
        if evidence and not has_strong and score <= WEAK * 2:
            risk.append("仅命中弱证据")
        results.append((cat, score, evidence, risk))
    results.sort(key=lambda x: x[1], reverse=True)
    return results

def compute_confidence(scores, clean_txt):
    risk_points = []
    if not scores or scores[0][1] <= 0:
        return 0.0, True, ["无法识别任何分类特征"]
    top_score = scores[0][1]
    risk_points.extend(scores[0][3])
    has_strong = any(not e.startswith("负面:") and not e.startswith("单据类型:") and not e.startswith("销售方:")
                     for e in scores[0][2])
    if has_strong:
        confidence = min(1.0, 0.85 + (top_score - STRONG) / 80.0)
    else:
        confidence = min(1.0, top_score / 60.0)
    found_amb = [kw for kw in AMBIGUOUS_KEYWORDS if kw in clean_txt]
    ambiguous_only = bool(found_amb and top_score <= WEAK * 2)
    if ambiguous_only:
        risk_points.append(f"仅命中模糊词: {', '.join(found_amb)}")
    needs_review = False
    if has_strong and confidence >= 0.85:
        # 有强证据且置信度高：直接自动分类，不需要确认
        needs_review = False
    elif confidence < 0.70:
        needs_review = True
        if not risk_points: risk_points.append("置信度过低")
    if not has_strong and len(scores) >= 2 and scores[1][1] > 0:
        gap = top_score - scores[1][1]
        if gap < 10:
            needs_review = True
            risk_points.append(f"与{scores[1][0]}分数接近({gap}分)")
    if ambiguous_only: needs_review = True
    return confidence, needs_review, risk_points

def classify(clean_txt, raw_text="", user_rules=None):
    fields = extract_fields(clean_txt, raw_text)
    doc_type = detect_document_type(fields, clean_txt)
    scores = score_categories(fields, clean_txt, user_rules)
    confidence, needs_review, risk_points = compute_confidence(scores, clean_txt)
    if scores and scores[0][1] > 0:
        best_cat, evidence = scores[0][0], scores[0][2]
    else:
        best_cat, evidence, needs_review = "其他", [], True
    # 打车/地铁/公交行程单 → 行程单（附件），航空行程单 → 机票（报销凭证）
    if doc_type in ('打车行程单', '地铁行程单', '公交行程单'):
        best_cat = '行程单'
    return {"document_type": doc_type, "expense_category": best_cat,
            "confidence": round(confidence, 2), "matched_evidence": evidence,
            "risk_points": risk_points, "needs_manual_review": needs_review}

def _user_rules_path(data_dir):
    return os.path.join(data_dir, "user_classifications.json")

def load_user_rules(data_dir):
    path = _user_rules_path(data_dir)
    if not os.path.exists(path): return []
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return []

def record_user_correction(data_dir, seller_name="", item_names=None, title="",
                           original_category="", correct_category=""):
    path = _user_rules_path(data_dir)
    rules = load_user_rules(data_dir)
    rules.append({"seller_name": seller_name, "item_names": item_names or [], "title": title,
                  "original_category": original_category, "correct_category": correct_category,
                  "created_at": datetime.datetime.now().isoformat()})
    seen = {}
    for r in rules:
        seen[(r.get("seller_name",""), r.get("correct_category",""))] = r
    rules = list(seen.values())
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(rules, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e: print(f"保存用户规则失败: {e}")
