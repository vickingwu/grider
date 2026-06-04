"""网格标的筛选器路由

GET /api/screener            批量评估候选池并按适宜度排序返回（同步，~18s，结果缓存1小时）
GET /api/screener?refresh=1  强制忽略缓存重新评估
GET /api/screener/candidates 仅返回候选池清单（不评分）
"""

from flask import Blueprint, request, jsonify

from app.services.screener_service import ScreenerService
from app.constants import (
    SCREENER_CANDIDATES, HTTP_OK, HTTP_INTERNAL_SERVER_ERROR
)
from app.utils.logger import get_logger

logger = get_logger(__name__)
bp = Blueprint('screener_routes', __name__)

_service = ScreenerService()


@bp.route('', methods=['GET'])
@bp.route('/', methods=['GET'])
def run_screener():
    """批量筛选适合网格交易的标的。"""
    try:
        force = request.args.get('refresh') in ('1', 'true', 'yes')
        payload = _service.screen(SCREENER_CANDIDATES, force_refresh=force)
        return jsonify({'success': True, 'data': payload}), HTTP_OK
    except Exception as e:
        logger.error(f"筛选执行失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': '筛选执行失败，请稍后重试'}), HTTP_INTERNAL_SERVER_ERROR


@bp.route('/candidates', methods=['GET'])
def list_candidates():
    """返回候选池清单。"""
    return jsonify({'success': True, 'data': SCREENER_CANDIDATES}), HTTP_OK
