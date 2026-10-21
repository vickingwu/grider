"""在线数据源的异常类型。"""

# 在线版无法提供的标的，错误文案统一以此前缀开头（前端/鱼盆服务据此识别）
UNSUPPORTED_PREFIX = "在线版暂不支持"


class ProviderError(Exception):
    """数据源取数/解析失败（网络错误、非 200、接口 code != 0、正文无法解析等）。"""


class UnsupportedOnlineError(ValueError):
    """在线版没有可用数据源的标的；消息以 UNSUPPORTED_PREFIX 开头，可直接展示给用户。"""
