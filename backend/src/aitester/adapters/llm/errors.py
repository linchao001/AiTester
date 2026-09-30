class ProviderError(Exception):
    """上游调用失败（网络/鉴权/配额等）。detail 面向用户，且不得包含 API Key。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ProviderConfigError(ProviderError):
    """配置缺失或非法，用户可依据 detail 自助修复。"""
