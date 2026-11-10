"""Resume Upstage jobs across short server requests; keep PDFs in the browser."""
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

MAX_SECONDS = 20 * 60


def result_urls(value):
    if isinstance(value, dict):
        for item in value.values(): yield from result_urls(item)
    elif isinstance(value, list):
        for item in value: yield from result_urls(item)
    elif isinstance(value, str) and re.fullmatch(r'/runs/[0-9a-f]{32}/(input|mid|low)\.pdf', value):
        yield value


def encode(state):
    body = base64.urlsafe_b64encode(json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    signature = hmac.new(os.environ['UPSTAGE_API_KEY'].encode(), ('im-jobs-v1:' + body).encode(), hashlib.sha256).hexdigest()
    return body + '.' + signature


def decode(ticket):
    try:
        if len(ticket) > 200_000: raise ValueError()
        body, signature = ticket.rsplit('.', 1)
        expected = hmac.new(os.environ['UPSTAGE_API_KEY'].encode(), ('im-jobs-v1:' + body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected): raise ValueError()
        return json.loads(base64.urlsafe_b64decode(body))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, '분석 작업 정보를 확인할 수 없습니다. PDF를 다시 선택해 주세요.')


def reply(state, requires_file=False):
    step = {'split': 0, 'prepare': 0, 'grade': 1, 'mask': 2, 'deliver': 2}[state['phase']]
    return {'ticket': encode(state), 'step': step, 'requiresFile': requires_file}


def cleanup(service, state):
    for item in [state['split']] + state.get('docs', []):
        for project, fid in item.get('files', {}).items():
            service.drop_file(project, fid)


def ensure(service, item, agents, data, filename):
    for project in {service.scope(agent) for agent in agents}:
        if project not in item['files']:
            item['files'][project] = service.client(project).files.create(file=(filename, data), purpose='user_data').id


def launch(service, agent, files):
    project = service.scope(agent)
    extra = {'config_id': service.CONFIG[agent]} if service.CONFIG.get(agent) else None
    response = service.client(project).responses.create(model=agent, include=['all'], input=[{'role': 'user', 'content': [{'type': 'input_file', 'file_id': files[project]}]}], extra_body=extra)
    return {'project': project, 'id': response.id}


def retrieve(service, job):
    response = service.client(job['project']).responses.retrieve(job['id'], include=['all']).model_dump()
    if response['status'] in {'queued', 'in_progress'}: return None
    if response['status'] != 'completed': raise HTTPException(502, 'Upstage 분석 작업에 실패했습니다. 다시 시도해 주세요.')
    return response


def start(service, data, filename):
    state = {'created': time.time(), 'digest': hashlib.sha256(data).hexdigest(), 'filename': filename[:180], 'phase': 'split', 'split': {'files': {}}}
    try:
        ensure(service, state['split'], [service.AGENT_SPLIT], data, filename)
        state['split']['job'] = launch(service, service.AGENT_SPLIT, state['split']['files'])
        return reply(state)
    except Exception:
        cleanup(service, state)
        raise


def doc_result(service, item, response):
    text = service.step_text(response, 'instruct')
    if service.step_text(response, '검토필요'):
        return {'문서종': item['name'], '검토필요': True, '사유': re.sub(r'^#+\s*|\*\*|【[^】]*】', '', text, flags=re.M).strip()}
    grade = service.parse_grade(text)
    if grade is None: raise HTTPException(502, '등급 판정 결과를 읽지 못했습니다.')
    field = lambda key: (re.search(key + r':\s*(.+)', text) or [None, ''])[1].strip()
    return {'문서종': item['name'], '등급': grade, '근거범주': field('근거범주'), '근거항목': field('근거항목') or service.grade_basis(text), '마스킹본': [], '비고': []}


def advance(service, ticket, data=None):
    state = decode(ticket)
    try:
        if time.time() - state['created'] > MAX_SECONDS:
            raise HTTPException(408, '분석 대기 시간이 20분을 넘었습니다. 잠시 후 다시 시도해 주세요.')
        if data is not None and hashlib.sha256(data).hexdigest() != state['digest']:
            raise HTTPException(400, '처음 선택한 PDF와 다릅니다. 다시 분석해 주세요.')
        phase = state['phase']
        if phase == 'split':
            response = retrieve(service, state['split']['job'])
            if response is None: return reply(state)
            # Parsing needs the original PDF's page count; only request its bytes at transitions.
            state['phase'] = 'prepare'
            return reply(state, True)
        if phase == 'prepare':
            if data is None: return reply(state, True)
            if 'docs' not in state:
                response = retrieve(service, state['split']['job'])
                with service.pymupdf.open(stream=data, filetype='pdf') as pdf: count = len(pdf)
                segments = service.parse_split(response, count)
                if not segments: raise HTTPException(502, '문서 분할 결과를 읽지 못했습니다.')
                notes = service.parse_notes(response)
                state['docs'] = []
                for name, ranges in segments:
                    item = {'rid': uuid.uuid4().hex, 'name': name, 'ranges': ranges, 'files': {}}
                    if name == service.OTHERS:
                        item['result'] = service.unsupported(name, notes.get(tuple(ranges), notes.get(None, '')))
                    state['docs'].append(item)
                for project, fid in state['split']['files'].items(): service.drop_file(project, fid)
                state['split']['files'] = {}
            # Launch one document per request so combined PDFs also stay below the function limit.
            for item in state['docs']:
                if 'result' in item or 'grade' in item: continue
                pdf = data if len(state['docs']) == 1 else service.cut(data, item['ranges'])
                ensure(service, item, [service.AGENT_GRADE], pdf, state['filename'])
                item['grade'] = launch(service, service.AGENT_GRADE, item['files'])
                return reply(state, True)
            state['phase'] = 'grade'
            return reply(state)
        if phase == 'grade':
            pending = False
            for item in state['docs']:
                if 'result' in item: continue
                try:
                    response = retrieve(service, item['grade'])
                    if response is None: pending = True; continue
                    item['result'] = doc_result(service, item, response)
                    item['exclude'] = re.findall(r'file[-_]\w+', json.dumps(response.get('output', [])))
                except HTTPException as error:
                    if len(state['docs']) == 1: raise
                    item['result'] = {'오류': str(error.detail)}
            if pending: return reply(state)
            state['phase'] = 'mask'
            return reply(state, True)
        if phase == 'mask':
            if data is None: return reply(state, True)
            for item in state['docs']:
                if 'masks' in item: continue
                result = item['result']
                agents = {lv: agent for lv, agent in [('mid', service.AGENT_MASK_MID), ('low', service.AGENT_MASK_LOW)] if lv in service.NEED.get(result.get('등급'), [])}
                item['masks'] = {}
                if not agents: continue
                if not all(agents.values()): raise HTTPException(500, '마스킹 워크플로우 설정이 필요합니다.')
                pdf = data if len(state['docs']) == 1 else service.cut(data, item['ranges'])
                ensure(service, item, agents.values(), pdf, state['filename'])
                with ThreadPoolExecutor(len(agents)) as executor:
                    futures = {lv: executor.submit(launch, service, agent, item['files']) for lv, agent in agents.items()}
                    for lv, future in futures.items(): item['masks'][lv] = future.result()
                return reply(state, True)
            state['phase'] = 'deliver'
            return reply(state)
        if phase == 'deliver':
            for item in state['docs']:
                for job in item.get('masks', {}).values():
                    try:
                        if retrieve(service, job) is None: return reply(state)
                    except HTTPException as error:
                        job['error'] = error.detail
            if data is None: return reply(state, True)
            return deliver(service, state, data)
        raise HTTPException(400, '분석 단계를 확인할 수 없습니다.')
    except Exception:
        cleanup(service, state)
        raise


def deliver(service, state, data):
    folders = []
    docs = []
    try:
        for item in state['docs']:
            rid = item['rid'];folder = service.RUNS / rid;folder.mkdir(exist_ok=True);folders.append(folder)
            pdf = data if len(state['docs']) == 1 else service.cut(data, item['ranges'])
            (folder/'input.pdf').write_bytes(pdf)
            result = item['result'];result['원본url'] = f'/runs/{rid}/input.pdf'
            result['쪽'] = ','.join(f'{a+1}-{b+1}' if a != b else str(a+1) for a,b in item['ranges'])
            for lv, job in item.get('masks', {}).items():
                masked = None if job.get('error') else service.find_pdf(retrieve(service, job), set(item.get('exclude', [])) | set(item['files'].values()))
                if masked is None:
                    result['비고'].append(f'{service.LABEL[lv]} 등급본을 만들지 못했습니다({job.get("error", "Mask 에이전트 출력에 PDF 없음")})')
                    continue
                (folder/f'{lv}.pdf').write_bytes(masked)
                result['마스킹본'].append({'등급': service.LABEL[lv], '출처': 'Studio Mask', 'url': f'/runs/{rid}/{lv}.pdf'})
            docs.append(result)
        duration = round(time.time()-state['created'], 1)
        if len(docs) == 1: result = {**docs[0], '소요초': duration}
        else:
            grades = [d['등급'] for d in docs if '등급' in d]
            result = {'결합': True, '등급': max(grades, key=service.RANK.get) if grades else None, '문서들': docs, '비고': [], '소요초': duration}
        def stream():
            try:
                for url in sorted(set(result_urls(result))):
                    rid, name = url.split('/')[2:]
                    content = (service.RUNS/rid/name).read_bytes()
                    try: preview = base64.b64encode(service.preview_run(rid, name).body).decode()
                    except HTTPException: preview = None
                    yield json.dumps({'file': {'path': url, 'pdf': base64.b64encode(content).decode(), 'preview': preview}}, separators=(',', ':'))+'\n'
                yield json.dumps({'step': 3, 'done': True, 'result': result}, ensure_ascii=False)+'\n'
            finally:
                for folder in folders: shutil.rmtree(folder, ignore_errors=True)
        return StreamingResponse(stream(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except Exception:
        for folder in folders: shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        cleanup(service, state)
