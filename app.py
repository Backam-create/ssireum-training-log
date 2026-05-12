"""
================================================================
백암초등학교 씨름부 훈련일지 자동 작성 프로그램 (v5)
- Flask + Groq API (Llama 3.3 70B)
- 외국어 자동 제거, 누적일지 버그 수정, 날짜picker, 자동계산
================================================================
"""

from flask import Flask, render_template, request, jsonify, send_file
from groq import Groq
from dotenv import load_dotenv
import zipfile
import io
import os
import re
import json
from datetime import datetime

load_dotenv()
app = Flask(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    print("⚠️  경고: .env 파일에 GROQ_API_KEY가 설정되지 않았습니다.")

client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.3-70b-versatile"

DATA_DIR = 'data'
os.makedirs(DATA_DIR, exist_ok=True)
RECORDS_PATH = os.path.join(DATA_DIR, 'records.json')
CUMULATIVE_HWPX = '씨름부_훈련일지_누적.hwpx'


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
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    abstract_patterns = [
        r'다양한\s*훈련', r'여러\s*기술', r'전반적인\s*실력',
        r'전체적인\s*경기력', r'종합하면', r'적절히\s*결합',
        r'경기력\s*향상을\s*도모', r'실력을\s*향상시키고\s*있',
        r'체력\s*향상을\s*도모',
    ]
    filtered = [l for l in lines if not any(re.search(p, l) for p in abstract_patterns)]
    lines = filtered if filtered else lines
    
    # 의미적으로 유사한 중복 문장 제거
    def extract_keywords(line):
        cleaned = re.sub(r'^-\s*', '', line)
        words = re.findall(r'[가-힣]{2,}', cleaned)
        # 흔한 서술어/조사/접미사 제거
        stopwords = {'실시', '진행', '통해', '위해', '강화', '단련', '훈련', '연습',
                     '반복', '하여', '교대', '세트', '반복하여', '실시함', '진행함'}
        # 조사 제거: 끝의 을/를/와/과/으로/로/에/이/가/는/은/의/도 제거
        stripped = []
        for w in words:
            w2 = re.sub(r'(을|를|와|과|으로|로|에서|에|이|가|는|은|의|도|함|하여)$', '', w)
            if len(w2) >= 2 and w2 not in stopwords:
                stripped.append(w2)
        return set(stripped)
    
    if len(lines) >= 3:
        unique_lines = [lines[0]]
        for line in lines[1:]:
            is_dup = False
            kw_new = extract_keywords(line)
            for existing in unique_lines:
                kw_existing = extract_keywords(existing)
                if kw_new and kw_existing:
                    overlap = len(kw_new & kw_existing) / max(len(kw_new), len(kw_existing))
                    if overlap >= 0.7:
                        is_dup = True
                        break
            if not is_dup:
                unique_lines.append(line)
        lines = unique_lines
    
    return '\n'.join(lines)


def load_records():
    if not os.path.exists(RECORDS_PATH):
        return []
    try:
        with open(RECORDS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_records(records):
    with open(RECORDS_PATH, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    try:
        backup_dir = os.path.join(DATA_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        today_str = datetime.now().strftime('%Y%m%d')
        backup_path = os.path.join(backup_dir, f'records_{today_str}.json')
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        backups = sorted([f for f in os.listdir(backup_dir) if f.startswith('records_')])
        if len(backups) > 60:
            for old in backups[:-60]:
                try:
                    os.remove(os.path.join(backup_dir, old))
                except Exception:
                    pass
    except Exception as e:
        print(f"⚠️ 백업 실패: {e}")


# ==========================================
# HWPX 핵심 함수들
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
# 누적 일지 생성 (v5 - 완전 재작성)
# ==========================================
def regenerate_cumulative_file():
    """모든 기록을 하나의 hwpx 파일로 합침."""
    records = load_records()
    print(f"[누적] 기록 {len(records)}건으로 누적 파일 생성 시작")

    if not records:
        if os.path.exists(CUMULATIVE_HWPX):
            os.remove(CUMULATIVE_HWPX)
        print("[누적] 기록 없음 → 누적 파일 삭제")
        return

    # 1) 첫 번째 일지로 베이스 생성
    first_filled = fill_template(make_replacements(records[0]))
    first_filled.seek(0)
    with zipfile.ZipFile(first_filled) as z:
        base_section = z.read('Contents/section0.xml').decode('utf-8')
    print(f"[누적] 베이스(1번째 일지) 생성 완료: {records[0].get('date', '?')}")

    # 2) 두 번째 일지부터 표 추출 → 페이지 나눔과 함께 이어붙이기
    all_insertions = []
    for idx, record in enumerate(records[1:], start=2):
        filled = fill_template(make_replacements(record))
        filled.seek(0)
        with zipfile.ZipFile(filled) as z:
            sec_xml = z.read('Contents/section0.xml').decode('utf-8')

        # 표를 포함한 단락(<hp:p>...<hp:tbl>...</hp:tbl>...</hp:p>) 추출
        tbl_para_pattern = re.compile(
            r'<hp:p\b[^>]*>(?:(?!</hp:p>).)*?<hp:tbl\b.*?</hp:tbl>(?:(?!</hp:p>).)*?</hp:p>',
            re.DOTALL
        )
        m = tbl_para_pattern.search(sec_xml)
        if not m:
            print(f"[누적] ⚠️ {idx}번째 일지 표 추출 실패: {record.get('date', '?')}")
            continue

        table_paragraph = m.group(0)
        print(f"[누적] {idx}번째 일지 표 추출 성공: {record.get('date', '?')} ({len(table_paragraph)}자)")

        # 일지 사이 여백 + 제목 + 표
        # "운동부 훈련 일지" 제목 단락 (charPrIDRef="9"=제목폰트, paraPrIDRef="20"=가운데정렬)
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
        # 빈 줄 여백 단락
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

    # 3) 베이스 section의 </hs:sec> 직전에 삽입
    if all_insertions:
        combined = ''.join(all_insertions)
        if '</hs:sec>' in base_section:
            new_section = base_section.replace('</hs:sec>', combined + '</hs:sec>', 1)
            print(f"[누적] 삽입 완료: {len(all_insertions)}건 추가 ({len(combined)}자)")
        else:
            print("[누적] ⚠️ '</hs:sec>' 태그를 찾을 수 없습니다!")
            new_section = base_section
    else:
        new_section = base_section

    # 4) hwpx 파일 작성
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

    # 5) 검증
    with zipfile.ZipFile(CUMULATIVE_HWPX) as z:
        verify_sec = z.read('Contents/section0.xml').decode('utf-8')
    tbl_count = len(re.findall(r'<hp:tbl\b', verify_sec))
    print(f"[누적] ✅ 파일 저장 완료: {CUMULATIVE_HWPX} (표 {tbl_count}개, 기대 {len(records)}개)")
    if tbl_count != len(records):
        print(f"[누적] ⚠️ 표 개수 불일치! 기대 {len(records)}개인데 {tbl_count}개")


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
6. **3줄이 모두 서로 다른 활동이어야 함. 같은 활동을 다르게 표현하여 반복하는 것 절대 금지.**
   금지 예시: 1줄 "턱걸이와 팔굽혀펴기를 교대로 반복함" + 3줄 "팔굽혀펴기와 턱걸이를 세트로 반복함" → 사실상 같은 내용
7. **추상적·총평 표현 절대 금지**:
   금지: '전반적인 실력을 향상', '체력을 키움', '도모함' 등
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
- 턱걸이 10회 3세트로 상체 근력을 강화함.
- 팔굽혀펴기 15회 3세트로 근지구력을 단련함.
- 왕복오래달리기로 심폐지구력 훈련을 실시함.

[좋은 예시 3]
키워드: 배밀기, 안다리걸기, 밭다리걸기, 스트레칭, 기초근력
출력:
- 준비 스트레칭으로 전신 근육과 관절을 풀어줌.
- 배밀기와 안다리걸기, 밭다리걸기 기술을 단계별로 반복 연습함.
- 기초근력훈련으로 팔굽혀펴기와 윗몸일으키기를 실시함.

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

    record = {
        'id': datetime.now().strftime('%Y%m%d_%H%M%S'),
        'date': data.get('date', ''),
        'place': data.get('place', ''),
        'total': data.get('total', ''),
        'attend': data.get('attend', ''),
        'absent': data.get('absent', ''),
        'absent_list': data.get('absent_list', ''),
        'training': data.get('training', ''),
        'notice': data.get('notice', ''),
        'etc': data.get('etc', ''),
        'created_at': datetime.now().isoformat(),
    }

    update_id = data.get('update_id')
    records = load_records()

    if update_id:
        updated = False
        for i, r in enumerate(records):
            if r.get('id') == update_id:
                record['id'] = update_id
                record['created_at'] = r.get('created_at', record['created_at'])
                records[i] = record
                updated = True
                break
        if not updated:
            records.append(record)
    else:
        records.append(record)

    save_records(records)

    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
        import traceback
        traceback.print_exc()

    return jsonify({'success': True, 'id': record['id'], 'count': len(records)})


@app.route('/download_hwpx', methods=['POST'])
def download_hwpx():
    data = request.json
    if not os.path.exists('양식.hwpx'):
        return jsonify({'error': '서버에 양식.hwpx 파일이 없습니다.'}), 404
    replacements = make_replacements(data)
    memory_zip = fill_template(replacements)
    memory_zip.seek(0)
    date_str = data.get('date', '훈련일지').replace('/', '-').replace(' ', '_').replace('(', '').replace(')', '')
    return send_file(memory_zip, as_attachment=True,
                     download_name=f"{date_str}_씨름부_훈련일지.hwpx",
                     mimetype="application/hwp+zip")


@app.route('/records', methods=['GET'])
def list_records():
    records = load_records()
    # 저장된 순서 그대로 반환 (사용자가 드래그/날짜순 정렬한 순서 유지)
    summary = [{'id': r.get('id'), 'date': r.get('date', ''), 'place': r.get('place', ''),
                'created_at': r.get('created_at', '')} for r in records]
    return jsonify({'records': summary, 'count': len(summary)})


@app.route('/records/<record_id>', methods=['GET'])
def get_record(record_id):
    for r in load_records():
        if r.get('id') == record_id:
            return jsonify(r)
    return jsonify({'error': '기록을 찾을 수 없습니다.'}), 404


@app.route('/records/<record_id>', methods=['DELETE'])
def delete_record(record_id):
    records = load_records()
    new_records = [r for r in records if r.get('id') != record_id]
    save_records(new_records)
    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
    return jsonify({'success': True, 'remaining': len(new_records)})


@app.route('/download_cumulative', methods=['GET'])
def download_cumulative():
    if not os.path.exists(CUMULATIVE_HWPX):
        # 혹시 파일이 없으면 지금 바로 생성 시도
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
    for r in load_records():
        if r.get('id') == record_id:
            replacements = make_replacements(r)
            memory_zip = fill_template(replacements)
            memory_zip.seek(0)
            date_str = r.get('date', '훈련일지').replace('/', '-').replace(' ', '_').replace('(', '').replace(')', '')
            return send_file(memory_zip, as_attachment=True,
                             download_name=f"{date_str}_씨름부_훈련일지.hwpx",
                             mimetype="application/hwp+zip")
    return jsonify({'error': '기록을 찾을 수 없습니다.'}), 404


@app.route('/records/reorder', methods=['POST'])
def reorder_records():
    """사이드바에서 드래그로 순서 변경 시 호출"""
    data = request.json
    new_order = data.get('order', [])  # [id1, id2, id3, ...]
    if not new_order:
        return jsonify({'error': '순서 데이터가 없습니다.'}), 400

    records = load_records()
    record_map = {r.get('id'): r for r in records}

    reordered = []
    for rid in new_order:
        if rid in record_map:
            reordered.append(record_map[rid])
            del record_map[rid]
    # 혹시 누락된 기록이 있으면 뒤에 추가
    for r in record_map.values():
        reordered.append(r)

    save_records(reordered)
    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
    return jsonify({'success': True, 'count': len(reordered)})


@app.route('/records/sort_by_date', methods=['POST'])
def sort_records_by_date():
    """날짜순 정렬 (이른 날짜가 먼저)"""
    records = load_records()

    def parse_date(record):
        """'2026년 5월 13일 (수)' → 정렬 가능한 값"""
        date_str = record.get('date', '')
        m = re.match(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', date_str)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (9999, 99, 99)  # 파싱 실패 시 맨 뒤로

    records.sort(key=parse_date)
    save_records(records)
    try:
        regenerate_cumulative_file()
    except Exception as e:
        print(f"⚠️ 누적 파일 갱신 실패: {e}")
    return jsonify({'success': True, 'count': len(records)})


if __name__ == '__main__':
    print("=" * 60)
    print("🤼 백암초 씨름부 훈련일지 자동 작성 프로그램 (v5)")
    print("=" * 60)
    print(f"📁 누적 일지: {os.path.abspath(CUMULATIVE_HWPX)}")
    print(f"📁 기록 데이터: {os.path.abspath(RECORDS_PATH)}")
    print(f"📁 자동 백업: {os.path.abspath(os.path.join(DATA_DIR, 'backups'))}")
    print("=" * 60)
    print("⚠️  중요: data 폴더를 절대 삭제하지 마세요!")
    print("=" * 60)
    print("👉 http://localhost:5002")
    print("=" * 60)
    app.run(port=5002, debug=True)
