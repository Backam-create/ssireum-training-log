"""
================================================================
백암초등학교 씨름부 훈련일지 자동 작성 프로그램 (v6 - Supabase)
- Flask + Groq API (Llama 3.3 70B)
- 데이터 저장: Supabase PostgreSQL (영구 저장, 서버 재시작 무관)
================================================================
"""

from flask import Flask, render_template, request, jsonify, send_file
from groq import Groq
from dotenv import load_dotenv
import zipfile
import io
import os
import re
import psycopg2
import psycopg2.extras
from datetime import datetime

load_dotenv()
app = Flask(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not GROQ_API_KEY:
    print("⚠️  경고: GROQ_API_KEY가 설정되지 않았습니다.")
if not DATABASE_URL:
    print("⚠️  경고: DATABASE_URL이 설정되지 않았습니다.")

client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.3-70b-versatile"
CUMULATIVE_HWPX = '씨름부_훈련일지_누적.hwpx'


# ==========================================
# DB 연결 및 기록 관리
# ==========================================
def get_db():
    """DB 연결 반환"""
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn


def load_records():
    """DB에서 모든 기록 불러오기 (sort_order 순서대로)"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM records ORDER BY sort_order ASC, created_at ASC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"⚠️ DB 읽기 실패: {e}")
        return []


def save_record(record):
    """기록 저장 (INSERT 또는 UPDATE)"""
    try:
        conn = get_db()
        cur = conn.cursor()
        # 현재 최대 sort_order 조회
        cur.execute("SELECT COALESCE(MAX(sort_order), -1) FROM records")
        max_order = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO records
                (id, date, place, total, attend, absent, absent_list,
                 training, notice, etc, created_at, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET
                date=EXCLUDED.date, place=EXCLUDED.place,
                total=EXCLUDED.total, attend=EXCLUDED.attend,
                absent=EXCLUDED.absent, absent_list=EXCLUDED.absent_list,
                training=EXCLUDED.training, notice=EXCLUDED.notice,
                etc=EXCLUDED.etc
        """, (
            record['id'], record['date'], record['place'],
            record['total'], record['attend'], record['absent'],
            record['absent_list'], record['training'],
            record['notice'], record['etc'],
            record['created_at'], max_order + 1
        ))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ DB 저장 실패: {e}")
        return False


def update_record(record_id, record):
    """기존 기록 수정 (sort_order 유지)"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE records SET
                date=%s, place=%s, total=%s, attend=%s, absent=%s,
                absent_list=%s, training=%s, notice=%s, etc=%s
            WHERE id=%s
        """, (
            record['date'], record['place'], record['total'],
            record['attend'], record['absent'], record['absent_list'],
            record['training'], record['notice'], record['etc'],
            record_id
        ))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ DB 수정 실패: {e}")
        return False


def delete_record_db(record_id):
    """기록 삭제"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM records WHERE id=%s", (record_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ DB 삭제 실패: {e}")
        return False


def reorder_records_db(new_order):
    """순서 변경 (id 배열 순서대로 sort_order 재할당)"""
    try:
        conn = get_db()
        cur = conn.cursor()
        for idx, record_id in enumerate(new_order):
            cur.execute(
                "UPDATE records SET sort_order=%s WHERE id=%s",
                (idx, record_id)
            )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ DB 순서변경 실패: {e}")
        return False


def sort_by_date_db():
    """날짜순 정렬 (이른 날짜가 먼저)"""
    records = load_records()

    def parse_date(r):
        m = re.match(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', r.get('date', ''))
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (9999, 99, 99)

    records.sort(key=parse_date)
    new_order = [r['id'] for r in records]
    return reorder_records_db(new_order)


# ==========================================
# 외국어 제거 및 중복 문장 처리
# ==========================================
def clean_korean_text(text):
    hanja_map = {
        '熟悉': '익숙', '練習': '연습', '訓練': '훈련', '學生': '학생',
        '指導': '지도', '安全': '안전', '健康': '건강', '改善': '개선',
        '計劃': '계획', '參加': '참가', '參席': '참석', '體力': '체력',
        '基礎': '기초', '技術': '기술',
    }
    for h, k in hanja_map.items():
        text = text.replace(h, k)
    text = re.sub(r'[\u4E00-\u9FFF\u3400-\u4DBF]+', '', text)
    foreign_map = {
        'mejorar': '개선', 'mejor': '향상', 'training': '훈련',
        'practice': '연습', 'student': '학생', 'exercise': '운동',
    }
    for f, k in foreign_map.items():
        text = re.sub(re.escape(f), k, text, flags=re.IGNORECASE)
    text = re.sub(r'\b[a-zA-Z]+\b', '', text)
    text = re.sub(r'[\u3040-\u309F\u30A0-\u30FF]+', '', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r' +([\.,])', r'\1', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    abstract_patterns = [
        r'다양한\s*훈련', r'여러\s*기술', r'전반적인\s*실력',
        r'전체적인\s*경기력', r'종합하면', r'적절히\s*결합',
        r'경기력\s*향상을\s*도모', r'실력을\s*향상시키고\s*있',
        r'체력\s*향상을\s*도모',
    ]
    filtered = [l for l in lines if not any(re.search(p, l) for p in abstract_patterns)]
    lines = filtered if filtered else lines

    def extract_keywords(line):
        cleaned = re.sub(r'^-\s*', '', line)
        words = re.findall(r'[가-힣]{2,}', cleaned)
        stopwords = {'실시', '진행', '통해', '위해', '강화', '단련', '훈련', '연습',
                     '반복', '하여', '교대', '세트', '반복하여', '실시함', '진행함'}
        stripped = []
        for w in words:
            w2 = re.sub(r'(을|를|와|과|으로|로|에서|에|이|가|는|은|의|도|함|하여)$', '', w)
            if len(w2) >= 2 and w2 not in stopwords:
                stripped.append(w2)
        return set(stripped)

    if len(lines) >= 2:
        unique = [lines[0]]
        for line in lines[1:]:
            kw_new = extract_keywords(line)
            is_dup = False
            for existing in unique:
                kw_ex = extract_keywords(existing)
                if kw_new and kw_ex:
                    overlap = len(kw_new & kw_ex) / max(len(kw_new), len(kw_ex))
                    if overlap >= 0.7:
                        is_dup = True
                        break
            if not is_dup:
                unique.append(line)
        lines = unique

    return '\n'.join(lines)


# ==========================================
# HWPX 파일 생성
# ==========================================
def xml_escape(text):
    if not text:
        return ''
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def build_paragraphs_xml(text, char_pr_id="7", para_pr_id="22"):
    if not text:
        return (
            f'<hp:p id="0" paraPrIDRef="{para_pr_id}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{char_pr_id}"><hp:t></hp:t></hp:run>'
            f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1200" '
            f'textheight="1200" baseline="1020" spacing="720" horzpos="0" '
            f'horzsize="41012" flags="393216"/></hp:linesegarray></hp:p>'
        )
    lines = text.split('\n')
    parts = []
    for i, line in enumerate(lines):
        parts.append(
            f'<hp:p id="{i}" paraPrIDRef="{para_pr_id}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{char_pr_id}">'
            f'<hp:t>{xml_escape(line)}</hp:t></hp:run>'
            f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1200" '
            f'textheight="1200" baseline="1020" spacing="720" horzpos="0" '
            f'horzsize="41012" flags="393216"/></hp:linesegarray></hp:p>'
        )
    return ''.join(parts)


def process_replacements(content_str, replacements):
    placeholder_to_align = {
        '[일시]': '20', '[훈련장소]': '20',
        '[총인원]': '20', '[참가인원]': '20', '[불참인원]': '20',
        '[불참자명단]': '22', '[훈련내용]': '22',
        '[전달사항]': '22', '[기타사항]': '22',
    }
    for placeholder, value in replacements.items():
        if placeholder not in content_str:
            continue
        if '\n' not in str(value):
            content_str = content_str.replace(placeholder, xml_escape(str(value)))
            continue
        para_pr_id = placeholder_to_align.get(placeholder, '22')
        pattern = re.compile(
            r'<hp:p\b[^>]*>(?:(?!</?hp:p\b).)*?'
            + re.escape(placeholder)
            + r'(?:(?!</?hp:p\b).)*?</hp:p>',
            re.DOTALL
        )
        new_paras = build_paragraphs_xml(str(value), "7", para_pr_id)
        content_str = pattern.sub(new_paras, content_str, count=1)
    return content_str


def fill_template(replacements):
    template_path = '양식.hwpx'
    memory_zip = io.BytesIO()
    with zipfile.ZipFile(template_path, 'r') as zin:
        with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                if item.filename.startswith('Contents/section'):
                    content_str = content.decode('utf-8')
                    content_str = process_replacements(content_str, replacements)
                    content = content_str.encode('utf-8')
                if item.filename == 'mimetype':
                    new_info = zipfile.ZipInfo(item.filename)
                    new_info.compress_type = zipfile.ZIP_STORED
                    zout.writestr(new_info, content)
                else:
                    zout.writestr(item, content)
    memory_zip.seek(0)
    return memory_zip


def make_replacements(record):
    return {
        '[일시]': record.get('date', ''),
        '[훈련장소]': record.get('place', ''),
        '[총인원]': record.get('total', ''),
        '[참가인원]': record.get('attend', ''),
        '[불참인원]': record.get('absent', ''),
        '[불참자명단]': record.get('absent_list', ''),
        '[훈련내용]': record.get('training', ''),
        '[전달사항]': record.get('notice', ''),
        '[기타사항]': record.get('etc', ''),
    }


# ==========================================
# 누적 일지 파일 생성
# ==========================================
def extract_table_paragraph(filled_section_xml):
    pattern = re.compile(
        r'<hp:p\b[^>]*>(?:(?!</hp:p>).)*?<hp:tbl\b.*?</hp:tbl>(?:(?!</hp:p>).)*?</hp:p>',
        re.DOTALL
    )
    m = pattern.search(filled_section_xml)
    return m.group(0) if m else None


def regenerate_cumulative_file():
    records = load_records()
    print(f"[누적] 기록 {len(records)}건으로 누적 파일 생성 시작")

    if not records:
        if os.path.exists(CUMULATIVE_HWPX):
            os.remove(CUMULATIVE_HWPX)
        return

    first_filled = fill_template(make_replacements(records[0]))
    first_filled.seek(0)
    with zipfile.ZipFile(first_filled) as z:
        base_section = z.read('Contents/section0.xml').decode('utf-8')
    print(f"[누적] 베이스: {records[0].get('date', '?')}")

    all_insertions = []
    for idx, record in enumerate(records[1:], start=2):
        filled = fill_template(make_replacements(record))
        filled.seek(0)
        with zipfile.ZipFile(filled) as z:
            sec_xml = z.read('Contents/section0.xml').decode('utf-8')

        table_paragraph = extract_table_paragraph(sec_xml)
        if not table_paragraph:
            print(f"[누적] ⚠️ {idx}번째 표 추출 실패: {record.get('date', '?')}")
            continue

        print(f"[누적] {idx}번째 추출 성공: {record.get('date', '?')}")

        title_para = (
            '<hp:p id="0" paraPrIDRef="20" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            '<hp:run charPrIDRef="9">'
            '<hp:t>운동부 훈련 일지</hp:t></hp:run>'
            '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1700" '
            'textheight="1700" baseline="1445" spacing="1020" '
            'horzpos="0" horzsize="51024" flags="393216"/>'
            '</hp:linesegarray></hp:p>'
        )
        spacer = (
            '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            '<hp:run charPrIDRef="0"><hp:t></hp:t></hp:run>'
            '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
            'textheight="1000" baseline="850" spacing="600" '
            'horzpos="0" horzsize="51024" flags="393216"/>'
            '</hp:linesegarray></hp:p>'
        )
        all_insertions.append(spacer + title_para + table_paragraph)

    if all_insertions:
        combined = ''.join(all_insertions)
        if '</hs:sec>' in base_section:
            new_section = base_section.replace('</hs:sec>', combined + '</hs:sec>', 1)
        else:
            new_section = base_section
    else:
        new_section = base_section

    memory_zip = io.BytesIO()
    with zipfile.ZipFile('양식.hwpx', 'r') as zin:
        with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                if item.filename == 'Contents/section0.xml':
                    content = new_section.encode('utf-8')
                if item.filename == 'mimetype':
                    new_info = zipfile.ZipInfo(item.filename)
                    new_info.compress_type = zipfile.ZIP_STORED
                    zout.writestr(new_info, content)
                else:
                    zout.writestr(item, content)

    memory_zip.seek(0)
    with open(CUMULATIVE_HWPX, 'wb') as f:
        f.write(memory_zip.read())

    tbl_count = len(re.findall(r'<hp:tbl\b', new_section))
    print(f"[누적] ✅ 완료: 표 {tbl_count}개")


# ==========================================
# 라우트
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    field = data.get('field', '')
    keywords = data.get('keywords', '')
    if not keywords:
        return jsonify({'error': '키워드를 입력해주세요.'}), 400

    field_prompts = {
        '훈련내용': """당신은 한국 초등학교 씨름부 지도교사입니다. 키워드로 훈련일지 '훈련내용'을 작성하세요.

[필수 규칙]
1. 오직 순수 한국어(한글)만 사용. 한자, 영어 등 외국 문자 절대 금지.
2. 분량은 정확히 3줄. (4줄 이상 금지)
3. 각 줄은 하이픈('-')으로 시작.
4. 문장 끝은 '~함', '~실시함', '~진행함' 중 하나로 통일.
5. 키워드 단어를 그대로 활용. 한 줄당 한 가지 구체적 활동만 기술.
6. 3줄이 모두 서로 다른 활동. 같은 활동을 다르게 표현하여 반복하는 것 절대 금지.
7. 추상적·총평 표현 절대 금지 (예: '전반적인 실력 향상', '도모함').
8. 부연 설명, 인사말 없이 본문만 출력.

[좋은 예시 1]
키워드: 스트레칭, 줄넘기, 빗당겨치기
출력:
- 전신 스트레칭으로 관절과 근육을 충분히 풀어줌.
- 줄넘기 5분 3세트로 심폐지구력을 강화함.
- 빗당겨치기 기술을 단계별로 분해하여 반복 연습함.

[좋은 예시 2]
키워드: 기초체력, 턱걸이, 팔굽혀펴기, 왕복오래달리기
출력:
- 턱걸이 10회 3세트로 상체 근력을 단련함.
- 팔굽혀펴기 15회 3세트로 근지구력을 강화함.
- 왕복오래달리기로 심폐지구력 훈련을 실시함.

[이번 키워드]
{keywords}

[출력]
""",
        '전달사항': """당신은 한국 초등학교 씨름부 지도교사입니다. 키워드로 '전달사항'을 작성하세요.

[필수 규칙]
1. 오직 순수 한국어(한글)만 사용. 외국 문자 절대 금지.
2. 분량은 정확히 1~3줄.
3. 각 줄은 하이픈('-')으로 시작.
4. 문장 끝은 '~함', '~안내함', '~당부함' 중 하나.
5. 부연 설명 없이 본문만 출력.

[예시]
키워드: 토요일 시합, 도시락
출력:
- 토요일 시합 일정과 집합 시간을 안내함.
- 점심 도시락은 각자 준비해 오도록 당부함.

[이번 키워드]
{keywords}

[출력]
""",
        '기타사항': """당신은 한국 초등학교 씨름부 지도교사입니다. 키워드로 '기타사항'을 작성하세요.

[필수 규칙]
1. 오직 순수 한국어(한글)만 사용. 외국 문자 절대 금지.
2. 분량은 정확히 1~3줄.
3. 각 줄은 하이픈('-')으로 시작.
4. 문장 끝은 '~함', '~확인함', '~조치함' 중 하나.
5. 학생 안전, 컨디션, 특이사항 중심.
6. 부연 설명 없이 본문만 출력.

[예시]
키워드: 김○○ 발목 통증
출력:
- 김○○ 선수의 우측 발목 경미한 통증을 확인함.
- 훈련 강도를 조절하고 냉찜질로 응급 조치함.

[이번 키워드]
{keywords}

[출력]
""",
    }

    prompt = field_prompts.get(field, field_prompts['훈련내용']).format(keywords=keywords)
    try:
        chat_completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "당신은 한국 초등학교 씨름부 지도교사입니다. 반드시 순수 한국어(한글)로만 답변하며, 한자나 영어 등 외국 문자는 절대 사용하지 않습니다."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1024, temperature=0.5,
        )
        result_text = chat_completion.choices[0].message.content.strip()
        result_text = clean_korean_text(result_text)
        return jsonify({'result': result_text})
    except Exception as e:
        return jsonify({'error': f'AI 호출 오류: {str(e)}'}), 500


@app.route('/save', methods=['POST'])
def save_only():
    data = request.json
    if not os.path.exists('양식.hwpx'):
        return jsonify({'error': '서버에 양식.hwpx 파일이 없습니다.'}), 404

    update_id = data.get('update_id')
    now = datetime.now().isoformat()

    if update_id:
        record = {
            'date': data.get('date', ''), 'place': data.get('place', ''),
            'total': data.get('total', ''), 'attend': data.get('attend', ''),
            'absent': data.get('absent', ''), 'absent_list': data.get('absent_list', ''),
            'training': data.get('training', ''), 'notice': data.get('notice', ''),
            'etc': data.get('etc', ''),
        }
        update_record(update_id, record)
        record_id = update_id
    else:
        record = {
            'id': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'date': data.get('date', ''), 'place': data.get('place', ''),
            'total': data.get('total', ''), 'attend': data.get('attend', ''),
            'absent': data.get('absent', ''), 'absent_list': data.get('absent_list', ''),
            'training': data.get('training', ''), 'notice': data.get('notice', ''),
            'etc': data.get('etc', ''), 'created_at': now,
        }
        save_record(record)
        record_id = record['id']

    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")

    records = load_records()
    return jsonify({'success': True, 'id': record_id, 'count': len(records)})


@app.route('/records', methods=['GET'])
def list_records():
    records = load_records()
    summary = [{'id': r.get('id'), 'date': r.get('date', ''),
                'place': r.get('place', ''), 'created_at': r.get('created_at', '')}
               for r in records]
    return jsonify({'records': summary, 'count': len(summary)})


@app.route('/records/<record_id>', methods=['GET'])
def get_record(record_id):
    records = load_records()
    for r in records:
        if r.get('id') == record_id:
            return jsonify(r)
    return jsonify({'error': '기록을 찾을 수 없습니다.'}), 404


@app.route('/records/<record_id>', methods=['DELETE'])
def delete_record(record_id):
    delete_record_db(record_id)
    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
    records = load_records()
    return jsonify({'success': True, 'remaining': len(records)})


@app.route('/records/reorder', methods=['POST'])
def reorder_records():
    data = request.json
    new_order = data.get('order', [])
    if not new_order:
        return jsonify({'error': '순서 데이터가 없습니다.'}), 400
    reorder_records_db(new_order)
    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
    return jsonify({'success': True})


@app.route('/records/sort_by_date', methods=['POST'])
def sort_records_by_date():
    sort_by_date_db()
    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
    return jsonify({'success': True})


@app.route('/download_cumulative', methods=['GET'])
def download_cumulative():
    if not os.path.exists(CUMULATIVE_HWPX):
        try:
            regenerate_cumulative_file()
        except Exception:
            pass
    if not os.path.exists(CUMULATIVE_HWPX):
        return jsonify({'error': '아직 작성된 일지가 없습니다.'}), 404
    return send_file(CUMULATIVE_HWPX, as_attachment=True,
                     download_name='씨름부_훈련일지_누적.hwpx',
                     mimetype="application/hwp+zip")


@app.route('/download_record/<record_id>', methods=['GET'])
def download_single_record(record_id):
    records = load_records()
    for r in records:
        if r.get('id') == record_id:
            memory_zip = fill_template(make_replacements(r))
            memory_zip.seek(0)
            date_str = r.get('date', '훈련일지').replace('/', '-').replace(' ', '_').replace('(', '').replace(')', '')
            return send_file(memory_zip, as_attachment=True,
                             download_name=f"{date_str}_씨름부_훈련일지.hwpx",
                             mimetype="application/hwp+zip")
    return jsonify({'error': '기록을 찾을 수 없습니다.'}), 404


if __name__ == '__main__':
    print("=" * 60)
    print("🤼 백암초 씨름부 훈련일지 자동 작성 프로그램 (v6)")
    print("=" * 60)
    print("👉 http://localhost:5002")
    print("=" * 60)
    app.run(port=5002, debug=True)
