# -*- coding: utf-8 -*-
# 作者：DeepSeek-V4.1-Flash 2026-11-01
# 最近修改：DeepSeek-V4.1-Flash 2026-11-01（新建，把「漏报盲测」抽样从现场敲脚本固化成命令）
"""漏报盲测抽样：从一片 worksheet 里**分层等距**抽段，生成隔离目录。

为什么要分层等距、不随机抽单行：
  漏报多发在连续两三行的衔接处（断句、指代、否定跨行），单行抽出来看不出问题。
  所以按行数等分成 N 个带，带内随机起点，取连续 M 行，段与段不重叠。

口径来源：`05_参考资料/2_给AI看/通读开工方案.md` 5.3 节。
  沿革：E06 后单片提到 10 段 × 10 行；**2026-11-01 降本改为每片 5 段 × 10 行 = 50 行，
  两片合计 100 行、一个 Agent 跑完**（依据见 `agent-craft`「单件成本预算」）。

用法：
  python blind_sample.py --worksheet <剧库>/02_工作区/pilot/<集>/worksheet_01.tsv \
        --ep-tag <集> --tag 01 [--segs 8] [--len 8] [--seed 20261101] [--base <隔离根>]

产物（<base>/<日期>/<集号>/blind-<tag>/）：
  sample.tsv       题面（行号/时间码/中文/英文），只放这段
  p4_semantic.md   判读口径（查漏用，不是评分表）
  terminology.json / character_tone.json   口径参考件（**可选**：给了就拷，没给不拷。
                                           术语表本身不入本库，见 docs/中间产物格式.md）
  交接卡片.md       贴给新会话的整段话，写明「查漏、不是挑准」
  audit_skeleton.json  回填骨架

**绝不拷候选清单**：放了就锚定，测试作废。
"""
import argparse
import datetime
import json
import pathlib
import random
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = pathlib.Path(__file__).resolve().parent


def read_ws(path):
    lines = pathlib.Path(path).read_text(encoding='utf-8-sig').splitlines()
    head, body = lines[0], [l for l in lines[1:] if l.strip()]
    return head, body


def pick_segments(n_rows, segs, length, seed):
    """分层等距：切成 segs 个等宽带，带内随机起点取连续 length 行。"""
    if segs * length > n_rows:
        raise SystemExit(f'抽不出来：{segs}×{length}={segs * length} > {n_rows} 行')
    band = n_rows // segs
    rnd = random.Random(seed)
    out = []
    for i in range(segs):
        lo = i * band
        hi = lo + band - length          # 带内起点上限（保证段尾不越界）
        start = rnd.randint(lo, max(lo, hi))
        out.append((start, start + length))   # 左闭右开，相对下标
    return out


def main():
    ap = argparse.ArgumentParser(description='漏报盲测抽样（分层等距）')
    ap.add_argument('--worksheet', required=True)
    ap.add_argument('--ep-tag', required=True, help='集标签，随便起，只用来分目录，如 01 或 E01')
    ap.add_argument('--tag', required=True, help='片号，如 01')
    ap.add_argument('--segs', type=int, default=5)
    ap.add_argument('--len', type=int, default=10)
    ap.add_argument('--seed', type=int, default=20261101)
    ap.add_argument('--with-rowno', action='store_true',
                    help='给没有行号列的分片（首列是时间码的那种）临时加一列序号，不写回源文件')
    ap.add_argument('--ln-col', type=int, default=0,
                    help='行号在第几列（0 起）。不给就自动找：首列是时间码的分片'
                         '写死取第 0 列会 ValueError')
    ap.add_argument('--base', required=True,
                    help='隔离根目录，必须在项目外。必填不给默认值——跑批产物落进库里等于污染真身')
    args = ap.parse_args()

    head, body = read_ws(args.worksheet)
    if args.with_rowno:
        # 首列是时间码的分片没有行号列，抽样与回写都需要一个稳定的行标识。
        # 只加在内存里的副本上，源分片不动；sample.tsv 会带上这一列，方便回写时定位。
        head = '行号\t' + head
        body = [f'{i + 1}\t{r}' for i, r in enumerate(body)]
    idx = pick_segments(len(body), args.segs, args.len, args.seed)

    def ln_of(line):
        """取这一行的行号。自动模式：从第 0 列起找第一个纯数字列。"""
        parts = line.split('\t')
        if args.ln_col:
            return int(parts[args.ln_col - 1])
        if parts and parts[0].strip().isdigit():
            return int(parts[0])
        for k, p in enumerate(parts):
            if p.strip().isdigit():
                print(f'注意：首列不是行号（{parts[0][:16]!r}），改用第 {k + 1} 列；'
                      f'可用 --ln-col {k + 1} 写死')
                args.ln_col = k + 1
                return int(p)
        raise SystemExit(f'这行找不到行号列。首列是时间码的分片没有行号列，加 --with-rowno；'
                             f'行号在中间某列时用 --ln-col：{line[:80]}')

    base = pathlib.Path(args.base).resolve()
    # 硬护栏：与 isolate_blind / isolate_review 同一条——隔离目录不许落在当前项目里。
    # 这件原来没这道检查（另两件一直有），2026-11-07 在库根跑了一次，产物就落进库了。
    try:
        base.relative_to(pathlib.Path.cwd())
        raise SystemExit(f'隔离目录 {base} 在当前项目内，起不到隔离作用，请换到项目外')
    except ValueError:
        pass

    out = base / datetime.date.today().strftime('%Y%m%d') / args.ep_tag / f'blind-{args.tag}'
    out.mkdir(parents=True, exist_ok=True)

    kept = [body[i] for a, b in idx for i in range(a, b)]
    (out / 'sample.tsv').write_text(head + '\n' + '\n'.join(kept) + '\n',
                                    encoding='utf-8', newline='\n')

    for name in ('p4_semantic.md',):
        shutil.copy2(TOOLS / 'prompts' / name, out / name)
    for src in (ROOT / '02_工作区' / 'global' / 'terminology.json',
                ROOT / '02_工作区' / 'pilot' / 'character_tone.json'):
        shutil.copy2(src, out / src.name)

    lns = [ln_of(r) for r in kept]
    ranges = [[ln_of(body[a]), ln_of(body[b - 1])] for a, b in idx]

    skeleton = {'episode': args.ep_tag, 'chunk': int(args.tag),
                'vo_ranges': [], 'issues': []}
    (out / 'audit_skeleton.json').write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')

    card = f"""# 交接：{args.ep_tag} 片{args.tag} 漏报复核（查漏测试）

> 读者：AI　｜　用途：这一片有没有**漏报**

## 这是什么测试

上一轮已经判过一遍候选。这次**不给你候选清单**（给了就锚定，测试作废），
只给你 {args.segs} 段共 {len(kept)} 行原文，问你：**这些行里有没有该改而没改的。**

- 重点是**找全**，不是挑准。判据符合就报，置信度低也报。
- `p4_semantic.md` 里「明确不报」的那些类别**一条都不要列**（润色、读着别扭、不够精确）。
- 这 {args.segs} 段在剧情上**不连续**，每段自成上下文。**不要因为「段与段接不上」就报剧情断裂**——那是抽样造成的，不是字幕错。
  同理：段首那一行的前半句常常在段外的上一行。凡是指控「缺主语 / 缺前半句 / 否定反了」，先确认逻辑上有没有可能在段外。
- 判不出来就少报，**别凑数**。

## 输入

- `sample.tsv`：题面。列＝行号 / 时间码 / 中文 / 英文。行号是 ASS 物理行号，报的时候照抄。
- `terminology.json`：译名与职衔 canon（与表不一致才算问题）。
- `character_tone.json`：人物说话风格卡；`calibrated=false` 的那张只作弱参考。

## 输出

写 `result.json` 到本目录，结构照 `audit_skeleton.json`：

```json
{{"episode": "{args.ep_tag}", "chunk": {int(args.tag)}, "vo_ranges": [],
 "issues": [{{"ln": 123, "dimension": "L1", "problem_type": "C2.5 关键信息被替换",
            "severity": "major", "confidence": 0.9,
            "evidence": "为什么观众会看错", "suggest": "一句中文建议",
            "cn": "该行中文原文", "en": "该行英文原文"}}]}}
```

- `problem_type` 必须带 C 编号（C1.1–C1.6 / C2.1–C2.5 / C3.1–C3.3）。
- `cn` / `en` 逐字照抄 sample.tsv，不许改写。
- 一条都没有就写空 `issues`，**这是合法结果**，不许硬凑。

## 本次抽样

段范围（ASS 行号）：{', '.join(f'L{a}–L{b}' for a, b in ranges)}
抽样行数：{len(kept)}　seed={args.seed}

## 红线

本目录是隔离目录。**只读本目录内的文件**，不要去读项目里的日志、判定记录、候选表——
读了就是污染，这一轮结果作废。
"""
    (out / '交接卡片.md').write_text(card, encoding='utf-8', newline='\n')

    print(f'隔离目录：{out}')
    print(f'  抽样 {args.segs} 段 × {args.len} 行 = {len(kept)} 行（seed={args.seed}）')
    print(f'  段范围：{", ".join(f"L{a}-L{b}" for a, b in ranges)}')
    print(f'  贴给新会话：读 {out / "交接卡片.md"}，做完把 result.json 写回同一目录。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
