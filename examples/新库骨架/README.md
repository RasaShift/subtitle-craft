# {{库名}}

> 一句话：这个库干什么、给谁用。

## 是什么

待填：两三句话说明白，别写成宣传。

## 怎么用

```bash
# 文档体检（提交时会自动跑一次）
python tools/doc_lint.py
# 文档超长，拆成两份
python tools/doc_split.py <文件> --dry-run
# 换行符守卫（整篇被换掉时报警，不报「文件内混用」）
python tools/eol_guard.py --check <文件或目录>
```

## 目录

| 路径 | 放什么 |
|---|---|
| `docs/` | 结论与判据，先查 `docs/文档落点表.md` 定类再写 |
| `docs/archive/` | 过期但还要查的整卷 |
| `tools/` | 脚本。只用标准库，路径从 `__file__` 推算 |
| `.workbuddy/memory/` | 当天工作日志，追加不覆盖 |

## 约定

- 文档写完跑一次 `python tools/doc_lint.py`，超上限就搬内容，不压字数。
- 改文件跟随它原本的换行符（`.gitattributes` 锁 LF）。
- 脚本头部写 `# 最近修改：<日期>（改了什么）`，不写作者。

## 换机器后要做一次

```bash
git config core.hooksPath .githooks
```

钩子是本机配置，不进版本库；不设这一条，提交前的两道守卫等于不存在。
