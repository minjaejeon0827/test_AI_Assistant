"""
assistant/console.py
콘솔 출력 안전장치 — Windows 터미널 인코딩 차이로 스크립트가 죽지 않게 한다.

★ 왜 필요한가 (TS-03)
  Windows Git Bash 처럼 Python 출력이 콘솔이 아닌 파이프로 잡히면 표준 출력 인코딩이 cp949 가 된다.
  cp949 에는 이모지(✅ ⚠️ ❌)가 없어 print 한 줄에서 UnicodeEncodeError 로 스크립트가 멈춘다.
  "python scripts/build_kb.py 를 실행하라" 는 조치 자체가 실패하는 셈이다.
  → 인코딩은 그대로 두고(한글은 정상 출력), 표현할 수 없는 문자만 '?' 로 바꾼다.
"""

from __future__ import annotations

import sys


def safe_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):   # 테스트 캡처 객체 등 reconfigure 가 없는 스트림
            pass
