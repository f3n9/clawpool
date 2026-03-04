#!/usr/bin/env python3
import os
import time


def should_stop(last_active_ts, idle_minutes, now_ts=None):
    now = now_ts or int(time.time())
    return (now - int(last_active_ts)) > int(idle_minutes) * 60


def main():
    idle_minutes = int(os.getenv("OPENCLAW_IDLE_MINUTES", "30"))
    print(f"idle-controller running with threshold={idle_minutes}m")


if __name__ == "__main__":
    main()
