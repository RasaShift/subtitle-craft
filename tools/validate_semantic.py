# 最近修改：DeepSeek-V4.1-Flash 2026-10-31（加质量闸机检两项：vo_ranges 形态 / 候选密度）
# 前次修改：DeepSeek 2026-10-25（--season 参数（跨季复用））
"""校验语言档返回的语义审读 JSON，并转成 findings 结构。

用法：
  python validate_semantic.py 02_工作区/pilot/<集>/semantic_chunk1.json
  python validate_semantic.py ... --to-findings 02_工作区/S01/findings/01x01.semantic.json

校验项：
  1. JSON 可解析、必备字段齐全
  2. ln 在底本中存在，且 tc/cn 与底本一致（防止模型编造行号或记忆漂移）
  3. dimension 取值合法、severity 合法、confidence ∈ [0,1]
  4. 输出合规率与按维度/类型分布统计
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ass_rows as D  # noqa: E402  # 本库解析，别再依赖另一个库的剧专用模块

ROOT = Path.cwd()      # 剧库根，按当前工作目录找季文件

DIMS = {'L0', 'L1', 'L2', 'L3', 'L4', 'L5'}

DENSITY_NOTE = ('本片行数按分片表算；密度＝候选条数 / 每 100 行。'
                '参照带 2.5–7.0（2026-11-01 按三集实测重定，每项可复算）：'
                '实测三集：7.0、4.6、2.3（条每百行），逐集往下掉。'
                '放宽依据：E03 掉到 2.6/2.0 后做过 64 行盲测召回（子 Agent 不看候选清单），'
                '11 条里真漏报 0、major 零漏 → 密度下滑是题材差异（E03 前半无尸体现场、'
                '无行动线、无警衔术语整套），不是判据砍过头。'
                '所以本闸只用来发现「判断失控」，不作产量指标：'
                '低于 2 且 ≥150 行提示去查漏报，高于 12 提示查是否滑回旧口径。'
                '（曾误标 12.4＝53/429：53 是「20 小时」那节的方案 4 推算值 69×0.77，'
                '不是实测，2026-10-31 已作废。）')


def gate_vo(vo, sheet_path):
    """质量闸第 3 项：vo_ranges 必须成段。

    为什么机检：提示词明写「标成 [23,23] 这种单点等于没做，L4 人物声音这一层会失去依据」，
    但原实现只把 vo_ranges 打印出来，单点凑数不会被发现。这里按形态校验。
    """
    print('\nVO 区间:', vo if vo else '未标注')
    if not vo:
        print('  ⚠ 闸3 未标注 VO（本片若无内心独白可接受，否则 L4 层无依据）')
        return True
    bad = []
    for rng in vo:
        if (not isinstance(rng, list)) or len(rng) != 2 or any(
                not isinstance(x, int) for x in rng):
            bad.append(rng)
        elif rng[0] > rng[1]:
            # 起点在终点之后＝形态错；起==止是「单行」，形态合法但属凑数，
            # 交给下面的 singles 判定，不混进这里（否则单点反例会被报成形态非法）
            bad.append(rng)
    if bad:
        print(f'  ✗ 闸3 vo_ranges 形态非法（应为 [起, 止] 且 起<止）：{bad[:6]}')
        return False
    singles = [r for r in vo if r[0] + 1 >= r[1]]
    if len(singles) * 2 > len(vo):
        print(f'  ⚠ 闸3 单点/两点区间占 {len(singles)}/{len(vo)} 过半，疑似凑数：{singles[:6]}')
    print(f'  闸3 通过：{len(vo)} 段区间，形态全部合法')
    return True


def gate_density(total, sheet_path, rows, ep, season):
    """质量闸第 5 项：报数是否离谱——按「条 / 100 行」输出，供阈值判断。

    不设硬阈值：密度本身不是错误，是给「后来怎么排批量」用的数字。
    """
    if not sheet_path:
        print('\n（未给 --sheet，跳过闸5 密度统计）')
        return True
    try:
        header = Path(sheet_path).read_text(encoding='utf-8-sig').strip().split('\n')
        n = sum(1 for r in header[1:] if r.strip())
    except Exception as e:
        print(f'\n（密度统计读分片表失败：{e}）')
        return True
    per100 = (total / n * 100) if n else 0
    print(f'\n闸5 候选密度：{total} 条 / {n} 行 = {per100:.1f} 条每 100 行'
          f'（全片 {len(rows)} 行 → 外推 {per100 * len(rows) / 100:.0f} 条）')
    print(f'  {DENSITY_NOTE}')
    # 2026-11-01 重排：零候选必须最先判。原来它排在 "<4" 之后，凡工作表 ≥150 行
    # 的片一旦是 0 条就命中偏低分支，这句话永远打不出来（死分支）。
    if per100 == 0:
        print('  ⚠ 零候选：新口径（2026-10-31）下整片 0 条是**合法结果**（没有理解类问题），'
              '但抽检时要优先查这一片是不是漏报')
    elif per100 > 12:
        print('  ⚠ 密度 >12/100 行，偏高：确认不是把「译得不够好」整族当问题在报'
              '（是否滑回旧口径）。三集实测带 2.3–7.0')
    elif per100 < 2 and n >= 150:
        # 带两位小数：9/451=1.9955 若只显示 1 位是 2.0，会和「<2」的判断自相矛盾
        print(f'  ⚠ 密度 {per100:.2f}/100 行 <2，偏低：先跑一次 64 行盲测漏报复核'
              '（子 Agent 不看候选清单）。这是提示不是硬闸，别为了凑数硬报')
    return True

SEV = {'critical', 'major', 'minor'}


def load_rows(ep: str, season: str = 'S01'):
    files = D.season_files(ROOT, season, only=int(ep))
    if not files:
        raise SystemExit(f'{season} 第 {ep} 集没找到 ass（在 {ROOT} 下递归找）')
    ass = files[0]
    return {r['ln']: r for r in D.iter_rows(ass)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='语言档返回的 JSON')
    ap.add_argument('--season', default='S01', help='季号，如 S01')
    ap.add_argument('--ep', default='01')
    ap.add_argument('--to-findings', default=None)
    ap.add_argument('--sheet', default='', help='本片分片表（用于闸5 密度统计）')
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding='utf-8'))
    issues = data.get('issues') or []
    if not isinstance(issues, list):
        raise SystemExit('issues 不是数组')

    rows = load_rows(args.ep, args.season)
    ok, bad = [], []
    for it in issues:
        ln = it.get('ln')
        r = rows.get(ln)
        problems = []
        if r is None:
            problems.append('行号不存在')
        else:
            if it.get('tc') and it['tc'] != r['start']:
                problems.append(f"时间码不符 {it['tc']} != {r['start']}")
            if it.get('cn') and it['cn'].strip() != r['cn'].strip():
                problems.append('中文原文不符')
            if it.get('en') and it['en'].strip() != r['en'].strip():
                problems.append('英文原文不符')
        if it.get('dimension') not in DIMS:
            problems.append(f"dimension 非法 {it.get('dimension')!r}")
        if it.get('severity') not in SEV:
            problems.append(f"severity 非法 {it.get('severity')!r}")
        try:
            c = float(it.get('confidence', 0))
            if not (0 <= c <= 1):
                problems.append('confidence 越界')
        except Exception:
            problems.append('confidence 非数值')
        (bad if problems else ok).append((it, problems))

    total = len(issues)
    rate = (len(ok) / total * 100) if total else 100.0
    print(f'条目 {total}：合规 {len(ok)} / 不合格 {len(bad)}，合规率 {rate:.1f}%')
    if bad:
        for it, ps in bad[:8]:
            print(f"  L{it.get('ln')}: {'; '.join(ps)}")

    if ok:
        print('\n维度分布:', dict(Counter(i.get('dimension') for i, _ in ok)))
        print('类型分布:', Counter(i.get('problem_type') for i, _ in ok).most_common(8))
        print('严重度:', dict(Counter(i.get('severity') for i, _ in ok)))

    # ---- 质量闸后两项（2026-10-31 加）：原先只打印不校验
    gate_vo(data.get('vo_ranges') or [], args.sheet)
    gate_density(total, args.sheet, rows, args.ep, args.season)

    if args.to_findings and ok:
        findings = []
        for it, _ in ok:
            r = rows[it['ln']]
            findings.append({
                'ln': it['ln'], 'tc': r['start'], 'cn': r['cn'], 'en': r['en'],
                'cn_new': (it.get('suggest') or '').strip() or r['cn'],
                'type': it.get('problem_type', ''), 'severity': it.get('severity', 'minor'),
                'confidence': float(it.get('confidence', 0)),
                'dimension': it.get('dimension', ''),
                'problem_type': it.get('problem_type', ''),
                'source_model': data.get('model', ''),
                'evidence': (it.get('evidence') or '')[:200],
            })
        out = Path(args.to_findings)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {'season': args.season[1:], 'episode': args.ep,
                   'findings': findings,
                   'pending': [], 'inserts': []}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n',
                       encoding='utf-8', newline='\n')
        print(f'\n已写 findings：{out}（{len(findings)} 条）')
    elif args.to_findings:
        print('无合规条目，未写 findings')


if __name__ == '__main__':
    main()
