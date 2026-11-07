# 最近修改：未记录 2026-11-01（2026-11-01 补署名；此前无此行，原改动内容无据可查）
# -*- coding: utf-8 -*-
"""ASS 中文节奏统计（只出数，不判红）。

为什么要有（2026-11-01，#131）：
    现有 subtitle_qc.py 的五项检查（CPS / 行长 / 最短停留 / 最长停留 / 间隔）
    是给 **SRT** 的，本项目的成品全是 ASS，一项都没覆盖。

    但**不要先定门槛**。Netflix 的 9 CPS 是给现代规范字幕定的，本项目的源是
    人人影视老盘，时间轴紧凑是常态。全库跑下来超线的可能是几百条，也可能是
    几千条——没数之前定阈值，会重演 #133 那次（闸值 30% 定在实测天花板 50%
    下面，每集都判失效，流程直接卡死）。

    所以这个脚本只回答三个问题：
      1. 全库中文 CPS 的分布长什么样（分位数，不是均值）
      2. 超 9 CPS 有几条、集中在哪几集
      3. 其他四项（行长/最短/最长/间隔）各有多少条

    拿到数再决定它是「统计指标」还是「逐条清单」。

口径：
    只统计对白样式（默认 Default），跳过 Mark（片头/声明）与 English（英文轨）。
    字数按**非空白字符**计，ASS 花括号里的标签先剥掉，\\N 只用于分行不计字数。
    CPS = 字数 ÷（结束 − 开始）。

用法：
    python ass_pacing.py                       # 全库汇总
    python ass_pacing.py --md OUT.md           # 另出 markdown
    python ass_pacing.py --tsv OUT.tsv         # 逐集明细
    python ass_pacing.py --file X.ass --worst 20   # 单集最差的 N 行
"""

from __future__ import annotations

# 作者：DeepSeek-V4.1-Flash 2026-11-01
# 最近修改：DeepSeek-V4.1-Flash 2026-11-01（新建）

import argparse
import pathlib
import re
import sys

# 根目录跟随**当前工作目录**（在哪个剧库里跑，就是那个剧库），不跟着脚本位置走——
# 脚本住在通用库 tools/ 下，跟着它走会指到通用库里根本不存在的 03_成品。
ROOT = pathlib.Path.cwd()
OUT_DIR = ROOT / "03_成品"      # 可被 --dir 覆盖

# 阈值：CJK 预设（与 subtitle_qc.py 的 PRESETS['cjk'] 一致）
CPS_LIMIT = 9.0          # 净片字幕惯例
LINE_LIMIT = 16          # 单行汉字数
MIN_DUR = 0.833          # 5 帧 @30fps，Netflix 下限
MAX_DUR = 7.0
GAP_MIN = 0.080          # 80ms

TAG = re.compile(r"\{[^}]*\}")
CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                 r"\uff00-\uffef\u3040-\u30ff]")
DRAW = re.compile(r"\\p[1-9]")


def ts(s: str) -> float:
    """ASS 时间码 H:MM:SS.cc -> 秒。"""
    h, m, rest = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse(path: pathlib.Path) -> list[dict]:
    """解析 ASS 的 Dialogue 行，返回 [{ln, start, end, dur, style, text, lines, chars, cps}]。"""
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    cues = []
    in_events = False
    for i, line in enumerate(raw.splitlines(), 1):
        if line.startswith("["):
            in_events = line.strip().lower() == "[events]"
            continue
        if not in_events or not line.startswith(("Dialogue:", "Comment:")):
            continue

        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        _, start, end, style = parts[0], parts[1], parts[2], parts[3]
        body = parts[9]

        if DRAW.search(body):
            continue                                    # 矢量绘图，不算字数

        # 本项目的中英双轨在同一条 Dialogue 里：中文 \N{\rEnglish}英文。
        # 统计中文节奏必须在英文段之前截断，否则字数翻倍、CPS 全错。
        cut = re.search(r"\{[^}]*\\r[A-Za-z]", body)
        if cut:
            body = body[:cut.start()]

        text = TAG.sub("", body)
        text = text.replace("\\h", " ")
        visual = [t.strip() for t in re.split(r"\\N", text, flags=re.I)]
        visible = "".join(visual).replace(" ", "").replace("\u3000", "")
        if not visible:
            continue

        try:
            t0, t1 = ts(start), ts(end)
        except ValueError:
            continue
        dur = round(t1 - t0, 3)
        if dur <= 0:
            continue

        cues.append({
            "ln": i, "start": t0, "end": t1, "dur": dur, "style": style,
            "text": " / ".join(visual),
            "lines": len([v for v in visual if v]),
            "chars": len(visible),
            "cjk": len(CJK.findall(visible)),
            "cps": len(visible) / dur,
            "maxline": max((len(v.replace(" ", "")) for v in visual), default=0),
        })

    cues.sort(key=lambda c: (c["start"], c["ln"]))
    for a, b in zip(cues, cues[1:]):
        a["gap"] = round(b["start"] - a["end"], 3)
    if cues:
        cues[-1]["gap"] = None
    return cues


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))
    return s[k]


def stats(path: pathlib.Path, style: str = "Default") -> dict:
    cues = [c for c in parse(path) if c["style"] == style]
    if not cues:
        return {"file": path, "cues": 0}

    cps = [c["cps"] for c in cues]
    gaps = [c["gap"] for c in cues if c["gap"] is not None]
    return {
        "file": path,
        "cues": len(cues),
        "chars": sum(c["chars"] for c in cues),
        "cps_p50": pct(cps, 50), "cps_p90": pct(cps, 90),
        "cps_p95": pct(cps, 95), "cps_p99": pct(cps, 99),
        "cps_max": max(cps),
        "over9": sum(1 for v in cps if v > CPS_LIMIT),
        "over12": sum(1 for v in cps if v > 12),
        "over15": sum(1 for v in cps if v > 15),
        "long16": sum(1 for c in cues if c["maxline"] > LINE_LIMIT),
        "long20": sum(1 for c in cues if c["maxline"] > 20),
        "short": sum(1 for c in cues if c["dur"] < MIN_DUR),
        "long_cue": sum(1 for c in cues if c["dur"] > MAX_DUR),
        "tight": sum(1 for g in gaps if 0 <= g < GAP_MIN),
        "overlap": sum(1 for g in gaps if g < 0),
    }


def fmt_row(s: dict) -> str:
    name = s["file"].stem.replace(".1080p.BluRay.x265-RARBG", "")
    return (f"{name}\t{s['cues']}\t{s['cps_p50']:.1f}\t{s['cps_p90']:.1f}\t"
            f"{s['cps_p95']:.1f}\t{s['cps_p99']:.1f}\t{s['cps_max']:.1f}\t"
            f"{s['over9']}\t{s['over12']}\t{s['over15']}\t{s['long16']}\t"
            f"{s['long20']}\t{s['short']}\t{s['long_cue']}\t{s['tight']}\t{s['overlap']}")


HEADER = ("文件\t对白行\tCPS中位\tp90\tp95\tp99\t最高\t超9\t超12\t超15\t"
          "行>16字\t行>20字\t短于0.833s\t长于7s\t间隔<80ms\t重叠")


def main() -> int:
    ap = argparse.ArgumentParser(description="ASS 中文节奏统计（只出数）")
    ap.add_argument("--dir", default="", help="成品目录，缺省 ./03_成品（相对当前工作目录）")
    ap.add_argument("--file", default="", help="只统计单个文件")
    ap.add_argument("--style", default="Default")
    ap.add_argument("--md", default="")
    ap.add_argument("--tsv", default="")
    ap.add_argument("--worst", type=int, default=0, help="配合 --file，列出最差的 N 行")
    ap.add_argument("--min-cues", type=int, default=20,
                    help="对白行数少于此值的文件跳过（预告片、样片）")
    args = ap.parse_args()

    if args.file:
        path = pathlib.Path(args.file)
        s = stats(path, args.style)
        if not s.get("cues"):
            print("没有对白行")
            return 0
        print(f"{path.name}: {s['cues']} 行，CPS 中位 {s['cps_p50']:.1f} / "
              f"p90 {s['cps_p90']:.1f} / 最高 {s['cps_max']:.1f}；"
              f"超 9 CPS {s['over9']} 行（{s['over9'] / s['cues']:.1%}）")
        if args.worst:
            cues = [c for c in parse(path) if c["style"] == args.style]
            print(f"\nCPS 最高的 {args.worst} 行：")
            for c in sorted(cues, key=lambda x: -x["cps"])[:args.worst]:
                print(f"  L{c['ln']:<5d} {c['dur']:4.1f}s {c['chars']:3d}字 "
                      f"{c['cps']:5.1f}CPS  {c['text'][:44]}")
            print(f"\n最长的 {args.worst} 行：")
            for c in sorted(cues, key=lambda x: -x["maxline"])[:args.worst]:
                print(f"  L{c['ln']:<5d} {c['maxline']:3d}字/行  {c['text'][:44]}")
        return 0

    root = pathlib.Path(args.dir) if args.dir else OUT_DIR
    files = sorted(root.rglob("*.ass"))
    if not files:
        raise SystemExit(f"没找到 ass：{root}")

    rows = []
    for f in files:
        s = stats(f, args.style)
        if s.get("cues", 0) >= args.min_cues:
            rows.append(s)

    if not rows:
        raise SystemExit("没有达到 --min-cues 的文件")

    tot_cues = sum(r["cues"] for r in rows)
    all_cps = []
    for f in files:
        all_cps += [c["cps"] for c in parse(f) if c["style"] == args.style]

    print(f"文件 {len(rows)} 个（跳过对白<{args.min_cues} 行的），"
          f"对白行合计 {tot_cues:,}")
    print(f"CPS 分位：中位 {pct(all_cps, 50):.1f} / p90 {pct(all_cps, 90):.1f} / "
          f"p95 {pct(all_cps, 95):.1f} / p99 {pct(all_cps, 99):.1f} / "
          f"最高 {max(all_cps):.1f}")
    print()

    def agg(key):
        return sum(r.get(key, 0) for r in rows)

    over = agg("over9")
    print(f"超 {CPS_LIMIT:g} CPS：{over:,} 行 = 全部对白的 {over / tot_cues:.2%}")
    print(f"  超 12：{agg('over12'):,} 行（{agg('over12') / tot_cues:.2%}）")
    print(f"  超 15：{agg('over15'):,} 行（{agg('over15') / tot_cues:.3%}）")
    print(f"行 > {LINE_LIMIT} 字：{agg('long16'):,} 行（{agg('long16') / tot_cues:.2%}）")
    print(f"行 > 20 字：{agg('long20'):,} 行（{agg('long20') / tot_cues:.2%}）")
    print(f"短于 {MIN_DUR}s：{agg('short'):,} 行（{agg('short') / tot_cues:.2%}）")
    print(f"长于 {MAX_DUR:g}s：{agg('long_cue'):,} 行（{agg('long_cue') / tot_cues:.2%}）")
    print(f"间隔 < {int(GAP_MIN * 1000)}ms：{agg('tight'):,} 处")
    print(f"重叠：{agg('overlap'):,} 处")

    worst = sorted(rows, key=lambda r: -(r["over9"] / max(1, r["cues"])))[:8]
    print("\n超线率最高的 8 个文件：")
    for r in worst:
        print(f"  {r['file'].stem[:44]:46} {r['over9']:4d}/{r['cues']:4d} = "
              f"{r['over9'] / r['cues']:5.1%}  p95 {r['cps_p95']:.1f}")

    if args.tsv:
        out = pathlib.Path(args.tsv)
        with out.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(HEADER + "\n")
            for r in sorted(rows, key=lambda x: x["file"].stem):
                fh.write(fmt_row(r) + "\n")
        print(f"\n逐集明细 -> {out}")

    if args.md:
        write_md(rows, all_cps, tot_cues, pathlib.Path(args.md))
    return 0


def write_md(rows: list[dict], cps: list[float], tot: int, out: pathlib.Path) -> None:
    def agg(k):
        return sum(r.get(k, 0) for r in rows)
    L = ["# 中文节奏统计（ASS，只出数不判红）", "",
         f"- 统计范围：{len(rows)} 个成品文件，对白行 {tot:,} 条",
         "- 只统计 `Style: Default`，跳过 Mark / English 轨",
         f"- CPS = 非空白字符 ÷ 时长；阈值按 CJK 惯例 9 CPS / 16 字每行", "",
         "## 分布", "", "| 指标 | 值 |", "|---|---|"]
    for name, v in (("CPS 中位", pct(cps, 50)), ("p90", pct(cps, 90)),
                    ("p95", pct(cps, 95)), ("p99", pct(cps, 99)),
                    ("最高", max(cps))):
        L.append(f"| {name} | {v:.1f} |")
    L += ["", "## 各项超线数", "",
          "| 项 | 条数 | 占对白 |", "|---|---|---|",
          f"| 超 9 CPS | {agg('over9'):,} | {agg('over9') / tot:.2%} |",
          f"| 超 12 CPS | {agg('over12'):,} | {agg('over12') / tot:.2%} |",
          f"| 超 15 CPS | {agg('over15'):,} | {agg('over15') / tot:.3%} |",
          f"| 行 > 16 字 | {agg('long16'):,} | {agg('long16') / tot:.2%} |",
          f"| 行 > 20 字 | {agg('long20'):,} | {agg('long20') / tot:.2%} |",
          f"| 短于 0.833s | {agg('short'):,} | {agg('short') / tot:.2%} |",
          f"| 长于 7s | {agg('long_cue'):,} | {agg('long_cue') / tot:.2%} |",
          f"| 间隔 < 80ms | {agg('tight'):,} | — |",
          f"| 重叠 | {agg('overlap'):,} | — |", "",
          "## 超线率最高的 10 个文件", "",
          "| 文件 | 超 9 CPS | 对白行 | 占比 | p95 |", "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -(r["over9"] / max(1, r["cues"])))[:10]:
        L.append(f"| {r['file'].stem.replace('.1080p.BluRay.x265-RARBG', '')} | "
                 f"{r['over9']} | {r['cues']} | {r['over9'] / r['cues']:.1%} | {r['cps_p95']:.1f} |")
    L.append("")
    out.write_text("\n".join(L), encoding="utf-8", newline="\n")
    print(f"报告 -> {out}")


if __name__ == "__main__":
    sys.exit(main())
