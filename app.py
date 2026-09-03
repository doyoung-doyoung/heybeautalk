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


def beauty_ai_answer(message, profile, services, info_step=1):
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
        "당신은 HeyBeauty의 한국어 뷰티 정보 안내 AI입니다. 한 번에 예약을 재촉하지 말고 "
        "3단계 대화로 정보를 나눠 안내하세요. 1단계는 원리와 목적, 2단계는 적합한 대상과 기대점, "
        "3단계는 주의사항과 상담 준비에 집중해 3~5문장으로 친절하고 간결하게 설명하세요. "
        "진단·처방·효과 보장은 하지 말고, 임신·수유·질환·복용약 또는 개인 피부 상태는 "
        "의료진 상담이 필요하다고 안내하세요. 사용자가 특정 시술에 관심을 보이면 아래 실제 "
        "입점 클리닉 중 한 곳을 클리닉명·서비스명·가격·지역과 함께 소개하고, 예약 버튼을 "
        "누르도록 자연스럽게 안내하세요. 서비스 카탈로그의 통화를 그대로 사용하고, 가격·가능 시간을 "
        "임의로 만들지 마세요.\n\n"
        f"현재 정보 단계: {info_step}/3\n"
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


def clinic_match_reply(category, services, is_thai=False, info_step=1):
    """Provides a booking-oriented response even when generative AI is disabled."""
    if not category or not services:
        return (
            "피부 고민에 맞는 시술을 찾아볼게요. 보톡스, 필러, 리프팅, 스킨부스터, 레이저 중 "
            "관심 있는 시술이나 고민을 알려주시면 입점 클리닉과 예약 시간을 바로 연결해 드려요."
        )
    education = {
        "보톡스": (
            ("보톡스는 특정 근육의 움직임을 일시적으로 줄여 표정 주름이나 턱 근육 관리에 쓰이는 시술이에요.",
             "사각턱 보톡스는 근육이 발달한 경우에 주로 고려하며, 뼈나 지방이 원인이라면 기대 결과가 다를 수 있어요.",
             "멍·붓기·일시적인 비대칭 등이 생길 수 있으니 복용약, 임신·수유, 질환 여부를 상담 때 꼭 알려주세요."),
            ("โบท็อกซ์ช่วยลดการทำงานของกล้ามเนื้อชั่วคราว จึงมักใช้ดูแลริ้วรอยหรือกล้ามเนื้อกราม.",
             "โบท็อกซ์กรามมักเหมาะเมื่อกรามดูกว้างจากกล้ามเนื้อ หากเกิดจากกระดูกหรือไขมัน ผลที่คาดหวังอาจต่างกัน.",
             "อาจมีรอยช้ำ บวม หรือความไม่สมมาตรชั่วคราว ควรแจ้งแพทย์เรื่องยา การตั้งครรภ์ การให้นม และโรคประจำตัว."),
        ),
        "필러": (
            ("필러는 볼륨이 부족한 부위를 보완하는 시술로, 입술·팔자·볼 등 부위마다 제품과 접근 방식이 달라요.",
             "원하는 모양뿐 아니라 얼굴 비율, 기존 시술 이력, 유지 기간을 함께 상담하면 더 적합한 계획을 세울 수 있어요.",
             "붓기·멍과 드물지만 중요한 혈관 관련 위험이 있어 정품 사용 여부와 시술자의 경험을 확인해야 해요."),
            ("ฟิลเลอร์ช่วยเติมวอลลุ่ม โดยชนิดผลิตภัณฑ์และวิธีฉีดจะแตกต่างกันตามบริเวณ เช่น ริมฝีปาก ร่องแก้ม หรือแก้ม.",
             "ควรปรึกษาทั้งรูปทรงที่ต้องการ สัดส่วนใบหน้า ประวัติการฉีดเดิม และระยะเวลาคงอยู่.",
             "อาจบวมหรือช้ำ และมีความเสี่ยงเกี่ยวกับหลอดเลือดที่พบได้น้อยแต่สำคัญ ควรตรวจสอบผลิตภัณฑ์และประสบการณ์แพทย์."),
        ),
        "리프팅": (
            ("리프팅 시술은 피부 탄력과 처짐 개선을 목적으로 하며 장비별로 에너지가 닿는 깊이와 방식이 달라요.",
             "피부 두께, 지방량, 처짐 정도에 따라 적합한 장비와 기대 결과가 달라질 수 있어요.",
             "통증·붓기·일시적 감각 변화 가능성이 있으므로 정품 팁과 적정 샷 수·강도를 의료진과 확인하세요."),
            ("หัตถการยกกระชับช่วยดูแลความหย่อนคล้อย โดยเครื่องแต่ละชนิดส่งพลังงานต่างระดับความลึก.",
             "เครื่องที่เหมาะและผลที่คาดหวังขึ้นกับความหนาผิว ปริมาณไขมัน และระดับความหย่อนคล้อย.",
             "อาจเจ็บ บวม หรือรู้สึกชาชั่วคราว ควรให้แพทย์ประเมินจำนวนช็อตและระดับพลังงานที่เหมาะสม."),
        ),
        "스킨부스터": (
            ("스킨부스터는 피부 보습과 결 개선을 돕는 주사 시술이며 제품마다 주요 성분과 목적이 달라요.",
             "건조함, 잔주름, 피부 컨디션 중 무엇을 우선할지에 따라 제품과 권장 횟수가 달라질 수 있어요.",
             "엠보싱·멍·붓기가 일시적으로 생길 수 있고 염증성 피부나 알레르기 이력이 있다면 먼저 알려야 해요."),
            ("สกินบูสเตอร์เป็นหัตถการฉีดที่เน้นความชุ่มชื้นและผิวสัมผัส โดยแต่ละผลิตภัณฑ์มีส่วนประกอบและเป้าหมายต่างกัน.",
             "ผลิตภัณฑ์และจำนวนครั้งขึ้นกับว่าต้องการเน้นผิวแห้ง ริ้วรอยเล็ก หรือสภาพผิวโดยรวม.",
             "อาจมีตุ่มนูน รอยช้ำ หรือบวมชั่วคราว หากผิวอักเสบหรือเคยแพ้ควรแจ้งแพทย์ก่อน."),
        ),
        "레이저": (
            ("레이저는 색소·피부결·흉터 등 고민에 따라 파장과 작용 방식이 다른 장비를 선택해요.",
             "같은 색소처럼 보여도 원인이 다를 수 있어 피부 상태와 최근 햇빛 노출, 과거 반응을 함께 확인해야 해요.",
             "시술 뒤에는 자외선 차단과 보습이 중요하며 붉음·딱지·색소 변화 가능성을 상담해야 해요."),
            ("เลเซอร์มีความยาวคลื่นและกลไกต่างกัน จึงเลือกตามปัญหา เช่น เม็ดสี ผิวสัมผัส หรือรอยสิว.",
             "แม้รอยจะดูคล้ายกัน สาเหตุอาจต่างกัน ควรประเมินสภาพผิว การโดนแดด และการตอบสนองจากการรักษาครั้งก่อน.",
             "หลังทำควรป้องกันแดดและเพิ่มความชุ่มชื้น พร้อมปรึกษาเรื่องรอยแดง สะเก็ด หรือการเปลี่ยนแปลงของสีผิว."),
        ),
    }
    match = services[0]
    price = f"฿{match['price']:,}" if match.get("currency") == "THB" else f"{match['price']:,}원"
    info_step = max(1, min(int(info_step or 1), 3))
    ko_steps, th_steps = education.get(category, (("시술의 원리와 내 피부 상태를 먼저 확인해 보세요.",) * 3, ("ควรทำความเข้าใจหัตถการและประเมินสภาพผิวก่อน.",) * 3))
    info = th_steps[info_step - 1] if is_thai else ko_steps[info_step - 1]
    if is_thai:
        return (
            f"ข้อมูลขั้นที่ {info_step}/3 · {info}\n\n"
            f"ด้านล่างมีคลินิกที่ให้บริการ {match['name']} ในราคา {price} ที่ {match['clinic_name']} "
            f"({match['district']}) ให้คุณเก็บไว้เปรียบเทียบได้"
        )
    return (
        f"정보 {info_step}/3 · {info}\n\n"
        f"아래에서 {match['district']}의 {match['clinic_name']}이 제공하는 "
        f"‘{match['name']}’({price})도 비교해 볼 수 있어요. 충분히 알아본 뒤 예약해도 괜찮아요."
    )


def suggested_questions(category, is_thai=False, info_step=1):
    """Returns two educational follow-ups and one clinic-discovery prompt."""
    if not category:
        return []
    info_step = max(1, min(int(info_step or 1), 3))
    questions = {
        "보톡스": (
            ("보톡스 효과는 보통 언제부터 느껴지나요?", "사각턱 보톡스가 저에게 맞는지 어떻게 알 수 있나요?"),
            ("보톡스 효과는 얼마나 유지되나요?", "시술 후 피해야 할 행동이 있나요?"),
            ("상담 전에 어떤 정보를 준비하면 좋나요?", "보톡스 시술 전 꼭 확인할 점은 무엇인가요?"),
        ),
        "필러": (
            ("필러는 부위별로 어떤 차이가 있나요?", "필러 종류는 어떻게 선택하나요?"),
            ("필러 효과는 얼마나 유지되나요?", "자연스러운 결과를 위해 무엇을 상담해야 하나요?"),
            ("필러 시술 전 꼭 확인할 점은 무엇인가요?", "시술 후 붓기는 보통 얼마나 가나요?"),
        ),
        "리프팅": (
            ("리프팅 장비마다 어떤 차이가 있나요?", "제 피부에는 어떤 리프팅이 맞을까요?"),
            ("리프팅 효과는 언제부터 보이나요?", "샷 수와 강도는 어떻게 정하나요?"),
            ("리프팅 전에 확인할 주의사항은 무엇인가요?", "시술 후 일상생활은 바로 가능한가요?"),
        ),
        "스킨부스터": (
            ("스킨부스터 제품마다 어떤 차이가 있나요?", "건조한 피부에도 도움이 될까요?"),
            ("몇 회 정도 받아야 하나요?", "효과는 얼마나 유지되나요?"),
            ("시술 후 엠보싱은 얼마나 가나요?", "상담 전에 알레르기 이력을 알려야 하나요?"),
        ),
        "레이저": (
            ("색소 레이저 종류는 어떻게 다른가요?", "제 피부 고민에는 어떤 레이저가 맞을까요?"),
            ("레이저는 몇 회 정도 받아야 하나요?", "시술 간격은 어떻게 정하나요?"),
            ("레이저 후 자외선 관리는 어떻게 하나요?", "민감한 피부가 확인할 점은 무엇인가요?"),
        ),
    }
    thai = {
        "보톡스": ("โบท็อกซ์เริ่มเห็นผลเมื่อไร?", "จะรู้ได้อย่างไรว่าโบท็อกซ์กรามเหมาะกับฉัน?"),
        "필러": ("ฟิลเลอร์แต่ละบริเวณต่างกันอย่างไร?", "ควรเลือกชนิดฟิลเลอร์อย่างไร?"),
        "리프팅": ("เครื่องยกกระชับแต่ละชนิดต่างกันอย่างไร?", "แบบไหนเหมาะกับสภาพผิวของฉัน?"),
        "스킨부스터": ("สกินบูสเตอร์แต่ละชนิดต่างกันอย่างไร?", "ต้องทำประมาณกี่ครั้ง?"),
        "레이저": ("เลเซอร์แต่ละชนิดต่างกันอย่างไร?", "เลเซอร์แบบไหนเหมาะกับปัญหาผิวของฉัน?"),
    }
    if is_thai:
        first, second = thai.get(category, ("หัตถการนี้ทำงานอย่างไร?", "ฉันควรเตรียมตัวอย่างไร?"))
        return [first, second, f"ต้องการค้นหาคลินิกใกล้ฉันที่ให้บริการ{category}ไหม?"]
    steps = questions.get(category, (((f"{category}은 어떤 시술인가요?", f"{category}이 저에게 맞을까요?"),) * 3))[info_step - 1]
    return [steps[0], steps[1], f"가까운 {category} 가능 클리닉을 찾아보고 싶으신가요?"]


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
    try:
        info_step = max(1, min(int(data.get("info_step", 1)), 3))
    except (TypeError, ValueError):
        info_step = 1
    if not message: return jsonify({"reply": "메시지를 입력해 주세요.", "profile": {}}), 400
    profile = save_chat_profile(user_id, message)
    query = profile.get("preferred_service") or ""
    is_thai = bool(re.search(r"[ก-๙]", message))
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
    reply = clinic_match_reply(query, services, is_thai=is_thai, info_step=info_step)
    if allow_ai:
        try:
            reply = beauty_ai_answer(message, profile, services, info_step=info_step) or reply
        except Exception:
            app.logger.exception("OpenAI beauty answer failed")
    lead = {"interested": bool(query), "category": query}
    if services:
        lead.update({"clinic_name": services[0]["clinic_name"], "service_name": services[0]["name"]})
    return jsonify({
        "reply": reply,
        "profile": profile,
        "services": services,
        "lead": lead,
        "info_step": info_step,
        "suggested_questions": suggested_questions(query, is_thai=is_thai, info_step=info_step),
    })


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
