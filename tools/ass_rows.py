# -*- coding: utf-8 -*-
"""ass_rows -- 遍历 ASS 的 Dialogue 行，拆好中英，给行号与时间码。

为什么有它：`blind_sample` / `validate_semantic` / `prefilter_semantic` 原来都靠
另外一个库的剧专用解析器。那个模块拖着一份**真实人名表**，
按红线不能进本库。这里只做它们真正用到的那部分：**行号、时间、中文、英文**。

口径与 `land_verify.load` 同源——不另写一套解析，否则两处切法不同，
同一行会得出不同的中英（实测过：自己切会把 `\\N{\\rEnglish}` 吃成空中文）。

行号是 ASS 文件里的**物理行号（1 起）**，和 findings、worksheet 里的行号是同一个东西。
"""
import pathlib
import re
import sys

import land_verify

# 英文里至少三个连续字母算「有英文」——用来判断「拆不开是因为这份字幕本来就没英文，
# 还是因为分隔符不是 {\rEnglish}」。不判的话，整份字幕会被静默判成「无英文」。
_LATIN = re.compile(r'[A-Za-z]{3,}')


def iter_rows(path):
    """读一个字幕，返回每条的 dict 列表（按文件顺序）。

    每项：`ln` 物理行号 / `start` 起时间码原文 / `end` 止时间码原文 /
    `start_s` 起秒数 / `end_s` 止秒数 / `style` 样式名 / `text` 整条正文 /
    `cn` 中文 / `en` 英文 / `tc` 时间码可读串。
    SRT 也给同样的结构（`style` 为空，`ln` 是块首行号）。
    """
    lines, cues, kind = land_verify.load(pathlib.Path(path))
    out = []
    for c in cues:
        f = c['prefix'].split(',') if c.get('prefix') else []
        start = f[1] if len(f) > 2 else ''
        end = f[2] if len(f) > 2 else ''
        out.append({
            'ln': c['line'] + 1,
            'start': start,
            'end': end,
            'start_s': (c['s'] or 0) / 1000.0,
            'end_s': (c['e'] or 0) / 1000.0,
            'style': f[3] if len(f) > 3 else '',
            'text': c.get('text') or '',
            'cn': c.get('zh') or '',
            'en': c.get('en') or '',
            'tc': f'{start} --> {end}' if start else '',
        })

    # 拆不开时必须出声：一条英文都没有，但正文里明明有拉丁词，说明分隔符不是
    # `中文\N{\rEnglish}英文`（常见于还没跑 MF6 的内联标签形态）。
    # 静默返回空英文，下游会把整份字幕判成「无英文」——这类假结论最难查。
    if out and not any(r['en'] for r in out):
        latin = sum(1 for r in out if _LATIN.search(r['text'] or ''))
        if latin > len(out) * 0.5:
            print(f'[警告] {pathlib.Path(path).name}：{latin}/{len(out)} 条正文里有拉丁词，'
                  f'但一条英文都没拆出来——分隔符多半不是「中文\\N{{\\rEnglish}}英文」。'
                  f'先跑清稿的文件层（MF6）把内联标签换成样式引用，再来。',
                  file=sys.stderr)
    return out


def season_files(root, season, only=None, pattern='*.ass'):
    """在 `root` 下递归找某季的集文件。

    `season` 给 `01` / `S01` / `1` 都行。只在给出的根目录下找，不猜剧库位置——
    根目录由调用方传（通常 `Path.cwd()`）。找不到返回空列表，不抛异常。
    """
    tag = str(season).lstrip('Ss').lstrip('0') or '0'
    num = f'S{int(tag):02d}'
    d = pathlib.Path(root)
    if not d.is_dir():
        return []
    files = sorted(p for p in d.rglob(pattern) if num in p.name.upper())
    if only is not None:
        ep = f'E{int(only):02d}'
        files = [f for f in files if ep in f.name.upper()]
    return files


def ep_of(p):
    """从文件名取集号，返回 `01x05` 形式（季两位 x 集两位）。取不到就返回文件名主干。

    两种命名都认：`S0xEy` 与 `0x0y` 这类（季两位 + 集两位）。返回格式跟老库的 `ep_of` 一致，
    因为下游的分片表、候选表里用的就是这一种。
    """
    name = pathlib.Path(p).name
    m = re.search(r'S(\d{1,2})E(\d{1,2})', name, re.I) or re.search(r'(\d{1,2})x(\d{1,2})', name)
    if not m:
        return pathlib.Path(p).stem
    return f'{int(m.group(1)):02d}x{int(m.group(2)):02d}'
