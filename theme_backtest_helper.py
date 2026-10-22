#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主题接近股 回测辅助脚本
自动从 screen_market.py --theme 输出中提取 BACKTEST_CODES，
或硬编码当前接近的主题股代码，方便喂给你的 niugu_backtest.py 系列验证历史表现。

用法：
  python3 theme_backtest_helper.py
  # 输出当前接近的代码 + 建议的回测命令

然后你可以：
  python3 niugu_backtest.py sz300607 sh688028 ...   （根据你的回测脚本实际参数调整）
"""

# 当前从 screen_market.py --theme 得到的接近主题股（可定期更新）
# 优先级：加分高 + 主题相关
CLOSE_THEME_CODES = [
    "sz002371",  # 北方华创 - 半导体设备平台 (AI扩产核心)
    "sz002747",  # 埃斯顿 - 人形-工业人形
    "sh600150",  # 中国船舶 - 军工-船舶制造 (中性观察)
    "sh603728",  # 鸣志电器 - 人形-灵巧手
    "sh600549",  # 厦门钨业 - 新材料-钨业
    "sz002050",  # 三花智控 - 人形-执行器
    "sz002031",  # 巨轮智能 - 人形-RV减速器
    "sh600316",  # 洪都航空 - 军工-航空航天
]

def main():
    print("当前接近通过的主题股代码（按加分排序）：")
    print(" ".join(CLOSE_THEME_CODES))
    print()
    print("建议回测命令（根据你的 niugu_backtest*.py 调整参数）：")
    print(f"python3 niugu_backtest.py {' '.join(CLOSE_THEME_CODES)}")
    print()
    print("或者先跑全市场基准对比这些代码的历史表现。")
    print("提示：定期运行 `python3 screen_market.py --theme` 更新列表。")
    print("回测重点观察：机器人/低空政策周期（如2019-2021新能源、2023AI等类似主线）中的胜率和超额收益。")

if __name__ == "__main__":
    main()