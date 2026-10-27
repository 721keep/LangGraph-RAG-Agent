"""Allowlisted deployment errors only; never display raw provider responses."""
MESSAGES = {
    'daily_limit_reached': '日常模型额度已达上限。请查看 daily-status，调整配置或等待下一 UTC 日期；不会自动重试。',
    'daily_input_limit': '输入、批量或单次工具调用超过日常安全上限。请缩小文件或缩短会话。',
    'daily_paused': '日常模型已暂停。请查看 daily-status，核验失败或未确认的请求后显式恢复。',
    'daily_provider_error': '模型服务错误或输出中断，调用已暂停。请核验结果，不要自动重传或重发。',
    'daily_login_required': '请登录并使用已保存的会话进行日常问答。',
    'daily_busy': '当前模型并发数已达上限。请等待正在执行的操作结束；不会自动重试。',
}
