# HeyBeauty MVP

뷰티 AI 채팅, 입점 클리닉의 서비스·예약 가능 시간, 그리고 클리닉 CRM을 연결한 Flask 데모입니다.

## 실행

Python 3.10 이상에서 아래를 실행하세요.

```powershell
python -m pip install -r requirements.txt
python app.py
```

브라우저에서 `http://127.0.0.1:5000`을 열면 사용자 화면, `/crm`을 열면 클리닉 CRM 데모를 볼 수 있습니다.

첫 실행 시 `heybeauty.db`가 생성되고, 3개 클리닉·6개 서비스·3개 고객·3개 예약 데이터가 자동으로 추가됩니다.

## Production deployment

Vercel deploys this Flask application directly from `app.py`. For a persistent
production database, apply `supabase/migrations/20260903_initial.sql` in the
Supabase SQL Editor, then configure the Supabase and OpenAI values listed in
`.env.example` as Vercel environment variables. Do not expose service-role or
OpenAI keys in browser code.

OpenAI answers are optional: a user must opt in for each message before it is
sent to OpenAI. The server removes Korean phone numbers and names from that
message, and makes the API request with response storage disabled.
