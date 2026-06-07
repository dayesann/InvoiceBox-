import os
import urllib.request
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

if not os.path.exists('static'): os.makedirs('static')

print("正在补齐缺失的 CSS 文件...")

# 这是一个极其稳定的源
url = "https://unpkg.com/element-plus@2.3.14/dist/index.css"
save_path = "static/element-plus.css"

try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as r, open(save_path, 'wb') as f:
        f.write(r.read())
    print(f"✅ 成功！已保存为 {save_path}")
except Exception as e:
    print(f"❌ 下载失败: {e}")
    print("请尝试手动下载：")
    print(url)
    print("然后重命名为 element-plus.css 放入 static 文件夹")