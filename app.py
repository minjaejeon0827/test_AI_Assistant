"""
* 테스트용 AI Assistant 프로그램

*** 참고 ***
*** ChatGPT 문서 ***
* OpenAI 오픈 소스 패키지 openai
터미널 설치 명령어
pip install openai==0.28.1
만약 이미 1.0.0 이상의 버전을 설치 할 경우 먼저 아래 명령어로 패키지 삭제 후 다시 설치 진행 필수!
OpenAI 패키지 삭제 명령어
pip uninstall openai

*** 파이썬 문서 ***
* with 문
참고 URL - https://docs.python.org/ko/3/reference/compound_stmts.html#index-16
참고 2 URL - https://velog.io/@hyungraelee/Python-with

*** 기타 문서 ***
* FastAPI, Streamlit, OpenAI 챗봇 만들기
참고 URL - https://youtu.be/n_MhxO16EaY?si=TyDrasy7Pa7OdTO3
참고 2 URL - https://dongdongfather.tistory.com/286
참고 3 URL - https://youtu.be/I_InS5HGtmE?si=lXODkiQ_A2PAfhUq
참고 4 URL - https://github.com/streamlit/llm-examples/blob/main/Chatbot.py#L1C1-L29C44

* Python 기반 웹 애플리케이션 UI 프레임워크 오픈 소스 패키지 streamlit
참고 URL - https://docs.streamlit.io/get-started/installation
참고 2 URL - https://streamlit.io/generative-ai
터미널 설치 명령어
pip install streamlit
"""

import openai            # OpenAI
import streamlit as st   # streamlit -> Elias(앨리아스) st

with st.sidebar:   # 파이썬 with 문 사용 및 좌측 사이드바 생성 (OpenAI API 키 입력 받는 용도)  
    openai_api_key = st.text_input("OpenAI API Key", key="chatbot_api_key", type="password")   # OpenAI API 키 입력 받기 및 해당 키 값 openai_api_key 변수 저장 (type='password' 사용하여 OpenAI API 키 값 노출 안 되도록 마스킹 처리)

    # None or Empty String Check
    # 참고 URL - https://stackoverflow.com/questions/9573244/how-to-check-if-the-string-is-empty-in-python
    # 참고 2 URL - https://hello-bryan.tistory.com/131
    # 참고 3 URL - https://jino-dev-diary.tistory.com/42
    # 참고 4 URL - https://claude.ai/chat/eaf7856e-1b5e-4c26-992e-de1683005638
    if openai_api_key:   # openai_api_key 변수 할당된 값이 None 또는 공백("")이 아닌 경우 (None or Empty String Check)
        openai.api_key = openai_api_key   # openai.api_key 변수에 입력 받은 openai_api_key 값 저장 (이렇게 처음에 OpenAI API 키 지정 한번 해 놓으면 OpenAI 패키지를 사용하는 코드 안에서는 더이상 따로 API 입력할 필요 없음.)
    st.markdown('---')   # 구분선 추가('---') - 혹시 밑에 다른 엘리멘트들을 추가할 때 대비해서 구현.

    # URL 링크 추가 정보 안내
    "[상상플렉스](https://www.ssflex.co.kr/)"
    "[(주)상상진화](https://imbu.co.kr/)"

# 메인 공간
st.header("[테스트] 상상플렉스 AI Assistant")   # "[테스트] 상상플렉스 AI Assistant" 프로그램 제목 화면 출력
st.caption("AI Assistant는 실수를 할 수 있습니다. 응답을 반드시 다시 확인해 주세요.")   # "AI Assistant는 실수를 할 수 있습니다. 응답을 반드시 다시 확인해 주세요." 프로그램 주석 화면 출력
st.markdown('---')   # 구분선 추가('---')

# st.session_state 초기화 코드
if "messages" not in st.session_state:   # "messages" - 사용자와 [테스트] 상상플렉스 AI Assistant 주고 받은 채팅 메시지 내역 (이전 대화 내역 모두 포함.)
    st.session_state["messages"] = [{"role": "assistant", "content": "오늘 어떤 도움을 드릴까요?"}]

for msg in st.session_state.messages:  # for 반복문 사용하여 st.session_state.messages 저장된 채팅 메시지 모두 확인 (이전 대화 내역 모두 포함.)
    st.chat_message(msg["role"]).write(msg["content"])   # "role"에 따라 아이콘 모양 다르게 채팅 메시지 작성 ("role" - 사용자 또는 AI Assistant / chat_message 모듈 - 채팅 메시지 한 블록 의미)

if prompt := st.chat_input():   # 사용자가 채팅 메시지 입력한 경우 (chat_input 모듈 - AI Assistant 화면 하단 사용자 채팅 메시지 입력)
    if not openai_api_key:   # OpenAI API 키 입력 하지 않은 경우
        st.info("OpenAI API Key 입력 부탁드립니다.")   # 프로그램 화면 마지막 출력된 채팅 메시지 하단 "OpenAI API Key 입력 부탁드립니다." 안내 메시지 출력.
        st.stop()    # Streamlit 스크립트 실행 즉시 중지

    # OpenAI API 키 입력 된 경우
    # client = OpenAI(api_key=openai_api_key)
    openai.api_key = openai_api_key
    st.session_state.messages.append({"role": "user", "content": prompt})   # 사용자가 입력한 채팅 메시지(prompt) 가져와서 st.session_state.messages 아이템 추가
    st.chat_message("user").write(prompt)   # 사용자가 입력한 채팅 메시지 프로그램 화면 출력

    # response = client.chat.completions.create(model="gpt-3.5-turbo", messages=st.session_state.messages)
    response = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=st.session_state.messages)   # ChatGPT 응답 받기 및 response 변수 저장
    # msg = response.choices[0].message.content
    msg = response["choices"][0]["message"]["content"]   # ChatGPT 텍스트 응답 메시지 msg 변수 저장

    st.session_state.messages.append({"role": "assistant", "content": msg})   # ChatGPT 텍스트 응답 메시지(msg) 가져와서 st.session_state.messages 아이템 추가
    st.chat_message("assistant").write(msg)   # ChatGPT 텍스트 응답 메시지 프로그램 화면 출력 ("role" - AI Assistant)

    print(st.session_state.messages)   # 사용자와 [테스트] 상상플렉스 AI Assistant 주고 받은 채팅 메시지 내역 터미널 출력 (이전 대화 내역 모두 포함.)
