"""浏览器在线版 Flask 应用工厂：复用 create_app() 的蓝图注册，去掉 DB/JWT/CORS/静态文件/文件日志。"""
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERNAL_ERROR_MESSAGE = "在线引擎内部错误，请刷新页面重试"
NOT_FOUND_MESSAGE = "未知接口"


def create_browser_app() -> Flask:
    """构造在线版应用：与本地相同的 /api 路由集合，所有错误均返回 JSON（永不返回 HTML 错误页）。"""
    app = Flask("grider_browser")
    app.config.update(ENV="browser", TESTING=False, PROPAGATE_EXCEPTIONS=False)
    from app.routes import register as register_blueprints  # 延迟导入：provider 须先注册（见 bootstrap/dispatch）
    register_blueprints(app)

    @app.errorhandler(404)
    def _not_found(_e):  # 未知 /api 路径也返回 JSON，前端 api.js 可读取 message
        return jsonify(success=False, message=NOT_FOUND_MESSAGE, error=NOT_FOUND_MESSAGE), 404

    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):  # 405/400/413 等保持原状态码，先于通用 Exception 处理器注册
        return jsonify(success=False, message=e.name, error=e.name), e.code

    @app.errorhandler(Exception)
    def _internal(e: Exception):  # 路由自身都有 try/except；这里兜底保证永不返回 HTML 500
        logger.error(f"在线引擎未捕获异常: {e}", exc_info=True)
        return jsonify(success=False, message=INTERNAL_ERROR_MESSAGE, error=INTERNAL_ERROR_MESSAGE), 500

    return app
