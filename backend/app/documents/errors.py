"""Public upload errors. Provider responses and credentials never reach the UI."""

from openai import APIError


ERROR_MESSAGES = {
    "embedding_quota_exhausted": "Embedding 额度不足，文档索引失败。请联系管理员检查服务额度。",
    "embedding_auth_failed": "Embedding 服务鉴权失败。请联系管理员检查凭据与访问权限。",
    "embedding_failed": "Embedding 服务调用失败，文档尚未完成索引。",
    "pdf_parse_failed": "PDF 解析失败，请检查文件是否损坏、加密或不含可提取的文本。",
    "document_parse_failed": "文档解析失败，请检查文件内容及格式。",
    "vector_write_failed": "向量写入失败，请联系管理员检查向量数据库。",
    "document_status_failed": "文档状态保存失败，请刷新列表确认处理结果。",
    "document_save_failed": "上传文件或文档记录保存失败，请刷新列表确认。",
    "document_unavailable": "文档已被删除或状态已改变，本次上传未标记为就绪。",
}


class DocumentIndexingError(Exception):
    def __init__(self, code: str, stage: str, http_status: int = 500):
        self.code = code
        self.stage = stage
        self.http_status = http_status
        self.message = ERROR_MESSAGES[code]
        super().__init__(self.message)


def classify_vector_error(error: Exception) -> DocumentIndexingError:
    # PGVector invokes the existing OpenAI-compatible Embedding client internally.
    # SDK errors originate in Embedding; SQL/driver errors belong to vector writes.
    if isinstance(error, APIError):
        body = getattr(error, "body", None)
        detail = body.get("error", body) if isinstance(body, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        codes = {
            str(value).lower()
            for value in (getattr(error, "code", None), detail.get("code"), detail.get("type"))
            if value
        }
        if getattr(error, "status_code", None) in (401, 403):
            return DocumentIndexingError("embedding_auth_failed", "embedding", 502)
        if codes & {"allocationquota.freetieronly", "insufficient_quota", "quota_exhausted"}:
            return DocumentIndexingError("embedding_quota_exhausted", "embedding", 503)
        return DocumentIndexingError("embedding_failed", "embedding", 502)
    return DocumentIndexingError("vector_write_failed", "vector_write")
