# 作者：DeepSeek-V4.1-Flash 2026-11-02
# 最近修改：DeepSeek-V4.1-Flash 2026-11-08（「检查 0 个」不再算通过，退出码 2）
"""
换行符守护：把「记住要检查」换成「跑一下就报」。

为什么有它：2026-11-02 一天之内同一个坑踩了四次（Python 文本模式读写把 CRLF
静默转成 LF），每次都靠人回想起来才发现，其中一次已经 `git diff` 从 5 行炸到
3869 行。记笔记拦不住，只能上工具。

用法：
    python tools/eol_guard.py --check 文件或目录...     # 只报，退出码 1 = 有异常
    python tools/eol_guard.py --fix  文件或目录...      # 按 git HEAD 的换行符还原

判法：拿工作区文件的 CRLF 比例与 `git show HEAD:<path>` 比。不一致就是被动过。
只比换行符，不碰内容；`--fix` 也是逐字节只改 \\r\\n / \\n。
新文件（git 里没有）、二进制扩展名、未跟踪文件一律跳过。

退出码（2026-11-08 加）：0 = 查过且一致；1 = 有文件被动了；**2 = 一个都没查**。
为什么单列 2：ROOT 按脚本位置取，拿本脚本去查别的库，文件全在 ROOT 之外，
`git_show` 一律返回 None → 全部跳过 → 老版本照样打印「全部一致」、返回 0。
**「检查 0 个」不是「没问题」，是「没查」**，两者不能共用一个退出码。
正确用法是把本脚本拷进那个库再跑（见 `docs/老库补装清单.md`）。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 明确当文本处理的扩展名；不在表里的一律跳过，避免碰二进制
TEXT_EXT = {'.md', '.tsv', '.json', '.py', '.txt', '.ass', '.srt', '.csv',
            '.html', '.yml', '.yaml', '.bat', '.sh'}
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv'}


def git_show(path: Path):
    """取 git HEAD 里的原始字节；文件不在版本库里返回 None。

    resolve() 会把 symlink 解到真身，所以 `C:/Users/Annoy/.workbuddy/skills/subtitle-read/`
    这种路径也能落到项目内、正常比对（那些技能是指回项目 .workbuddy/skills/ 的软链）。
    解完仍在本项目之外的（从市场装的技能、用户目录里的杂项）一律返回 None 跳过，
    不许抛异常——2026-11-02 扫整个 C 盘 skills 目录时就是在这儿 ValueError 崩的。
    """
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None
    r = subprocess.run(['git', 'show', f'HEAD:{rel}'],
                       cwd=ROOT, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def git_clean(path: Path) -> bool:
    """git 是否认为这个文件没变（内容层面，忽略换行归一）。"""
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return False
    r = subprocess.run(['git', 'diff', '--quiet', '--', rel], cwd=ROOT)
    return r.returncode == 0


def nl_profile(b: bytes):
    """返回 (CRLF 数, 纯 LF 数)。"""
    crlf = b.count(b'\r\n')
    return crlf, b.count(b'\n') - crlf


def collect(targets):
    out = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            out += [f for f in p.rglob('*')
                    if f.is_file()
                    and f.suffix.lower() in TEXT_EXT
                    and not any(s in f.parts for s in SKIP_DIRS)]
        elif p.is_file():
            out.append(p)
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', nargs='+', metavar='PATH')
    ap.add_argument('--fix', nargs='+', metavar='PATH')
    a = ap.parse_args()
    if not a.check and not a.fix:
        sys.exit('要么 --check，要么 --fix')

    bad, fixed, skipped = [], [], 0
    for p in collect(a.check or a.fix):
        old = git_show(p)
        if old is None:
            skipped += 1
            continue
        cur = p.read_bytes()
        oc, ol = nl_profile(old)
        cc, cl = nl_profile(cur)
        # 原文件本身两种混用的情况放过，只揪「整篇被换掉」
        old_kind = 'crlf' if oc > ol else 'lf'
        cur_kind = 'crlf' if cc > cl else 'lf'
        if old_kind == cur_kind:
            continue
        # autocrlf 的正常形态：库里存 LF、工作区检出成 CRLF，git 认为内容没变。
        # 这不是回归，是 checkout 的转换——2026-11-02 扫 04_工具 时 10 个 overrides/*.json
        # 全被这条误报（mtime 还是 10-23）。反方向（库里 CRLF、工作区变 LF）不放过，
        # 那正是 11-02 那天 .ass 被写坏的样子。
        if old_kind == 'lf' and cur_kind == 'crlf' and git_clean(p):
            continue
        if a.fix:
            t = cur.decode('utf-8-sig')
            if old_kind == 'crlf':
                t = t.replace('\r\n', '\n').replace('\n', '\r\n')
            else:
                t = t.replace('\r\n', '\n')
            out = t.encode('utf-8')
            if cur.startswith(b'\xef\xbb\xbf'):
                out = b'\xef\xbb\xbf' + out
            p.write_bytes(out)
            fixed.append((p, old_kind, cur_kind))
        else:
            bad.append((p, old_kind, cur_kind, oc, cc))

    if a.fix:
        for p, ok, ck in fixed:
            print(f'已还原  {p}  {ck} → {ok}')
        print(f'还原 {len(fixed)} 个，跳过（不在 git/未跟踪）{skipped} 个')
        if len(fixed) == 0 and skipped:
            print('一个都没动过——注意：跳过 = 没检查，不等于本来就是对的。')
        return 0

    for p, ok, ck, oc, cc in bad:
        print(f'异常  {p}')
        print(f'      git HEAD {ok}（CRLF {oc}）→ 现在 {ck}（CRLF {cc}）')
    if bad:
        print(f'\n{len(bad)} 个文件的换行符被动了。跑 --fix 还原，或改回你的写入方式。')
        return 1
    checked = len(collect(a.check)) - skipped
    if checked == 0:
        # 2026-11-08（GQ-18）：这里是「绿的但没查」最典型的样子。老版本会打印
        # 「全部一致」并返回 0，人不会怀疑。查了 0 个，唯一诚实的结论是「没查」。
        print(f'跳过 {skipped} 个，检查 0 个——**没查，不是通过**。')
        print('最常见原因：拿本脚本去查别的库。ROOT 按脚本位置取，库外文件全被跳过；')
        print('正确做法是把 eol_guard.py 拷进那个库再跑（见 docs/老库补装清单.md）。')
        return 2
    print(f'换行符全部一致（检查 {checked} 个，跳过 {skipped} 个）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
