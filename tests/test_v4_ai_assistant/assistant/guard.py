"""
assistant/guard.py
입력 검증(LLM 호출 전) · 출력 검증(사용자에게 보여주기 전)

★ 두 함수 모두 LLM 을 호출하지 않는다. 여기서 막으면 호출 0회 · 비용 0원이다.

★ 코드잇 스프린트 국비지원 학원 파트 4 고급 팀 프로젝트(validation/patterns.py) 프롬프트 인젝션 패턴을 참고하되 도메인에 맞게 좁혔다
  광고 도메인 패턴을 그대로 쓰면 기술지원 질문이 오탐된다.
    · "명령 무시"  → AutoCAD 에는 '명령'(명령어)이 있다. "캐드 명령이 무시돼요"는 정상 문의다.
    · "설정 알려"  → "라이선스 설정 알려주세요"는 정상 문의다.
    · "규칙 무시"  → "C드라이브 규칙 무시하고 D에 깔아도 돼요?"는 정상 문의다.
  → 지시·프롬프트 앞에 '이전/위의/모든' 같은 한정어가 있을 때만 잡는다.
    골든 데이터셋 over_block_guard 문항으로 과잉 차단을 계속 측정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import config as C
from .text import normalize, squeeze

# region 입력 검증

INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("지시_무시", re.compile(
        r"(이전|위의|앞의|모든|기존|지금까지)(의)?(지시|지침|프롬프트|instruction)s?(를|을|은|는)?(모두|전부)?(무시|잊어|삭제|해제)"
        r"|ignore(all|the)?(previous|above|prior)instructions?|disregard(all|previous|above)")),
    ("프롬프트_탈취", re.compile(
        r"(시스템|system)(프롬프트|prompt|메시지|message|지침)(을|를)?(알려|보여|출력|공개|말해|reveal|print|show)"
        r"|(프롬프트|prompt)(를|을)?(그대로)?(출력|공개|보여|reveal|print)"
        r"|revealyour(system)?prompt|whatareyourinstructions")),
    ("역할_재정의", re.compile(
        r"(너는|당신은|youare)(이제|now)(부터)?.{0,12}(아니|아냐|not|이다|입니다|야)"
        r"|(개발자|디버그|관리자|admin|developer|god)모드|dan모드|무제한모드|actas(dan|jailbreak)")),
    ("제약_해제", re.compile(
        r"(필터|검열|안전장치|가드레일|guardrail|filter)(없이|해제|끄고|off|bypass)"
        r"|jailbreak|withoutanyrestriction|nolimits")),
    ("구분자_위조", re.compile(
        r"<\|im_(start|end)\|>|\[/?(system|assistant|inst)\]|###system|<<sys>>|```system")),
)

_MSG_INJECTION = "요청을 처리할 수 없습니다. Autodesk 제품 설치와 관련된 내용으로 다시 질문해 주세요."
_MSG_EMPTY = "궁금하신 내용을 한 문장으로 알려주세요. (예: AutoCAD 2026 설치 방법)"
_MSG_TOO_LONG = f"질문이 너무 깁니다. {C.MAX_INPUT_CHARS}자 이내로 핵심만 정리해 주세요."


@dataclass(frozen=True)
class InputCheck:
    action: str     # "pass" | "invalid" | "blocked"
    message: str = ""
    rule: str = ""


def check_input(text: str) -> InputCheck:
    """사용자 질문 검증 — 형식 오류와 프롬프트 인젝션을 막는다."""
    stripped = (text or "").strip()
    if len(stripped) < C.MIN_INPUT_CHARS:
        return InputCheck("invalid", _MSG_EMPTY, "too_short")
    if len(stripped) > C.MAX_INPUT_CHARS:
        return InputCheck("invalid", _MSG_TOO_LONG, "too_long")
    # 자모·기호만 있는 입력("ㅋㅋㅋ", "???")
    if not any(ch.isalnum() and not ("\u3130" <= ch <= "\u318f") for ch in stripped):
        return InputCheck("invalid", _MSG_EMPTY, "no_content")

    # ★ 원문과 압축본 양쪽을 본다.
    #   압축본만 보면 "<|im_start|>" 의 | 가 지워져 놓치고, 원문만 보면 "이 전 지 시 무 시"를 놓친다.
    low = normalize(stripped).replace(" ", "")
    tight = squeeze(stripped)
    for name, pat in INJECTION_PATTERNS:
        if pat.search(tight) or pat.search(low):
            return InputCheck("blocked", _MSG_INJECTION, name)
    return InputCheck("pass")


# endregion 입력 검증

# region 출력 검증

_URL = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|kr|co|xyz|info|biz)\b", re.IGNORECASE)
_PHONE = re.compile(r"\b0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}\b")
_LEAK = re.compile(
    r"\b(sk|rk)-[A-Za-z0-9_\-]{16,}\b|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|반드시 지킬 규칙|\[\[HANDOFF\]\]를? ?출력",
)

_MSG_LEAK = "내부 설정 정보가 포함되어 답변을 표시하지 않았습니다. 다시 질문해 주세요."


@dataclass(frozen=True)
class OutputCheck:
    action: str            # "pass" | "fixed" | "blocked"
    text: str
    removed: tuple[str, ...] = ()


def check_output(text: str, allowed_sources: str) -> OutputCheck:
    """답변에 근거 문서에 없는 링크·도메인·전화번호가 있으면 지운다.

    allowed_sources: 이번 답변의 근거 문서 본문 + 상담원 연락처.
      여기에 등장한 주소·번호만 허용한다. 기술지원에서 지어낸 다운로드 링크는
      오답을 넘어 악성 파일 유도로 이어질 수 있어 가장 위험한 환각이다.
    """
    if not text:
        return OutputCheck("pass", text)
    if _LEAK.search(text):
        return OutputCheck("blocked", _MSG_LEAK)

    allowed = normalize(allowed_sources)
    removed: list[str] = []

    def _drop(m: re.Match[str]) -> str:
        token = m.group(0)
        if normalize(token).rstrip(".,") in allowed:
            return token
        removed.append(token)
        return "[확인되지 않은 정보 삭제]"

    fixed = _URL.sub(_drop, text)       # 전체 주소를 먼저 본다
    fixed = _DOMAIN.sub(_drop, fixed)   # "download-autocad.xyz" 처럼 스킴 없이 쓴 도메인
    fixed = _PHONE.sub(_drop, fixed)
    if removed:
        return OutputCheck("fixed", fixed, tuple(removed))
    return OutputCheck("pass", text)


# endregion 출력 검증
