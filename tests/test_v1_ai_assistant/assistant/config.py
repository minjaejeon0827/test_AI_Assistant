"""
assistant/config.py
튜닝 가능한 값을 전부 이 파일 한 곳에 모은다.

★ 설계 원칙
  · 코드 안에 매직넘버를 두지 않는다. 임계값을 바꿀 때 이 파일만 보면 된다.
  · 모든 값은 환경 변수로 덮어쓸 수 있다. 평가 때만 값을 바꿔 돌리기 위해서다.
  · OPENAI_API_KEY 가 없어도 import 가 깨지지 않는다. (CI · 키 없는 데모 대응)
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """프로젝트 루트 .env 를 프로세스 환경에 올린다. (python-dotenv 의존성 없이)

    ★ os.getenv 는 셸 환경 변수만 본다. .env 에 적어도 아무도 읽지 않으면 기본값이 쓰인다.
      이미 셸에 설정된 값은 덮어쓰지 않는다 — 일회성 실행(평가 스윕 등)이 우선이다.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()


def _f(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: str) -> int:
    return int(os.getenv(name, default))


# region 경로

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

KB_MD = Path(os.getenv("ASSISTANT_KB_MD", DATA_DIR / "install_kb.md"))
KB_JSON = Path(os.getenv("ASSISTANT_KB_JSON", DATA_DIR / "install_kb.json"))
INDEX_DIR = Path(os.getenv("ASSISTANT_INDEX_DIR", DATA_DIR / "kb_index"))
VECTORS_NPY = INDEX_DIR / "vectors.npy"
INDEX_META = INDEX_DIR / "meta.json"

# 사내 배포 링크 실제 주소. links.local.json 은 커밋하지 않는다(.gitignore).
LINKS_LOCAL = Path(os.getenv("ASSISTANT_LINKS", DATA_DIR / "links.local.json"))

DB_PATH = Path(os.getenv("ASSISTANT_DB_PATH", DATA_DIR / "assistant.db"))

# endregion 경로

# region 모델

# 모델명은 수시로 추가·폐기된다. 여기 한 곳에서만 관리한다.
CHAT_MODELS: dict[str, str] = {
    "gpt-5.6-terra": "균형형 — 품질과 비용의 절충 (기본값)",
    "gpt-5.6-luna": "경량형 — 비용 민감 워크로드",
    "gpt-5.6-sol": "고성능형 — 복잡한 문의 대응",
}
DEFAULT_MODEL = os.getenv("ASSISTANT_MODEL", "gpt-5.6-terra")

# 요약은 품질보다 비용이 중요하다 — 경량 모델을 쓴다.
SUMMARY_MODEL = os.getenv("ASSISTANT_SUMMARY_MODEL", "gpt-5.6-luna")

EMBED_MODEL = os.getenv("ASSISTANT_EMBED_MODEL", "text-embedding-3-small")

# LLM 호출 방식 — "chat"(Chat Completions) | "responses"(Responses API)
# 검토 결과는 README '마이그레이션 기록'에 있다. 골든 데이터셋 생성 평가로 비교한 뒤 기본값을 바꾼다.
LLM_BACKEND = os.getenv("ASSISTANT_LLM_BACKEND", "chat")
LLM_BACKENDS = ("chat", "responses")

DEFAULT_TEMPERATURE = _f("ASSISTANT_TEMPERATURE", "0.2")
REQUEST_TIMEOUT = _f("ASSISTANT_REQUEST_TIMEOUT", "30")
MAX_RETRIES = _i("ASSISTANT_MAX_RETRIES", "2")
EMBED_TIMEOUT = _f("ASSISTANT_EMBED_TIMEOUT", "8")

# endregion 모델

# region 검색

TOP_K = _i("ASSISTANT_TOP_K", "3")

# 하이브리드 순위 가중치. 어휘 신호를 더 믿는다 —
# 5개 Autodesk 제품 청크가 제품명만 다르고 문장이 거의 같아서 임베딩으로는 구분이 안 된다.
HYBRID_W_LEXICAL = _f("ASSISTANT_W_LEXICAL", "0.6")
HYBRID_W_VECTOR = _f("ASSISTANT_W_VECTOR", "0.4")

# ★ 근거 게이트 — 이 값보다 관련도가 낮으면 LLM 을 부르지 않고 상담원 연결로 전환한다.
#   dev 분할에서 "여유 최대화" 규칙으로 정했다(python scripts/eval_rag.py --sweep).
#     앵커:   전환 문항 최고점 0.265 ↔ 답변 문항 0.609 사이 빈틈의 한가운데 = 0.45 (양쪽 여유 ±0.16)
#     무앵커: 전환 문항 최고점 0.261 ↔ 답변 문항 1.000 사이 빈틈의 한가운데 = 0.63 (양쪽 여유 ±0.37)
#   성능은 test 분할로만 보고한다. test 결과를 보고 이 값을 바꾸지 않는다.
#   벡터 게이트: 측정 전에는 끈다(1.01). 인덱스를 만든 뒤 스윕 결과로 켠다.
#   두 게이트는 OR 이다 — 어느 한쪽 근거라도 충분하면 답변한다.
HANDOFF_MIN_LEXICAL = _f("ASSISTANT_HANDOFF_MIN_LEXICAL", "0.45")
# 제품명이 없는 질문은 도메인이 보장되지 않는다("프린터 드라이버 설치"). 더 강한 어휘 근거를 요구한다.
HANDOFF_MIN_LEXICAL_UNANCHORED = _f("ASSISTANT_HANDOFF_MIN_LEXICAL_UNANCHORED", "0.63")
HANDOFF_MIN_VECTOR = _f("ASSISTANT_HANDOFF_MIN_VECTOR", "1.01")

# 제품명 없이 "설치 좀 도와주세요"처럼 일반 용어만 있으면 상담원이 아니라 제품을 되묻는다.
# 단어의 평균 IDF 가 이 값 미만이면 일반 용어로 본다(청크 절반 이상에 등장하는 수준).
GENERIC_WORD_MAX_IDF = _f("ASSISTANT_GENERIC_WORD_MAX_IDF", "0.7")

# 벡터 검색을 끄는 스위치 (예산 소진 · 장애 · 오프라인 데모)
VECTOR_ENABLED = os.getenv("ASSISTANT_VECTOR_ENABLED", "1") != "0"

# 질문 끝말·군더더기 bigram. 관련도 계산에서 뺀다.
# "~할까요", "~알려주세요" 같은 어미가 분모를 부풀려 정상 질문이 상담원으로 넘어가는 것을 막는다.
QUERY_STOP_BIGRAMS = frozenset(
    "해요 하나 나요 까요 어요 세요 주세 니다 는데 은데 인데 하는 어떻 떻게 할까 을까 싶어 싶은 "
    "되나 되요 돼요 가요 인가 건가 거예 예요 에요 알려 려주 줘요 해줘 주실 실래 래요 수있 있나 "
    "있을 은요 는요 이요 혹시 그럼 제가 저희 지금 좀더 해야 야하 하나요 면되 되는 는지 지요 요 "
    # 의문사·허용 표현 — 무엇을 묻는지가 아니라 어떻게 묻는지를 나타낸다
    "뭘로 뭐로 무엇 안돼 안되 돼도 되도 괜찮 찮나 찮을 찮아 도와 와주 부탁 "
    # 절차 메타어 — "설치 방법 · 순서 · 절차"는 주제가 아니라 "절차를 알려달라"는 뜻이다
    "방법 순서 절차 과정".split()
)

# 표기 변이 → 표준어. 질문과 문서 양쪽에 같은 규칙을 적용한다.
# 원본 가이드 자체가 "라이센스"와 "라이선스"를 섞어 쓴다.
SYNONYMS: tuple[tuple[str, str], ...] = (
    ("라이센스", "라이선스"),
    ("license", "라이선스"),
    ("인스톨", "설치"),
    ("install", "설치"),
    ("재부팅", "재시작"),
    ("리부팅", "재시작"),
    ("reboot", "재시작"),
    ("경로", "위치"),
    ("폴더", "위치"),
    # 라이선스 "등록 · 활성화 · 인증"은 이 도메인에서 같은 절차를 가리킨다.
    # 원본 가이드도 제목은 "라이선스 활성화", 본문은 "라이선스 인증"으로 섞어 쓴다.
    ("등록", "인증"),
    ("활성화", "인증"),
    # 불규칙 활용 — 어간이 달라지면 bigram 이 겹치지 않는다(바꾸다 → 바꿔, 누르다 → 눌러)
    ("바꿔", "바꾸"),
    ("눌러", "누르"),
)

# 관련도 계산 시 문서에 한 번도 없는 단어의 가중치 상한.
# 상한이 없으면 오타·구어체 단어 하나가 핵심어 여러 개의 일치를 덮어버린다.
# "한 번도 안 나온 단어"를 "한 번 나온 단어"와 같은 정보량으로 본다(df=1 의 IDF).
UNSEEN_IDF_AS_DF = _f("ASSISTANT_UNSEEN_IDF_AS_DF", "1")

# endregion 검색

# region 대화 이력

KEEP_RECENT_TURNS = _i("ASSISTANT_KEEP_TURNS", "6")          # 원문 그대로 보내는 최근 턴 수
SUMMARY_BATCH_MESSAGES = _i("ASSISTANT_SUMMARY_BATCH", "4")  # 이만큼 밀려나면 요약을 갱신한다
SUMMARY_MAX_CHARS = _i("ASSISTANT_SUMMARY_MAX_CHARS", "400")

# endregion 대화 이력

# region 사용량 상한

# 대화 1건 기준. 키 소유자(사용자 또는 배포자)의 비용을 예측 가능한 범위로 묶는다.
MAX_CALLS_PER_CONVERSATION = _i("ASSISTANT_MAX_CALLS", "30")
MAX_TOKENS_PER_CONVERSATION = _i("ASSISTANT_MAX_TOKENS", "60000")
# 전체 일일 상한. 새 대화를 열면 대화별 상한이 초기화되므로 배포자 키 보호에는 이것이 필요하다.
MAX_CALLS_PER_DAY = _i("ASSISTANT_MAX_CALLS_PER_DAY", "500")

MAX_INPUT_CHARS = _i("ASSISTANT_MAX_INPUT_CHARS", "1000")
MIN_INPUT_CHARS = _i("ASSISTANT_MIN_INPUT_CHARS", "2")

# endregion 사용량 상한

# region 상담원 연결

# 공개 데모에서 실제 콜센터 번호를 노출하지 않는다. 실제 배포 시 환경 변수로 번호를 넣는다.
SUPPORT_CONTACT = os.getenv("ASSISTANT_SUPPORT_CONTACT", "㈜상상진화 기술지원 콜센터")

# endregion 상담원 연결


def has_openai_key() -> bool:
    """서버 측 키(.env · 환경 변수) 존재 여부. 사이드바 입력 키는 app.py 가 따로 다룬다."""
    return bool(os.getenv("OPENAI_API_KEY"))
