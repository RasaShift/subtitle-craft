#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-07（第 3 类改成扫目录：硬编码 7 个脚本，新加的 7 件一直没被查）
"""env_check.py -- 环境体检：一条命令报「这台机器上这套东西还在不在、齐不齐」

为什么有它：换机器、重装、隔一阵回来开工，第一件事总是猜「钩子还挂着吗」「换行符
规则还在吗」。猜错的代价是后面所有结论都建在错前提上，而且不会报错。
把该查的压成一条命令，就没有猜的理由。

用法：
    python tools/env_check.py

报六类：
  1 版本库    有没有 git、几个提交、工作区干不干净
  2 换行符     .gitattributes 在不在、是不是锁 LF、提交钩子挂没挂
  3 工具      tools/ 下每个脚本多少行（扫目录，新增的也自动看得见）
  4 文档      docs/ 下每份多少行、有没有超上限
  5 自检      selftest.py 跑不跑得过
  6 依赖      只用标准库；Python 版本太低会明说

只读，不改任何东西。缺东西就报缺，不自动补。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
DOCS = ROOT / 'docs'
# 工具分两档：核心件是产品定义里的四件（缺了本库就不成立），脚手架是钩子与自检依赖的。
# 其余脚本不列名单——扫目录自动纳入。2026-11-07 修：原来硬编码 7 个，之后新加的
# land_verify / accept / text_scan / style_normalize / build_base / bilingual_pairs /
# term_candidates 七件一直没被查，结尾却照样打「环境齐了」——清单式检查必然过时，
# 和上面 DOC_LIMITS 那段是同一个道理，当时只对文档套了，脚本没套。
CORE_TOOLS = ['subtitle_probe.py', 'align_bilingual.py', 'triage_diff.py', 'land_verify.py']
SCAFFOLD_TOOLS = ['doc_lint.py', 'doc_split.py', 'eol_guard.py', 'selftest.py']
# 只列「名字固定」的文档。工序文档不在这查——它们会改名（2026-11-06 就改过一轮），
# 硬编码在这里等于给未来的自己埋一个静默失效。工序文档的行数由 doc_lint 的 glob 管。
# 判定记录不列在这里：账本不设总量上限，只控单卷（那条由 doc_lint 的 `vol` 管）。
# 2026-11-07 前这里写 400、doc_lint 写 350、落点表写「不设限」——同一个数三处必漂，
# 判定记录自己那条早判了「取消 400 行总量上限」，代码却还留着，属于账本违反自己。
DOC_LIMITS = {'接棒必读.md': 60, '待办台账.md': 60,
              '文档落点表.md': 80, '流程总图.md': 100, '命名准则.md': 60}


def sh(*args):
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        return (r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip())
    except FileNotFoundError:
        return (127, '', 'git 不在 PATH 里')


def lines_of(p: Path):
    return len(p.read_bytes().decode('utf-8-sig').split('\n')) - 1


def main() -> int:
    bad = []

    print('== 1 版本库')
    code, out, err = sh('git', 'rev-parse', '--is-inside-work-tree')
    if code != 0:
        print('  [缺] 这不是 git 仓库（或没装 git）')
        bad.append('版本库')
    else:
        n = sh('git', 'rev-list', '--count', 'HEAD')[1]
        dirty = sh('git', 'status', '--porcelain')[1]
        print(f'  仓库正常，{n or "0"} 个提交，工作区{"有未提交改动" if dirty else "干净"}')

    print('== 2 换行符与钩子')
    ga = ROOT / '.gitattributes'
    if not ga.is_file():
        print('  [缺] .gitattributes 不在——换行符没人管')
        bad.append('gitattributes')
    else:
        txt = ga.read_text(encoding='utf-8-sig')
        lock = 'eol=lf' in txt
        print(f'  .gitattributes {"锁 LF" if lock else "【没锁 LF】"}')
        if not lock:
            bad.append('LF 未锁')
    hooks = sh('git', 'config', 'core.hooksPath')[1]
    if hooks != '.githooks':
        print(f'  [缺] 提交钩子没挂（core.hooksPath={hooks or "空"}）'
              f'——新机器要跑：git config core.hooksPath .githooks')
        bad.append('钩子')
    else:
        print('  提交钩子已挂（.githooks）')

    print('== 3 工具（扫目录，新增脚本自动纳入）')
    found = sorted(p for p in TOOLS.glob('*.py'))
    for p in found:
        tags = [t for t, names in (('核心', CORE_TOOLS), ('脚手架', SCAFFOLD_TOOLS))
                if p.name in names]
        print(f'  {p.name}  {lines_of(p)} 行' + (f'  [{"·".join(tags)}]' if tags else ''))
    for name in CORE_TOOLS + SCAFFOLD_TOOLS:
        if not (TOOLS / name).is_file():
            print(f'  [缺] {name}')
            bad.append(name)
    print(f'  共 {len(found)} 个脚本；核心 {len(CORE_TOOLS)} 件、脚手架 {len(SCAFFOLD_TOOLS)} 件缺了才报错')

    print('== 4 文档')
    for p in sorted(DOCS.rglob('*.md')):
        n = lines_of(p)
        lim = DOC_LIMITS.get(p.name)
        flag = ''
        if lim and n > lim:
            flag = f'  [超上限 {lim}]'
            bad.append(f'{p.name} 超限')
        print(f'  {p.relative_to(ROOT).as_posix()}  {n} 行{flag}')
    for p in sorted(DOCS.glob('*.tsv')):
        print(f'  {p.name}  {lines_of(p)} 行')

    print('== 5 自检')
    code, out, err = sh(sys.executable, str(TOOLS / 'selftest.py'))
    last = (out or err).strip().split('\n')[-1] if (out or err) else ''
    print(f'  {"过" if code == 0 else "[不过]"}  {last}')
    if code != 0:
        bad.append('自检')

    print('== 6 依赖')
    print(f'  Python {sys.version.split()[0]}，只用标准库')
    if sys.version_info < (3, 8):
        print('  [警告] 版本偏低，脚本按 3.8+ 写')
        bad.append('Python 版本')

    print()
    if bad:
        print(f'有 {len(bad)} 项要处理：' + '、'.join(bad))
        return 1
    print('环境齐了。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
