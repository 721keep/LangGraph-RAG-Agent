"""Safe, structured results for existing resource endpoints; no new API contract."""
import requests


def safe_error(status, payload):
    detail = payload.get('detail') if isinstance(payload, dict) else None
    known = {
        'F1.5 preserves existing test data; DELETE disabled.':
            'F1.5 preserves existing test data; DELETE disabled. 当前隔离环境禁止删除，数据已保留。',
        'You do not have permission to access this knowledge base.':
            '没有权限访问此知识库。请使用所属账号，或联系管理员。',
        'Document ownership could not be verified.':
            '无法确认文档归属，服务器已拒绝操作。',
    }
    if isinstance(detail, str) and detail in known:
        return known[detail]
    return {
        401: '登录已失效，请重新登录后再操作。',
        403: '服务器拒绝此操作：当前账号没有权限，或此环境已禁用该操作。',
        404: '资源不存在或已不可访问，请刷新列表核验。',
        409: '资源状态发生冲突，或名称已存在。请刷新后检查。',
        422: '提交内容不符合要求。请检查名称、描述或文件格式。',
        429: '请求过于频繁，请稍后手动重试。',
    }.get(status, '服务器未能完成请求。请刷新状态后再决定下一步操作。')


def request(method, path, *, data=None, expected=None, required_fields=()):
    import api_utils
    import streamlit as st
    headers = {'Accept': 'application/json',
               'Authorization': f"Bearer {st.session_state['user'].access_token}"}
    kwargs = {'headers': headers, 'timeout': api_utils.TIMEOUT}
    if data is not None:
        kwargs['json'] = data
    try:
        response = getattr(requests, method.lower())(api_utils.BASE_URL + path, **kwargs)
        response.raise_for_status()
        value = None if response.status_code == 204 else response.json()
        if (expected is not None and not isinstance(value, expected)) or (
                required_fields and (not isinstance(value, dict) or any(not value.get(k) for k in required_fields))):
            return {'ok': False, 'status_code': response.status_code, 'error_type': 'protocol',
                    'message': '服务器响应格式异常，未更新本地状态。', 'data': None}
        return {'ok': True, 'status_code': response.status_code, 'data': value, 'message': ''}
    except requests.exceptions.HTTPError as error:
        response = error.response
        status = response.status_code if response is not None else None
        try:
            payload = response.json() if response is not None else {}
        except ValueError:
            payload = {}
        result = {'ok': False, 'status_code': status, 'error_type': 'http',
                  'message': safe_error(status, payload), 'data': None}
        # The owned Thread endpoint is checked separately before history loads.
        # Only this exact existing backend response means no checkpoint yet;
        # generic 404/permission/network errors must retain the current view.
        if (method.upper() == 'GET' and path.startswith('/chat/') and status == 404
                and isinstance(payload, dict) and payload.get('detail') == 'Chat history not found'):
            result['error_code'] = 'chat_history_absent'
        return result
    except requests.exceptions.Timeout:
        message = '请求等待超时，操作结果待核验；服务器可能已收到请求。请刷新，勿直接重复提交。'
        kind = 'timeout'
    except requests.exceptions.RequestException:
        message = '网络连接中断，操作结果待核验。请检查连接并刷新，勿直接重复提交。'
        kind = 'network'
    except (ValueError, TypeError):
        message = '服务器响应格式异常，未更新本地状态。'
        kind = 'protocol'
    return {'ok': False, 'status_code': None, 'error_type': kind, 'message': message, 'data': None}
