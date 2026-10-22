#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主题潜力股监控脚本（人形机器人 + 低空经济）
集成你的《股票操作手册》框架。

用法：
  python3 theme_watch.py          # 打印推荐代码清单 + 建议命令
  python3 theme_watch.py --score  # 直接调用 score_stocks.py 打分所有重点标的

它会输出：
- 按主题分组的准确代码
- 一键复制的 score 命令
- 当前你系统下的观察重点
"""

import subprocess
import sys
import os

# === 准确的主题潜力代码（基于缓存验证 + 最新催化剂） ===
# 人形机器人（执行器、传动、电机、视觉、本体/产线等核心）
ROBOT_CORE = [
    ("688017", "绿的谐波"),   # 谐波减速器龙头，特斯拉链
    ("688028", "沃尔德"),     # 精密工具，强势候选（你的系统打分高）
    ("300607", "拓斯达"),     # 本体+产线，加分7/7
    ("002050", "三花智控"),   # 执行器+热管理
    ("300718", "长盛轴承"),   # 轴承+丝杠
    ("300124", "汇川技术"),   # 伺服平台
    ("002747", "埃斯顿"),     # 工业+人形
    ("603728", "鸣志电器"),   # 灵巧手电机
    ("688322", "奥比中光"),   # 3D视觉
    ("300953", "震裕科技"),   # 丝杠
    ("300100", "双林股份"),   # 丝杠
    ("002031", "巨轮智能"),   # RV减速器
]

# 低空经济（整机、动力、电推进、应用）
LOW_ALTITUDE = [
    ("002085", "万丰奥威"),   # 整机+通航+eVTOL
    ("300681", "英搏尔"),     # 电推进
    ("688070", "纵横股份"),   # 无人机+治理系统
]

# AI + 半导体设备（新增）
AI_SEMI = [
    ("002371", "北方华创"),   # 半导体设备平台龙头
    ("688012", "中微公司"),   # 刻蚀设备
    ("688072", "拓荆科技"),   # 薄膜沉积
    ("688256", "寒武纪"),     # AI芯片
    ("688981", "中芯国际"),   # 晶圆制造
    ("300496", "中科创达"),   # AI边缘/智能
]

# 新增主题: 消费/军工/新材料 (主板)
CONSUME_MILITARY_NEW = [
    ("600519", "贵州茅台"),   # 消费-高端白酒
    ("600887", "伊利股份"),   # 消费-乳制品
    ("600600", "青岛啤酒"),   # 消费-啤酒
    ("600150", "中国船舶"),   # 军工-船舶
    ("600316", "洪都航空"),   # 军工-航空
    ("600516", "方大炭素"),   # 新材料-碳素
    ("600549", "厦门钨业"),   # 新材料-钨业
]

ALL_THEME = ROBOT_CORE + LOW_ALTITUDE + AI_SEMI + CONSUME_MILITARY_NEW

def print_watchlist():
    print("=" * 60)
    print("2026 主题潜力股监控清单（人形机器人 + 低空经济 + AI/半导体设备）")
    print("=" * 60)
    print("\n【人形机器人核心】")
    for code, name in ROBOT_CORE:
        print(f"  {code}  {name}")

    print("\n【低空经济】")
    for code, name in LOW_ALTITUDE:
        print(f"  {code}  {name}")

    print("\n【AI + 半导体设备】（新增）")
    for code, name in AI_SEMI:
        print(f"  {code}  {name}")

    print("\n【消费/军工/新材料】（新增，主板）")
    for code, name in CONSUME_MILITARY_NEW:
        print(f"  {code}  {name}")

    codes = [c for c, _ in ALL_THEME]
    print("\n" + "-" * 60)
    print("一键打分命令（直接复制运行）：")
    print(f"python3 score_stocks.py {' '.join(codes)}")
    print("-" * 60)

    print("\n推荐重点观察（结合你的手册 + 当前打分）：")
    print("  1. 沃尔德 (688028)  — 你的系统 ★ 强势候选，加分6/7")
    print("  2. 拓斯达 (300607)  — 加分7/7，仅差站稳20日线")
    print("  3. 北方华创 (002371) — 半导体设备平台龙头，AI扩产核心")
    print("  4. 中微公司 (688012) — 刻蚀设备，技术壁垒高")
    print("  5. 三花智控 (002050) — 估值相对友好，执行器逻辑强")
    print("  6. 万丰奥威 (002085) — 低空估值最安全之一")
    print("  7. 绿的谐波 (688017) — 催化剂最强，但估值需等回调")

    print("\n使用建议（严格按《股票操作手册》）：")
    print("- 先跑上面命令看最新打分")
    print("- 只在通过一票否决（尤其是站上20日线 + 估值合理）时重点评估")
    print("- 大盘绿灯 + 相对强度强 + 加分≥6 再考虑参与")
    print("- 持续跟踪半年报、订单、特斯拉/宇树进展")

def run_score():
    codes = [c for c, _ in ALL_THEME]
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "score_stocks.py")] + codes
    print("正在调用 score_stocks.py 打分所有主题潜力股...\n")
    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"调用失败: {e}")
        print("请手动运行：")
        print(" ".join(cmd))

def print_woerde_checklist():
    print("\n" + "=" * 60)
    print("沃尔德 (688028) 手册自检卡（快速复核版） - 当前最接近的标的")
    print("=" * 60)
    print("""
第一关 一票否决（必须全过，你的打分器已通过大部分）：
[ ] 趋势向上：站上MA250 + 年线上翘（当前已过，134 > 73.72）
[ ] 不在下跌通道（当前已过，仍站MA20 +5.2%）
[ ] 估值不透支（PB 8.76，你的打分器已过）
[ ] 无重大利空

第二关 加分项（当前6/7，目标≥6）：
[ ] 均线多头排列 (MA5>MA10>MA20>MA60)   ← 当前最缺这一条 (MA5略低于MA10)
[ ] 强于大盘 (20日跑赢指数)   ✓
[ ] 相对强度分位≥70%   ✓
[ ] 换手健康   ✓
[ ] 量能温和 (1<VR<3)   ✓ (当前~1.13)
[ ] 60日涨幅 0~50%   ✓ (+34%)
[ ] 不超买 (距MA20 <15%)   ✓

买点确认（严格按手册）：
[ ] 放量突破平台/年线
[ ] 缩量回踩不破
[ ] 叠加催化剂（订单/半年报/机器人大会消息）
""")
    print("建议：把上面 checklist 复制到笔记，每周更新一次状态。")

if __name__ == "__main__":
    if "--score" in sys.argv or "-s" in sys.argv:
        run_score()
    elif "--checklist" in sys.argv or "-c" in sys.argv:
        print_watchlist()
        print_woerde_checklist()
    else:
        print_watchlist()
        print("\n额外：加 --checklist 或 -c 可打印沃尔德详细自检卡")
        print("提示：加 --score 或 -s 参数可直接打分全部标的")
