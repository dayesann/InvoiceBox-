import os
import urllib.request
import ssl

# 1. 准备环境
ssl._create_default_https_context = ssl._create_unverified_context
if not os.path.exists('static'): os.makedirs('static')

# 2. 定义资源 (使用 unpkg 稳定源)
files = {
    "vue.js": "https://unpkg.com/vue@3.3.4/dist/vue.global.js",
    "element-plus.js": "https://unpkg.com/element-plus@2.3.14/dist/index.full.js",
    "element-plus.css": "https://unpkg.com/element-plus@2.3.14/dist/index.min.css",
    "icons.js": "https://unpkg.com/@element-plus/icons-vue@2.1.0/dist/index.iife.min.js"
}

print("🚀 开始下载标准资源...")

for name, url in files.items():
    path = f"static/{name}"
    print(f"⬇️ 正在下载 {name}...")
    try:
        # 伪装浏览器下载
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as r, open(path, 'wb') as f:
            f.write(r.read())
        
        # 检查大小
        size = os.path.getsize(path)
        if size < 1000: # 小于1KB肯定是坏的
            print(f"❌ 警告：{name} 似乎下载失败 (大小仅 {size}B)")
        else:
            print(f"✅ {name} 成功 ({int(size/1024)} KB)")
    except Exception as e:
        print(f"❌ {name} 下载出错: {e}")

print("\n资源准备完毕！")