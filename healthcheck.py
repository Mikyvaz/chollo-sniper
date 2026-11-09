#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import time

HEARTBEAT_FILE = "/tmp/heartbeat"
MAX_HEARTBEAT_AGE = 300  # 5 minutos

if not os.path.exists(HEARTBEAT_FILE):
    sys.exit(1)

try:
    mtime = os.path.getmtime(HEARTBEAT_FILE)
    age = time.time() - mtime
    if age > MAX_HEARTBEAT_AGE:
        sys.exit(1)
    sys.exit(0)
except Exception:
    sys.exit(1)

