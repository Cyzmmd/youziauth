"""SWU dormitory protocol adapter. Protocol reference: swu-daka 42d8973.

No historical form IDs, coordinates, plaintext caches or raw-response logging.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from dorm_checkin import CheckinError, Task, now

ORIGIN = 'https://of.swu.edu.cn'
BASE = '/gateway/fighter-baida/api/'
SELECT = BASE + 'form-instance/select'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an authentication header to a login redirect or another host.
        raise CheckinError('login_required', '登录已失效，请重新登录')


def request(method, path, token, *, query=None, body=None, form=None):
    if not path.startswith('/gateway/') or any(c in token for c in '\r\n'):
        raise CheckinError('error', '接口参数无效')
    url = ORIGIN + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    headers = {
        'Accept': 'application/json', 'fighter-auth-token': token,
        'Origin': ORIGIN, 'Referer': ORIGIN + '/baidaForm/',
        'User-Agent': 'Mozilla/5.0 AliApp(DingTalk/7.8.5.1) com.alibaba.android.rimet.diswu',
        'X-Requested-With': 'com.alibaba.android.rimet.diswu',
        'Cookie': 'SESSION=SESSION; access_token=' + token,
    }
    data = None
    if form is not None:
        boundary = 'youziauth' + uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                 for key, value in form.items()]
        data = (''.join(parts) + f'--{boundary}--\r\n').encode()
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json;charset=UTF-8'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('oversized response')
        result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise CheckinError('login_required', '登录已失效，请重新登录') from None
        raise CheckinError('network_error', '学校接口暂不可用，请稍后重试') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CheckinError('network_error', '无法连接学校接口，请检查网络后重试') from None
    except (ValueError, UnicodeError):
        raise CheckinError('error', '学校接口返回格式不正确，请重新登录或稍后重试') from None
    if not isinstance(result, dict):
        raise CheckinError('error', '学校接口返回格式不正确')
    if str(result.get('code')) in ('401', '403'):
        raise CheckinError('login_required', '登录已失效，请重新登录')
    if result.get('code') != 200:
        raise CheckinError('error', '学校接口未确认操作，请在学校页面核实')
    return result.get('data')


def required(data, key):
    value = data.get(key)
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise CheckinError('error', '今日任务字段不完整，已停止')
    return str(value)


class SwuApi:
    def __init__(self, transport=request):
        self.transport = transport

    def user(self, token):
        data = self.transport('GET', '/gateway/fighter-middle/api/auth/user', token,
                              query={'appType': 'fighter-portal'})
        subject = data.get('subject') if isinstance(data, dict) else None
        if not isinstance(subject, dict) or not (subject.get('username') or subject.get('loginName')):
            raise CheckinError('login_required', '无法确认登录身份，请重新登录')
        return str(subject.get('username') or subject['loginName'])

    def today(self, token, student, current):
        records = []
        for page in range(1, 11):
            data = self.transport('POST', BASE + 'cqtj/getTransitionByToday', token,
                                  form={'pageNum': str(page), 'pageSize': '50'})
            if not isinstance(data, dict) or not isinstance(data.get('records'), list):
                raise CheckinError('error', '今日任务列表格式不正确')
            batch = data['records']
            records.extend(batch)
            total = data.get('total')
            if not batch or (total is not None and len(records) >= int(total)) or (total is None and len(batch) < 50):
                break
        else:
            raise CheckinError('error', '任务数量异常，无法完整确认今日任务')
        candidates = {str(r.get('id')): r for r in records if isinstance(r, dict)
                      and r.get('tsrq') == current.date().isoformat()
                      and '查寝' in json.dumps(r, ensure_ascii=False)}
        if not candidates:
            return None
        if len(candidates) != 1:
            raise CheckinError('error', '发现多条今日查寝任务，请在学校页面选择处理')
        record = next(iter(candidates.values()))
        task_id, form_id = required(record, 'id'), required(record, 'formId')
        data = self.transport('GET', SELECT, token,
                              query={'dataId': task_id, 'formId': form_id, 'procDefId': ''})
        if not isinstance(data, dict) or required(data, 'xh') != student:
            raise CheckinError('error', '任务账号与登录账号不一致，已停止')
        if data.get('tsrq') and data['tsrq'] != record['tsrq']:
            raise CheckinError('error', '任务表单日期不一致，已停止')
        if data.get('id') and str(data['id']) != task_id:
            raise CheckinError('error', '任务表单编号不一致，已停止')
        publish_id = required(data, 'cqfbid')
        times = data.get('qdsj') or [record.get('qdkssj'), record.get('qdjssj')]
        if isinstance(times, str):
            try:
                times = json.loads(times) if times.startswith('[') else times.split(',')
            except ValueError:
                times = []
        if not isinstance(times, list) or len(times) != 2:
            raise CheckinError('error', '今日任务时段不明确，已停止')
        payload = dict(data, id=task_id, cqfbid=publish_id, xh=student, tsrq=record['tsrq'])
        dorm = self.transport('POST', BASE + 'cqlc/getDormitory', token, body=payload)
        fields = {}
        if isinstance(dorm, dict):
            for item in dorm.get('columnList') or []:
                if not isinstance(item, dict):
                    continue
                if item.get('prop') in ('qsqddd', 'qdbj') and item.get('value'):
                    fields[item['prop']] = str(item['value'])
                if item.get('address'):
                    fields.setdefault('qsqddd', str(item['address']))
                if item.get('qdbj'):
                    fields.setdefault('qdbj', str(item['qdbj']) + '米')
        return Task(task_id, form_id, publish_id, student, record['tsrq'],
                    str(record.get('cqzmc') or record.get('title') or '今日查寝')[:120],
                    times[0], times[1], str(data.get('qdjg')) == '1',
                    fields.get('qsqddd') or str(data.get('qsqddd') or ''),
                    fields.get('qdbj') or str(data.get('qdbj') or ''), str(data.get('formId') or ''))

    def verify(self, token, task, position):
        data = self.transport('POST', BASE + 'cqlc/verify', token,
                              query={'businessKey': task.id}, body={'mapData': position})
        if not isinstance(data, dict) or data.get('isArea') is not True:
            raise CheckinError('location_required', '学校未确认当前位置在打卡范围内，未提交')

    def submit(self, token, task, position):
        location = dict(position, isArea=True, tip='当前在签到范围内')
        return self.transport('POST', BASE + 'form-instance/save', token,
                              query={'formId': task.form_id, 'isSubmitProcess': 'false'}, body={
            'id': task.id, 'businessKey': task.id, 'formId': task.form_id, 'cqfbid': task.publish_id,
            'xh': task.student, 'tsrq': task.date, 'dksj': now().strftime('%Y-%m-%d %H:%M'),
            'qdjg': '0', '$qdjg': '未签到', 'qdtj': '1', 'ycdksfcl': '', 'isArchive': '',
            'qdsj': [task.start, task.end], 'qsqddd': task.address, 'qdbj': task.radius, 'qddz': location,
        })

    def is_signed(self, token, task):
        data = self.transport('GET', SELECT, token,
                              query={'dataId': task.id, 'formId': task.form_id, 'procDefId': ''})
        return (isinstance(data, dict) and str(data.get('xh')) == task.student
                and (not data.get('id') or str(data['id']) == task.id)
                and (not data.get('tsrq') or data['tsrq'] == task.date)
                and str(data.get('qdjg')) == '1')
