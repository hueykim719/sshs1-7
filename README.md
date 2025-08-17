# 1-7 ClassHub (Modern Aurora UI, Year=2025 Fixed)

- 모바일 친화 UI, 오로라 그라디언트 배경, 글래스 카드, 필 버튼
- 과제/수행: 정렬·필터·완료·색상·첨부, **연도 2025 고정(월·일만 입력)**
- 홈: 오늘·내일 카운트 + 다가오는 일정 5개
- 공지(기타): 텍스트·태그·핀 + 파일 첨부, 링크 자동 인식
- 시간표 이미지 업로드, 준비물 관리
- PWA(install), ICS 캘린더 피드(`/calendar.ics`)
- 관리자 모드: 관리자 코드로 전환(간단)

## 실행
```bash
pip install -r requirements.txt
cp .env.example .env  # SECRET_KEY, ADMIN_CODE 설정
flask --app app run
