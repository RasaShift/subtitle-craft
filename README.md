# 字幕精校通用库

用 AI 做长周期字幕精校的通用工具与方法。**不是字幕校对软件**，是一套「一个人带 AI 施工队」干活用的件。

来源：《嗜血法医》116 集、《是，大臣》/《是，首相》38 集的实战。剧集数据、术语表、成品一律不在这里。

**目录名一律用英文**（为了别人 clone 下来能看懂）：`tools/` = 工具（脚本真身 + 自检与环境体检）、`docs/` = 文档（方法正文 + 台账 + 判定记录 + 落点表，旧档在 `docs/archive/`）、`examples/` = 示例（新剧怎么接进来、样式预设、可复制的新库骨架）、`skills/` = 技能（**可选**，不装也能跑全部工具。**要装就把 `skills/` 下的每个目录整个复制**到你的技能目录 `<用户主目录>/.workbuddy/skills/`，重启会话生效——`agent-craft` 是方法论，四份 `subtitle-*` 是清稿 / 对稿 / 审稿 / 验收四道工序的入口）。

---

## English

A method + toolkit for long-form subtitle proof-reading with AI agents, built for **one person running an AI crew** across dozens of episodes. Not a subtitle editor.

**25 self-contained tools** (probe · bilingual alignment · diff triage · landing & verify · style normalization · acceptance), four documented workflows (cleanup · alignment · review · acceptance), four AI skill entry points, and `scaffold.py` to stamp out a new project skeleton with a self-check. Four more scripts need a project directory layout to run.

Built from real runs on two series (116 + 38 episodes). **No episode data, term lists or finished subtitles are shipped** — those stay in private project repos. Python 3 standard library only. License: MIT.

---

## 动手前必读

**新会话先看两处**：接手这个库接着开发，先读 `docs/待办台账.md` 开头的「现在到哪」（**状态只在那里**），再按 `docs/文档落点表.md` 找内容放哪；拿它接**你自己**的剧，直接读 `examples/新剧接入.md`（从零到能判，十二步）。

**开工前先答八个问题**：`docs/接活单.md`——来源 / 校过没 / 手上有什么 / 给谁看 / 要多准 / 不能动的 / 译名表 / 交付形态。**八问是剧级属性，一部剧问一次**，答案落剧级一份，集级只记一行档位。不答就定档，等于替用户猜档位。

**版本与改动**：看 `CHANGELOG.md`，每次发版在那儿记新增 / 变更 / 修复。0.x 阶段命令行参数与退出码仍会变，升级前先看对应版本那一节。

**为什么长这样**（定位给谁用、三层资产怎么分、哪些方案已被否决）写在开发者的上游决策文档里，**不随本仓库发布**——一份内容只有一个真身，这里不复制。本库自己的判断依据全在 `docs/判定记录.md`。

---

## 一份字幕走一遍

```
体检 → 对齐（可选）→ 攒词表（可选）→ 清稿·文件层 → 清稿·文本层
     → 对稿 → 定档 → 审稿（按档）→ 并成一张清单落地 → 验收
```

步骤之间「谁产出、谁吃」、每条硬顺序的理由、**现在断在哪**，看 `docs/流程总图.md`；每步怎么判看 `docs/工序-*.md`；名字怎么起看 `docs/命名准则.md`。

---

## 现在有什么

| 件 | 状态 | 干什么 |
|---|---|---|
| `tools/subtitle_probe.py` | 可用 | 字幕体检：SRT/ASS 双格式、UTF-8/UTF-16/BOM 自动嗅探、CPS/行长/停留、三类事故（`<font>` 残留、金额问号、花括号残留）；粘连词单列「嫌疑线索」，不参与合格判定 |
| `tools/align_bilingual.py` | 可用（三条判据全过，9 集已跑） | 双版本对齐：中文当骨架、英文挂 0/1 条，按时间轴配（不看文本相似度），输出配对表与未配对清单 |
| `tools/triage_diff.py` | 可用（一部剧 538 条挑出 2 条候选） | 对稿：把双版本差异分「漏译 / 误译 / 表述差异 / 无法判定」，**出候选不给定案** |
| `tools/bilingual_pairs.py` | 可用 | 双语字幕直出配对表：手上只有一份双语 ASS/SRT 就能进对稿，不必先凑两条轨 |
| `tools/text_scan.py` | 可用（同一部剧 538 条挑出 129 条、英文段 0 改动） | 清稿·文本层扫描：译名错写 / 标签残留 / 半角标点贴字 / 中英间距 / 花括号残留，**只做唯一改法**；标签带参数、中文有折行的一律挡进「需人判」表，输出直接喂 `land_verify` |
| `tools/land_verify.py` | 可用（补行 2026-11-06 已落） | 落地：按「条号 + 旧文本」双锚点写回，再逐条复算——证明只改了该改的，没多改。**补行**另给一张表，插在哪由时间算、`after_no` 只核对 |
| `tools/style_normalize.py` | 可用（v1：做 `MF1`–`MF6`、`MF8` 与引用断言；`MF6`/`MF8` 要显式 `--f6`/`--f8`） | 文件层样式规范化：头部与样式表换基准，坐标/字号按画布比例换算；`0×0` 与旧样式改名都要显式声明，不许猜。样式三套可选（含字号、颜色、描边、边距），见 `examples/样式预设/`（带样张）；`base` 子命令按片源比例从它换算出别的比例（4:3 老剧用） |
| `tools/term_candidates.py` | 可用 | 术语/专名候选提取：**只挑候选不给译法**。屏幕打前 40 条（候选 / 次数 / 首现 / 型 / 中文片段）；`--tsv` 写出的底稿是 7 列，后三列 `规定译法 / 已见错写法 / 备注` 留空交人填 |
| `tools/findings2tsv.py` | 可用 | findings JSON → 改动表：`ln/cn/cn_new/source_model/evidence` 纯映射，不做合并、不做判断。补上「审稿产出到落地之间」那段断链——原先靠人对照合并，`evidence` 必然整段丢 |
| `tools/accept.py` | 可用 | 验收：一条命令报出该对的全部数字（条数 / 对白行 / 非对白行变化 / BOM / 改动 / 补行位置 / 落盘密度 / 定档痕迹），有 ✗ 退出码 1 |
| `tools/build_base.py` | 可用 | 底本合成：两份单语 SRT → 一份双语 ASS（配对调对齐器、样式可抄现成成品，画布按轴换到 `--video`）。起点没有 ASS 的剧用它 |

**一共 30 件工具，其中 25 件能独立跑**：上表 11 件主用，另有 7 件工程件、12 件审稿辅助；**5 件（`blind_sample` / `isolate_review` / `prefilter_semantic` / `validate_semantic` / `gen_progress`）要剧库目录结构才跑得动**，对外按 25 件算。四道工序里**三道必经（清稿 · 对稿 · 验收）、审稿可选**、文档骨架可复制（`tools/scaffold.py` + `examples/新库骨架/`，建完当场自检）。
**还差的**是**换一部剧**走完整链：已有两集在《嗜血法医》上跑完 0–8 步，其中一集（2026-11-08）连**定档 · 审稿 · 验收**三道人判工序也走通了（只落 C1 一层，不是全量）。另一部剧（《是，大臣》）的进度**不写在这里**——写死的状态三十天就过期，去它自己库的进度台账看。**整链目前只在一部剧上走通过**，工具也只有三件在两部剧上跑过真数据（见下「已知不足」）——你接新剧，仍会是第一个在陌生素材形态上走全程的人。

## 用法

**命令都在本库根执行**，参数里写的目录/文件是你的剧库路径——工具和剧库是两个根，不 `cd` 进剧库，剧库里也不用放 `tools/`：

```bash
python tools/subtitle_probe.py <目录或文件...> --tsv <输出.tsv> [--strict]
python tools/align_bilingual.py <中文.srt> <英文.srt> --tsv <配对表.tsv>
python tools/align_bilingual.py <目录> --zh-tag .简 --en-tag .英 --tsv <输出目录>
python tools/triage_diff.py <配对表.tsv> [--term 词表.tsv] [--offset 错位区间.tsv] --tsv <候选.tsv>
python tools/bilingual_pairs.py <双语字幕.ass|.srt> --tsv <配对表.tsv>
python tools/text_scan.py <字幕.ass> [--term 词表.tsv] [--tsv 改动.tsv] [--report 需人判.tsv]
python tools/land_verify.py apply  <成品.ass|.srt> [改动.tsv] [--inserts 补行.tsv] [-o 输出] [--in-place]
python tools/land_verify.py verify <改前> <改后> [改动.tsv] [--inserts 补行.tsv]
python tools/style_normalize.py check  <字幕.ass...> [--video 1920x1080] [--base 基准.ass]
python tools/style_normalize.py apply  <字幕.ass> --base 基准.ass [-o 输出] [--from-res WxH] [--style-map 旧名=新名] [--f6]
python tools/style_normalize.py base   <源基准.ass> --video 1440x1080 -o <新基准.ass>   # 4:3 剧用
python tools/term_candidates.py <字幕或目录> [--min 2] [--tsv 输出.tsv]
python tools/accept.py <改前> <改后> [--changes 改动.tsv] [--inserts 补行.tsv] [--stage 定档.md]
python tools/build_base.py <中文.srt> <英文.srt> -o <底本.ass> [--style-from 参照.ass] [--video 1920x1080]

python tools/selftest.py      # 全部工具的函数级回归，末尾报「通过 N，失败 0」，改完代码先跑这个
python tools/doc_lint.py      # 文档体检；想让它提交时自动跑，先 git config core.hooksPath .githooks
python tools/env_check.py     # 换机器/隔很久回来，先跑这个
python tools/scaffold.py <目录>   # 把文档骨架展开成一个新库，建完当场跑一次体检
```

参数里没有剧名。编码自动嗅探，不用先转码。

## 约定

- **参数里不出现剧名、季号、集号规则**——出现就说明抽错了
- **一份内容只有一个真身**：这里是工具真身，各剧库只存自己的数据
- 脚本只用标准库（不含任何第三方依赖）

进度看 `docs/待办台账.md`，为什么这么定看 `docs/判定记录.md`，内容放哪看 `docs/文档落点表.md`。

## 已知不足

- `subtitle_probe.py` 的「疑似粘连词」靠长度阈值，只作线索不作判据——准确做法要拿第二版英文交叉比对，那属于对齐器
- `align_bilingual.py` 只按时间轴配，两版时间轴若整体平移/变速（片头长度不同、帧率不一致）会大面积配错；配错时靠「文本长度相关」这条自检兜（同集 0.76 / 错集 -0.02），但不自动校准
- **补行只做「插一条新行」，句群整段重排还没做**：单句漏译现在能落（`--inserts`），但整段错位要重排多行、SRT 补行还要重排序号，这两类仍交不出去
- **并表仍靠人**：单份清单能先转成改动表（`findings2tsv`），但「对稿定案 + 审稿清单」并成一张、并表前后条数对不对得上，本库没有工具（`all.tsv` 的列见 `docs/中间产物格式.md`）
- 判翻译对错靠判据不靠工具：审稿那道**机判项为零**，全靠人／模型按 `docs/工序-审稿.md` 的十四条读
- **「行内多话轮」这条（审稿 `F7`）依赖原文带话轮标记**：英文一行有多个 `- `、中文没跟上，才查得出来；整份不带 `- ` 的老剧，这条降级成「只在话轮丢失导致歧义时报」，不逐条补齐
- **对稿 → 定档 → 审稿这一段没有机器闸**：`定档.md` 是手写的，工具证实不了「审稿真读过定档」，只能靠人盯——全库唯一卡不住的一处（审稿本身是可选工序，默认不审，见 `docs/工序-审稿.md`）
- **只有三件工具在两部剧的真实数据上跑过**（文件层样式器 / 文本层扫描器 / 落地），其余只有一部剧的数；接新剧别把「跑通过」当成「已通用」
- 流程层还有哪些断点、哪一步会悄悄过期，**不在这里复制**——真身在 `docs/流程总图.md` 第 3 节
