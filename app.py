import os
import re
import sqlite3
import sys
from datetime import datetime

vendor_path = os.path.join(os.path.dirname(__file__), ".vendor")
if os.path.isdir(vendor_path):
    sys.path.insert(0, vendor_path)

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
DATABASE = os.path.join(app.root_path, "heybeauty.db")


def db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


def init_db():
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


def save_chat_profile(user_id, message):
    connection = db()
    old = connection.execute("SELECT * FROM chat_profiles WHERE user_id=?", (user_id,)).fetchone()
    profile = dict(old) if old else {"name": None, "phone": None, "concern": None, "preferred_service": None}
    phone = re.search(r"01[0-9][-\s]?\d{3,4}[-\s]?\d{4}", message)
    name = re.search(r"(?:저는|이름은)\s*([가-힣]{2,4})", message)
    if phone:
        profile["phone"] = re.sub(r"\s", "", phone.group())
    if name:
        profile["name"] = name.group(1)
    categories = {"보톡스": ["보톡스", "사각턱", "주름"], "필러": ["필러", "입술", "볼륨"], "리프팅": ["리프팅", "슈링크", "처짐"], "스킨부스터": ["스킨부스터", "물광", "건조"], "레이저": ["레이저", "피코", "색소", "흉터", "여드름"]}
    found = next((key for key, terms in categories.items() if any(term in message for term in terms)), None)
    if found:
        profile["preferred_service"] = found
    concerns = [term for term in ["턱 라인", "주름", "건조", "색소", "흉터", "여드름", "처짐"] if term in message]
    if concerns:
        profile["concern"] = ", ".join(concerns)
    connection.execute("""INSERT INTO chat_profiles(user_id,name,phone,concern,preferred_service,updated_at)
      VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,phone=excluded.phone,
      concern=excluded.concern,preferred_service=excluded.preferred_service,updated_at=excluded.updated_at""",
      (user_id, profile["name"], profile["phone"], profile["concern"], profile["preferred_service"], datetime.now().isoformat(timespec="seconds")))
    connection.commit(); connection.close()
    return profile


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/crm")
def crm():
    return render_template("crm.html")


@app.get("/api/clinics")
def clinics():
    connection = db()
    result = rows(connection.execute("""SELECT s.*, c.name clinic_name, c.district, c.rating FROM services s
      JOIN clinics c ON c.id=s.clinic_id ORDER BY c.id, s.id"""))
    connection.close()
    for service in result: service["slots"] = service["slots"].split(",")
    return jsonify(result)


@app.post("/api/chat")
def chat():
    data = request.get_json() or {}
    message, user_id = data.get("message", "").strip(), data.get("user_id", "demo-user")
    if not message: return jsonify({"reply": "메시지를 입력해 주세요.", "profile": {}}), 400
    profile = save_chat_profile(user_id, message)
    connection = db()
    query = profile.get("preferred_service") or ""
    services = rows(connection.execute("""SELECT s.*, c.name clinic_name, c.district FROM services s
      JOIN clinics c ON c.id=s.clinic_id WHERE s.category=? OR s.name LIKE ? LIMIT 3""", (query, f"%{query}%")))
    if not services: services = rows(connection.execute("SELECT s.*, c.name clinic_name, c.district FROM services s JOIN clinics c ON c.id=s.clinic_id LIMIT 3"))
    connection.close()
    for service in services: service["slots"] = service["slots"].split(",")
    detail = f"‘{query}’ 관련으로 " if query else ""
    reply = detail + "맞춤 클리닉을 골랐어요. 시술 전에는 의료진과 피부 상태·부작용을 꼭 상담해 주세요. 아래 서비스에서 예약을 선택하면, 채팅에서 알려주신 정보가 자동으로 채워집니다."
    return jsonify({"reply": reply, "profile": profile, "services": services})


@app.post("/api/bookings")
def create_booking():
    data = request.get_json() or {}
    required = ["service_id", "slot", "name", "phone"]
    if any(not str(data.get(key, "")).strip() for key in required):
        return jsonify({"error": "예약자 이름, 연락처, 시간은 필수입니다."}), 400
    connection = db()
    service = connection.execute("SELECT * FROM services WHERE id=?", (data["service_id"],)).fetchone()
    if not service or data["slot"] not in service["slots"].split(","):
        connection.close(); return jsonify({"error": "선택한 예약 시간을 찾을 수 없습니다."}), 400
    now = datetime.now().isoformat(timespec="seconds")
    customer = connection.execute("SELECT id FROM customers WHERE phone=?", (data["phone"],)).fetchone()
    if customer:
        customer_id = customer["id"]
        connection.execute("UPDATE customers SET name=?, concern=?, preferred_service=? WHERE id=?", (data["name"], data.get("concern", ""), service["name"], customer_id))
    else:
        customer_id = connection.execute("INSERT INTO customers(name,phone,concern,preferred_service,notes,created_at) VALUES(?,?,?,?,?,?)", (data["name"], data["phone"], data.get("concern", ""), service["name"], "채팅 예약으로 생성", now)).lastrowid
    appointment_id = connection.execute("INSERT INTO appointments(clinic_id,service_id,customer_id,slot,status,source,created_at) VALUES(?,?,?,?,?,?,?)", (service["clinic_id"], service["id"], customer_id, data["slot"], "상담대기", "HeyBeauty 채팅", now)).lastrowid
    connection.commit(); connection.close()
    return jsonify({"id": appointment_id, "message": "예약 요청이 접수되었습니다. 클리닉에서 확인 후 연락드립니다."}), 201


@app.get("/api/crm")
def crm_data():
    clinic_id = request.args.get("clinic_id", 1, type=int)
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
    connection = db(); connection.execute("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id)); connection.commit(); connection.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
