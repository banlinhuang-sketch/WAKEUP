"""项目启动入口。"""

from voice_wakeup_tester.cli import main


if __name__ == "__main__":
    # 统一从 CLI 主入口启动，GUI 与 headless 都走同一条分发路径。
    raise SystemExit(main())
