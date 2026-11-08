#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-08T2130（词表加载拒绝「错写法是另一条译法子串」；摘要按规则打「处 / 行」两个数）
"""text_scan.py -- 清稿·文本层扫描：只按唯一改法机改，判断一律不碰

管四条（编号与改法见 docs/工序-清稿-规则清单.md 第二节）：

  MT1 译名错写    词表第三列精确替换；最长匹配优先；单字错写只报不改
  MT2 标签残留    <font> / <i> / <b> / <u> / <s> 删标签，正文不动
  MT3 标点与间距  半角标点贴中文 → 全角；中文与拉丁字母之间补一个半角空格
  MT4 花括号残留  `{}` / `{\\}` 删；带参数的只报不改（是不是样式引用要人看）

**不管两条**：MT5 单词粘连、MT6 金额符号被吃——改法不唯一，退回对稿/审稿，
本工具连候选都不出（`subtitle_probe.py` 的 `long_word` / `money_q` 两列管数）。

为什么 import land_verify 的 load / plain：锚点口径必须与落地工具**逐字一致**。
自己再写一份 plain，就是在赌两个实现永远不漂；漂了的表现是「扫描说改了、落地报失配」。

用法：
    python tools/text_scan.py <字幕.ass> [--term 词表.tsv] [--tsv X.mech.tsv] [--report X.需人判.tsv]

输出：
  mech.tsv   五列 `no / old / new / rule / note`，前三列直接喂 `land_verify.py apply`
             多出的两列 read_changes 不看（它只取前三列）
  需人判表   四列 `no / rule / 说明 / 当前文本`——这些条目本工具一个字都不动

退出码：0 正常（含 0 改动）；2 参数/输入错。

**只支持 ASS**：文本层排在文件层之后，那时输入已经是 ASS；SRT 的折行与序号规则不同，
不在这件工具的射程里（和补行「只支持 ASS」同一个取舍）。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import land_verify as lv            # noqa: E402

CJK = re.compile(r'[\u4e00-\u9fff]')
LATIN = re.compile(r'[A-Za-z]')
TAG_HTML = re.compile(r'</?(?:font|i|b|u|s)\b[^>]*>', re.I)
BRACE = re.compile(r'\{([^{}]*)\}')
HALF2FULL = {',': '，', '.': '。', ';': '；', ':': '：', '?': '？', '!': '！'}

SRC_RULE = 'MT1 MT2 MT3 MT4'.split()


# ---------------------------------------------------------------- 词表

def load_terms(path):
    """读词表，返回 [(错写法, 规定译法)]，按长度降序（最长匹配优先）。

    第三列「已见错写法」按 `|` `;` `；` 分隔，可写多个。
    **同一个错写法登记两次 → 拒绝加载**，不许工具挑一个——挑错了没人看得出来。
    **错写法是某条规定译法的一部分 → 也拒绝加载**（2026-11-08T2130，剧库 #5）：
    `伯纳` 是 `伯纳德` 的前缀，替换出来是「伯纳德德」，剧库 E02 实测 9 处。
    这条能挡在扫描之前，就别等成品里看见叠字再回改。
    """
    rows, seen, dests = [], {}, set()
    for i, line in enumerate(lv.decode(path).splitlines()):
        if not line.strip():
            continue
        if i == 0 and ('原文' in line or 'term' in line.lower()):
            continue
        parts = line.split('\t')
        if len(parts) < 2:
            lv.fail(f'词表第 {i + 1} 行不足两列（原文 / 规定译法）：{line[:40]}')
        dst = parts[1].strip()
        if not dst:
            lv.fail(f'词表第 {i + 1} 行「{parts[0]}」没填规定译法')
        dests.add(dst)
        raw = parts[2].strip() if len(parts) > 2 else ''
        for w in [x.strip() for x in re.split(r'[|;；]', raw) if x.strip()]:
            if w in seen:
                lv.fail(f'词表里错写法「{w}」登记了两遍（译法：{seen[w]} / {dst}）'
                        f'——同一处只能有一个答案')
            seen[w] = dst
            rows.append((w, dst))
    # 子串冲突：错写法出现在任一条规定译法里，替换就会叠字。
    # 单字错写不在此列——r_mt1 已把它们排除在替换之外，只报不改，造不出叠字。
    for w, dst in rows:
        if len(w) < 2:
            continue
        for d2 in dests:
            if w != d2 and w in d2:
                lv.fail(f'词表里错写法「{w}」（→「{dst}」）是规定译法「{d2}」的一部分'
                        f'——替换后叠字。两条改一条：错写法写长，或确认这条不该机改')
    rows.sort(key=lambda r: -len(r[0]))
    return rows


# ---------------------------------------------------------------- 四条规则

def r_mt1(s, terms):
    """词表错写法 → 规定译法。**单遍扫描，不在替换结果上重扫。**

    两遍扫描会造出新词：`伯拿`→`伯纳德` 之后，新串里含 `伯纳`，又被
    `伯纳`→`伯纳德` 命中一次，得到「伯纳德德」。2026-11-07 在 E01 真数据上
    一次撞了 4 条（#152/#299/#332/#460）。所以用一次 `re.sub`：替换产出的
    文本不再参与匹配，这也正是「补丁不该套补丁」的意思。

    单字错写只报不改：当子串会误伤别的词（`奥` 之于 `奥德曼`）。
    """
    if not terms:
        return s, [], []
    usable = [(w, d) for w, d in terms if len(w) >= 2]
    holds = [('MT1', f'单字错写「{w}」只报不改（当子串会误伤）')
             for w, _ in terms if len(w) < 2 and w in s]
    if not usable:
        return s, [], holds

    # 已按长度降序排过（load_terms 保证），长的排前面 = 最长匹配优先
    pat = re.compile('|'.join(re.escape(w) for w, _ in usable))
    mapping = dict(usable)
    cnt = {}

    def repl(m):
        w = m.group(0)
        cnt[w] = cnt.get(w, 0) + 1
        return mapping[w]

    new = pat.sub(repl, s)
    hits = [('MT1', f'「{w}」→「{d}」{cnt[w]} 处') for w, d in usable if w in cnt]
    return new, hits, holds


def r_mt3(s, punct=True, space=True):
    """半角标点贴中文换全角；中文与**拉丁字母**之间补一个半角空格。

    数字不补：`2万1千793票`、`第487卷` 是中文的常规写法，补了空格反而错。
    已有空格的相邻位置不重复补（prev 是空格时那对字符不相邻）。
    """
    hits = []
    if punct:
        out, n_p = [], 0
        for i, ch in enumerate(s):
            if ch in HALF2FULL:
                prev = s[i - 1] if i else ''
                nxt = s[i + 1] if i + 1 < len(s) else ''
                if prev and CJK.match(prev) and (not nxt or CJK.match(nxt) or nxt == ' '):
                    out.append(HALF2FULL[ch])
                    n_p += 1
                    continue
            out.append(ch)
        if n_p:
            hits.append(('MT3', f'半角标点换全角 {n_p} 处'))
        s = ''.join(out)

    if space:
        out, n_s, prev = [], 0, ''
        for ch in s:
            if prev and ((CJK.match(prev) and LATIN.match(ch)) or
                         (LATIN.match(prev) and CJK.match(ch))):
                out.append(' ')
                n_s += 1
            out.append(ch)
            prev = ch
        if n_s:
            hits.append(('MT3', f'中英之间补空格 {n_s} 处'))
        s = ''.join(out)

    return s, hits, []


# ---------------------------------------------------------------- 门禁

def inspect(head_raw):
    """这条能不能整条替换。返回 (可否, 原因, 要删的 HTML 标签数, 要删的空花括号数)。

    **必须在原始串上数，不能看 plain 之后的结果**：`lv.plain()` 会先把 `{...}` 和
    `<...>` 一律抹掉，等它抹完再看就永远看不到这两类残留，MT2 / MT4 会静默失效
    （2026-11-07 首版就是这么错的：扫描报「MT3 130 处」，标签和花括号一处没抓到）。

    整条替换还会**压掉中文里的 `\\N` 折行**（land_verify 的 zh 是空格拼出来的），
    带参数的花括号也会被连参数一起抹掉，这两类一律挡，不赌。
    """
    if '\\N' in head_raw:
        return False, '中文有 \\N 折行，整条替换会压成一行', 0, 0

    n_empty = 0
    for m in BRACE.finditer(head_raw):
        if m.group(1).strip() in ('', '\\'):
            n_empty += 1
        else:
            return False, f'中文里有带参数的花括号「{m.group(0)}」，可能是样式引用', 0, 0

    leftover = BRACE.sub('', head_raw)
    if '{' in leftover or '}' in leftover:
        return False, '中文里有不配对的花括号', 0, 0

    tags = len(TAG_HTML.findall(leftover))
    rest = TAG_HTML.sub('', leftover)
    if '<' in rest or '>' in rest:
        return False, '中文里有不认识的尖括号', 0, 0
    return True, None, tags, n_empty


# ---------------------------------------------------------------- 主流程

def scan(path, terms, punct=True, space=True):
    """返回 (改动行, 需人判行, 统计)。只算，不落盘。"""
    lines, cues, kind = lv.load(path)
    if kind != 'ass':
        lv.fail(f'文本层只支持 ASS：{path.name} 是 SRT。文本层排在文件层之后，'
                f'先跑 build_base / style_normalize 把它变成 ASS')
    rows, holds, stat = [], [], {r: 0 for r in SRC_RULE}
    n_changed = n_hold = 0

    for c in cues:
        zh = c['zh']
        if not zh:
            continue                      # 纯英文条（标题卡/组标）不碰
        old = lv.plain(zh)
        if not old:
            continue
        if '\t' in old:
            holds.append((c['no'], '门禁', '文本里有制表符，TSV 装不下', old))
            n_hold += 1
            continue

        head_raw = c['text'].split(lv.EN_MARK)[0]
        ok, why, tags, braces = inspect(head_raw)
        if not ok:
            holds.append((c['no'], '门禁', why, old))
            n_hold += 1
            continue

        # MT4 的“改动”已经在 plain 里完成了（plain 会抹掉 `{...}`），所以只记命中；
        # MT2 不一样：plain **不认尖括号**，标签原样留着，必须在这里真删。
        s, hits, notes = old, [], []
        if tags:
            s = TAG_HTML.sub('', s)
            hits.append(('MT2', f'删 HTML 标签 {tags} 个'))
        if braces:
            hits.append(('MT4', f'删空花括号 {braces} 处'))
        if terms:
            s, h, hd = r_mt1(s, terms)
            hits += h
            notes += hd
        s, h, hd = r_mt3(s, punct, space)
        hits += h
        notes += hd

        if s == old and not hits:
            for rule, msg in notes:       # 有话说但不动文本 → 也进需人判
                holds.append((c['no'], rule, msg, old))
                n_hold += 1
            continue
        if notes:
            # 这条既能改、又有要人看的地方：整条不进 mech，避免「改了一半以为改完了」
            holds.append((c['no'], notes[0][0], '；'.join(m for _, m in notes) + '（本条未改）', old))
            n_hold += 1
            continue

        for rule, _ in hits:
            stat[rule] += 1
        rows.append((c['no'], old, s, ';'.join(sorted({r for r, _ in hits})),
                     ';'.join(m for _, m in hits)))
        n_changed += 1

    return rows, holds, {'changed': n_changed, 'hold': n_hold, 'rules': stat}


def cell(s):
    return str(s).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')


def write_tsv(path, header, rows):
    body = '\t'.join(header) + '\n' + ''.join('\t'.join(cell(x) for x in r) + '\n' for r in rows)
    Path(path).write_text(body, encoding='utf-8-sig', newline='\n')
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description='清稿·文本层扫描（MT1–MT4，只挑候选、只做唯一改法）')
    ap.add_argument('subtitle')
    ap.add_argument('--term', help='词表（四列：原文 / 规定译法 / 已见错写法 / 备注）')
    ap.add_argument('--tsv', help='机改清单输出路径（不填只打印摘要）')
    ap.add_argument('--report', help='需人判清单输出路径')
    ap.add_argument('--no-punct', action='store_true', help='不做半角标点换全角')
    ap.add_argument('--no-space', action='store_true', help='不补中英间距')
    a = ap.parse_args()

    path = Path(a.subtitle)
    if not path.exists():
        lv.fail(f'找不到字幕：{path}')
    terms = load_terms(Path(a.term)) if a.term else []

    rows, holds, st = scan(path, terms, not a.no_punct, not a.no_space)

    print(f'扫描 {path.name}：可机改 {st["changed"]} 条，需人判 {st["hold"]} 条')
    if st['changed']:
        # 2026-11-08T2130（剧库 #11）：原来只打命中次数，而 tsv 按行落盘——同一条命中
        # 两个词表条目时摘要比 tsv 多 1（实测 MT1 报 2、tsv 只 1 行）。而「先看规则分布、
        # 某条占九成就先怀疑匹配键」这条做法（工序-对稿-手册）靠的正是这个数。
        landed = {}
        for r in rows:
            landed[r[3]] = landed.get(r[3], 0) + 1
        print('按规则：' + ' / '.join(
            f'{k} {v} 处、{landed.get(k, 0)} 行'
            for k, v in st['rules'].items() if v))
    if a.tsv:
        write_tsv(a.tsv, ['no', 'old', 'new', 'rule', 'note'], rows)
        print(f'机改清单已写：{a.tsv}（{len(rows)} 行）')
    if a.report and holds:
        write_tsv(a.report, ['no', 'rule', 'note', 'text'], holds)
        print(f'需人判清单已写：{a.report}（{len(holds)} 行）')
    if not a.tsv and not a.report:
        for r in rows[:10]:
            print('  ' + ' | '.join(str(x) for x in r[:3]))
        if len(rows) > 10:
            print(f'  （只打印前 10 条，共 {len(rows)}；看全量给 --tsv）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
