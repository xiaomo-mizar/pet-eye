# -*- coding: utf-8 -*-
"""PyInstaller 打包入口：只做一件事——把 GUI 跑起来。"""

import sys

from pet_eye.app import main

if __name__ == "__main__":
    sys.exit(main())
