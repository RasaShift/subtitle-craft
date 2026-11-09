#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-09（MF8 底纹条删除落地，`--f8`，默认关）
"""style_normalize.py -- 文件层样式规范化：头部与样式表换成基准，坐标按比例换算

为什么有它：文本层有 `land_verify.py` 管「只改该改的字」，文件层一直没有工具——
画布比例错了、样式表几套面孔、字号写法不一，只能手改，而手改整片会错位。
判据在 `docs/工序-清稿-规则清单.md`（MF1–MF8）。本脚本只做能写成断言的那几条。

做（F1–F6、F8）：
  · `[Script Info]` 整段换成基准的（画布比例、WrapStyle、ScaledBorderAndShadow 一起统一）
  · `[V4+ Styles]` 整段换成基准的样式表（字体、字号、颜色、描边、边距）
  · Dialogue 里的 `\\pos \\move \\org \\clip \\iclip` 坐标按轴换算，
    `\\fs \\fsp \\bord \\xbord \\ybord \\shad \\xshad \\yshad \\blur` 按轴换算
  · 编码 / BOM / 换行符默认保持原样（显式 `--encoding` 才改）
  · F6 内联标签统一成 `{\\rX}`，但必须显式给 `--f6`——不给开关一个标签都不动
  · F8 底纹条（署名块的黑底）整条删，同样要显式给 `--f8`；判据见下方 MF8 一段
不做（会打印出来，别当成做了）：
  · 矢量裁切 `\\clip(m ...)` 不动，逐条报出来

关于画布比例（2026-11-05 定，2026-11-08 补尺度）：
  播放器按「视频宽 ÷ PlayResX」「视频高 ÷ PlayResY」分别缩放，所以**宽高比必须等于
  视频显示宽高比**；比例不一致时本脚本拒绝执行——那不是「统一」，是修错，得先人看一眼。
  **数值本身不影响播放，但影响编辑**：字号、描边、边距都是绝对像素，纵向坐标系越大，
  同样的数渲染出来越小。所以纵向固定取交付高度 1080（＝三套预设母版的高度），横向按
  显示宽高比算：16:9 → 1920×1080，4:3 → 1440×1080，2.35:1 → 2538×1080。这样预设的
  字号描边 1:1 搬过来，不用换算。**别填片源的像素分辨率**：PAL 720×576 的像素比是 1.25
  而 DAR 是 1.333，填了不报错——横向按 1.25 算错，纵向把字号描边缩一半（79→42、边 2→1）。

用法：
    python style_normalize.py check  <文件或目录...> [--video 1920x1080] [--base 基准.ass]
    python style_normalize.py apply  <目标.ass> --base 基准.ass [-o 输出] [--dry-run]
                                     [--from-res 384x288] [--style-map 旧名=新名] [--f6] [--f8]
    python style_normalize.py verify <改前> <改后> <基准.ass> [--video WxH] [--from-res WxH] [--f6] [--f8]

四个显式开关，都是「不许工具自己猜」的地方：
  --from-res   原文件 PlayRes 是 0×0 时（旧片源常见），声明它原本按哪个坐标系排的
  --style-map  换样式表要同时改引用；旧样式名对不上基准时，显式给出对应关系
  --f6 / --f8  要删东西的规则（换标签、删条），不给开关就一条都不动——删错了撤不回

退出码：0 成功 / 1 复算不过 / 2 参数或输入错 / 3 dry-run 无可改项
"""
import argparse
import re
import sys
from pathlib import Path

SEC = re.compile(r'^\[(.+)\]\s*$')
# 只列「换画布时要动」的标签。`\fscx/\fscy` 是百分比、`\an` 是锚点、`\frz` 等是角度，
# 换画布都不动，故意不在这里。2026-11-07 人眼截 0:00:05 帧抓到：`\fscx105` 曾被当
# x 轴长度乘 5 变 525，声明字横向炸出画面。判据见 docs/工序-清稿-规则清单.md:112
# 「\an \fscx \fscy 不动（锚点与百分比语义不变）」。
TAG = re.compile(r'\\(fsp|fs|pos|move|org|iclip|clip|ybord|xbord|bord|yshad|xshad|shad|blur)'
                 r'(\([^)]*\)|[-\d.]+)?')
NUM = re.compile(r'-?\d+(?:\.\d+)?')
INLINE = re.compile(r'\\(?:fs|fsp|fn|c&|1c&|3c&|4c&|[xy]?bord|[xy]?shad|blur|b\d)')
BLOCKY = re.compile(r'^[\u25a0-\u25ff\u2580-\u259f\u2b1b\u2b1c\s]+$')
POS_TAGS = {'pos': 2, 'org': 2, 'clip': 4, 'iclip': 4, 'move': 4}
SCALAR_Y = {'fs', 'bord', 'ybord', 'shad', 'yshad', 'blur'}
SCALAR_X = {'fsp', 'xbord', 'xshad'}


class Bad(Exception):
    pass


def decode_of(path):
    """读成文本 + 原编码/BOM/换行符。写回要照着来——换行符不许顺手改成 LF。"""
    raw = path.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        enc, text = 'utf-16', raw.decode('utf-16')
    elif raw[:3] == b'\xef\xbb\xbf':
        enc, text = 'utf-8-sig', raw.decode('utf-8-sig')
    elif b'\x00' in raw[:8]:
        enc, text = 'utf-16-le', raw.decode('utf-16-le')
    else:
        enc, text = 'utf-8', raw.decode('utf-8')
    # 换行符必须在**解码后的文本**上判，不能在原始字节上找 `b'\r\n'`——UTF-16 的
    # CRLF 是 `\r\x00\n\x00`，中间夹着零字节，字节层永远匹配不到，会静默判成 LF，
    # 写回时把整篇 CRLF 改成 LF 而一句提示都没有（2026-11-07 拿法医 UTF-16 源字幕撞出来的）。
    # land_verify 一直是文本层判断（`lines[0].endswith('\r\n')`），这边当时漏了。
    return {'path': path, 'text': text, 'enc': enc, 'bom': raw[:3] == b'\xef\xbb\xbf',
            'eol': '\r\n' if '\r\n' in text[:8192] else '\n',
            'lines': text.splitlines()}


def blocks(lines):
    """按段标题切块：{段名: (起始行, 结束行)}，结束行是下一个段标题的位置。"""
    out, name, start = {}, None, 0
    for i, ln in enumerate(lines):
        m = SEC.match(ln.strip())
        if m:
            if name:
                out[name] = (start, i)
            name, start = m.group(1), i
    if name:
        out[name] = (start, len(lines))
    return out


def playres(lines):
    x = y = None
    for ln in lines:
        s = ln.strip()
        if s.lower().startswith('playresx:'):
            x = s.split(':', 1)[1].strip()
        elif s.lower().startswith('playresy:'):
            y = s.split(':', 1)[1].strip()
    if x is None and y is None:
        return None
    return (x or '0', y or '0')


def as_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def styles_of(lines):
    """[(行号, 样式名, 字段列表)]，只认 [V4+ Styles] 段里的 Style 行。"""
    b = blocks(lines)
    if 'V4+ Styles' not in b:
        return []
    lo, hi = b['V4+ Styles']
    out = []
    for i in range(lo, hi):
        s = lines[i].strip()
        if s.lower().startswith('style:'):
            f = s.split(':', 1)[1].split(',')
            out.append((i, f[0].strip(), [x.strip() for x in f]))
    return out


def events_of(lines):
    """[(行号, 类型, 字段列表)]，Dialogue 与 Comment 都收，其余行不收。"""
    b = blocks(lines)
    if 'Events' not in b:
        return []
    lo, hi = b['Events']
    out = []
    for i in range(lo, hi):
        s = lines[i].strip()
        for kind in ('Dialogue', 'Comment'):
            if s.lower().startswith(kind.lower() + ':'):
                f = s.split(':', 1)[1].split(',', 9)
                out.append((i, kind, [x.strip() for x in f]))
    return out


def visible(text):
    """去掉所有 {…} 覆盖标签后的可见文本——判「正文一字未动」用它。"""
    return re.sub(r'\{[^}]*\}', '', text)


def fmt(v):
    s = f'{v:.2f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def rescale_styles(sec, src, target):
    """把样式段从 src 坐标系换到 target。字号/描边/阴影/纵边距按 y，横边距按 x。

    依据 docs/工序-清稿-规则清单.md MF2：画布只认比例、不认数值，改画布必须同步
    缩放坐标、字号、边距、描边，漏一样就整片错位。按轴而非等比，是因为播放器
    就是按轴缩放的（见本文件开头）。

    sec 是样式段的行列表（含 Format 与 Style 行）；返回 (新行列表, (sx, sy) 或 None)。
    build_base 造底本时也调这里——缩放规则只能有一份。
    """
    sx, sy = target[0] / src[0], target[1] / src[1]
    fields = []
    for ln in sec:
        if ln.strip().lower().startswith('format:'):
            fields = [x.strip() for x in ln.split(':', 1)[1].split(',')]
            break
    if not fields:
        return sec, None
    idx = {name: i for i, name in enumerate(fields)}
    out = []
    for ln in sec:
        if not ln.strip().lower().startswith('style:'):
            out.append(ln)
            continue
        parts = ln.split(',')
        touched = False
        for name, k in (('Fontsize', sy), ('Outline', sy), ('Shadow', sy),
                        ('MarginV', sy), ('MarginL', sx), ('MarginR', sx)):
            i = idx.get(name)
            if i is None or i >= len(parts):
                continue
            try:
                v = float(parts[i])
            except ValueError:
                continue
            parts[i] = str(int(round(v * k)))
            touched = True
        out.append(','.join(parts) if touched else ln)
    return out, (sx, sy)


def scale_tags(text, sx, sy):
    """按轴换算覆盖标签。返回 (新文本, 动了几处, 没处理的矢量裁切数)。"""
    moved, skipped = [0], [0]

    def one(name, arg):
        if not arg:
            return '\\' + name + (arg or '')
        if name in ('clip', 'iclip') and arg.startswith('(') and re.search(r'[a-zA-Z]', arg[1:]):
            skipped[0] += 1                      # 矢量裁切：不动，交给人工
            return '\\' + name + arg
        nums = NUM.findall(arg)
        if not nums:
            return '\\' + name + arg
        if name in POS_TAGS:
            vals = [float(n) for n in nums]
            for k in range(0, min(len(vals), POS_TAGS[name]), 2):
                vals[k] *= sx
                if k + 1 < len(vals):
                    vals[k + 1] *= sy
            moved[0] += 1
            inner = ','.join(fmt(v) for v in vals)
            if name in ('clip', 'iclip') and len(vals) < 4:
                return '\\' + name + '(' + inner + ')'
            return '\\' + name + '(' + inner + ')'
        v = float(nums[0])
        v = v * (sy if name in SCALAR_Y else sx)
        moved[0] += 1
        return '\\' + name + fmt(v)

    out = TAG.sub(lambda m: one(m.group(1), m.group(2)), text)
    return out, moved[0], skipped[0]


# ---------------------------------------------------------------- MF6 内联标签 → 样式引用
# 判据全文见 docs/工序-清稿-规则清单.md「MF6 具体怎么换」。三条同时成立才算英文段起点：
# 在 `\N` 之后、紧跟一串只含样式类参数的 `{...}`、这串之后第一个可打印字符是拉丁字母。
# 后接中文的是中文折行，不归 MF6——这一条把「中文句子中间换字体」整批挡在外面。
F6_OK = {'fn', 'fs', 'b', 'i', 'c', '1c', '2c', '3c', '4c',
         'bord', 'xbord', 'ybord', 'shad', 'xshad', 'yshad'}
F6_SEQ = re.compile(r'(\\N)((?:\{[^{}]*\}){1,})')
# 只取标签的字母部分：`\b0` 取到 `b`、`\fs14` 取到 `fs`、`\3c&H..` 取到 `3c`。
# 取成 `b0` / `fs14` 会整批误判成「不在白名单」（2026-11-07 第一版就是这么错的）。
F6_TAG = re.compile(r'\\([0-9]?[a-z]+)')
F6_ZH = re.compile(r'[\u4e00-\u9fff]')
F6_EN = re.compile(r'[A-Za-z]')
F6_TAIL = 20


def mf6(text, name='English'):
    r"""把「\N + 一串样式参数 + 英文」整段换成「\N{\rEnglish}」。

    返回 (新文本, 换了几处, 没换的理由计数)。已经是 `{\rX}` 的不动、也不计入理由。
    块里出现基准不覆盖的参数一律不换：`\pos` `\move` `\clip` `\an` `\fad` `\blur`
    `\fscx` `\fscy` 抹掉就是挪位或少一段淡入，而基准只管字号字体颜色，接不住。

    块序列后面「先出现中文」= 中文折行，不归 MF6，静默放过。
    含作前后要跳过标点：英文常以 `- ` 开头（`- What do you want?`），
    只认「紧跟字母」会漏掉一整类（2026-11-07 实测 43 条）。
    """
    swapped, holds = 0, {}

    def one(m):
        nonlocal swapped
        tail = text[m.end():m.end() + F6_TAIL]
        zh, en = F6_ZH.search(tail), F6_EN.search(tail)
        if not en or (zh and zh.start() < en.start()):
            return m.group(0)                      # 后面先出现中文（或压根没英文）
        names = {n.lower() for n in F6_TAG.findall(m.group(2))}
        if any(n.startswith('r') for n in names):
            return m.group(0)                      # 已经是样式引用，合规
        bad = sorted(names - F6_OK)
        if bad:
            key = ' '.join('\\' + b for b in bad)
            holds[key] = holds.get(key, 0) + 1
            return m.group(0)
        swapped += 1
        return m.group(1) + '{\\r' + name + '}'

    return F6_SEQ.sub(one, text), swapped, holds


# ---------------------------------------------------------------- MF8 底纹条删除
# 判据全文见 docs/工序-清稿-规则清单.md「MF8 具体怎么删」。两条同时成立才删：
# ① 可见字符只剩方块字符；② 同一起始时间还有别的 Dialogue 行、且那行可见字符非空。
# 第②条**不是**规则清单原来写的「同刻有署名行被删」——那是事后视角（删署名行时顺手删了它），
# 工具运行时看不到「将被删」，只能看当前文件。实测旧库 49 条里 41 条满足「同刻有署名行」，
# 换成「同刻有别的行」后 49/49 全部成立、0 条孤单（2026-11-09，法医 S01–S08 原稿 96 份）。
def mf8_scan(lines):
    """[(行号, 条序, 起始时间, 可见文本)]——MF8 与复算共用这一份原料。

    条序按 Dialogue 出现顺序，与 `cue_map` 的键一致；两边各算一遍会走岔。
    """
    out, n = [], 0
    for i, kind, f in events_of(lines):
        if kind != 'Dialogue':
            continue
        n += 1
        out.append((i, n, f[1].strip() if len(f) > 1 else '',
                    visible(f[9] if len(f) > 9 else '').strip()))
    return out


def mf8_pick(lines):
    """该删哪些条。返回 (行号集合, 条序集合, 统计)。

    统计里 `孤单未删` 是第②条挡下来的——正文里孤立的方块行（没别的行陪着）
    删了就是凭空少一句，宁可留着让人看。
    """
    rows = mf8_scan(lines)
    live = {}
    for _, _, t, v in rows:
        if v:
            live[t] = live.get(t, 0) + 1
    idx, cues, cand, alone = set(), set(), 0, 0
    for i, n, t, v in rows:
        if not v or not BLOCKY.match(v):
            continue
        cand += 1
        if live.get(t, 0) - 1 <= 0:          # 同刻除了它自己，没有别的非空行
            alone += 1
            continue
        idx.add(i)
        cues.add(n)
    return idx, cues, {'候选': cand, '删': len(idx), '孤单未删': alone}


def tag_values(text):
    """独立解析：[(标签名, 数值列表)]。复算用它比，不复用 scale_tags——不能自己验自己。"""
    out = []
    for name, arg in TAG.findall(text):
        if not arg:
            continue
        if name in ('clip', 'iclip') and re.search(r'[a-zA-Z]', arg[1:] or ''):
            continue
        nums = [float(n) for n in NUM.findall(arg)]
        if nums:
            out.append((name, nums))
    return out


def apply_file(src, base, encoding=None, from_res=None, style_map=None, f6=False, f8=False):
    """算出新文件的行列表。不落盘。返回 (lines, 统计, 警告, 源信息)。

    style_map：「旧样式名 → 基准里的样式名」。换样式表必须同时改引用，
    否则正文里的 `\\r旧名` 和 Dialogue 的样式字段会静默回落 Default，一句错都不报。
    """
    style_map = style_map or {}
    src_d = decode_of(src)
    base_d = decode_of(base)
    sl, bl = src_d['lines'], base_d['lines']
    sb, bb = blocks(sl), blocks(bl)

    for need in ('Script Info', 'V4+ Styles'):
        if need not in sb:
            raise Bad(f'{src.name} 没有 [{need}] 段')
        if need not in bb:
            raise Bad(f'基准 {base.name} 没有 [{need}] 段')

    src_res, base_res = playres(sl), playres(bl)
    w0, h0 = (as_int(src_res[0]), as_int(src_res[1])) if src_res else (0, 0)
    if w0 <= 0 or h0 <= 0:
        # PlayRes 为 0 的稿子（旧片源常见）没法从文件里读坐标系，只能由人声明——
        # 工具绝不替它猜一个，猜错就是整片错位且没人看得出。
        if not from_res:
            raise Bad(f'{src.name} 的 PlayRes 缺失或为 0（{src_res}）：没有坐标系可换算。'
                      f'知道它原本按哪个坐标系排的，就用 --from-res 1920x1080 这类显式声明；'
                      f'不知道就先问人，别让工具猜')
        w0, h0 = from_res
    if not base_res or as_int(base_res[0]) <= 0 or as_int(base_res[1]) <= 0:
        raise Bad(f'基准 {base.name} 的 PlayRes 缺失或为 0')

    w1, h1 = as_int(base_res[0]), as_int(base_res[1])
    sx, sy = w1 / w0, h1 / h0

    warn = []
    if not src_res or as_int(src_res[0]) <= 0 or as_int(src_res[1]) <= 0:
        warn.append(f'原文件 PlayRes 是 {src_res}，按 --from-res {w0}×{h0} 换算——'
                    f'这个值是声明的，不是读出来的，改完要截一帧核对位置')
    if abs(w1 / h1 - w0 / h0) > 0.005:
        warn.append(f'画布比例变了：{w0}×{h0}（{w0 / h0:.3f}）→ {w1}×{h1}（{w1 / h1:.3f}）'
                    f'——这属于改比例（修错），字形宽度会变，改完必须截一帧人看')

    base_names = [n for _, n, _ in styles_of(bl)]
    if len(set(base_names)) != len(base_names):
        raise Bad(f'基准 {base.name} 的样式名有重复：{base_names}')

    f6_name = style_map.get('English', 'English')
    if f6 and f6_name not in base_names:
        # 规则清单里「原样有问题」的第 2 类：换了就是 {\rEnglish} 静默回落 Default。
        # 不许挑 Default 顶上——那样一句错都不报，整片英文其实已经套错样式。
        raise Bad(f'基准 {base.name} 里没有「{f6_name}」样式，开不了 MF6：换出来的 '
                  f'{{\\r{f6_name}}} 播放时会静默回落 Default。'
                  f'先给基准补一条英文样式，或用 --style-map English=基准里的英文样式名 指明')

    mf8_del, _mf8_cues, mf8_st = mf8_pick(sl) if f8 else (set(), set(), {})

    out, moved_tags, skipped, moved_events = [], 0, 0, 0
    f6_done, f6_hold = 0, {}
    for i, ln in enumerate(sl):
        # 头部与样式表整段换成基准的
        if sb['Script Info'][0] <= i < sb['Script Info'][1]:
            if i == sb['Script Info'][0]:
                out.extend(bl[bb['Script Info'][0]:bb['Script Info'][1]])
            continue
        if sb['V4+ Styles'][0] <= i < sb['V4+ Styles'][1]:
            if i == sb['V4+ Styles'][0]:
                out.extend(bl[bb['V4+ Styles'][0]:bb['V4+ Styles'][1]])
            continue
        m = None
        for kind in ('Dialogue', 'Comment'):
            if ln.strip().lower().startswith(kind.lower() + ':'):
                m = kind
                break
        if m != 'Dialogue':
            out.append(ln)                       # 其它行一个字节不动
            continue
        if i in mf8_del:
            continue                             # MF8：整条删掉（判据见 mf8_pick）
        parts = ln.split(',', 9)
        if len(parts) < 10:
            warn.append(f'第 {i + 1} 行 Dialogue 字段不足 10 段，原样保留：{ln[:40]}')
            out.append(ln)
            continue
        head, text = parts[:9], parts[9]
        if style_map:
            head[3] = style_map.get(head[3].strip(), head[3])
        new_text, n, sk = scale_tags(text, sx, sy)
        if style_map:
            new_text = re.sub(r'\\r([^\\}]+)',
                              lambda m: '\\r' + style_map.get(m.group(1), m.group(1)), new_text)
        if f6:
            new_text, n6, why6 = mf6(new_text, f6_name)
            f6_done += n6
            for k, v in why6.items():
                f6_hold[k] = f6_hold.get(k, 0) + v
        moved_tags += n
        skipped += sk
        moved_events += 1
        out.append(','.join(head) + ',' + new_text)

    missing = {}
    for ln in out:
        if not ln.strip().lower().startswith('dialogue:'):
            continue
        f = ln.split(',', 9)
        name = f[3].strip() if len(f) > 3 else ''
        if name and name not in base_names:
            missing[name] = missing.get(name, 0) + 1
        for ref in re.findall(r'\\r([^\\}]+)', f[9] if len(f) > 9 else ''):
            if ref and ref not in base_names:
                missing['\\r' + ref] = missing.get('\\r' + ref, 0) + 1
    if missing:
        top = ', '.join(f'{k}×{v}' for k, v in list(missing.items())[:4])
        raise Bad(f'换基准后这些样式名在基准里不存在：{top}（共 {len(missing)} 种）——'
                  f'播放时会静默回落 Default，一个字都不报。'
                  f'用 --style-map 旧名=新名 显式改名后再跑，别让它悄悄掉')

    st = {'画布': f'{w0}×{h0} → {w1}×{h1}', '换算': f'x×{fmt(sx)} y×{fmt(sy)}',
          'Dialogue': moved_events, '标签': moved_tags, '样式': len(base_names),
          '矢量裁切未动': skipped, '编码': encoding or src_d['enc']}
    if f8:
        st['MF8'] = f'删 {mf8_st.get("删", 0)} 条'
        if mf8_st.get('孤单未删'):
            st['MF8 未删'] = f'{mf8_st["孤单未删"]} 条是孤立方块行'
    if f6:
        st['MF6'] = f'换 {f6_done} 处'
        if f6_hold:
            top = '；'.join(f'{k}×{v}' for k, v in sorted(
                f6_hold.items(), key=lambda kv: -kv[1])[:3])
            st['MF6 未换'] = top + (f'（另有 {len(f6_hold) - 3} 类）' if len(f6_hold) > 3 else '')
    return out, st, warn, src_d


def has_bom(path):
    return Path(path).read_bytes()[:3] == b'\xef\xbb\xbf'


def write_out(lines, src_d, dst, encoding):
    eol = src_d['eol']
    text = eol.join(lines) + (eol if src_d['text'].endswith(('\n',)) else '')
    enc = encoding or src_d['enc']
    dst.write_bytes(text.encode(enc))


def passthrough_lines(lines):
    """除 Dialogue、[Script Info]、[V4+ Styles] 之外的行——这些必须逐字不变。

    头部与样式表整段换成基准，行数本来就可能和原来不同，所以不能拿「非 Dialogue 行数」
    当判据（第一版就这么写的，两边恰好一样才没露）。这里只比真正的旁路段：
    Events 里的 Comment、[Fonts]、[Graphics]、Aegisub 的工程垃圾。
    """
    b = blocks(lines)
    skip = [b[n] for n in ('Script Info', 'V4+ Styles') if n in b]
    out = []
    for i, ln in enumerate(lines):
        if any(lo <= i < hi for lo, hi in skip):
            continue
        if ln.strip().lower().startswith('dialogue:'):
            continue
        out.append(ln)
    return out


def cue_map(lines):
    """{条序: (样式名, 文本)}，Dialogue 才收，序号按出现顺序。"""
    out, n = {}, 0
    for _, kind, f in events_of(lines):
        if kind != 'Dialogue':
            continue
        n += 1
        out[n] = (f[3] if len(f) > 3 else '', f[9] if len(f) > 9 else '')
    return out


def verify(before, after, base, video=None, from_res=None, f6=False, f8=False):
    """七条判据。期望值从「改前 + 基准比例」独立重算，不看 apply 怎么算的。"""
    bd, ad, xd = decode_of(before), decode_of(after), decode_of(base)
    bl, al, xl = bd['lines'], ad['lines'], xd['lines']
    issues = []

    bw, bh = (as_int(v) for v in (playres(bl) or ('0', '0')))
    if (bw <= 0 or bh <= 0) and from_res:
        bw, bh = from_res
    xw, xh = (as_int(v) for v in (playres(xl) or ('0', '0')))
    aw, ah = (as_int(v) for v in (playres(al) or ('0', '0')))
    if (aw, ah) != (xw, xh):
        issues.append(f'画布不是基准值：改后 {aw}×{ah}，基准 {xw}×{xh}')
    if video:
        vw, vh = video
        if xh and abs(xw / xh - vw / vh) > 0.005:
            issues.append(f'基准画布比例 {xw}:{xh} 与视频 {vw}:{vh} 不一致——基准本身不合格')

    # ① 头部整段等于基准
    def seg(lines, name):
        b = blocks(lines)
        return lines[b[name][0]:b[name][1]] if name in b else None

    for name in ('Script Info', 'V4+ Styles'):
        s, x = seg(al, name), seg(xl, name)
        if s != x:
            k = next((i for i, (p, q) in enumerate(zip(s or [], x or [])) if p != q), 0)
            got = (s or [''])[k][:40] if s else '缺段'
            want = (x or [''])[k][:40] if x else '缺段'
            issues.append(f'[{name}] 与基准不一致（第 {k + 1} 行）：'
                          f'改后「{got}」，基准「{want}」')

    # ② 条数与可见文本：正文一字未动
    bc, ac = cue_map(bl), cue_map(al)
    if f8:
        # MF8 本来就要整条删掉底纹条，期望值先把改前按同一判据折一遍、再重编条序；
        # 不折就会一律报「条数变了」——与 MF6 折改前同理。这不会变成「自己验自己」：
        # 删多了、删错了、该删的没删，折完的条数或逐条正文照样对不上，一样红。
        _i8, exp8, _s8 = mf8_pick(bl)
        keep = [(n, v) for n, v in sorted(bc.items()) if n not in exp8]
        bc = {i: v for i, (_n, v) in enumerate(keep, 1)}
    if len(bc) != len(ac):
        issues.append(f'Dialogue 条数变了：{len(bc)} → {len(ac)}')
    else:
        diff = [k for k in bc if visible(bc[k][1]) != visible(ac[k][1])]
        if diff:
            issues.append(f'{len(diff)} 条的正文变了（只许动样式）：{diff[:6]}')

    # ③ 标签数值按比例到位（独立解析，不复用换算函数）
    if not bw or not bh or not len(bc) == len(ac):
        return issues, {'条数': len(bc), '正文变': len(bc) - len(set(bc)), '标签不符': 0}
    sx, sy = xw / bw, xh / bh
    bad = []
    for k in bc:
        bt, at = bc[k][1], ac.get(k, ('', ''))[1]
        if f6:
            # MF6 本来就要删掉 \fs / \fn / \c 这些数值标签，期望值先把改前按同一判据折一遍；
            # 不折就得每条报「标签数 1→0」——第二部剧实测 704 条假红。
            # 这不会变成「自己验自己」：误删了该留的（比如带 \pos 的条），mf6 不折它、改后却少了，照样红。
            bt = mf6(bt)[0]
        bv, av = tag_values(bt), tag_values(at)
        if len(bv) != len(av):
            bad.append((k, f'标签数 {len(bv)}→{len(av)}'))
            continue
        for (n1, v1), (n2, v2) in zip(bv, av):
            if n1 != n2 or len(v1) != len(v2):
                bad.append((k, f'{n1} 变成 {n2}'))
                break
            ax, ay = (sx if n1 in SCALAR_X else 1), (sy if n1 in SCALAR_Y else 1)
            if n1 in POS_TAGS:
                # 坐标按轴换算，但 \\move 的时间参数不许动——第一版把 t1/t2 也乘了，
                # 于是「期望 7848.75 实际 2093」这种假红冒出来（2026-11-05 第二部剧实测）
                lim = POS_TAGS[n1]
                want = [v * (sx if j % 2 == 0 else sy) if j < lim else v
                        for j, v in enumerate(v1)]
            else:
                want = [v1[0] * (sy if n1 in SCALAR_Y else sx)]
            if any(abs(w - g) > 0.02 for w, g in zip(want, v2)):
                bad.append((k, f'{n1} 期望 {[round(w, 2) for w in want]} 实际 {v2}'))
                break
    if bad:
        issues.append(f'{len(bad)} 条的标签数值不对：{bad[:4]}')

    # ④ 引用断言：样式字段与 \r 引用都已声明
    names = {n for _, n, _ in styles_of(al)}
    undecl = []
    for k, (style, text) in ac.items():
        if style and style not in names:
            undecl.append((k, f'样式 {style}'))
        for ref in re.findall(r'\\r([^\\}]+)', text):
            if ref and ref not in names and ref not in ('', 'Default'):
                undecl.append((k, f'\\r{ref}'))
    if undecl:
        issues.append(f'{len(undecl)} 处引用了没声明的样式（会静默回落 Default）：{undecl[:4]}')

    # ⑤ 旁路段逐字不变（Comment、Fonts、Graphics 等）；头部与样式表本来就要换，不比
    pb, pa = passthrough_lines(bl), passthrough_lines(al)
    if pb != pa:
        first = next((i for i, (x, y) in enumerate(zip(pb, pa)) if x != y), min(len(pb), len(pa)))
        issues.append(f'旁路段被改了（第 {first + 1} 行起）：'
                      f'「{pb[first][:30] if first < len(pb) else "缺行"}」→ '
                      f'「{pa[first][:30] if first < len(pa) else "多行"}」')
    if [n for n in blocks(bl)] != [n for n in blocks(al)]:
        issues.append(f'段落结构变了：{[n for n in blocks(bl)]} → {[n for n in blocks(al)]}')

    # ⑥ BOM 与编码
    if bd['bom'] != ad['bom']:
        issues.append('BOM 状态变了——播放器可能认成另一个文件')

    return issues, {'条数': len(bc), '正文变': len([k for k in bc if k in ac and
                                                  visible(bc[k][1]) != visible(ac[k][1])]),
                    '标签不符': len(bad)}


def check(path, video=None, base=None):
    """只读诊断：画布比例、样式表、引用、内联标签、底纹条候选、编码。"""
    path = Path(path)
    base = Path(base) if base else None
    d = decode_of(path)
    lines = d['lines']
    b = blocks(lines)
    res = playres(lines)
    w, h = (as_int(res[0]), as_int(res[1])) if res else (0, 0)
    st = styles_of(lines)
    ev = events_of(lines)
    dl = [(i, f) for i, k, f in ev if k == 'Dialogue']
    names = {n for _, n, _ in st}

    refs, inline_n, inline_lines = set(), 0, 0
    bar = 0
    for _, f in dl:
        text = f[9] if len(f) > 9 else ''
        n = len(INLINE.findall(text))
        inline_n += n
        inline_lines += 1 if n else 0
        for r in re.findall(r'\\r([^\\}]+)', text):
            if r and r not in names and r != 'Default':
                refs.add(r)
        vis = visible(text).strip()
        if vis and BLOCKY.match(vis) and len(vis) > 0:
            bar += 1

    r = {'file': path.name, 'res': f'{w}×{h}' if res else '缺', '比例': f'{w / h:.3f}' if h else '-',
         '样式': len(st), 'cue': len(dl), '内联标签': inline_n, '占用条': inline_lines,
         '未声明引用': len(refs), '底纹条?': bar,
         '编码': d['enc'], 'BOM': '有' if d['bom'] else '无',
         '换行': 'CRLF' if d['eol'] == '\r\n' else 'LF', '段': len(b)}
    notes = []
    if not res or w <= 0 or h <= 0:
        notes.append('PlayRes 缺失或为 0：坐标系无效，播放器行为不可预期')
    elif video:
        vw, vh = video
        if abs(w / h - vw / vh) > 0.005:
            notes.append(f'画布比例 {w / h:.3f} 与视频 {vw / vh:.3f} 不一致：'
                         f'字会被非等比拉伸（横竖缩放不同倍）')
    if refs:
        notes.append(f'引用了没声明的样式 {sorted(refs)}：播放时静默回落 Default，不报错')
    if bar:
        notes.append(f'{bar} 条疑似底纹条（可见字符只剩方块字符）——F8，'
                     f'加 --f8 才删；这里只是候选数，同刻没有别的行的那几条不会被删')
    if inline_lines:
        notes.append(f'{inline_lines} 条带内联标签（{inline_n} 处）——F6 统一成 {{\\rX}} 要判断，本版只报')
    if base:
        bl = decode_of(base)['lines']
        bx = playres(bl)
        if bx and (as_int(bx[0]), as_int(bx[1])) != (w, h):
            notes.append(f'与基准画布不同：基准 {bx[0]}×{bx[1]}')
        bn = [n for _, n, _ in styles_of(bl)]
        if bn and bn != [n for _, n, _ in st]:
            notes.append(f'样式表与基准不同：本文件 {len(st)} 条 {[n for _, n, _ in st]}，基准 {len(bn)} 条 {bn}')
    return r, notes


def parse_video(s):
    if not s:
        return None
    m = re.match(r'^(\d+)\s*[x×*]\s*(\d+)$', s.strip())
    if not m:
        sys.exit(f'--video 要写成 1920x1080 这种：{s!r}')
    return int(m.group(1)), int(m.group(2))


def parse_map(items):
    out = {}
    for s in items or []:
        if '=' not in s:
            sys.exit(f'--style-map 要写成 旧名=新名：{s!r}')
        k, v = s.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def cmd_base(src, target, out):
    """从一份现成基准换算出新基准：改 PlayRes，样式数值按轴缩，其余原样。

    为什么要它：基准样例是 1920×1080（16:9），4:3 的剧（1980s 老剧）用不了。
    以前每部非 16:9 的剧都要手写脚本改一遍——改 PlayResX、横边距乘比例，
    漏一样就整片偏。这是 MF2 的直接应用，不该由人手工做。
    """
    d = decode_of(src)
    lines = d['text'].splitlines()
    w0 = h0 = 0
    for ln in lines:
        m = re.match(r'\s*PlayResX\s*:\s*(\d+)', ln, re.I)
        if m:
            w0 = int(m.group(1))
        m = re.match(r'\s*PlayResY\s*:\s*(\d+)', ln, re.I)
        if m:
            h0 = int(m.group(1))
    if not (w0 and h0):
        raise Bad(f'{src.name} 的 PlayRes 是 0 或缺失——基准必须有明确坐标系才谈得上换算。'
                  f'先人工定一个（或换一份基准），别让工具猜')

    final, sec, inside, sc = [], [], False, None
    for ln in lines:
        s = ln.strip()
        low = s.lower().replace(' ', '')
        m = re.match(r'\s*PlayRes([XY])\s*:', ln, re.I)
        if m:
            k = m.group(1).upper()
            final.append(f'PlayRes{k}: {target[0] if k == "X" else target[1]}')
            continue
        if low.startswith('[v4+styles]'):
            inside = True
            final.append(ln)
            continue
        if inside and s.startswith('[') and s.endswith(']'):
            inside = False
            new_sec, sc = rescale_styles(sec, (w0, h0), target)
            final.extend(new_sec)
            sec = []
            final.append(ln)
            continue
        if inside:
            sec.append(ln)
            continue
        final.append(ln)
    if sec:                       # 样式段一直写到文件尾（少见，但不能丢）
        new_sec, sc = rescale_styles(sec, (w0, h0), target)
        final.extend(new_sec)

    out.write_bytes(b'\xef\xbb\xbf' + ('\r\n'.join(final) + '\r\n').encode('utf-8'))
    y = sc[1] if sc else 1.0
    hint = ''
    if y < 0.75 or y > 1.5:
        hint = (f'  ⚠ 纵向 {h0} → {target[1]}（×{fmt(y)}），字号与描边整体'
                f'{"缩小" if y < 1 else "放大"}。填的是片源像素分辨率就会这样——'
                f'纵向该取交付高度 1080、横向按显示宽高比算（MF2）。')
    return {'源': f'{w0}×{h0}', '目标': f'{target[0]}×{target[1]}',
            '比例': f'字号与纵边距 ×{fmt(sc[1])}，横边距 ×{fmt(sc[0])}' if sc else '无需换算',
            '样式': len([1 for ln in final if ln.strip().lower().startswith('style:')]),
            '警告': hint}


def main():
    ap = argparse.ArgumentParser(description='文件层样式规范化：头部/样式表换基准，坐标按比例换算')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('paths', nargs='+')
    c.add_argument('--video')
    c.add_argument('--base')
    a = sub.add_parser('apply')
    a.add_argument('target')
    a.add_argument('--base', required=True)
    a.add_argument('-o', '--out')
    a.add_argument('--encoding')
    a.add_argument('--from-res', help='原文件 PlayRes 为 0 时，显式声明它原本的坐标系，如 384x288')
    a.add_argument('--style-map', action='append', help='旧样式名=基准里的样式名，可重复')
    a.add_argument('--video', help='视频分辨率，用于校验基准比例，如 1920x1080')
    a.add_argument('--dry-run', action='store_true')
    a.add_argument('--f6', action='store_true',
                   help='MF6：把「\\N + 一串样式参数 + 英文」换成 \\N{\\rEnglish}（需要基准里有 English 样式）')
    a.add_argument('--f8', action='store_true',
                   help='MF8：删底纹条（可见字符只剩方块、且同刻还有别的非空行）。不给就不删')
    b = sub.add_parser('base', help='按视频分辨率生成一份基准（从现成基准换算）')
    b.add_argument('src', help='源基准 .ass')
    b.add_argument('--video', required=True, help='目标分辨率，如 1440x1080')
    b.add_argument('-o', '--out', required=True, help='输出基准 .ass')
    v = sub.add_parser('verify')
    v.add_argument('before')
    v.add_argument('after')
    v.add_argument('base')
    v.add_argument('--video')
    v.add_argument('--from-res')
    v.add_argument('--f6', action='store_true', help='复算时按 MF6 折一遍改前，别按「标签数变少」报红')
    v.add_argument('--f8', action='store_true', help='复算时按 MF8 折一遍改前，别按「条数变少」报红')
    args = ap.parse_args()

    if args.cmd == 'base':
        try:
            st = cmd_base(Path(args.src), parse_video(args.video), Path(args.out))
        except Bad as e:
            sys.exit(f'拒绝执行：{e}')
        print(f'基准：{st["源"]} → {st["目标"]}（{st["比例"]}）· 样式 {st["样式"]} 条')
        print(f'  输出：{args.out}')
        if st.get('警告'):
            print(st['警告'])
        return 0

    if args.cmd == 'check':
        files = []
        for x in args.paths:
            p = Path(x)
            if p.is_dir():
                files += sorted(f for f in p.iterdir() if f.suffix.lower() in ('.ass', '.ssa'))
            elif p.is_file():
                files.append(p)
            else:
                sys.exit(f'路径不存在：{p}')
        if not files:
            sys.exit('没有 .ass / .ssa 文件')
        rows, notes = [], []
        for f in files:
            r, n = check(f, parse_video(args.video), args.base)
            rows.append(r)
            notes += [(f.name, x) for x in n]
        cols = ['file', 'res', '比例', '样式', 'cue', '内联标签', '占用条', '未声明引用',
                '底纹条?', '编码', 'BOM', '换行', '段']
        print('| ' + ' | '.join(cols) + ' |')
        print('|' + '---|' * len(cols))
        for r in rows:
            print('| ' + ' | '.join(str(r[c]) for c in cols) + ' |')
        if notes:
            print('')
            for name, n in notes:
                print(f'  · {name}：{n}')
        return 0

    if args.cmd == 'verify':
        issues, st = verify(Path(args.before), Path(args.after), Path(args.base),
                            parse_video(args.video), parse_video(args.from_res),
                            f6=args.f6, f8=args.f8)
        print(f'复算：条数 {st["条数"]} / 正文变 {st["正文变"]} / 标签不符 {st["标签不符"]}')
        for x in issues:
            print('  ERROR', x)
        return 1 if issues else 0

    src, base = Path(args.target), Path(args.base)
    try:
        out, st, warn, src_d = apply_file(src, base, args.encoding,
                                          parse_video(args.from_res), parse_map(args.style_map),
                                          f6=args.f6, f8=args.f8)
    except Bad as e:
        sys.exit(f'拒绝执行：{e}')
    for w in warn:
        print('  WARN', w)
    print(f'落地：{st["画布"]}（{st["换算"]}）· Dialogue {st["Dialogue"]} 条 · '
          f'标签 {st["标签"]} 处 · 样式 {st["样式"]} 条 · 编码 {st["编码"]}')
    if st.get('MF8'):
        print(f'  MF8：{st["MF8"]}'
              + (f'；未删 {st["MF8 未删"]}' if st.get('MF8 未删') else ''))
    if st.get('MF6'):
        print(f'  MF6：{st["MF6"]}'
              + (f'；未换 {st["MF6 未换"]}' if st.get('MF6 未换') else ''))
    if st['矢量裁切未动']:
        print(f'  注意：{st["矢量裁切未动"]} 处矢量裁切没动，坐标会随画布偏移，要人工看')
    if args.dry_run:
        return 0 if st['标签'] else 3

    dst = Path(args.out) if args.out else src.with_suffix('.norm' + src.suffix)
    write_out(out, src_d, dst, args.encoding)
    issues, st2 = verify(src, dst, base, parse_video(args.video), parse_video(args.from_res),
                         f6=args.f6, f8=args.f8)
    print(f'复算：条数 {st2["条数"]} / 正文变 {st2["正文变"]} / 标签不符 {st2["标签不符"]}')
    for x in issues:
        print('  ERROR', x)
    if issues:
        return 1
    print(f'  输出：{dst}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
