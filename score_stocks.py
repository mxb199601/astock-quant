#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股自检打分器（兼容入口）。推荐: python cli.py score 600519 ..."""
import sys
from cli import main

if __name__ == "__main__":
    sys.exit(main(["score", *sys.argv[1:]]) or 0)
