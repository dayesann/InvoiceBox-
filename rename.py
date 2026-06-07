import os

# 目标文件名标准
mapping = {
    "vue.js": "vue.global.js",
    "element-plus.js": "index.full.js", 
    "element-plus.css": "index.min.css"
}

os.chdir('static')
files = os.listdir()

print("正在标准化文件名...")
for old_name, new_name in mapping.items():
    if old_name in files:
        if os.path.exists(new_name):
            os.remove(new_name) # 如果新名字已存在，先删掉，防止冲突
        os.rename(old_name, new_name)
        print(f"✅ 重命名: {old_name} -> {new_name}")
    elif new_name in files:
        print(f"🆗 已存在: {new_name}")
    else:
        print(f"⚠️ 没找到: {old_name} (如果 {new_name} 也不在，可能会缺文件)")

print("文件名整理完毕。")