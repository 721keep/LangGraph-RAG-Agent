from uuid import UUID

import httpx
import requests
import streamlit as st
from config import settings
from loguru import logger
from streamlit.runtime.uploaded_file_manager import UploadedFile

BASE_URL = settings.backend_base_url
TIMEOUT = 30
UPLOAD_TIMEOUT = (settings.document_upload_connect_timeout, settings.document_upload_read_timeout)

from runtime_errors import MESSAGES as RUNTIME_ERROR_MESSAGES

UPLOAD_ERROR_MESSAGES = {
    **RUNTIME_ERROR_MESSAGES,
    "embedding_quota_exhausted": "Embedding 额度不足，请联系管理员检查服务额度。",
    "embedding_auth_failed": "Embedding 服务鉴权失败，请联系管理员检查凭据与访问权限。",
    "embedding_failed": "Embedding 服务调用失败，文档尚未完成索引。",
    "pdf_parse_failed": "PDF 解析失败，请检查文件是否损坏、加密或不含可提取的文本。",
    "document_parse_failed": "文档解析失败，请检查文件内容及格式。",
    "vector_write_failed": "向量写入失败，请联系管理员检查向量数据库。",
    "document_status_failed": "文档状态保存失败，请刷新列表确认处理结果。",
    "document_save_failed": "上传文件或文档记录保存失败，请刷新列表确认。",
    "document_unavailable": "文档已被删除或状态已改变，本次上传未标记为就绪。",
    "upload_auth_failed": "上传请求鉴权失败，请重新登录或检查访问权限。",
    "upload_http_failed": "服务器拒绝或未能完成上传，请刷新文档列表查看状态。",
    "upload_timeout": "上传等待超时，处理结果待确认；后端可能仍在处理。请刷新列表，勿直接重复上传。",
    "upload_network_failed": "上传连接中断，处理结果待确认。请刷新列表，勿直接重复上传。",
    "upload_result_unknown": "未能确认上传结果，请刷新文档列表查看状态。",
}


def _post_document_upload(url: str, file: UploadedFile) -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }
    try:
        response = requests.post(
            url,
            files={"file": (file.name, file.getvalue(), file.type)},
            headers=headers,
            timeout=UPLOAD_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("status") == "ready" and result.get("document_id"):
            return result
        code = "upload_result_unknown"
    except requests.exceptions.HTTPError as error:
        response = error.response
        try:
            body = response.json()
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
        except ValueError:
            detail = {}
        detail = detail if isinstance(detail, dict) else {}
        code = detail.get("error_code")
        if not isinstance(code, str) or code not in UPLOAD_ERROR_MESSAGES:
            code = "upload_auth_failed" if response.status_code in (401, 403) else "upload_http_failed"
        return {
            "status": "unknown" if detail.get("status") == "unknown" else "failed",
            "document_id": detail.get("document_id"),
            "error_code": code,
            "message": UPLOAD_ERROR_MESSAGES[code],
        }
    except requests.exceptions.Timeout:
        code = "upload_timeout"
    except requests.exceptions.RequestException:
        code = "upload_network_failed"
    except Exception:
        code = "upload_result_unknown"
    # A lost response cannot prove that the backend stopped. Never retry here.
    logger.warning("Upload result uncertain: code={}", code)
    return {"status": "unknown", "error_code": code, "message": UPLOAD_ERROR_MESSAGES[code]}


def document_failure_message(document: dict) -> str:
    """Only display safe, recognized errors, including for historical records."""
    raw = document.get("error_message") or ""
    code = raw.split(":", 1)[0].strip()
    if code in UPLOAD_ERROR_MESSAGES:
        return UPLOAD_ERROR_MESSAGES[code]
    if "AllocationQuota.FreeTierOnly" in raw or "Free quota exhausted" in raw:
        return UPLOAD_ERROR_MESSAGES["embedding_quota_exhausted"]
    return "文档处理失败，未提供可安全展示的具体原因。"

## Auth --------------------------------------------------------------------


def register_user(register_data: dict) -> dict:
    """
    Registers a user with the API.

    Args:
        register_data: A dictionary containing user registration data.

    Returns:
        A dictionary containing the API response JSON if successful, or an empty dictionary otherwise.
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(f"{BASE_URL}/auth/signup", headers=headers, json=register_data, timeout=TIMEOUT)
        response.raise_for_status()

        logger.info("Successfully registered user")
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"Register user failed with status {e.response.status_code}. Response: {e.response.text}")
        if e.response.status_code in [401, 403]:
            return e.response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"Register user failed with RequestError: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Register user failed with an unexpected exception: {str(e)}")
        return {}


def login_user(email: str, password: str) -> dict:
    """
    Logs in a user with the API.

    Args:
        email: The email of the user.
        password: The password of the user.

    Returns:
        A dictionary containing the API response JSON if successful, or an empty dictionary otherwise.
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"username": email, "password": password}

    try:
        response = requests.post(f"{BASE_URL}/auth/login", headers=headers, data=data, timeout=TIMEOUT)
        response.raise_for_status()

        logger.info("Successfully logged in user")
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"Login user failed with status {e.response.status_code}. Response: {e.response.text}")
        if e.response.status_code in [401, 403]:
            return e.response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"Login user failed with RequestError: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Login user failed with an unexpected exception: {str(e)}")
        return {}


## Chat --------------------------------------------------------------------

def get_chat_config() -> dict:
    """Retrieve the available chat model configuration."""

    try:
        response = requests.get(
            f"{BASE_URL}/chat/config",
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to retrieve chat config: {str(e)}"
        )
        return {}

async def simple_chat_stream(chat_data: dict):
    """
    Calls the streaming chat endpoint and yields text chunks.

    Args:
        chat_data: Dictionary containing chat parameters

    Yields:
        str: Text chunks from the stream
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            async with client.stream("POST", f"{BASE_URL}/chat/", json=chat_data) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield line
        except httpx.HTTPStatusError as e:
            await e.response.aread()
            try:
                code = e.response.json().get('detail', {}).get('error_code')
            except (ValueError, AttributeError):
                code = None
            from runtime_errors import MESSAGES
            if isinstance(code, str) and code in MESSAGES:
                import json
                yield json.dumps({'type': 'error', 'code': code})
                return
            raise Exception(
                f"Chat server responded with error: {e.response.status_code} {e.response.reason_phrase}"
            ) from e
        except httpx.RequestError as e:
            raise Exception(f"Failed to connect to chat server: {str(e)}") from e


async def chat_stream(chat_data: dict, thread_id: UUID):
    """
    Calls the streaming chat endpoint and yields text chunks.
    This function is used for authenticated users, so it has a memory and access to the tools.

    Args:
        chat_data: Dictionary containing chat parameters

    Yields:
        str: Text chunks from the stream
    """
    headers = {
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers) as client:
        try:
            async with client.stream("POST", f"{BASE_URL}/chat/{thread_id}", json=chat_data) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield line
        except httpx.HTTPStatusError as e:
            await e.response.aread()
            try:
                code = e.response.json().get('detail', {}).get('error_code')
            except (ValueError, AttributeError):
                code = None
            from runtime_errors import MESSAGES
            if isinstance(code, str) and code in MESSAGES:
                import json
                yield json.dumps({'type': 'error', 'code': code})
                return
            raise Exception(
                f"Chat server responded with error: {e.response.status_code} {e.response.reason_phrase}"
            ) from e
        except httpx.RequestError as e:
            raise Exception(f"Failed to connect to chat server: {str(e)}") from e


def get_chat_history(thread_id: UUID, *, detailed: bool = False) -> list | dict:
    """
    Retrieves the chat history for a specific s ID.

    Args:
        thread_id: The UUID of the chat session.

    Returns:
        A list of chat history messages.
    """
    if detailed:
        from resource_api import request
        return request("get", f"/chat/{thread_id}", expected=list)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }

    try:
        response = requests.get(f"{BASE_URL}/chat/{thread_id}", headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"API response failed with status {e.response.status_code}. Response: {e.response.text}")
        if e.response.status_code in [401, 404]:
            return e.response.json()
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"API call failed with RequestError: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"API call failed with an unexpected exception: {str(e)}")
        return []


## Threads -----------------------------------------------------------------


def create_new_thread() -> dict:
    """
    Creates a new thread in the API.

    Returns:
        A dictionary containing the API response JSON.
    """

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }

    try:
        response = requests.post(f"{BASE_URL}/threads/", headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"API call for 'create_new_thread' failed with status {e.response.status_code}. "
            "Response: {e.response.text}"
        )
        if e.response.status_code in [401]:
            return e.response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"API call for 'create_new_thread' failed with RequestError: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"API call for 'create_new_thread' failed with an unexpected exception: {str(e)}")
        return {}


def get_thread(thread_id: UUID, *, detailed: bool = False) -> dict:
    """
    Retrieves a specific thread from the API.

    Args:
        thread_id: The UUID of the thread to retrieve.

    Returns:
        A dictionary containing the API response JSON.
    """

    if detailed:
        from resource_api import request
        return request("get", f"/threads/{thread_id}", expected=dict, required_fields=("id",))

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }

    try:
        response = requests.get(f"{BASE_URL}/threads/{thread_id}", headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"API call for 'get_thread' failed with status {e.response.status_code}. Response: {{{{e.response.text}}}}"
        )
        if e.response.status_code in [401, 403, 404]:
            return e.response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"API call for 'get_thread' failed with RequestError: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"API call for 'get_thread' failed with an unexpected exception: {str(e)}")
        return {}


def get_user_threads(*, detailed: bool = False) -> list | dict:
    """
    Retrieves the user's threads from the API.

    Returns:
        A dictionary containing the API response JSON.
    """

    if detailed:
        from resource_api import request
        return request("get", "/threads/", expected=list)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }
    try:
        response = requests.get(f"{BASE_URL}/threads/", headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"API call for 'get_user_threads' failed with status {e.response.status_code}. Response: {e.response.text}"
        )
        if e.response.status_code in [401]:
            return e.response.json()
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"API call for 'get_user_threads' failed with RequestError: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"API call for 'get_user_threads' failed with an unexpected exception: {str(e)}")
        return []


def update_thread(thread_id: UUID, title: str) -> dict:
    """
    Update an existing thread in the API.

    Args:
        thread_id: The UUID of the thread to update.
        title: The title of the thread to update.

    Returns:
        A dictionary containing the API response JSON.
    """

    headers = {
        # "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }

    data = {"title": title}
    try:
        response = requests.patch(f"{BASE_URL}/threads/{thread_id}", json=data, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"API call for 'update_thread' failed with status {e.response.status_code}. Response: {e.response.text}"
        )
        if e.response.status_code in [401, 403, 404]:
            return e.response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"API call for 'update_thread' failed with RequestError: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"API call for 'update_thread' failed with an unexpected exception: {str(e)}")
        return {}


def delete_thread(thread_id: UUID) -> dict:
    """
    Deletes a thread from the API.

    Args:
        thread_id: The UUID of the thread to delete.

    Returns:
        A dictionary containing the response from the API.
    """

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }

    try:
        response = requests.delete(f"{BASE_URL}/threads/{thread_id}", headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return {"status": "ok", "detail": f"Thread with ID {thread_id} successfylly deleted."}
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"API call for 'delete_thread' failed with status {e.response.status_code}. Response: {e.response.text}"
        )
        if e.response.status_code in [401, 403, 404]:
            return e.response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.error(f"API call for 'delete_thread' failed with RequestError: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"API call for 'delete_thread' failed with an unexpected exception: {str(e)}")
        return {}

## Knowledge Bases ----------------------------------------------------------


def get_knowledge_bases(*, detailed: bool = False) -> list | dict:
    """List owned KBs; detailed results preserve failures without emptying UI state."""
    from resource_api import request
    result = request("get", "/knowledge-bases/", expected=list)
    return result if detailed else (result["data"] if result["ok"] else [])


def create_knowledge_base(
    name: str,
    description: str | None = None,
    *, detailed: bool = False,
) -> dict:
    from resource_api import request
    result = request("post", "/knowledge-bases/", data={"name": name, "description": description}, expected=dict, required_fields=("id",))
    return result if detailed else (result["data"] if result["ok"] else {})


def delete_knowledge_base(knowledge_base_id: UUID, *, detailed: bool = False) -> bool | dict:
    from resource_api import request
    result = request("delete", f"/knowledge-bases/{knowledge_base_id}")
    return result if detailed else result["ok"]


def set_thread_knowledge_base(
    thread_id: UUID,
    knowledge_base_id: UUID | None,
    *, detailed: bool = False,
) -> dict:
    from resource_api import request
    result = request("patch", f"/threads/{thread_id}/knowledge-base",
                     data={"knowledge_base_id": str(knowledge_base_id) if knowledge_base_id else None}, expected=dict, required_fields=("id",))
    return result if detailed else (result["data"] if result["ok"] else {})


def upload_knowledge_base_document(
    knowledge_base_id: UUID,
    file: UploadedFile,
) -> dict:
    """Upload with a dedicated timeout and an explicit ready/failed/unknown result."""
    return _post_document_upload(
        f"{BASE_URL}/documents/knowledge-bases/{knowledge_base_id}/upload", file,
    )


def list_knowledge_base_documents(
    knowledge_base_id: UUID,
    *, detailed: bool = False,
) -> list | dict | None:
    from resource_api import request
    result = request("get", f"/documents/knowledge-bases/{knowledge_base_id}", expected=list)
    return result if detailed else (result["data"] if result["ok"] else None)


def upload_document(thread_id: UUID, file: UploadedFile) -> dict:
    return _post_document_upload(f"{BASE_URL}/documents/upload/{thread_id}", file)


def list_document(thread_id: UUID) -> list | None:
    """
    Retrieves the list of documents from the API.

    Returns:
        A list of documents if successful, or None otherwise.
    """

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {st.session_state['user'].access_token}",
    }

    try:
        response = requests.get(f"{BASE_URL}/documents/{thread_id}", headers=headers, timeout=TIMEOUT)
        response.raise_for_status()

        logger.info("Successfully retrieved document list")
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"List documents failed with status {e.response.status_code}. Response: {e.response.text}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"List documents failed with RequestError: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"List documents failed with an unexpected exception: {str(e)}")
        return None


def delete_document(document_id: str, *, detailed: bool = False) -> bool | dict:
    """Errors return False for legacy callers; detailed callers receive safe context."""
    from resource_api import request
    result = request("delete", f"/documents/{document_id}")
    return result if detailed else result["ok"]
