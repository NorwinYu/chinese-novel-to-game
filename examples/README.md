# 示例文本

`demo-novel.txt` 是为本仓库自写的 5 章短文，按 **CC0** 释放，用来在不接触任何受版权保护作品的情况下跑通 ⓪①（书型诊断、清洗切章），也可以作为最小样本试抽取。

```bash
python -m n2g init examples/demo-novel.txt --slug demo --title 示例
python -m n2g ingest --slug demo
python -m n2g profile --slug demo
```

产物写在 `games/demo/build/`（已被 gitignore）。示例只有两千多字，能验证流程和命令，不足以做出一局完整的游戏；真正的书请按 README 先做切片。

文中的人物、地点均为虚构：张三（扛货的年轻人）、李四（消息灵通的闲人）、王五（木匠）、赵掌柜（杂货铺老板）、青石城。
