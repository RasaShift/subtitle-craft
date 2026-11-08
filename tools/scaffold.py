#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-07（--with-skills 带上 `命名准则.md`：工序文档靠它才读得通）
"""scaffold.py -- 一条命令把「文档骨架」展开成一个新库

为什么有它：三样资产里「文档骨架模板」一直只有本库自己用的一份。
新库要建同一套东西只能手抄，抄漏一项不报错——等到某天跑体检才发现，
那时已经写了几十份文档，改起来比一开始建对贵得多。

用法：
    python tools/scaffold.py <目标目录> [--name 库名] [--with-skills] [--force]

做四件事（加 --with-skills 是五件）：
  1 复制 examples/新库骨架/ 的文档与配置，替换 {{库名}} {{日期}} 占位
  2 复制三个脚手架脚本：doc_lint / doc_split / eol_guard
  3 建 .workbuddy/memory/
  4 加 --with-skills 时：复制 4 份 SKILL.md + 判据型工序文档（docs/工序-*.md）+ 手册 + 命名准则（称呼表）+ 新剧接入模板
  5 在新库里真跑一次 doc_lint —— 不过就说明这套骨架本身坏了，别拿去用

--with-skills 带手册、不带实证（docs/工序-*-手册.md 带，-实证.md 不带）。
**2026-11-07 改**：手册已脱敏入库（11 份 1739 行），而 4 份技能正文都指向它
（`docs/工序-<名>-手册.md`）——不带就是 4 处死链，新库从建起来那天就坏。
实证仍不带：它记的是「哪一集踩了什么坑」，含真实集号，新库得自己攒。

不覆盖已存在的文件：撞名跳过并报出来。真要覆盖用 --force，它会逐个报改了哪些。

判据：新库建完，`python tools/doc_lint.py` 退出码 0。
来源：本库 2026-11-07 把「文档骨架缺可复制形态」补上（台账第 4 条）。
"""
import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKELETON = ROOT / 'examples' / '新库骨架'
TOOLS = ('doc_lint.py', 'doc_split.py', 'eol_guard.py')
# 需要替换占位符的文件：按后缀和文件名两种认法（.gitattributes 和钩子都没后缀）
PLAIN = {'.md', '.txt'}


def is_plain(p: Path) -> bool:
    return p.suffix.lower() in PLAIN or p.name in ('.gitattributes', 'pre-commit')


def copy_skeleton(dst: Path, name: str, date: str, force: bool):
    made, skipped = [], []
    for src in sorted(SKELETON.rglob('*')):
        if not src.is_file():
            continue
        rel = src.relative_to(SKELETON)
        target = dst / rel
        if target.exists() and not force:
            skipped.append(rel.as_posix())
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_plain(src):
            text = src.read_text(encoding='utf-8')
            text = text.replace('{{库名}}', name).replace('{{日期}}', date)
            target.write_text(text, encoding='utf-8', newline='\n')
        else:
            shutil.copy2(src, target)
        made.append(rel.as_posix())
    return made, skipped


def copy_tools(dst: Path, force: bool):
    made, skipped = [], []
    for n in TOOLS:
        src = ROOT / 'tools' / n
        target = dst / 'tools' / n
        if not src.is_file():
            print(f'  [警告] 本库缺 tools/{n}，新库不会有这一件')
            continue
        if target.exists() and not force:
            skipped.append(f'tools/{n}')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        made.append(f'tools/{n}')
    return made, skipped


def copy_skills(dst: Path, force: bool):
    """技能一块：4 份 SKILL.md + 判据型工序文档 + 手册 + 命名准则 + 新剧接入模板两份。

    **带手册、不带实证**：手册 2026-11-07 已脱敏入库，而 4 份技能正文都指着它
    （`docs/工序-<名>-手册.md`），不带就是死链。实证仍不带——它记的是「哪一集
    踩了什么坑」，含真实集号，新库要自己攒。
    判据型工序文档必须一起搬：技能正文只留指针，指向的 `docs/工序-*.md` 没有就是一堆死链。
    """
    made, skipped = [], []
    pairs = [(p, Path('skills') / p.parent.name / 'SKILL.md')
             for p in sorted((ROOT / 'skills').glob('subtitle-*/SKILL.md'))]
    # 排除实证、**带手册**（2026-11-07 改：技能正文指着手册，不带就是死链；手册已脱敏入库）。
    # **用 in 不用 endswith**：`工序-审稿-实证-品控.md` 的 stem 以「-品控」
    # 结尾，endswith('-实证') 漏放它——2026-11-07 实测带出去过一份实证，那是红线。
    pairs += [(p, Path('docs') / p.name) for p in sorted((ROOT / 'docs').glob('工序-*.md'))
              if '-实证' not in p.stem]
    # 命名准则不是工序文档，是称呼表（DR0–DR5 代号、剧型三个词、前缀登记）。
    # 带过去的工序文档里有两处指向它（工序-审稿 指第五节、工序-对稿 指第三节），
    # 不带就是死链；它 60 行、不含台词，可以进新库。
    pairs.append((ROOT / 'docs' / '命名准则.md', Path('docs') / '命名准则.md'))
    # 下面四份被「已经带过去的东西」直接引用，不带就是**第二层死链**（2026-11-07 第二轮审计实地点出）：
    #   手册 → 判断纪律 / findings写法；4 份技能 → 中间产物格式；
    #   命名准则与工序-审稿-判据.md → 工序-审稿-判据表.tsv。
    # 判据表是 .tsv，glob `工序-*.md` 天然抓不到，必须单列。四份都已脱敏、不含台词。
    pairs += [(ROOT / 'docs' / n, Path('docs') / n) for n in (
        '工序-审稿-判据表.tsv', '判断纪律.md', 'findings写法.md', '中间产物格式.md',
        '派活单模板.md', '流程总图.md')]
    pairs += [(p, Path('examples') / p.name) for p in (ROOT / 'examples' / '新剧接入.md',
                                                      ROOT / 'examples' / '词表样例.tsv')]
    if not pairs:
        print('  [警告] 本库找不到可搬的技能或工序文档')
    for src, rel in pairs:
        if not src.is_file():
            print(f'  [警告] 本库缺 {src.relative_to(ROOT).as_posix()}，新库不会有这一件')
            continue
        target = dst / rel
        if target.exists() and not force:
            skipped.append(rel.as_posix())
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
        made.append(rel.as_posix())
    return made, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description='把文档骨架展开成一个新库')
    ap.add_argument('target', help='新库目录（不存在就建）')
    ap.add_argument('--name', help='库名，写进 README 与接棒必读；默认用目录名')
    ap.add_argument('--with-skills', action='store_true',
                    help='连技能一块建：4 份 SKILL.md + 工序文档（判据 + 手册）+ 命名准则 + 判据表 / 判断纪律 / findings写法 / 中间产物格式（不含实证）')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的文件（危险，先看清楚）')
    a = ap.parse_args()

    if not SKELETON.is_dir():
        sys.exit(f'找不到骨架目录：{SKELETON}')
    dst = Path(a.target).resolve()
    name = a.name or dst.name
    date = datetime.date.today().isoformat()

    dst.mkdir(parents=True, exist_ok=True)
    memory = dst / '.workbuddy' / 'memory'
    memory.mkdir(parents=True, exist_ok=True)
    keep = memory / '.gitkeep'
    if not keep.exists():
        keep.write_text('', encoding='utf-8')
    # 三层目录：`examples/新剧接入.md:21` 定了这个结构，成品与中间件不能混在一起。
    # 2026-11-08 加（剧库报的 GQ-9：建完没有成品层，finalB2.ass 和 mech/mech2 同目录）。
    for sub in ('01_源字幕', '02_工作区', '03_成品'):
        d = dst / sub
        d.mkdir(parents=True, exist_ok=True)
        k = d / '.gitkeep'
        if not k.exists():
            k.write_text('', encoding='utf-8')

    made1, skip1 = copy_skeleton(dst, name, date, a.force)
    made2, skip2 = copy_tools(dst, a.force)
    made, skipped = made1 + made2, skip1 + skip2
    if a.with_skills:
        made3, skip3 = copy_skills(dst, a.force)
        made += made3
        skipped += skip3

    for f in made:
        print(f'  建 {f}')
    for f in skipped:
        print(f'  跳过（已存在）{f}')
    print(f'\n{len(made)} 个文件建好，{len(skipped)} 个跳过。库名「{name}」，日期 {date}。')

    lint = dst / 'tools' / 'doc_lint.py'
    if not lint.is_file():
        print('[未自检] 新库里没有 tools/doc_lint.py')
        return 1
    r = subprocess.run([sys.executable, str(lint)], cwd=dst, capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    tail = out.strip().split('\n')[-1]
    print(f'自检文档体检：{"过" if r.returncode == 0 else "不过"}  {tail}')

    # 骨架自带的每一份，缺一份就是模板坏了，别当预期 WARN 混过去。
    # 2026-11-08 改成直接查磁盘：原写法靠 doc_lint 输出「找不到 docs/X」来判，只覆盖
    # `LIMITS` 里那几份 md——`卡点.tsv`、`.gitattributes` 这类根本不在它的检查范围，
    # 缺了照样绿。现在按骨架目录逐份核，模板里放什么就核什么。
    missing = [p.relative_to(SKELETON).as_posix() for p in sorted(SKELETON.rglob('*'))
               if p.is_file() and not (dst / p.relative_to(SKELETON)).exists()]
    if missing:
        print(f'  [真问题] 骨架文件没建出来：{" / ".join(missing[:6])}')
        return 1

    warns = [l for l in out.split('\n') if l.startswith('WARN')]
    if warns:
        print(f'  预期 WARN {len(warns)} 条，不是骨架坏了：')
        for l in warns[:12]:
            print(f'    {l}')
        if len(warns) > 12:
            print(f'    ……还有 {len(warns) - 12} 条')
        # 骨架只带六份「每个库都必须有」的：接棒必读 / 待办台账 / 台账详情 / 判定记录 / 落点表 / 进度台账。
        # `做法` 只带一、二节（七条常驻原则 + 三级阶梯）：「跟隔壁库差什么」那节是通用库
        # 自己的账，新库没有「隔壁」。2026-11-08 由「不带」改成「带通用部分」——剧库第一棒
        # 报 GQ-4：三级阶梯在新库里读不到，同一个坑记到第五次还只是「记」。
        print('    两类来源，都不是问题：① 命名准则、流程总图、判据表、判断纪律、findings写法、中间产物格式、派活单模板 都带上了——')
        print('       它们被手册和技能正文直接引用，不带就是死链；')
        print('    ② 实证（docs/工序-*-实证.md）记的是「哪一集踩了什么坑」，含真实集号，只留本机；手册已带上。')
        print('    还有两处断是故意的：新库里 5 处指向 `-实证.md` 的指针失效（实证要自己攒），')
        print('    `tools/` 整个目录也不在模板里，文档点名的工具要自己从原库拷过去。')
    if r.returncode != 0:
        print('  骨架本身有问题，别拿去用——先在本库把模板改对')
        return 1

    print('\n还要手工做两件（脚本不替你做，它们改的是你的机器或仓库）：')
    print(f'  cd {dst} && git init && git config core.hooksPath .githooks')
    print('  然后照 docs/接棒必读.md 的「第一件事」开工')
    return 0


if __name__ == '__main__':
    sys.exit(main())
