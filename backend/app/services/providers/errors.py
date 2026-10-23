"""在线数据源的异常类型。"""

# 在线版无法提供的标的，错误文案统一以此前缀开头（前端/鱼盆服务据此识别）
UNSUPPORTED_PREFIX = "在线版暂不支持"
# 数据源限流/空页重试耗尽后的统一文案（前端按前缀渲染「数据源繁忙」）
BUSY_MESSAGE = "数据源繁忙，请稍后刷新"


class ProviderError(Exception):
    """数据源取数/解析失败（网络错误、非 200、接口 code != 0、正文无法解析等）。"""


class UnsupportedOnlineError(ValueError):
    """在线版没有可用数据源的标的；消息以 UNSUPPORTED_PREFIX 开头，可直接展示给用户。"""


class DataSourceBusyError(ValueError):
    """所有在线源在重试后仍无数据（限流/空页/连接重置）；消息固定为 BUSY_MESSAGE，可直接展示给用户。"""

    def __init__(self, message: str = BUSY_MESSAGE) -> None:
        super().__init__(message)
