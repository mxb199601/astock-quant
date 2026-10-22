# 周易峰谷 v3 + 波段轨 · 2026-10-13

> 研究笔记，**不构成投资建议 / 非荐股**。只读主板缓存；不改 MES；不进 joint/action。  
> Zoey：「那就再加上波段」— 在峰谷上叠连接 major swings 的波段线（如其图）。

## 1. 定义

| 层 | 含义 | OHLC |
|----|------|------|
| 细 zigzag | 化之迹（swing_k=3） | 既存 `detect_swings`+`build_zigzag` |
| **波段折点** | 大摆动 | 细 zz 上腿波幅 ≥ `band_min_leg_pct`（默认 **8%**）筛 major，顶底交替 |
| **上轨** | 连最近两 major 顶，外推至确认日 | 线性 |
| **下轨** | 连最近两 major 底，外推 | 线性 |
| 峰谷 | 周易 v3 阴穷/阳穷 | 既存 `detect_fenggu_v3_events` |
| 上下文 | 价在轨中位置、轨斜率 | 无前视（只用 conf≤当日的 major） |

## 2. PATH

三谷买 → 三峰前卖（确认日收盘；15bps；5/10/20 + 至峰≤60），按 `band_pos_bucket` / `band_slope` / `band_buy_zone` 切片。对照：无波段门的 v3 ALL。

## 3. 输出

`output/feng3/band/`：events、path、summary、样例图、report。无 HS300。
