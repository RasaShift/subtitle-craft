#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-06（新建：两份单语 SRT → 一份双语 ASS 底本）
"""build_base.py -- 两份单语 SRT → 一份双语 ASS 底本

依据（改这个工具之前先读这三条，它们都不在这份文件里）：
  · 画布只认比例、不认数值，宽高比必须等于视频显示宽高比
        —— docs/工序-清稿-规则清单.md 的 MF2（那条声明了「对造画布的工具同样成立」）
  · 缩放按轴：字号 / 描边 / 阴影 / 纵边距走 y，横边距走 x
        —— tools/style_normalize.py 的 SCALAR_Y，不另立一套
  · 落盘编码 UTF-8 BOM + CRLF
        —— 同上规则清单 MF1
第一版把「源 PlayRes 是 0」按 ASS 规范补成 384×288，等于原样保留 4:3，
配 16:9 片源就是横向拉伸 33%——上面第一条正是要防这个。别再犯。

为什么有它：本库对 SRT 是「能读能写、不产 ASS」——体检、对齐、落地都吃得下 SRT，
却没有一件能造出成品形态的 ASS。而多数新剧的起点正是简 / 英两份独立 SRT。
`ffmpeg -i a.srt a.ass` 也能转，但转出来的东西进不了审稿：
  · 没有样式表，全挂在 ffmpeg 的默认样式上（字号、边距、PlayRes 都不对）
  · 两份 SRT 不会合并，而 `{\\rEnglish}` 要求的 English 样式也不存在——
    没有那条样式，英文会按中文样式的字号字体渲染
  · 编码约定（BOM / CRLF）它不管
本工具造的是「按本库成品约定」的底本，不是通用格式转换器。通用转换用 ffmpeg 更好。

用法：
  python build_base.py <中文.srt> <英文.srt> -o <底本.ass>
         [--style-from 参照.ass] [--video 1920x1080] [--title 片名] [--lf] [--no-bom]

配对：直接调 align_bilingual.run()，配对算法只有那一份（中文当骨架、英文挂载）。

画布（最容易错的一处）：抄来的样式按**轴**换到 `--video` 声明的分辨率——
字号、描边、阴影、纵边距按 y，横边距按 x。不这么做，4:3 的样式配 16:9 的片源，
字形会被横向拉伸。老库成品就是这么错的（384×288 配 1080p，横向多放大 33%），
**不是范本**。源 PlayRes 写成 0 时按 ASS 规范理解为 384×288，然后照样换算。

三条不替人做决定的：
  · **时间跟中文轨**。英文比中文长时只报告、不自动延长——延长会和下一条重叠，
    两行叠在一起比早收更糟。要改是人看见了再改。
  · **未配对的中文照样写进去**，只写中文、不留空的英文段（空的 `{\\rEnglish}` 会留一行空白）。
  · **样式默认值只是占位**。真做剧必须 `--style-from` 抄一份现成成品的样式表，
    不抄就只能当草稿看，不能当成品。

输出：CRLF + BOM（老库成品惯例），`--lf` / `--no-bom` 可改。
退出码：0 成功；2 参数或输入错。
"""
import argparse
import pathlib
import re
import sys

from align_bilingual import parse_srt, run, decode as decode_bytes
from style_normalize import rescale_styles      # 缩放规则只有一份，住在这儿


def ass_tc(ms):
    """毫秒 → ASS 时间码 `H:MM:SS.cc`（厘秒，不是毫秒）。"""
    ms = max(0, int(ms))
    return '%d:%02d:%02d.%02d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000 // 10)


# SRT 里常带旧式 HTML 标签，不转就会原样显示在画面上。
# 颜色要转成 ASS 的 `&HBBGGRR&`——ASS 是 BGR 倒着写的，这是老库里踩过一次的坑。
FONT_COLOR = re.compile(r'<font\s+[^>]*color\s*=\s*["\']?#?([0-9a-fA-F]{6})["\']?[^>]*>', re.I)
HTML_TAG = [('<i>', '{\\i1}'), ('</i>', '{\\i0}'),
            ('<b>', '{\\b1}'), ('</b>', '{\\b0}'),
            ('<u>', '{\\u1}'), ('</u>', '{\\u0}')]
ANY_TAG = re.compile(r'</?[a-zA-Z][^>]*>')


def body(text):
    """SRT 的一段正文 → ASS 的一行正文（换行转 \\N，花括号保住，HTML 标签转内联）。"""
    # 先归并「只有标签没有字」的行：字幕组卡片常写成 <font> 单独一行、文字另起一行，
    # 不归并就会留下一个只有颜色标签的视觉空行，整块字幕被顶上去了。
    raw, merged = [ln.strip() for ln in text.splitlines() if ln.strip()], []
    for ln in raw:
        if merged and not ANY_TAG.sub('', ln).strip():
            merged[-1] += ln        # 本行只有标签 → 挂到上一行末尾（收尾的 </font> 常见）
        elif merged and not ANY_TAG.sub('', merged[-1]).strip():
            merged[-1] += ln        # 上一行只有标签 → 接到本行前面（开头的 <font> 常见）
        else:
            merged.append(ln)

    out = []
    for ln in merged:
        # 先保住原有的花括号，再转 HTML 标签，否则自己造的 {\i1} 会被一起转义
        ln = ln.replace('{', '\\{').replace('}', '\\}')
        ln = FONT_COLOR.sub(lambda m: '{\\c&H%s&}' % ''.join(
            [m.group(1)[4:6], m.group(1)[2:4], m.group(1)[0:2]]).lower(), ln)
        ln = ln.replace('</font>', '{\\c}')
        for src, dst in HTML_TAG:
            ln = ln.replace(src, dst)
        ln = ANY_TAG.sub('', ln)        # 认不出的标签剥掉，总比把 <xxx> 显示在画面上好
        out.append(ln)
    return '\\N'.join(out)


DEFAULT_HEAD = """[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,56,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,45,1
Style: English,Microsoft YaHei,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,45,1

[Events]
Format: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text
"""


def take_style(path):
    """从现成 ASS 抄 PlayRes 与样式表。返回 (PlayRes 字典, 样式段行列表, 源画布, 提示列表)。

    源画布要按轴读：PlayResX 写成 0 或缺失时，按 ASS 规范理解为 384×288（4:3）。
    这个 4:3 **不能原样留下**——播放器按「视频宽 ÷ PlayResX」「视频高 ÷ PlayResY」
    分别缩放，4:3 的画布配 16:9 的片源，字形会被横向拉伸。老库成品就是这么错的
    （384×288 配 1080p 宽屏，横向多放大 33%），不是范本。
    """
    lines = decode_bytes(pathlib.Path(path)).splitlines()
    res, sec, inside, notes = {}, [], False, []
    for ln in lines:
        s = ln.strip()
        m = re.match(r'(PlayResX|PlayResY|WrapStyle)\s*:\s*(\S+)', s, re.I)
        if m:
            res[m.group(1)] = m.group(2)
        if s.lower().replace(' ', '').startswith('[v4+styles]'):
            inside = True
        if inside:
            if s.startswith('[') and s.endswith(']') and not s.lower().replace(' ', '').startswith('[v4+styles]'):
                break
            sec.append(ln)
    while sec and not sec[-1].strip():
        sec.pop()

    def num(k, dflt):
        try:
            v = int(res.get(k, 0))
            return v if v > 0 else dflt
        except ValueError:
            return dflt
    src = (num('PlayResX', 384), num('PlayResY', 288))
    if num('PlayResX', 0) <= 0 or num('PlayResY', 0) <= 0:
        notes.append('源 PlayRes 是 0 或缺失，按 ASS 规范理解为 384×288（4:3）')
    return res, sec, src, notes


def ensure_en_style(sec, ratio=0.75):
    """样式表里没有 English 时，从 Default 复制一条，只缩字号。

    必须有这条：正文写的是 `{\\rEnglish}`，没有它英文会挂在 Default 上，
    跟中文一样大，两行挤在一起分不开。字号比中文小一档是双语字幕的通行做法。
    """
    if any(re.match(r'\s*Style:\s*English\s*,', ln, re.I) for ln in sec):
        return sec, False
    for i, ln in enumerate(sec):
        if not re.match(r'\s*Style:\s*Default\s*,', ln, re.I):
            continue
        parts = ln.split(',')
        if len(parts) < 3:
            break
        try:
            parts[2] = str(max(8, round(int(parts[2]) * ratio)))
        except ValueError:
            break
        new = ','.join(parts).replace('Style: Default', 'Style: English', 1)
        return sec[:i + 1] + [new] + sec[i + 1:], True
    return sec, False


def build(zh, en, title='', style_from=None, video=None):
    """两份 cue 列表 → (ASS 全文, 统计字典)。不落盘。

    video=None 表示「不知道片源分辨率」，此时沿用源画布、不做换算，并在报告里说清。
    片源比例是外部事实，库里查不到就不猜——猜错是整片字形拉伸，而且没人看得出。
    """
    rows, _used, _pairs = run(zh, en)
    notes = []

    if style_from:
        res, sec, src, notes2 = take_style(style_from)
        notes += notes2
        if video and tuple(video) != tuple(src):
            sec, sc = rescale_styles(sec, src, video)
            target = tuple(video)
            if sc:
                notes.append('样式从源 %d×%d 换到 %d×%d：字号/描边/纵边距 ×%s，横边距 ×%s'
                             % (src[0], src[1], target[0], target[1],
                                ('%.2f' % sc[1]).rstrip('0').rstrip('.'),
                                ('%.2f' % sc[0]).rstrip('0').rstrip('.')))
        else:
            target = tuple(src)
            if video is None:
                notes.append('画布沿用源 %d×%d，没换算（没给 --video）；'
                             '片源要是 16:9 而源是 4:3，字会被横向拉伸——给 --video 让我换' % src)
        sec, added = ensure_en_style(sec)
        if added:
            notes.append('样式表里没有 English 样式，从 Default 复制一条、字号缩到 75%')
        elif not sec:
            notes.append('抄来的样式表是空的，会退回占位样式')
        head = '[Script Info]\nTitle: %s\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n' % (
            title, target[0], target[1])
        if 'WrapStyle' in res:
            head += 'WrapStyle: %s\n' % res['WrapStyle']
        head += 'ScaledBorderAndShadow: yes\n\n' + '\n'.join(sec) + \
                '\n\n[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
    else:
        head = DEFAULT_HEAD.format(title=title)
        notes.append('用的是占位样式（1920×1080 / 微软雅黑 / 56+40）——当草稿看，别当成品')
        if video and tuple(video) != (1920, 1080):
            notes.append('占位样式画布固定 1920×1080，--video 只对 --style-from 生效')

    out = [head]
    paired = unpaired = en_longer = 0
    loose = []
    for r in rows:
        a, b = r['zh'], r['en']
        text = body(a['text'])
        if b is not None:
            paired += 1
            en_txt = body(b['text'])
            if en_txt:
                text += '\\N{\\rEnglish}' + en_txt
            if b['e'] > a['e'] + 500:      # 超半秒算「读不完」，只报不修
                en_longer += 1
        else:
            unpaired += 1
            loose.append((r['flag'], a['text'].replace('\n', ' ')[:40]))
        out.append('Dialogue: 0,%s,%s,Default,,0,0,0,,%s' % (ass_tc(a['s']), ass_tc(a['e']), text))

    text = '\n'.join(out) + '\n'
    stat = {'中文': len(rows), '配对': paired, '未配对': unpaired,
            '英文更长': en_longer, '松散': loose, '提示': notes}
    return text, stat


def main():
    ap = argparse.ArgumentParser(description='两份单语 SRT → 一份双语 ASS 底本')
    ap.add_argument('zh', help='中文 SRT')
    ap.add_argument('en', help='英文 SRT')
    ap.add_argument('-o', '--out', required=True, help='输出 ASS')
    ap.add_argument('--style-from', help='从这份现成 ASS 抄 PlayRes 与样式表（强烈建议）')
    ap.add_argument('--video',
                    help='片源显示分辨率。给了才把抄来的样式按轴换到这个画布；不给就沿用源画布、不换算')
    ap.add_argument('--title', default='', help='Script Info 的 Title')
    ap.add_argument('--lf', action='store_true', help='用 LF 换行（默认 CRLF）')
    ap.add_argument('--no-bom', action='store_true', help='不写 BOM（默认写）')
    a = ap.parse_args()

    zp, ep = pathlib.Path(a.zh), pathlib.Path(a.en)
    for p in (zp, ep):
        if not p.exists():
            print('找不到文件：%s' % p, file=sys.stderr)
            return 2
    if a.style_from and not pathlib.Path(a.style_from).exists():
        print('找不到 --style-from 指定的文件：%s' % a.style_from, file=sys.stderr)
        return 2

    zh, badz = parse_srt(zp)
    en, bade = parse_srt(ep)
    if not zh:
        print('中文 SRT 没解析出任何一条：%s' % zp, file=sys.stderr)
        return 2
    if badz or bade:
        print('  注意：解析时跳过了 %d 条中文、%d 条英文（没有时间码的块）' % (badz, bade))

    video = None
    if a.video:
        m = re.match(r'^(\d+)\s*[x×*]\s*(\d+)$', a.video.strip(), re.I)
        if not m:
            print('--video 要写成 1920x1080 这种形式：%s' % a.video, file=sys.stderr)
            return 2
        video = (int(m.group(1)), int(m.group(2)))

    text, st = build(zh, en, a.title, a.style_from, video)
    data = text.replace('\n', '\r\n').encode('utf-8')
    if not a.no_bom:
        data = b'\xef\xbb\xbf' + data
    pathlib.Path(a.out).write_bytes(data)

    print('%s → %s' % (zp.name, a.out))
    print('  中文 %d 条 / 配对 %d / 未配对 %d' % (st['中文'], st['配对'], st['未配对']))
    if st['松散']:
        print('  未配对的 %d 条（不丢，中文留在底本里）：' % len(st['松散']))
        for flag, txt in st['松散'][:6]:
            print('    %-12s %s' % (flag, txt))
        if len(st['松散']) > 6:
            print('    …… 另 %d 条' % (len(st['松散']) - 6))
    if st['英文更长']:
        print('  ⚠ %d 条英文比中文区间长半秒以上——时间跟的是中文轨，要不要延长人看' % st['英文更长'])
    for n in st['提示']:
        print('  ⚠ %s' % n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
