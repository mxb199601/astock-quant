# CLI 路由（本仓）

在 `ROOT` 用 `$PY cli.py …`。参数以 `$PY cli.py <cmd> -h` 为准。

| 命令 | 默认产出 | 备注 |
|------|----------|------|
| `status` | stdout | 红绿灯 + 缓存 + 上次刷新 |
| `live [代码…]` | stdout | 默认沪深300实时 |
| `refresh --all` | 缓存 `cache/kline_sina/` | 用户点名全市场才 `--universe` |
| `action` | `output/action_today.csv` `.txt` | **唯一买入向出口** |
| `recap` | `output/recap_latest.md` | Grok骨架×仓位×游资槽位；票只来自 action |
| `joint` | `output/joint_signals.csv` | 完整通过 ∩ MENGα冷静 |
| `screen --mode robust` | `output/full_pass.csv` 等 | 结构扫描 |
| `score CODE --mode robust` | stdout | 个股自检 |
| `alpha --top 40` | `output/menga_today.csv` | 观察，非买 |
| `cool --top 30` | `output/cool_pool.csv` | 观察池 |
| `patterns` | `output/patterns_today.csv` | 观察 |
| `formulas list\|compare\|scan` | `output/formula_*.csv` | 对照，非主策略 |
| `chan` / `chan --compare` | `output/chan_today.csv` | 否决/观察 |
| `yaogu` | `output/yaogu_prelaunch.csv` | 低精度高召回 |
| `forecast CODE --no-industry` | stdout | 边弱，research_value=low |
| `backtest --mode robust --exit tight --no-plot` | `output/backtest_events.csv` | 默认 OPT 窗；决策勿当 PATH |
| `stress --sample 400` | `output/stress_test.csv` | OPT vs PATH |
| `hist-validate --sample 600` | `output/hist_validate_summary.md` | 慢；用户点名才跑 |
| `up-prob --sample 600` | `output/up_prob_summary.md` | 固定持有上涨概率 |
| `edge` / `--portfolio` / `--equity` | `output/edge_ladder.csv` 等 | PATH 阶梯 |
| `discipline` / `--week` | `output/daily/discipline_*.md` | 系统自检 |
| `holdings check` | `output/holdings.csv` | 先 `init` / `from-action` |
| `daily` | `output/daily/YYYY-MM-DD.md` `latest.md` | 刷新+扫盘+破戒 |
| `report --open` | `output/dashboard.html` | 汇总页 |
| `serve` | http://127.0.0.1:8765 | 与 CLI 同源 |

默认入场 `--mode robust`（反热 + 指数低波）；默认出场决策锚点 **PATH + tight**。关闭低波才加 `--no-low-vol`。
