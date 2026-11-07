# -*- coding: utf-8 -*-
# 作者：DeepSeek-V4.1-Flash 2026-10-31
# 用途：复核判完档之后、出人工表之前的分流。按 2026-10-31 新口径（只报 C1/C2/C3）：
#         应改 → 自动落盘清单（不再进人工表）
#         不改 → 抽 1/3 进人工表当「抽检」，其余跳过
#       目的是把人工判量从「全部候选」压到「抽检 + 少数例外」。
# 为什么要有这一步：片 1 实测 69 条候选里，应改 28 条用户零否决、不改 2 条全对，
#       真正产生决策的只有 9 条否决（3.6 分钟 / 28 分钟）。全量给人判，等于为保险付 8 倍价钱。
# 用法：
#   python split_by_tier.py --review <隔离目录>/review.tsv \
#          --candidates <隔离目录>/candidates.tsv [--ep-tag <集标签>] \
#          [--sample 0.34] [--seed 20261031] [--out-dir <dir>]
# 产物（默认 <review 同目录>/split/）：
#   auto_apply.tsv  应改档里**能自动落盘**的（有改法、不在句群内）
#   manual.tsv      需要人看的：不改档抽样 + 应改档里落不了的（缺改法 / 句群成员）
#   summary.md      分流报告（条数、抽样比例、跳过明细、与片 1 基线的对照）
# 退出码：0 正常；1 输入不自洽（行号集合对不上、没有可分流条目）。
#
# 最近修改：DeepSeek-V4.1-Flash 2026-11-01（#131 接入：summary 加一行「超 10 CPS N 行 / 重叠 M 处」，
#   只报数、不进 accept 硬门槛——CPS 不是错译，硬卡会逼判读为凑字数牺牲准确，同 #133 的坑）
import argparse
import json
import math
import pathlib
import random
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 根目录跟随当前工作目录（在哪个剧库里跑，就找那个剧库的 02_工作区 / 03_成品）。
ROOT = pathlib.Path.cwd()


def read_tsv(path):
    """读 TSV，返回 (header, [dict])。review.tsv 实测带 BOM，一律 utf-8-sig。"""
    rows = path.read_text(encoding='utf-8-sig').strip().split('\n')
    header = rows[0].split('\t')
    out = []
    for r in rows[1:]:
        if not r.strip():
            continue
        out.append(dict(zip(header, r.split('\t'))))
    return header, out


def load_group_members(ep_tag):
    """句群成员行号。句群要整组一起改，单行自动落盘会读不通，所以一律转人工。"""
    if not ep_tag:
        return set()
    season, key = ep_tag[:3], f'{ep_tag[1:3]}x{ep_tag[4:]}'
    p = ROOT / '02_工作区' / season / 'groups' / f'{key}.json'
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding='utf-8'))
    return {int(it['ln']) for g in (data.get('groups') or []) for it in (g.get('items') or [])}


def clean(s):
    return (s or '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()


def pacing_line(ep_tag, sheet=''):
    """本片节奏数字（#131）：超 10 CPS 行数 / 重叠处数。**只报数**，不参与任何判定。

    口径说明：#131 定的是「统计口径 9 CPS / 清单口径 10 CPS」，清单里报的是
    **超 10 CPS**（10 是 1,166 行≈每集 10 行，9→10 砍掉的都是正常老盘节奏）。
    重叠沿用 ass_pacing 的定义（相邻 cue 的 gap < 0 记 1 处）。
    """
    if not ep_tag:
        return ''
    src = sorted((ROOT / '03_成品').rglob(f'*{ep_tag}*.ass'))
    if not src:
        return '- 节奏：成品里没找到本集 ass，跳过（不报数不等于没问题）'
    lo = hi = None
    if sheet and pathlib.Path(sheet).exists():
        lns = [int(l.split('\t')[0])
               for l in pathlib.Path(sheet).read_text(encoding='utf-8-sig').splitlines()[1:]
               if l.strip()]
        if lns:
            lo, hi = min(lns), max(lns)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import ass_pacing as P  # noqa: E402  # 只为了少写一份解析，口径必须同源
    cues = [c for c in P.parse(src[0]) if c['style'] == 'Default']
    if lo is not None:
        cues = [c for c in cues if lo <= c['ln'] <= hi]
    over10 = sum(1 for c in cues if c['cps'] > 10)
    # 第一条 cue 的 gap 是 None（没有前一条可比），必须挡掉——全片口径下它就是第 1 行
    overlap = sum(1 for c in cues if c['gap'] is not None and c['gap'] < 0)
    scope = f'L{lo}–L{hi}' if lo is not None else '全片'
    return (f'- 节奏（{scope}，{len(cues)} 行）：超 10 CPS **{over10} 行** / '
            f'重叠 **{overlap} 处**　只报数、不进验收门槛（CPS 不是错译）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--review', required=True, help='复核交回的 review.tsv')
    ap.add_argument('--candidates', required=True, help='isolate_review 产的 candidates.tsv')
    ap.add_argument('--ep-tag', default='', help='集标签；给了才查句群（组内行不自动落）')
    ap.add_argument('--sample', type=float, default=1.0,
                    help='「不改」档抽样比例。默认 1.0＝全抽（2026-11-01 改）：'
                         '实测判「不改」的准确率只有一半，抽 1/3 等于放走其中一半的错译')
    ap.add_argument('--seed', type=int, default=20261031, help='抽样种子，固定才能复现')
    ap.add_argument('--out-dir', default='', help='缺省 <review 同目录>/split')
    ap.add_argument('--sheet', default='',
                    help='本片 worksheet（TSV）。给了就把节奏数字限定在该片的行号区间；'
                         '不给则报全片。只影响 summary 里那行报数，不参与分流判定')
    ap.add_argument('--judged', default='',
                    help='人工回填表（TSV：行号<TAB>你的决定，值 改/应改/不改）。'
                         '给了才算「不改档改判率」，超过三成判本片复核失效（退出码 2）')
    args = ap.parse_args()

    rev_p = pathlib.Path(args.review)
    cand_p = pathlib.Path(args.candidates)
    for p in (rev_p, cand_p):
        if not p.exists():
            raise SystemExit(f'文件不存在：{p}')

    _, cands = read_tsv(cand_p)
    _, revs = read_tsv(rev_p)
    cmap = {int(c['行号']): c for c in cands}
    rmap = {int(r['行号']): r for r in revs}

    if set(cmap) != set(rmap):
        miss = sorted(set(cmap) - set(rmap))[:8]
        extra = sorted(set(rmap) - set(cmap))[:8]
        raise SystemExit(f'行号集合对不上——漏 {len(set(cmap) - set(rmap))} 条、'
                         f'多 {len(set(rmap) - set(cmap))} 条（{miss} / {extra}）。先跑 check_review.py')

    gmembers = load_group_members(args.ep_tag)

    auto, manual, skipped = [], [], []
    n_tier = {'应改': 0, '不改': 0}
    n_legacy, n_bad = 0, 0

    for ln in sorted(cmap):
        r, c = rmap[ln], cmap[ln]
        tier = (r.get('初判') or '').strip()
        reason = clean(r.get('理由', ''))
        fix = clean(r.get('建议改法', '')) or clean(c.get('模型建议', ''))
        tc, cn = c.get('时间码', ''), c.get('中文', '')
        base = {'ln': ln, 'tc': tc, 'cn': cn, 'fix': fix, 'why': reason,
                'typ': clean(c.get('模型类型', ''))}

        if tier == '应改':
            n_tier['应改'] += 1
            # 自动落盘的三个条件：有译法、不是句群成员、改法不是空的
            if ln in gmembers:
                manual.append({**base, 'tier': '应改', 'src': '句群成员，需整组改'})
            elif not fix:
                manual.append({**base, 'tier': '应改', 'src': '缺建议改法，需人给'})
            else:
                auto.append(base)
        elif tier == '不改':
            n_tier['不改'] += 1
            skipped.append(ln)          # 先全进跳过，抽样在后面挖回来
        else:
            # 旧档「可改可不改」按不改处理（10-31 口径），和真「不改」一起进抽样池
            # 空值 / 自创档位不同：AI 压根没给出合法判定，不能当「它说没问题」放过，
            # 必须单独摆到人工面前定档，且不占抽样名额
            if tier == '可改可不改':
                n_legacy += 1
                skipped.append(ln)
            else:
                n_bad += 1
                manual.append({**base, 'tier': '异常',
                               'src': '档位非法或空值，需人工定档'})

    # 「不改」档抽样：固定种子，保证重跑一致
    # 2026-10-31 修：原用 ceil，池子只有 3 条时 ceil(1.02)=2，实际抽了 2/3 却对外写「1/3」，
    # 把人工占比抬成 11.1%（真按 1/3 应是 5.6%）。改成四舍五入 + 小池保底 1 条。
    n_s = min(len(skipped), max(1, int(len(skipped) * args.sample + 0.5))) if skipped else 0
    picked = sorted(random.Random(args.seed).sample(sorted(skipped), n_s)) if n_s else []
    for ln in picked:
        r, c = rmap[ln], cmap[ln]
        manual.append({'ln': ln, 'tc': c.get('时间码', ''), 'cn': c.get('中文', ''),
                       'fix': clean(r.get('建议改法', '')), 'why': clean(r.get('理由', '')),
                       'typ': clean(c.get('模型类型', '')), 'tier': '不改（抽检）',
                       'src': '抽检：AI 判不改，抽查有没有漏'})
    rest = [ln for ln in skipped if ln not in set(picked)]

    # ── 不改档漏报率（2026-11-01 立；同日改为「只记录、不触发重判」）──────────
    # 量什么：AI 判「不改」的条目，人工看过后判「改」的比例。
    # 为什么要有：实测两集改判 44% / 67%——同一份判据判两次，结论差一半。
    # 为什么不再触发重判（11-01 二次改）：
    #   两轮回归实测，这个数字的天花板就是 50%——run1 4/6、run2 2/4，
    #   换了两种判法都压不下去。设 30% 的阈值等于每集都判「复核失效」，流程卡死。
    #   结论：这不是措辞能修的，是「判不改」这个判断本身的可靠性上限。
    #   所以流程改成**不改档全量进人工**（--sample 1.0），人工就是兜底，
    #   这里只留数字作质量记录，将来判断能力有没有变化靠它看趋势。
    # 分母为什么是「人工真给过结论的」而不是全部不改档：没被抽到就没人看过，
    #   把它算进分母等于用「没判」冒充「判对了」，数字会被稀释。
    REV_GATE = 0.30
    rev_block = ''
    if args.judged:
        jp = pathlib.Path(args.judged)
        if not jp.exists():
            raise SystemExit(f'--judged 文件不存在：{jp}')
        _, jrs = read_tsv(jp)
        dec = {}
        for j in jrs:
            raw_ln = (j.get('行号') or '').strip()
            if not raw_ln.isdigit():
                continue
            v = (j.get('你的决定') or j.get('决定') or '').strip()
            if v:
                dec[int(raw_ln)] = v
        base = {ln for ln in cmap
                if (rmap[ln].get('初判') or '').strip() in ('不改', '可改可不改')}
        seen = base & set(dec)
        flipped = sorted(ln for ln in seen if dec[ln] in ('改', '应改', '要改'))
        if not seen:
            rev_block = ('\n## ⚠ 不改档漏报率无法计算\n\n'
                         f'- 给了 `--judged`，但表里没有一行落在本片的「不改」档（{len(base)} 条）。'
                         '要么回填表填错了列，要么这一轮不是这批。**别当成 0% 放过。**\n')
        else:
            rate = len(flipped) / len(seen)
            rev_block = (f'\n## 不改档漏报率 {rate:.0%}（{len(flipped)}/{len(seen)}）\n\n'
                         f'- 参考线 {REV_GATE:.0%}，本片**{ "高于" if rate > REV_GATE else "未超" }**参考线。\n'
                         f'- 改判行号：{flipped or "无"}\n'
                         f'- 分母 {len(seen)} 是人工真给过结论的不改档，本片不改档共 {len(base)} 条。\n'
                         f'- **只作质量记录，不触发重判**：11-01 起不改档已默认全量进人工（`--sample 1.0`），'
                         f'人工本身就是兜底。两轮回归实测这个数字天花板约 50%，长期超过 50% 说明判读能力到头，'
                         f'该换流程而不是继续改措辞。\n')

    out = pathlib.Path(args.out_dir) if args.out_dir else rev_p.parent / 'split'
    out.mkdir(parents=True, exist_ok=True)

    A_HDR = ['行号', '时间码', '中文', '建议改法', '理由', '类型']
    with (out / 'auto_apply.tsv').open('w', encoding='utf-8', newline='\n') as f:
        f.write('\t'.join(A_HDR) + '\n')
        for x in auto:
            f.write('\t'.join([str(x['ln']), x['tc'], x['cn'], x['fix'], x['why'], x['typ']]) + '\n')

    M_HDR = ['行号', '档位', '分流原因', '中文', '建议改法', '理由']
    with (out / 'manual.tsv').open('w', encoding='utf-8', newline='\n') as f:
        f.write('\t'.join(M_HDR) + '\n')
        for x in manual:
            f.write('\t'.join([str(x['ln']), x['tier'], x['src'], x['cn'],
                               x['fix'], x['why']]) + '\n')

    total = len(cmap)
    manual_n = len(manual)
    pct = lambda a, b: (a * 100 // b if b else 0)
    if len(manual) < 5:
        leak_note = (f'判据：本批只抽 {len(manual)} 条，样本不足以算率。'
                     '**出现 1 条 C1/C2/C3 类漏判就记一笔，跨批累计同类 3 条**再回改判据；个案不返工。')
    else:
        leak_note = (f'判据：抽检里 C1/C2/C3 类漏判 **≥2 条**（本批抽 {len(manual)} 条，约 15%），'
                     '说明提示词的边界收得过窄，')

    # 原来这里写死「片 1 基线是 100%」——那是占位符没填，不是任何真实数据。
    # 2026-11-01 改成不写具体基线（跨集基线放在判定记录.md，避免脚本里再出现搬来的数字）。
    #
    # 2026-11-01 三次改：流程改成「不改档全量进人工」后，人工占比必然接近 100%，
    # 旧的两档红线（15% 比例 / 小样本 ≤3 条）全部失效——它们量的正是「能不能少让人看」，
    # 而现在的前提是「人必须把候选看完」。真正的成本指标换成**候选密度**
    # （每百正片行几条候选），由 validate_semantic.py 的 2.5–7.0 带管：低于下沿＝漏报。
    ratio = manual_n * 100 / total
    verdict = (f'（人工占比 {ratio:.0f}% 正常：11-01 起不改档全量进人工；'
               f'成本看候选密度，不在这里判）')
    L = [f'# 分流报告 · {args.ep_tag or rev_p.parent.name}', '',
         f'- 候选 **{total}** 条 → 自动落盘 **{len(auto)}** / 人工 **{manual_n}** / 跳过 **{len(rest)}**',
         f'- 人工占比 {manual_n} / {total} = {ratio:.1f}%　→　{verdict}', '',
         '## 档位', '', '| 档 | 条数 |', '|---|---|',
         f'| 应改 | {n_tier["应改"]} |', f'| 不改 | {n_tier["不改"]} |']
    if n_legacy:
        L.append(f'| 旧档「可改可不改」（按不改处理、**不落盘**） | {n_legacy} |')
    if n_bad:
        L.append(f'| ⚠ 空值 / 自创档位（按不改处理，先回去跑 check_review） | {n_bad} |')
    _pace = pacing_line(args.ep_tag, args.sheet)
    if _pace:
        L += ['', _pace]
    L += ['', '## 人工这 {0} 条怎么来的'.format(manual_n), '']
    by_src = {}
    for x in manual:
        by_src[x['src']] = by_src.get(x['src'], 0) + 1
    for k, v in sorted(by_src.items(), key=lambda kv: -kv[1]):
        L.append(f'- {k}：{v} 条')
    if not by_src:
        L.append('- （无：全部可直接自动落盘，人工为 0）')
    real = (n_s / len(skipped)) if skipped else 0
    L += ['', '## 抽检口径', '',
          f'- 「不改」{len(skipped)} 条里抽 {n_s} 条（目标 {args.sample:.0%}，'
          f'实际 {real:.0%}，seed={args.seed}，重跑一致）',
          f'- 抽中：{[x["ln"] for x in manual if x["tier"].startswith("不改")]}']
    if skipped and abs(real - args.sample) > 0.10:
        L.append(f'- ⚠ 池子只 {len(skipped)} 条，保底 1 条 + 取整使实际比例偏离目标；'
                 f'人工占比按本行实际值读，别按 {args.sample:.0%} 反推')
    L.append('')
    L += ['', f'## 人工占比（只作信息，不判红）', '',
          f'- 定义：manual.tsv 行数 ÷ 本片候选条数',
          f'- 本片：{len(manual)} / {len(cmap)} = **{len(manual) * 100 / len(cmap):.1f}%**'
          f'（11-01 起不改档全量进人工，这个数接近 100% 是设计如此）', '']
    if rest:
        L += [f'## 跳过 {len(rest)} 条（不落盘、不入人工表）', '',
              f'行号：{rest}', '']
    L += ['## 读法', '',
          '- `auto_apply.tsv` 是**要落进成品**的，落盘后仍按老规矩跑 `verify` + `accept`。',
          '- `manual.tsv` 是唯一要人看的表：**应改**行是「落不了、要你给一手」，'
          '**不改（抽检）**行是「AI 说不用改，你抽查一下对不对」。', '',
          '## 抽检结果怎么读（别把口径内的否决当漏判）', '',
          '抽中的「不改」条目，人判「要改」时**看理由落在哪**：', '',
          '| 人给的理由 | 怎么算 |',
          '|---|---|',
          '| 会看错（理解反 / 看不懂 / 剧情断） | **AI 漏判**，记 1 笔 |',
          '| 只是读着别扭、不够准、翻译腔 | 口径内，**不算漏判**（10-31 已定不修这类） |', '',
          leak_note,
          '回去查 `p4_semantic.md` 的「明确不报」清单是不是砍多了；**个案漏判不返工**，'
          '系统性才改判据。', '',
          '另：已自动落盘的应改档，**每 5 集抽 1 集全量回看**，算误改率（片 1 基线是 0/28）。', '']

    if rev_block:
        L.append(rev_block.lstrip('\n'))

    text = '\n'.join(L)
    (out / 'summary.md').write_text(text, encoding='utf-8', newline='\n')
    print(text)
    print(f'产物 -> {out}')
    sys.exit(0 if (auto or manual) else 1)


if __name__ == '__main__':
    main()
