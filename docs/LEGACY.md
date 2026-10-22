# 遗留 / 旁路脚本

主决策链只认：`cli.py`、`app.py`、`lib/*`。  
下列文件为历史实验或早期入口，**不要当作当前买入算法**。

| 路径 | 说明 | 建议 |
|------|------|------|
| `niugu_backtest.py` | 早期牛股回测 v1 | 仅考古 |
| `niugu_backtest_v3.py` | 牛股实验 v3 | 仅考古 |
| `niugu_backtest_v4.py` | 牛股实验 v4 | 仅考古 |
| `niugu_backtest_v5.py` | 牛股实验 v5 | 仅考古 |
| `screen_market.py` | 旧全市场扫盘 | 用 `cli.py screen` |
| `theme_screen.py` | 旧主题筛选 | 用 `cli.py screen` / 主题池 |
| `theme_watch.py` | 旧主题盯盘 | 用 Web / `cli.py status` |
| `score_stocks.py` | 旧打分入口 | 用 `cli.py score` |
| `validate_history.py` | 历史校验辅助 | 可选 |
| `advanced_theme_backtest.py` | 主题回测辅助 | 用 `cli.py backtest` |
| `theme_backtest_helper.py` | 同上 | 用 `cli.py backtest` |
| `test_formula3.py` | 公式三试验 | 非 pytest 套件 |

若需清理：可整目录迁入 `_legacy/`（需你确认后再动文件，避免误删）。

当前权威算法地图：`docs/算法全景.md`。
