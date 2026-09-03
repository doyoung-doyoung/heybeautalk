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


def beauty_ai_answer(message, profile, services, info_step=1, is_thai=False):
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
    answer_language = "태국어" if is_thai else "한국어"
    instructions = (
        f"당신은 HeyBeauty의 뷰티 정보 안내 AI입니다. 반드시 사용자의 질문에 직접 답하고 {answer_language}로만 답하세요. "
        "ChatGPT에서 뷰티 정보를 묻는 것처럼 핵심 개념, 작용 방식, 대표적인 활용을 충분히 설명하세요. "
        "질문이 부작용·유지 기간·시술 부위 등 특정 항목이면 그 항목을 중심으로 답하세요. "
        "답변은 읽기 좋은 2~3개 문단으로 작성하고, 사용자가 묻지 않은 클리닉 홍보나 예약 유도는 본문에 넣지 마세요. "
        "진단·처방·효과 보장은 하지 말고, 임신·수유·질환·복용약 또는 개인 피부 상태는 "
        "의료진 상담이 필요하다고 짧게 안내하세요. 효과를 보장하거나 진단·처방하지 마세요.\n\n"
        f"사용자 관심 시술: {profile.get('preferred_service') or '미확인'}\n"
        f"참고용 서비스 카탈로그(사용자가 클리닉을 물을 때만 활용): {catalog}"
    )
    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=safe_message,
        max_output_tokens=350,
        store=False,
    )
    return response.output_text.strip()


def fallback_beauty_answer(category, message="", services=None, is_thai=False):
    """Provides a useful, language-matched answer when generative AI is unavailable."""
    lowered = message.lower()
    asks_clinic = any(term in lowered for term in ("클리닉", "병원", "예약", "คลินิก", "จอง"))
    asks_side_effects = any(term in lowered for term in ("부작용", "주의", "ผลข้างเคียง", "อันตราย"))
    asks_areas = any(term in lowered for term in ("어디", "부위", "บริเวณไหน", "ฉีดที่ไหน"))
    if asks_clinic and services:
        choices = []
        for item in services:
            price = f"฿{item['price']:,}" if item.get("currency") == "THB" else f"{item['price']:,}원"
            choices.append(f"{item['clinic_name']} · {item['district']} · {item['name']} · {price}")
        heading = "ใกล้คุณมีคลินิกที่ให้บริการดังต่อไปนี้" if is_thai else "현재 확인할 수 있는 가까운 클리닉이에요."
        ending = "เลือกดูเวลาว่างและส่งคำขอจองได้จากการ์ดด้านล่าง" if is_thai else "아래 카드에서 예약 가능 시간을 확인할 수 있어요."
        return f"{heading}\n\n" + "\n".join(f"{i + 1}. {choice}" for i, choice in enumerate(choices)) + f"\n\n{ending}"
    detail_answers = {
        "보톡스": {
            "areas": (
                "보톡스는 이마·미간·눈가·콧등처럼 표정 때문에 생기는 주름 부위와 턱·승모근·종아리처럼 발달한 근육의 부피를 줄이는 목적으로 사용돼요. 다한증 관리를 위해 겨드랑이·손·발 등에 사용되기도 합니다. 부위마다 필요한 용량과 주입 위치가 달라 의료진의 해부학적 평가가 중요합니다.",
                "โบท็อกซ์ฉีดได้หลายบริเวณ เช่น หน้าผาก ระหว่างคิ้ว หางตา และสันจมูก เพื่อลดริ้วรอยจากการแสดงสีหน้า รวมถึงกราม บ่า หรือน่องเพื่อลดการทำงานของกล้ามเนื้อที่เด่น นอกจากนี้ยังใช้บริเวณรักแร้ มือ หรือเท้าเพื่อลดเหงื่อได้ ปริมาณและตำแหน่งฉีดต่างกันในแต่ละบริเวณ จึงควรให้แพทย์ประเมินก่อน",
            ),
            "side_effects": (
                "보톡스 후에는 주사 부위의 통증·붓기·멍·두통이 일시적으로 나타날 수 있어요. 위치나 용량이 적절하지 않으면 눈꺼풀 처짐, 표정 비대칭, 씹는 힘의 변화처럼 주변 근육에 영향을 줄 수 있습니다. 대부분 일시적이지만 호흡·삼킴 곤란이나 심한 알레르기 반응이 나타나면 즉시 진료를 받아야 합니다.",
                "ผลข้างเคียงที่พบได้หลังฉีดโบท็อกซ์ ได้แก่ ปวด บวม ช้ำบริเวณเข็ม หรือปวดศีรษะชั่วคราว หากตำแหน่งหรือปริมาณไม่เหมาะสม อาจเกิดหนังตาตก สีหน้าไม่สมมาตร หรือแรงเคี้ยวเปลี่ยนไปได้ ส่วนใหญ่มักเป็นชั่วคราว แต่หากหายใจหรือกลืนลำบาก หรือมีอาการแพ้รุนแรง ควรพบแพทย์ทันที",
            ),
        }
    }
    intent = "side_effects" if asks_side_effects else "areas" if asks_areas else None
    if intent and category in detail_answers:
        ko, th = detail_answers[category][intent]
        return th if is_thai else ko

    answers = {
        "보톡스": (
            "보톡스(Botox)는 보툴리눔 톡신을 적절한 양으로 주입해 해당 부위 근육의 움직임을 일정 기간 줄이는 시술이에요. 표정근의 반복적인 움직임을 완화해 이마·미간·눈가 같은 표정 주름을 개선하거나, 발달한 턱 근육의 부피를 줄여 얼굴선을 정리하는 데 사용됩니다.\n\n"
            "미용 목적 외에도 다한증이나 일부 근육 질환 등의 치료에 사용됩니다. 효과는 영구적이지 않고 시간이 지나면서 서서히 감소하며, 시술 부위와 용량에 따라 결과가 달라질 수 있어 자격을 갖춘 의료진의 평가와 시술이 중요합니다.",
            "โบท็อกซ์ (Botox) คือสารโบทูลินัมท็อกซิน (Botulinum Toxin) ที่ใช้ในปริมาณเหมาะสมเพื่อลดการทำงานของกล้ามเนื้อบริเวณที่ฉีดชั่วคราว ในด้านความงามนิยมใช้ลดริ้วรอยจากการแสดงสีหน้า เช่น หน้าผาก รอยขมวดคิ้ว และรอยตีนกา รวมถึงช่วยให้กรามดูเรียวขึ้นในผู้ที่มีกล้ามเนื้อกรามใหญ่\n\n"
            "นอกจากนี้ยังใช้ทางการแพทย์ เช่น ลดเหงื่อมากผิดปกติหรือรักษาภาวะกล้ามเนื้อบางชนิด ผลไม่อยู่ถาวรและจะค่อย ๆ ลดลงตามเวลา ตำแหน่งและปริมาณที่ฉีดมีผลต่อทั้งผลลัพธ์และความปลอดภัย จึงควรได้รับการประเมินและฉีดโดยแพทย์หรือผู้ประกอบวิชาชีพที่ได้รับอนุญาต",
        ),
        "필러": (
            "필러는 히알루론산 등의 물질을 주입해 꺼진 부위의 볼륨을 보완하거나 얼굴 윤곽을 다듬는 시술이에요. 입술, 볼, 팔자주름, 턱 끝 등 다양한 부위에 사용되며 제품의 성질과 주입 깊이는 목적에 따라 달라집니다.\n\n효과는 제품과 부위에 따라 일정 기간 유지된 뒤 서서히 감소합니다. 얼굴 비율과 기존 시술 이력을 함께 평가해야 하며, 혈관 관련 합병증을 포함한 위험이 있어 숙련된 의료진에게 받는 것이 중요합니다.",
            "ฟิลเลอร์คือสารเติมเต็มที่ฉีดเพื่อเพิ่มวอลลุ่มหรือปรับรูปหน้า โดยมักใช้กรดไฮยาลูโรนิก บริเวณที่นิยม ได้แก่ ริมฝีปาก แก้ม ร่องแก้ม และคาง ชนิดของผลิตภัณฑ์และระดับความลึกในการฉีดจะแตกต่างกันตามเป้าหมาย\n\nผลลัพธ์ไม่ถาวรและจะค่อย ๆ ลดลงตามชนิดผลิตภัณฑ์และตำแหน่ง ควรให้แพทย์ประเมินสัดส่วนใบหน้าและประวัติการฉีดเดิม เพราะมีความเสี่ยงรวมถึงภาวะแทรกซ้อนเกี่ยวกับหลอดเลือด",
        ),
        "리프팅": (
            "리프팅은 초음파·고주파 같은 에너지를 피부층에 전달해 탄력과 처짐 개선을 돕는 시술이에요. 장비마다 에너지가 도달하는 깊이와 작용 방식이 달라 피부 두께, 지방량, 처짐 정도에 맞춰 선택합니다.\n\n결과는 개인 상태와 장비·강도에 따라 다르며 즉시 느껴지는 변화와 시간이 지나며 나타나는 변화가 함께 있을 수 있습니다. 의료진의 상태 평가 후 적절한 장비와 에너지 수준을 정하는 것이 중요합니다.",
            "การยกกระชับเป็นหัตถการที่ใช้พลังงาน เช่น อัลตราซาวด์หรือคลื่นวิทยุ ส่งลงสู่ชั้นผิวเพื่อช่วยเรื่องความกระชับและความหย่อนคล้อย เครื่องแต่ละชนิดทำงานในระดับความลึกต่างกัน จึงต้องเลือกตามความหนาผิว ปริมาณไขมัน และระดับความหย่อนคล้อย\n\nผลลัพธ์แตกต่างกันในแต่ละคนและอาจมีทั้งการเปลี่ยนแปลงทันทีและค่อย ๆ ชัดขึ้น ควรให้แพทย์ประเมินเพื่อเลือกเครื่องและระดับพลังงานที่เหมาะสม",
        ),
        "스킨부스터": (
            "스킨부스터는 히알루론산이나 피부 개선 성분을 피부에 주입해 수분감, 피부결, 잔주름 개선을 돕는 시술이에요. 제품마다 성분과 목적이 달라 현재 피부 고민에 맞춰 선택합니다.\n\n필요 횟수와 유지 기간은 제품과 피부 상태에 따라 달라질 수 있습니다. 주입 후 일시적인 엠보싱, 붓기, 멍이 생길 수 있어 피부 상태와 알레르기 이력을 의료진에게 알려야 합니다.",
            "สกินบูสเตอร์คือการฉีดกรดไฮยาลูโรนิกหรือสารบำรุงเข้าสู่ผิวเพื่อช่วยเพิ่มความชุ่มชื้น ปรับผิวสัมผัส และลดริ้วรอยเล็ก ๆ ผลิตภัณฑ์แต่ละชนิดมีส่วนประกอบและเป้าหมายต่างกัน จึงเลือกตามปัญหาผิว\n\nจำนวนครั้งและระยะเวลาของผลลัพธ์ขึ้นกับผลิตภัณฑ์และสภาพผิว หลังฉีดอาจมีตุ่มนูน บวม หรือรอยช้ำชั่วคราว ควรแจ้งแพทย์เกี่ยวกับสภาพผิวและประวัติการแพ้",
        ),
        "레이저": (
            "피부 레이저는 특정 파장의 빛 에너지를 이용해 색소, 혈관, 흉터 또는 피부결 같은 고민을 개선하는 시술이에요. 치료 목표에 따라 사용하는 파장과 장비, 에너지 강도가 달라집니다.\n\n겉으로 비슷해 보이는 색소도 원인이 다를 수 있으므로 정확한 피부 평가가 먼저 필요합니다. 시술 후에는 자외선 차단과 보습이 중요하고, 피부 타입에 따라 붉음이나 색소 변화 가능성을 고려해야 합니다.",
            "เลเซอร์ผิวหนังใช้พลังงานแสงในช่วงคลื่นเฉพาะเพื่อดูแลปัญหา เช่น เม็ดสี เส้นเลือด รอยสิว หรือผิวสัมผัส เป้าหมายแต่ละแบบต้องใช้ชนิดเครื่อง ความยาวคลื่น และระดับพลังงานต่างกัน\n\nรอยสีที่ดูคล้ายกันอาจมีสาเหตุต่างกัน จึงควรประเมินผิวก่อน หลังทำควรป้องกันแดดและเพิ่มความชุ่มชื้น รวมถึงพิจารณาความเสี่ยงเรื่องรอยแดงหรือสีผิวเปลี่ยนตามสภาพผิวแต่ละคน",
        ),
    }
    ko, th = answers.get(category, ("궁금한 시술명이나 피부 고민을 조금 더 구체적으로 알려주세요.", "กรุณาระบุชื่อหัตถการหรือปัญหาผิวที่ต้องการทราบเพิ่มเติม"))
    return th if is_thai else ko


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
        "보톡스": ("ฉีดโบท็อกซ์บริเวณไหนได้บ้าง?", "โบท็อกซ์มีผลข้างเคียงอะไรบ้าง?", "คลินิกที่รับฉีดโบท็อกซ์ใกล้ฉันมีที่ไหนบ้าง?"),
        "필러": ("ฉีดฟิลเลอร์บริเวณไหนได้บ้าง?", "ฟิลเลอร์มีผลข้างเคียงอะไรบ้าง?", "คลินิกที่รับฉีดฟิลเลอร์ใกล้ฉันมีที่ไหนบ้าง?"),
        "리프팅": ("เครื่องยกกระชับแต่ละชนิดต่างกันอย่างไร?", "การยกกระชับมีผลข้างเคียงอะไรบ้าง?", "คลินิกยกกระชับใกล้ฉันมีที่ไหนบ้าง?"),
        "스킨부스터": ("สกินบูสเตอร์แต่ละชนิดต่างกันอย่างไร?", "สกินบูสเตอร์มีผลข้างเคียงอะไรบ้าง?", "คลินิกสกินบูสเตอร์ใกล้ฉันมีที่ไหนบ้าง?"),
        "레이저": ("เลเซอร์แต่ละชนิดต่างกันอย่างไร?", "เลเซอร์มีผลข้างเคียงอะไรบ้าง?", "คลินิกเลเซอร์ใกล้ฉันมีที่ไหนบ้าง?"),
    }
    if is_thai:
        return list(thai.get(category, ("หัตถการนี้ทำงานอย่างไร?", "มีผลข้างเคียงอะไรบ้าง?", "มีคลินิกใกล้ฉันไหม?")))
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
    categories = {"보톡스": ["보톡스", "사각턱", "주름", "โบท็อกซ์", "โบท๊อก", "โบทอก", "โบกราม"], "필러": ["필러", "입술", "볼륨", "ฟิลเลอร์"], "리프팅": ["리프팅", "슈링크", "처짐", "ยกกระชับ"], "스킨부스터": ["스킨부스터", "물광", "건조", "ผิวแห้ง", "สกินบูสเตอร์"], "레이저": ["레이저", "피코", "색소", "흉터", "여드름", "เลเซอร์", "ฝ้า", "สิว"]}
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
    reply = fallback_beauty_answer(query, message=message, services=services, is_thai=is_thai)
    if allow_ai:
        try:
            reply = beauty_ai_answer(message, profile, services, info_step=info_step, is_thai=is_thai) or reply
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
