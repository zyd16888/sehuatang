
import re


def special_char_sub(text):
    # 用于保存 code 块
    protected_code = {}

    # 替换函数，用于 re.sub 中
    def replace_code_block(match):
        idx = len(protected_code)
        placeholder = f"CODEBLOCKPLACEHOLDER{idx}UNIQUE"
        protected_code[placeholder] = match.group(0)  # 包括反引号的完整内容
        return placeholder

    # 第一步：用正则替换所有 `code` 块为占位符
    text = re.sub(r"`[^`]+`", replace_code_block, text)

    # 第二步：转义 MarkdownV2 特殊字符
    old_strs = [
        "_", "*", "[", "]", "(", ")", "~", "`", ">", "#",
        "+", "-", "=", "|", "{", "}", ".", "!"
    ]
    new_strs = [
        r"\_", r"\*", r"\[", r"\]", r"\(", r"\)", r"\~", r"\`", r"\>", r"\#",
        r"\+", r"\-", r"\=", r"\|", r"\{", r"\}", r"\.", r"\!"
    ]
    for old, new in zip(old_strs, new_strs):
        text = text.replace(old, new)

    # 第三步：还原 code 块
    for placeholder, code in protected_code.items():
        text = text.replace(placeholder, code)

    return text


if __name__ == "__main__":
    text = """
com-452 中出しされたパパ活美少女 「ゴムして」って言ったよね

磁力链接：
`magnet:?xt=urn:btih:B1B5FB29ADCC2A05EECB7539AC70BFF455AB7CA7`

发布时间：2025-05-26 12:32:48

"""
    text = special_char_sub(text)
    print(text)