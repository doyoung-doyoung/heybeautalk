import os
import re
import sqlite3
import sys
from datetime import datetime

vendor_path = os.path.join(os.path.dirname(__file__), ".vendor")
if os.path.isdir(vendor_path):
    sys.path.insert(0, vendor_path)

from flask import Flask, jsonify, render_template, request

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
SUPABASE_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
CLOUD_MODE = bool(SUPABASE_URL and SUPABASE_KEY)
if CLOUD_MODE:
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
if OPENAI_API_KEY:
    from openai import OpenAI
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None

app = Flask(__name__)
DATABASE = os.path.join(app.root_path, "heybeauty.db")


def db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


def init_db():
    if CLOUD_MODE:
        return
    connection = db()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS clinics (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, district TEXT NOT NULL,
          rating REAL NOT NULL, description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS services (
          id INTEGER PRIMARY KEY, clinic_id INTEGER NOT NULL, name TEXT NOT NULL,
          category TEXT NOT NULL, price INTEGER NOT NULL, duration TEXT NOT NULL,
          slots TEXT NOT NULL, FOREIGN KEY(clinic_id) REFERENCES clinics(id)
        );
        CREATE TABLE IF NOT EXISTS customers (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, concern TEXT,
          preferred_service TEXT, notes TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_profiles (
          user_id TEXT PRIMARY KEY, name TEXT, phone TEXT, concern TEXT,
          preferred_service TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS appointments (
          id INTEGER PRIMARY KEY, clinic_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
          customer_id INTEGER NOT NULL, slot TEXT NOT NULL, status TEXT NOT NULL,
          source TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(clinic_id) REFERENCES clinics(id),
          FOREIGN KEY(service_id) REFERENCES services(id),
          FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
    """)
    if connection.execute("SELECT COUNT(*) FROM clinics").fetchone()[0] == 0:
        connection.executemany("INSERT INTO clinics VALUES (?, ?, ?, ?, ?)", [
            (1, "라이트 피부과", "강남구", 4.8, "피부과 전문의 상담과 자연스러운 안티에이징 시술"),
            (2, "루미에르 클리닉", "서초구", 4.7, "맞춤형 리프팅·피부결 관리 클리닉"),
            (3, "온유 메디컬", "송파구", 4.6, "여드름 흉터와 색소 케어 중심의 메디컬 에스테틱"),
        ])
        connection.executemany("INSERT INTO services VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (1, 1, "국산 보톡스", "보톡스", 89000, "20분", "2026-09-05 11:00,2026-09-06 14:00,2026-09-08 16:00"),
            (2, 1, "입술 필러 1cc", "필러", 290000, "40분", "2026-09-05 15:00,2026-09-07 11:00,2026-09-09 13:00"),
            (3, 2, "슈링크 유니버스 300샷", "리프팅", 390000, "45분", "2026-09-06 10:30,2026-09-07 14:30,2026-09-10 12:00"),
            (4, 2, "물광 스킨부스터", "스킨부스터", 220000, "40분", "2026-09-05 13:00,2026-09-08 11:30,2026-09-10 16:30"),
            (5, 3, "피코토닝", "레이저", 120000, "30분", "2026-09-05 10:00,2026-09-06 16:00,2026-09-09 14:00"),
            (6, 3, "여드름 흉터 프락셀", "레이저", 260000, "50분", "2026-09-07 13:00,2026-09-08 15:00,2026-09-10 10:00"),
        ])
        now = datetime.now().isoformat(timespec="seconds")
        connection.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (1, "김민지", "010-1234-5678", "턱 라인", "국산 보톡스", "첫 방문 · 자연스러운 결과 선호", now),
            (2, "박서연", "010-2345-6789", "건조함", "물광 스킨부스터", "알레르기 없음", now),
            (3, "이준호", "010-3456-7890", "색소", "피코토닝", "점심 시간 예약 선호", now),
        ])
        connection.executemany("INSERT INTO appointments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            (1, 1, 1, 1, "2026-09-05 11:00", "예약확정", "채팅", now),
            (2, 2, 4, 2, "2026-09-08 11:30", "상담대기", "채팅", now),
            (3, 3, 5, 3, "2026-09-09 14:00", "방문완료", "전화", now),
        ])
    connection.commit()
    connection.close()


def service_slots(service):
    value = service.get("slots", [])
    return value.split(",") if isinstance(value, str) else value


def flatten_service(service, include_rating=False):
    clinic = service.pop("clinics", {}) or {}
    service["clinic_name"] = clinic.get("name", "클리닉")
    service["district"] = clinic.get("district", "")
    if include_rating:
        service["rating"] = clinic.get("rating")
    service["currency"] = service.get("currency", "THB")
    service["slots"] = service_slots(service)
    return service


def beauty_ai_answer(message, profile, services):
    """Generates an opted-in answer without sending phone numbers or names."""
    if not openai_client:
        return None
    safe_message = re.sub(r"01[0-9][-\s]?\d{3,4}[-\s]?\d{4}", "[연락처 삭제]", message)
    safe_message = re.sub(r"((?:저는|이름은)\s*)[가-힣]{2,4}", r"\1[이름 삭제]", safe_message)
    def catalog_price(item):
        return f"฿{item['price']:,} THB" if item.get("currency") == "THB" else f"{item['price']:,} KRW"

    catalog = "; ".join(
        f"{item['clinic_name']} / {item['name']} / {catalog_price(item)}"
        for item in services
    )
    instructions = (
        "당신은 HeyBeauty의 한국어 뷰티 정보 안내 AI입니다. 시술의 일반적 목적, "
        "기대할 수 있는 점, 흔한 주의사항을 3~5문장으로 친절하고 간결하게 설명하세요. "
        "진단·처방·효과 보장은 하지 말고, 임신·수유·질환·복용약 또는 개인 피부 상태는 "
        "의료진 상담이 필요하다고 안내하세요. 사용자가 특정 시술에 관심을 보이면 아래 실제 "
        "입점 클리닉 중 한 곳을 클리닉명·서비스명·가격·지역과 함께 소개하고, 예약 버튼을 "
        "누르도록 자연스럽게 안내하세요. 서비스 카탈로그의 통화를 그대로 사용하고, 가격·가능 시간을 "
        "임의로 만들지 마세요.\n\n"
        f"사용자 관심 시술: {profile.get('preferred_service') or '미확인'}\n"
        f"추천 가능한 실제 서비스: {catalog}"
    )
    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=safe_message,
        max_output_tokens=350,
        store=False,
    )
    return response.output_text.strip()


def clinic_match_reply(category, services):
    """Provides a booking-oriented response even when generative AI is disabled."""
    if not category or not services:
        return (
            "피부 고민에 맞는 시술을 찾아볼게요. 보톡스, 필러, 리프팅, 스킨부스터, 레이저 중 "
            "관심 있는 시술이나 고민을 알려주시면 입점 클리닉과 예약 시간을 바로 연결해 드려요."
        )
    match = services[0]
    price = f"฿{match['price']:,}" if match.get("currency") == "THB" else f"{match['price']:,}원"
    if re.search(r"[ก-๙]", category):
        return (
            f"คุณสนใจ {category} ใช่ไหม? {match['district']}의 {match['clinic_name']}에서 "
            f"‘{match['name']}’ 서비스를 {price}에 안내하고 있어요. 아래 예약 버튼을 눌러 "
            "가능한 시간을 확인하고 예약 요청을 진행할 수 있어요."
        )
    return (
        f"{category}에 관심이 있으시군요. {match['district']}의 {match['clinic_name']}에서 "
        f"‘{match['name']}’ 서비스를 {price}에 안내하고 있어요. "
        f"아래 ‘예약 시간 보기’를 누르면 가능한 시간을 확인하고 바로 예약 요청할 수 있어요. "
        "시술 전에는 의료진과 피부 상태·부작용을 꼭 상담해 주세요."
    )


def save_chat_profile(user_id, message):
    if CLOUD_MODE:
        result = supabase.table("chat_profiles").select("*").eq("user_id", user_id).execute().data
        old = result[0] if result else None
    else:
        connection = db()
        old = connection.execute("SELECT * FROM chat_profiles WHERE user_id=?", (user_id,)).fetchone()
    profile = dict(old) if old else {"name": None, "phone": None, "concern": None, "preferred_service": None}
    phone = re.search(r"01[0-9][-\s]?\d{3,4}[-\s]?\d{4}", message)
    name = re.search(r"(?:저는|이름은)\s*([가-힣]{2,4})", message)
    if phone:
        profile["phone"] = re.sub(r"\s", "", phone.group())
    if name:
        profile["name"] = name.group(1)
    categories = {"보톡스": ["보톡스", "사각턱", "주름", "โบท็อกซ์"], "필러": ["필러", "입술", "볼륨", "ฟิลเลอร์"], "리프팅": ["리프팅", "슈링크", "처짐", "ยกกระชับ"], "스킨부스터": ["스킨부스터", "물광", "건조", "ผิวแห้ง", "สกินบูสเตอร์"], "레이저": ["레이저", "피코", "색소", "흉터", "여드름", "เลเซอร์", "ฝ้า", "สิว"]}
    found = next((key for key, terms in categories.items() if any(term in message for term in terms)), None)
    if found:
        profile["preferred_service"] = found
    concerns = [term for term in ["턱 라인", "주름", "건조", "색소", "흉터", "여드름", "처짐"] if term in message]
    if concerns:
        profile["concern"] = ", ".join(concerns)
    payload = {"user_id": user_id, "name": profile["name"], "phone": profile["phone"], "concern": profile["concern"], "preferred_service": profile["preferred_service"], "updated_at": datetime.now().isoformat(timespec="seconds")}
    if CLOUD_MODE:
        supabase.table("chat_profiles").upsert(payload).execute()
    else:
        connection.execute("""INSERT INTO chat_profiles(user_id,name,phone,concern,preferred_service,updated_at)
          VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,phone=excluded.phone,
          concern=excluded.concern,preferred_service=excluded.preferred_service,updated_at=excluded.updated_at""", tuple(payload.values()))
        connection.commit(); connection.close()
    return profile


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "cloud_mode": CLOUD_MODE,
        "supabase_url_configured": bool(SUPABASE_URL),
        "supabase_service_key_configured": bool(SUPABASE_SERVICE_ROLE_KEY),
        "supabase_anon_key_configured": bool(SUPABASE_ANON_KEY),
    })


@app.get("/crm")
def crm():
    return render_template("crm.html")


@app.get("/api/clinics")
def clinics():
    if CLOUD_MODE:
        result = supabase.table("services").select("*, clinics(name,district,rating)").eq("is_active", True).order("clinic_id").execute().data
        return jsonify([flatten_service(item, include_rating=True) for item in result])
    connection = db()
    result = rows(connection.execute("""SELECT s.*, c.name clinic_name, c.district, c.rating FROM services s
      JOIN clinics c ON c.id=s.clinic_id ORDER BY c.id, s.id"""))
    connection.close()
    for service in result: service["slots"] = service_slots(service)
    return jsonify(result)


@app.post("/chat")
@app.post("/api/chat")
def chat():
    data = request.get_json() or {}
    message, user_id = data.get("message", "").strip(), data.get("user_id", "demo-user")
    allow_ai = bool(data.get("allow_ai"))
    if not message: return jsonify({"reply": "메시지를 입력해 주세요.", "profile": {}}), 400
    profile = save_chat_profile(user_id, message)
    query = profile.get("preferred_service") or ""
    if CLOUD_MODE:
        request_query = supabase.table("services").select("*, clinics(name,district)").eq("is_active", True)
        if query: request_query = request_query.eq("category", query)
        services = [flatten_service(item) for item in request_query.limit(3).execute().data]
        if not services:
            services = [flatten_service(item) for item in supabase.table("services").select("*, clinics(name,district)").eq("is_active", True).limit(3).execute().data]
    else:
        connection = db()
        services = rows(connection.execute("""SELECT s.*, c.name clinic_name, c.district FROM services s
          JOIN clinics c ON c.id=s.clinic_id WHERE s.category=? OR s.name LIKE ? LIMIT 3""", (query, f"%{query}%")))
        if not services: services = rows(connection.execute("SELECT s.*, c.name clinic_name, c.district FROM services s JOIN clinics c ON c.id=s.clinic_id LIMIT 3"))
        connection.close()
        for service in services: service["slots"] = service_slots(service)
    reply = clinic_match_reply(query, services)
    if allow_ai:
        try:
            reply = beauty_ai_answer(message, profile, services) or reply
        except Exception:
            app.logger.exception("OpenAI beauty answer failed")
    lead = {"interested": bool(query), "category": query}
    if services:
        lead.update({"clinic_name": services[0]["clinic_name"], "service_name": services[0]["name"]})
    return jsonify({"reply": reply, "profile": profile, "services": services, "lead": lead})


@app.post("/bookings")
@app.post("/api/bookings")
def create_booking():
    data = request.get_json() or {}
    required = ["service_id", "slot", "name", "phone"]
    if any(not str(data.get(key, "")).strip() for key in required):
        return jsonify({"error": "예약자 이름, 연락처, 시간은 필수입니다."}), 400
    if CLOUD_MODE:
        service_result = supabase.table("services").select("*").eq("id", data["service_id"]).execute().data
        service = service_result[0] if service_result else None
    else:
        connection = db()
        service = connection.execute("SELECT * FROM services WHERE id=?", (data["service_id"],)).fetchone()
    if not service or data["slot"] not in service_slots(service):
        if not CLOUD_MODE: connection.close()
        return jsonify({"error": "선택한 예약 시간을 찾을 수 없습니다."}), 400
    now = datetime.now().isoformat(timespec="seconds")
    if CLOUD_MODE:
        found = supabase.table("customers").select("id").eq("phone", data["phone"]).limit(1).execute().data
        customer = found[0] if found else None
    else:
        customer = connection.execute("SELECT id FROM customers WHERE phone=?", (data["phone"],)).fetchone()
    if customer:
        customer_id = customer["id"]
        if CLOUD_MODE: supabase.table("customers").update({"name": data["name"], "concern": data.get("concern", ""), "preferred_service": service["name"]}).eq("id", customer_id).execute()
        else: connection.execute("UPDATE customers SET name=?, concern=?, preferred_service=? WHERE id=?", (data["name"], data.get("concern", ""), service["name"], customer_id))
    else:
        if CLOUD_MODE:
            customer_id = supabase.table("customers").insert({"name": data["name"], "phone": data["phone"], "concern": data.get("concern", ""), "preferred_service": service["name"], "notes": "채팅 예약으로 생성"}).execute().data[0]["id"]
        else: customer_id = connection.execute("INSERT INTO customers(name,phone,concern,preferred_service,notes,created_at) VALUES(?,?,?,?,?,?)", (data["name"], data["phone"], data.get("concern", ""), service["name"], "채팅 예약으로 생성", now)).lastrowid
    if CLOUD_MODE:
        appointment_id = supabase.table("appointments").insert({"clinic_id": service["clinic_id"], "service_id": service["id"], "customer_id": customer_id, "slot": data["slot"], "status": "상담대기", "source": "HeyBeauty 채팅"}).execute().data[0]["id"]
    else:
        appointment_id = connection.execute("INSERT INTO appointments(clinic_id,service_id,customer_id,slot,status,source,created_at) VALUES(?,?,?,?,?,?,?)", (service["clinic_id"], service["id"], customer_id, data["slot"], "상담대기", "HeyBeauty 채팅", now)).lastrowid
        connection.commit(); connection.close()
    return jsonify({"id": appointment_id, "message": "예약 요청이 접수되었습니다. 클리닉에서 확인 후 연락드립니다."}), 201


@app.get("/api/crm")
def crm_data():
    clinic_id = request.args.get("clinic_id", 1, type=int)
    if CLOUD_MODE:
        clinic_rows = supabase.table("clinics").select("*").eq("id", clinic_id).execute().data
        appointment_rows = supabase.table("appointments").select("*, customers(name,phone,concern,notes), services(name)").eq("clinic_id", clinic_id).order("slot").execute().data
        for item in appointment_rows:
            customer, service = item.pop("customers", {}) or {}, item.pop("services", {}) or {}
            item.update({"customer_name": customer.get("name"), "phone": customer.get("phone"), "concern": customer.get("concern"), "notes": customer.get("notes"), "service_name": service.get("name")})
        return jsonify({"clinic": clinic_rows[0] if clinic_rows else None, "appointments": appointment_rows})
    connection = db()
    clinic = connection.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone()
    appointments = rows(connection.execute("""SELECT a.*, cu.name customer_name, cu.phone, cu.concern, cu.notes,
      s.name service_name FROM appointments a JOIN customers cu ON cu.id=a.customer_id
      JOIN services s ON s.id=a.service_id WHERE a.clinic_id=? ORDER BY a.slot""", (clinic_id,)))
    connection.close()
    return jsonify({"clinic": dict(clinic) if clinic else None, "appointments": appointments})


@app.patch("/api/appointments/<int:appointment_id>")
def update_appointment(appointment_id):
    status = (request.get_json() or {}).get("status")
    if status not in ["상담대기", "예약확정", "방문완료", "취소"]:
        return jsonify({"error": "올바른 상태를 선택해 주세요."}), 400
    if CLOUD_MODE: supabase.table("appointments").update({"status": status}).eq("id", appointment_id).execute()
    else:
        connection = db(); connection.execute("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id)); connection.commit(); connection.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
