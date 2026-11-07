# 最近修改：未记录 2026-10-31（2026-11-01 补署名；此前无此行，原改动内容无据可查）
# 作者：DeepSeek 2026-10-25
"""语义候选预筛：把「已改过」的行挑出来，只留真新增给人工判。

为什么需要它：
  某一集试点时，通读报出 76 条，其中 **31 条（41%）在前两轮查表/对轨时
  已经改掉了**——等于让用户白看四成。剩下 34 集沿用同一模式，必须先筛。

四类判据：
  真新增        该行没有旧 finding                       → 进草稿
  已改过        有旧 finding（旧改法已审过、已落地）        → 跳过，但列出两侧措辞供抽查
  仅英文轨      suggest 为空（英文原文 typo，中文不用动）  → 只登记
  旧补丁未落地  有旧 finding 但成品仍是源文               → 报警（那条补丁没应用）

为什么不按"改法是否相同"再分一档：
  同一批实测 31 条重合里，26 条措辞与旧改法不同，但逐条看**没有一条意思不同**
  （"交通糟透了" vs "交通真要命"）。中文改写字符差异大，相似度也区分不了
  （同义改写相似度能低到 0.20）。所以只要旧 finding 存在就跳过，不再制造"待裁决"队列。

另有两道校验：
  - 行号重复：同一行在两批里被报出且改法不同 → 报错（相邻批有 8 行重叠）
  - 成品行号折算：源字幕与成品会因**补行**错开（全库 37 处 inserts，分布在 22 集，
    某一集就有 3 处：源 906 行 / 成品 909 行）。同一个 ln 直接查两边，442 行以后会
    查错行 → 「旧补丁未落地」这项检查会静默失效（见 #44）。现在按"时间码优先、
    补行折算兜底"把成品行对齐回源行号，对不上的行不下结论（宁可不判，不误报）。

用法：
  python prefilter_semantic.py --ep-tag <集标签>
  python prefilter_semantic.py --ep-tag <集标签> --ignore "*.semantic.json"   # 复核上次结果
  python prefilter_semantic.py --ep-tag <集标签> --dry-run                    # 只看统计不写文件
"""
import argparse
import glob
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

import ass_rows as D  # noqa: E402  # 本库解析，别再依赖另一个库的剧专用模块

ROOT = pathlib.Path(__file__).resolve().parents[2]
PUNCT = str.maketrans({'，': ',', '。': '.', '！': '!', '？': '?', '：': ':',
                       '；': ';', '、': ',', '（': '(', '）': ')',
                       '「': '"', '」': '"', '“': '"', '”': '"'})


def norm(s):
    """比对用归一：去空白 + 全角标点转半角 + 去标点。

    只用于判"是不是同一句话"，绝不用于改原文——字幕里标点是排版的一部分。
    """
    t = re.sub(r'\s+', '', str(s or '')).translate(PUNCT)
    return re.sub(r'[,.:;!?"\'()\-—…·]', '', t)


def load_inserts(season, key, root=None):
    """读该集的补行记录，返回「补在第几行之后」的列表（已排序）。

    补行落在 patch 的 `inserts` 里，每项带 after_ln。同一处可能补多行，
    所以 after_ln 会重复——折算行号时按个数累计，不去重。
    """
    base = (root or ROOT) / '02_工作区' / season / 'patch'
    p = base / f'{key}.patch.json'
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding='utf-8'))
    return sorted(int(i['after_ln']) for i in (data.get('inserts') or []) if i.get('after_ln') is not None)


def align_dst(src_rows, dst_rows, insert_after):
    """把成品行对齐回源行号，返回 {源ln: 成品行}（对不上的不给键）。

    为什么要对齐：补行只加在成品上，源字幕不动，所以第 N 行以后两边错开。
    旧实现用同一个 ln 同时查两边，「旧补丁未落地」会在错位区间里对着别的行下结论。

    命中顺序（前一步中就不走下一步）：
      ① 起止时间码完全相同 —— 最可靠，改内容不改时间码
      ② 按补行折算的行号 —— 补丁顺手改了时间码时兜底
      ③ 都对不上 —— 不给键，调用方跳过该行的结论（宁可不判，不误报）
    """
    by_tc, dup = {}, 0
    for r in dst_rows.values():
        tc = (r.get('start'), r.get('end'))
        if tc in by_tc:
            dup += 1
            continue
        by_tc[tc] = r

    def shifted(ln):
        return ln + sum(1 for a in insert_after if a < ln)

    out, hit_tc, hit_ln, miss = {}, 0, 0, 0
    for ln, s in src_rows.items():
        r = by_tc.get((s.get('start'), s.get('end')))
        if r is not None:
            out[ln] = r
            hit_tc += 1
            continue
        r = dst_rows.get(shifted(ln))
        if r is not None:
            out[ln] = r
            hit_ln += 1
            continue
        miss += 1
    return out, {'时间码': hit_tc, '折算': hit_ln, '未对上': miss, '成品时间码重复': dup}


def changed_bits(sg, old_new):
    """列出「模型想改成」与「旧轮次改成」各自独有的字（去重保序）。

    只做给人眼扫的原始事实呈现，**不做任何「是不是同一个问题」的判断**——
    试过按改动区段位置判「同行两问题」，84/121/126 这类措辞变体也被判成不同处，
    误报比抓到的真问题多。所以这里只把两侧独有的字摆出来：L205 一眼看到
    「模型独有「酥」／旧改法独有「汉堡」」，就知道模型动的是另一处。
    """
    a, b = norm(sg), norm(old_new)
    if a == b:
        return ''
    sb, sa = set(b), set(a)
    only_a = ''.join(dict.fromkeys(c for c in a if c not in sb))
    only_b = ''.join(dict.fromkeys(c for c in b if c not in sa))
    bits = []
    if only_a:
        bits.append(f'模型独有「{only_a}」')
    if only_b:
        bits.append(f'旧改法独有「{only_b}」')
    return ' / '.join(bits)


def classify(cand, old_row, final_cn, src_cn, final_en=None, src_en=None):
    """单条候选归类。返回 (类别, 说明)。纯函数，便于单测。

    为什么还要看英文轨（2026-10-31 修）：
      旧 finding 有一大类是 `en_typo` / 机械 pass——**中文一字不动，只改英文轨**
      （`cn_new == cn`，即只改了大小写或标点这种不改变意思的情况）。拿中文比「成品是不是
      还等于源文」，这类必然相等 → 每一条都被误报成「旧补丁未落地」，把「先查
      apply 是否漏跑」这种严重告警变成噪音。实测某一集三条全属此类，
      成品英文轨其实已经改好了。
      正确判法：`cn_new == cn` 的旧 finding 要看**英文轨**有没有变成 en_after。

    为什么 en_typo 型旧 finding 下，模型提中文要算「真新增」（2026-10-31 补）：
      `cn_new == cn` 意味着旧轮次**中文一字未动**。若本片模型动的恰恰是中文，
      那旧 finding 根本没覆盖这件事——不是「已改过」，是**中文轨上从没处理过的
      新问题**。实测一集有三条全被旧实现当「已改过」静默剔掉，
      而三条都是真错（could have had it all ／ you're up ／ dad day）。
      反向例（别把这条判据放太松）：模型 suggest 与**成品中文**相同 → 成品已经是
      模型要的样子，仍归「已改过」，不许塞进新增队列充数。

    已知盲点（2026-10-31 实测，**是有意保留的取舍，不是 bug**）：
      旧 finding 改的是中文、且**同一行另有第二个问题**时，整行仍被跳过，
      第二处会静默消失。实测一条：某个专名的旧写法已被改过，
      与「熊爪汉堡→熊爪酥」（`bear claw` 误译，成品至今未改、全库仅此一处）同在一行。
      自动判据抓不到它——**这是设计取舍**：10-25 已明说「只要旧 finding 存在就跳过，
      不再制造待裁决队列」，而位置启发式试过、误报更多（见 `changed_bits` 注释）。
      所以留两个口子：① 跳过明细输出 `两处差异` 列，两侧独有字摆出来（L205 一眼可见）；
      ② `--force-new 行号,...` 人工捞回。**每批必须人工扫一眼跳过明细。**
    """
    sg = (cand.get('suggest') or '').strip()
    if not sg:
        return '仅英文轨', '英文原文 typo，中文本身无误'
    ln = cand['ln']
    if old_row is None:
        return '真新增', ''
    old_new = str(old_row.get('cn_new') or '')
    old_cn = str(old_row.get('cn') or '')

    # 旧 finding 是"只动英文轨"型（中文前后一致）→ 拿英文轨判落地
    if norm(old_new) == norm(old_cn):
        old_en = str(old_row.get('en_after') or '').strip()
        # 先报最贵的告警：旧补丁在成品里根本没落地（先查 apply 是否漏跑）
        if old_en and final_en is not None and src_en is not None:
            if norm(final_en) == norm(src_en) and norm(src_en) != norm(old_en):
                extra = ''
                if final_cn is not None and norm(sg) != norm(final_cn):
                    extra = f'；另外模型还提了中文：{sg}（同行两处，别忘了捞）'
                return '旧补丁未落地', (f'旧 finding 说英文轨改成「{old_en[:40]}」，'
                                        f'但成品仍是源文{extra}')
        # 旧英文轨已落地（或无法核）→ 看模型动的是不是**中文**：
        # cn_new == cn 说明旧轮次中文一字未动，模型提中文就是中文轨上的新问题
        if final_cn is not None and norm(sg) != norm(final_cn):
            return '真新增', f'旧 finding 只动英文轨（中文一字未改），模型提的是中文：{sg}'
        if old_en and final_en is not None and norm(final_en) == norm(old_en):
            return '已改过', f'旧轮次已改英文轨（中文不动）：{old_en[:40]}'
        # 无 en_after 可比时不下结论（宁可不判，不误报）
        return '已改过', '旧轮次为只动英文轨的 finding（中文不动），无法核英文轨'

    # 常规：中文有改动，按中文判落地
    if src_cn is not None and final_cn is not None and norm(final_cn) == norm(src_cn):
        return '旧补丁未落地', f'旧 finding 说改成「{old_new}」，但成品仍是源文'
    if norm(old_new) == norm(sg):
        return '已改过', f'旧轮次已改为「{old_new}」'
    bits = changed_bits(sg, old_new)
    return '已改过', (f'旧轮次改为「{old_new}」（措辞不同，意义相同）'
                      + (f'；两处差异：{bits}——同行可能还有第二个问题，见跳过明细' if bits else ''))


def load_candidates(pat):
    """读若干 chunk JSON，返回 issues 列表；跨批行号重复且改法不同就报错。"""
    files = sorted(glob.glob(str(pat)))
    if not files:
        raise SystemExit(f'没有候选文件：{pat}')
    out, seen = [], {}
    for f in files:
        data = json.loads(pathlib.Path(f).read_text(encoding='utf-8'))
        for i in data.get('issues') or []:
            ln = int(i['ln'])
            if ln in seen and norm(seen[ln].get('suggest')) != norm(i.get('suggest')):
                raise SystemExit(
                    f'L{ln} 在两批里给出不同改法（{seen[ln].get("suggest")!r} vs '
                    f'{i.get("suggest")!r}），请先人工定一条再跑')
            if ln not in seen:
                seen[ln] = i
                out.append(i)
    return out, [pathlib.Path(f).name for f in files]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ep-tag', required=True, help='集标签，随便起，只用来分目录')
    ap.add_argument('--chunks', default='', help='候选 glob；缺省取 pilot/<集>/semantic_chunk*.json')
    ap.add_argument('--ignore', default='', help='忽略某些 findings 文件（逗号分隔 glob，如复核自己上次的产物）')
    ap.add_argument('--force-new', default='', help='人工捞回：这些行号强制归入「真新增」（逗号分隔，如 205）。'
                                                   '用于「同行两问题」等判据抓不到的盲点，须在任务记录里写明理由')
    ap.add_argument('--out-dir', default='')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    season, key = args.ep_tag[:3], f'{args.ep_tag[1:3]}x{args.ep_tag[4:]}'
    chunks = pathlib.Path(args.chunks) if args.chunks else (
        ROOT / '02_工作区' / 'pilot' / args.ep_tag / 'semantic_chunk*.json')
    cands, names = load_candidates(chunks)

    # 旧 findings：本集所有 *.json，可排除指定文件
    ign = [g.strip() for g in args.ignore.split(',') if g.strip()]
    fdir = ROOT / '02_工作区' / season / 'findings'
    old = {}
    used = []
    for p in sorted(fdir.glob(f'{key}*.json')):
        if any(pathlib.Path(p.name).match(g) for g in ign):
            continue
        used.append(p.name)
        for x in json.loads(p.read_text(encoding='utf-8')).get('findings') or []:
            old.setdefault(int(x['ln']), x)     # 同名多文件时先到先得，与 merge 一致

    # 源文与成品现状：用来判断"旧补丁有没有落地"
    src = [p for p in D.season_files(ROOT, season) if D.ep_of(p) == key]
    src_rows = {r['ln']: r for r in D.iter_rows(src[0])} if src else {}
    dst = sorted((ROOT / '03_成品').rglob(f'*{args.ep_tag}*.ass'))
    dst_raw = {r['ln']: r for r in D.iter_rows(dst[0])} if dst else {}
    inserts = load_inserts(season, key)
    dst_rows, align_stat = align_dst(src_rows, dst_raw, inserts) if dst_raw else ({}, {'时间码': 0, '折算': 0, '未对上': 0, '成品时间码重复': 0})

    buckets = {'真新增': [], '已改过': [], '仅英文轨': [], '旧补丁未落地': []}
    force = {int(x) for x in args.force_new.replace('，', ',').split(',') if x.strip()}
    for c in sorted(cands, key=lambda x: int(x['ln'])):
        ln = int(c['ln'])
        cat, why = classify(c, old.get(ln),
                            dst_rows.get(ln, {}).get('cn'),
                            src_rows.get(ln, {}).get('cn'),
                            dst_rows.get(ln, {}).get('en'),
                            src_rows.get(ln, {}).get('en'))
        if ln in force and cat != '真新增':
            why = f'【人工捞回】原判「{cat}」：{why}'
            cat = '真新增'
        buckets[cat].append((ln, c, why))

    out_dir = pathlib.Path(args.out_dir) if args.out_dir else (
        ROOT / '02_工作区' / season / 'semantic')
    n_new = len(buckets['真新增'])

    print(f'{args.ep_tag}: 候选 {len(cands)} 条（来自 {len(names)} 个文件）')
    if inserts:
        print(f'  补行 {len(inserts)} 处（after_ln {inserts}），成品行号已折算；'
              f'对齐：时间码 {align_stat["时间码"]} / 折算 {align_stat["折算"]} / 未对上 {align_stat["未对上"]}')
    print(f'  旧 findings：{", ".join(used) or "无"}' + (f'（已忽略 {", ".join(ign)}）' if ign else ''))
    for k in ('真新增', '已改过', '仅英文轨', '旧补丁未落地'):
        if buckets[k]:
            print(f'  {k}: {len(buckets[k])} 条')
    print(f'  → 需要人工判的：{n_new} 条')

    if buckets['旧补丁未落地']:
        print('  ! 严重：以下行的旧补丁没落地，成品仍是源文——先查 apply 是否漏跑')
        for ln, _c, why in buckets['旧补丁未落地'][:10]:
            print(f'    L{ln} {why}')

    if args.dry_run:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    def w(name, header, rows):
        p = out_dir / f'{key}_{name}.tsv'
        body = '\t'.join(header) + '\n' + '\n'.join('\t'.join(map(str, r)) for r in rows) + '\n'
        p.write_text(body, encoding='utf-8', newline='\n')
        return p

    p_new = w('新增', ['ln', 'cn_new', 'type', 'severity', 'evidence', 'en_after'],
              [[ln, c.get('suggest', ''), c.get('problem_type', ''),
                c.get('severity', ''), (c.get('evidence') or '')[:160], '']
               for ln, c, _ in buckets['真新增']])
    p_diff = w('已改过', ['ln', '类别', '我的改法', '旧 finding 类型', '说明'],
               [[ln, cat, c.get('suggest', ''), (old.get(ln) or {}).get('type', ''), why]
                for cat in ('已改过', '仅英文轨', '旧补丁未落地')
                for ln, c, why in buckets[cat]])

    print(f'  新增草稿 -> {p_new.name}（{n_new} 条，可直接填 fill_findings）')
    print(f'  跳过明细 -> {p_diff.name}（含两侧措辞，想抽查时看）')
    print(f'  目录：{out_dir}')


if __name__ == '__main__':
    main()
