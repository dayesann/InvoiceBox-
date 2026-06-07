import os

print("开始生成 final.html...")

# 1. 检查原料
if not os.path.exists('templates/index.html'):
    print("❌ 错误：找不到 templates/index.html")
    exit()

# 2. 读取 helper
def read_asset(name):
    path = f"static/{name}"
    if not os.path.exists(path):
        print(f"❌ 错误：找不到资源文件 {path}，请先运行 download_fix.py")
        return ""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

# 3. 读取 HTML
with open('templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

print("正在读取 JS/CSS 文件...")
css = read_asset('index.min.css')
vue = read_asset('vue.global.js')
ele = read_asset('index.full.js')
ico = read_asset('icons.js')

if not css or not vue or not ele or not ico:
    print("❌ 终止：资源文件缺失，无法生成。")
    exit()

# 4. 注入
print("正在焊接...")
html = html.replace('<link rel="stylesheet" href="/static/index.min.css" />', f'<style>{css}</style>')

js_bundle = f"<script>{vue}</script>\n<script>{ele}</script>\n<script>{ico}</script>"
html = html.replace('<script src="/static/vue.global.js"></script>', js_bundle)
html = html.replace('<script src="/static/index.full.js"></script>', '')
html = html.replace('<script src="/static/icons.js"></script>', '')

# 5. 保存
with open('templates/final.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("✅ 成功！已生成 templates/final.html")