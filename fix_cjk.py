# Read the file
with open(r'c:\Users\Richmond\Desktop\Codebase\Pseudo-Scribe\generate.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The actual content has double backslashes in the Python string literals
old_text = 'lines.append("\\\\\\\\usepackage[hidelinks]{hyperref}\\\\n\\\\n")'
new_text = 'lines.append("\\\\\\\\usepackage[hidelinks]{hyperref}\\\\n")\n    # CJK package for Unicode character support (e.g., Chinese characters in UTF-8 examples)\n    lines.append("\\\\\\\\usepackage{CJK}\\\\n")'

if old_text in content:
    content = content.replace(old_text, new_text)
    with open(r'c:\Users\Richmond\Desktop\Codebase\Pseudo-Scribe\generate.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Successfully added CJK package to preamble')
else:
    print('Pattern not found')
