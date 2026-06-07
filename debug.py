import sys
import fitz # PyMuPDF
import re

def debug_pdf(path):
    print(f"--- 正在分析: {path} ---")
    try:
        with fitz.open(path) as doc:
            text = ""
            for page in doc:
                text += page.get_text()
        
        print("\n[原始文本内容]:")
        print("-" * 30)
        print(text)
        print("-" * 30)
        
        # 模拟金额匹配
        print("\n[尝试匹配金额]:")
        patterns = [
            r'(小写|金额|价税合计|总额)[^0-9\n]*?([¥￥]?\s*\d{1,3}(,\d{3})*(\.\d{2})?)',
            r'票价[：:]\s*([¥￥]?\s*[\d.]+)',
            r'￥\s*([\d.]+)',
            r'(\d+\.\d{2})'
        ]
        for p in patterns:
            match = re.search(p, text)
            if match:
                print(f"规则 '{p}' 匹配到: {match.group(0)}")
            else:
                print(f"规则 '{p}' 未匹配")

    except Exception as e:
        print(f"错误: {e}")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        debug_pdf(sys.argv[1])
    else:
        print("请将 PDF 文件拖到这个脚本上运行，或者输入: python debug.py 文件名.pdf")
        # 你也可以在这里写死一个路径来测试
        # debug_pdf("test.pdf")
    input("\n按回车键退出...")