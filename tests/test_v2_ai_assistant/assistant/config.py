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
#   "여유 최대화" 규칙으로 정한다(python scripts/eval_rag.py --sweep): 넘길 문항 최고점 위에서
#   가장 넓은 빈틈의 한가운데. 성능은 봉인된 측정 분할로만 보고하고, 그 결과를 보고 값을 바꾸지 않는다.
#   토크나이저마다 점수 분포가 달라 값을 따로 둔다.
#
#   형태소(kiwi) — 2라운드 튜닝셋(dev + 퇴역 test v1, 63문항)
#     앵커:   넘길 문항 최고점 0.216 ↔ 답할 문항 최저점 0.588 → 한가운데 0.40 (여유 ±0.19)
#     무앵커: 넘길 문항 최고점 0.382 ↔ 1.000 → 한가운데 0.69 (답할 문항 3건뿐이라 과잉 전환 목표보다 안전 우선)
HANDOFF_MIN_LEXICAL = _f("ASSISTANT_HANDOFF_MIN_LEXICAL", "0.40")
HANDOFF_MIN_LEXICAL_UNANCHORED = _f("ASSISTANT_HANDOFF_MIN_LEXICAL_UNANCHORED", "0.69")
#   bigram (폴백) — 1라운드 dev(23문항)
#     앵커 0.265 ↔ 0.609 → 0.45 (여유 ±0.16) · 무앵커 0.261 ↔ 1.000 → 0.63
HANDOFF_MIN_LEXICAL_BIGRAM = _f("ASSISTANT_HANDOFF_MIN_LEXICAL_BIGRAM", "0.45")
HANDOFF_MIN_LEXICAL_UNANCHORED_BIGRAM = _f("ASSISTANT_HANDOFF_MIN_LEXICAL_UNANCHORED_BIGRAM", "0.63")
#   벡터 게이트: 측정 전에는 끈다(1.01). 인덱스를 만든 뒤 스윕의 사전 등록 규칙으로 켠다.
#   어휘 게이트와 OR 다 — 어느 한쪽 근거라도 충분하면 답변한다.
HANDOFF_MIN_VECTOR = _f("ASSISTANT_HANDOFF_MIN_VECTOR", "1.01")

# 제품명 없이 "설치 좀 도와주세요"처럼 일반 용어만 있으면 상담원이 아니라 제품을 되묻는다.
# 단어의 평균 IDF 가 이 값 미만이면 일반 용어로 본다(청크 절반 이상에 등장하는 수준).
GENERIC_WORD_MAX_IDF = _f("ASSISTANT_GENERIC_WORD_MAX_IDF", "0.7")

# 벡터 게이트를 제품이 특정된(앵커) 질문에만 쓴다.
# "프린터 드라이버 설치"처럼 무관한 질문도 '설치' 때문에 설치 문서와 코사인이 높게 나온다.
VECTOR_GATE_ANCHORED_ONLY = os.getenv("ASSISTANT_VECTOR_GATE_ANCHORED_ONLY", "1") != "0"

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
)

# bigram 모드 전용 — 불규칙 활용을 원문에서 맞춘다(바꾸다 → 바꿔, 누르다 → 눌러).
# 형태소 모드에서는 분석기가 어간을 돌려주므로 쓰지 않는다. 원문을 바꾸면 오히려 분석이 깨진다.
SYNONYMS_BIGRAM_ONLY: tuple[tuple[str, str], ...] = (
    ("바꿔", "바꾸"),
    ("눌러", "누르"),
)

# ★ 토크나이저 — "kiwi"(형태소 분석, 기본) | "bigram"(이전 방식)
#   kiwipiepy 가 없거나 초기화에 실패하면 자동으로 bigram 으로 내려간다.
#   두 방식은 점수 분포가 달라 임계값을 따로 둔다(아래 HANDOFF_MIN_* 참고).
TOKENIZER = os.getenv("ASSISTANT_TOKENIZER", "kiwi")

# 형태소 모드에서 점수에서 빼는 어간 — 품사(조사·어미·접사·보조용언·의존명사)로 걸러지지 않는 기능성 어휘
#   경동사·형식 형용사 / 질문 메타 명사 / 담화 부사. 활용형이 아니라 어간 하나만 적는다.
MORPH_STOP = frozenset(
    "하 되 있 없 주 드리 싶 맞 알리 알 보 말 가 오 어떻 그렇 이렇 저렇 "
    "방법 순서 절차 과정 문의 부탁 질문 답변 설명 안내 관련 경우 때 정도 "
    "좀 혹시 그럼 지금 그냥 꼭 이제 또 다시 바로 어떻게 왜 잘 안 못 다 "
    "뭐 무엇 어디 언제 "
    "법 도와주 돕".split()          # 2라운드 튜닝셋: "등록하는 법" · "도와주세요"
)

# 지시어 — 주제를 담지 않는다. 점수에서 빼고 "직전 주제를 이어받으라"는 신호로 쓴다.
MORPH_DEICTIC = frozenset("마찬가지 똑같 동일 같 똑같이".split())

# 어간 단위 동의어 (형태소 모드). 문서와 질문에 같은 규칙을 적용한다.
MORPH_SYNONYMS: dict[str, str] = {
    "깔": "설치",        # 깔다 → 설치하다
    "깔다": "설치",      # 분석기 오분석 보정: "레빗 깔다가" → 깔다/NNG 가/JKS (도메인에 '깔다' 명사는 없다)
    "켜": "실행",        # 켜다 → 실행하다
    "끝나": "완료",      # 설치 끝나고 → 설치가 완료되면        (2라운드 튜닝셋 G-005)
    "누르": "클릭",      # 버튼 누르면 → 버튼을 클릭합니다      (문서도 두 표현을 섞어 쓴다)
    "사이트": "홈페이지",  # manage 사이트 → manage.autodesk.com 홈페이지 (G-025)
    # ↓ test2 측정 후 핫픽스: 받침이 다른 원문 치환이 조사를 깨뜨려 어간 단위로 옮겼다
    #   "활성화는" → 원문 치환 "인증는" → 분석기가 '늘' 을 만들어냈다 (T2-014)
    "활성": "인증",      # 활성화 → 활성/NNG 화/XSN
    "install": "설치",
    "license": "라이선스",
    "reboot": "재시작",
}

# ★ 문제 상황 게이트 (test2 측정 후 핫픽스 · T2-050)
#   지식 베이스는 "정상 설치 절차"만 담고 있다. 질문에 문제 상황 어휘가 있고 지식 베이스가 그 어휘를
#   전혀 다루지 않으면, 다른 단어("고객번호")가 겹쳐도 답하지 않고 상담원에게 넘긴다.
#   "에너지박스 고객번호를 잊어버렸어요" 에 인증 절차를 안내하면 틀린 안내다.
#   어휘가 지식 베이스에 등장하면(향후 '오류 대응' 문서 추가 등) 이 게이트는 자동으로 풀린다.
PROBLEM_WORDS = frozenset(
    "잊 잊어버리 분실 잃 잃어버리 오류 에러 실패 고장 멈추 튕기 충돌 손상 삭제 제거 지우 환불".split()
)

# 제품명·버전을 지운 자리에 넣는 자리표시자(형태소 모드). 지운 자리에 조사만 남으면
# "레빗은요" → "은" 이 명사로 분석된다. 자리표시자가 앞말 역할을 해 조사로 붙게 한다.
MORPH_PLACEHOLDER = "qqq"

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
