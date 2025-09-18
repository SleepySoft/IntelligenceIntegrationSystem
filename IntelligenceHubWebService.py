import json
import os
import traceback
import uuid
import logging
from functools import wraps
from typing import List

import datetime
import threading

import dateutil
from flask import Flask, request, jsonify, session, redirect, url_for, render_template, abort, send_file

from GlobalConfig import *
from Scripts.mongodb_exporter import export_mongodb_data
from ServiceComponent.IntelligenceDistributionPageRender import get_intelligence_statistics_page
from ServiceComponent.IntelligenceHubDefines import APPENDIX_MAX_RATE_SCORE
from ServiceComponent.RateStatisticsPageRender import get_statistics_page
from ServiceComponent.UserManager import UserManager
from Tools.CommonPost import common_post
from MyPythonUtility.ArbitraryRPC import RPCService
from ServiceComponent.RSSPublisher import RSSPublisher, FeedItem
from ServiceComponent.PostManager import generate_html_from_markdown
from ServiceComponent.ArticleRender import default_article_render
from ServiceComponent.ArticleQueryRender import render_query_page
from ServiceComponent.ArticleListRender import default_article_list_render
from IntelligenceHub import CollectedData, IntelligenceHub, ProcessedData, APPENDIX_TIME_ARCHIVED

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def post_collected_intelligence(url: str, data: CollectedData, timeout=10) -> dict:
    """
    Post collected intelligence to IntelligenceHub (/collect).
    :param url: IntelligenceHub url (without '/collect' path).
    :param data: Collector data.
    :param timeout: Timeout in second
    :return: Requests response or {'status': 'error', 'reason': 'error description'}
    """
    if not isinstance(data, CollectedData):
        return {'status': 'error', 'reason': 'Data must be CollectedData format.'}
    return common_post(f'{url}/collect', data.model_dump(exclude_unset=True), timeout)


def post_processed_intelligence(url: str, data: ProcessedData, timeout=10) -> dict:
    """
    Post processed data to IntelligenceHub (/processed).
    :param url: IntelligenceHub url (without '/processed' path).
    :param data: Processed data.
    :param timeout: Timeout in second
    :return: Requests response or {'status': 'error', 'reason': 'error description'}
    """
    if not isinstance(data, ProcessedData):
        return {'status': 'error', 'reason': 'Data must be ProcessedData format.'}
    return common_post(f'{url}/processed', data.model_dump(exclude_unset=True), timeout)


class WebServiceAccessManager:
    def __init__(self,
                 rpc_api_tokens: List[str],
                 collector_tokens: List[str],
                 processor_tokens: List[str],
                 user_manager: UserManager,
                 deny_on_empty_config: bool = False):
        self.rpc_api_tokens = rpc_api_tokens
        self.collector_tokens = collector_tokens
        self.processor_tokens = processor_tokens
        self.user_manager = user_manager
        self.deny_on_empty_config = deny_on_empty_config

    def check_rpc_api_token(self, token: str) -> bool:
        return (not self.deny_on_empty_config) if not self.rpc_api_tokens else (token in self.rpc_api_tokens)

    def check_collector_token(self, token: str) -> bool:
        return (not self.deny_on_empty_config) if not self.rpc_api_tokens else (token in self.collector_tokens)

    def check_processor_token(self, token: str) -> bool:
        return (not self.deny_on_empty_config) if not self.rpc_api_tokens else (token in self.processor_tokens)

    def check_user_credential(self, username: str, password: str, client_ip) -> int or None:
        if self.user_manager:
            result, _ = self.user_manager.authenticate(username, password, client_ip)
            return result
        else:
            return 1 if not self.deny_on_empty_config else None

    @staticmethod
    def login_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'logged_in' not in session or not session['logged_in']:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function


class IntelligenceHubWebService:
    def __init__(self, *,
                 intelligence_hub: IntelligenceHub,
                 access_manager: WebServiceAccessManager,
                 rss_publisher: RSSPublisher):

        # ---------------- Parameters ----------------

        self.intelligence_hub = intelligence_hub
        self.access_manager = access_manager
        self.rss_publisher = rss_publisher
        self.wsgi_app = None

        # ---------------- RPC Service ----------------

        self.rpc_service = RPCService(
            rpc_stub=self.intelligence_hub,
            token_checker=self.access_manager.check_rpc_api_token,
            error_handler=self.handle_error
        )

    # ---------------------------------------------------- Routers -----------------------------------------------------

    def register_routers(self, app: Flask):

        self.wsgi_app = app

        # --------------------------------------------------- Config --------------------------------------------------

        class CustomJSONEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, datetime.datetime):
                    return obj.strftime("%Y-%m-%d %H:%M:%S")
                # TODO: Add more data type support.
                return super().default(obj)

        app.json_encoder = CustomJSONEncoder

        # -------------------------------------------------- Security --------------------------------------------------

        @app.before_request
        def refresh_session():
            session.modified = True

        @app.route('/login', methods=['GET', 'POST'])
        def login():
            if request.method == 'POST':
                client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
                             request.headers.get('X-Real-IP', '').strip() or
                             request.remote_addr)

                username = request.form['username']
                password = request.form['password']

                user_id = self.access_manager.check_user_credential(username, password, client_ip)

                if user_id:
                    session['logged_in'] = True
                    session['user_id'] = user_id
                    session['username'] = username
                    session['login_ip'] = client_ip
                    session.permanent = True
                    return redirect(url_for('show_post', article='index'))
                else:
                    logger.info(f"Login fail - IP: {client_ip}, Username: {username}")
                    return "Invalid credentials", 401
            return render_template('login.html')

        @app.route('/logout')
        @WebServiceAccessManager.login_required
        def logout():
            session.clear()
            return redirect(url_for('login'))

        # ---------------------------------------------- Post and Article ----------------------------------------------

        @app.route('/')
        def index():
            return redirect(url_for('show_post', article='index')) \
                if session.get('logged_in') \
                else self.get_rendered_md_post('index_public') or abort(404)

        @app.route('/post/<path:article>')
        @WebServiceAccessManager.login_required
        def show_post(article):
            """
            Render a Markdown article as HTML with caching mechanism.

            Args:
                article: URL path of the Markdown file (relative to 'posts' directory)

            Returns:
                Rendered HTML template or 404 error
            """
            return self.get_rendered_md_post(article) or abort(404)

        # -------------------------------------------- API and Open Service --------------------------------------------

        @app.route('/api', methods=['POST'])
        @WebServiceAccessManager.login_required
        def rpc_api():
            try:
                response = self.rpc_service.handle_flask_request(request)
                return response
            except Exception as e:
                    print('/api Error', e)
                    print(traceback.format_exc())
                    response = ''
            return response

        @app.route('/collect', methods=['POST'])
        def collect_api():
            try:
                data = dict(request.json)
                if not data.get('UUID', ''):
                    raise ValueError('Invalid UUID.')

                if self.access_manager.check_collector_token(data.get('token', '')):
                    result = self.intelligence_hub.submit_collected_data(data)
                    response = 'queued' if result else 'error',
                else:
                    response = 'invalid token'

                return jsonify(
                    {
                        'resp': response,
                        'uuid': data.get('UUID', '')
                    })
            except Exception as e:
                logger.error(f'collect_api() fail: {str(e)}')
                return jsonify({'resp': 'error', 'uuid': ''})

        @app.route('/manual_rate', methods=['POST'])
        def submit_rating():
            try:
                data = request.get_json()
                _uuid = data.get('uuid')
                ratings = data.get('ratings')

                self.intelligence_hub.submit_intelligence_manual_rating(_uuid, ratings)

                return jsonify({'status': 'success', 'message': 'Ratings saved'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500

        # ---------------------------------------------------- Pages ---------------------------------------------------

        @app.route('/rssfeed.xml', methods=['GET'])
        def rssfeed_api():
            try:
                count = request.args.get('count', default=100, type=int)
                threshold = request.args.get('threshold', default=6, type=int)

                intelligences, _ = self.intelligence_hub.query_intelligence(
                    threshold = threshold, skip = 0, limit = count)

                try:
                    rss_items = self._articles_to_rss_items(intelligences)
                    feed_xml = self.rss_publisher.generate_feed(
                        'IIS',
                        '/intelligence',
                        'IIS Processed Intelligence',
                        rss_items)
                    return feed_xml
                except Exception as e:
                    logger.error(f"Rss Feed API error: {str(e)}", stack_info=True)
                    return 'Error'
            except Exception as e:
                logger.error(f'rssfeed_api() error: {str(e)}', stack_info=True)
                return 'Error'

        @app.route('/intelligences', methods=['GET'])
        def intelligences_list_api():
            try:
                offset = request.args.get('offset', default=0, type=int)
                count = request.args.get('count', default=10, type=int)
                threshold = request.args.get('threshold', default=6, type=int)

                if count > 100:
                    count = 100

                intelligences, total_count = self.intelligence_hub.query_intelligence(
                    threshold = threshold, skip = offset, limit = count)
                return default_article_list_render(intelligences, offset, count, total_count)

            except Exception as e:
                logger.error(f'intelligences_list_api() error: {str(e)}', stack_info=True)
                return jsonify({"error": "Server error"}), 500

        @app.route('/intelligences/query', methods=['GET', 'POST'])
        @WebServiceAccessManager.login_required
        def intelligences_query_api():
            form_data = request.form if request.method == 'POST' else {}

            # Parse form data
            params = {
                'start_time': form_data.get('start_time', ''),
                'end_time': form_data.get('end_time', ''),
                'locations': form_data.get('locations', ''),
                'peoples': form_data.get('peoples', ''),
                'organizations': form_data.get('organizations', ''),
                # 'keywords': form_data.get('keywords', ''),
                'page': int(form_data.get('page', 1)),
                'per_page': int(form_data.get('per_page', 10))
            }

            # Convert to query parameters
            query_params = {}
            if params['start_time'] and params['end_time']:
                query_params['period'] = (
                    datetime.datetime.fromisoformat(params['start_time']),
                    datetime.datetime.fromisoformat(params['end_time'])
                )

            for field in ['locations', 'peoples', 'organizations']:
                if params[field]:
                    query_params[field] = [x.strip() for x in params[field].split(',')]

            # if params['keywords']:
            #     query_params['keywords'] = params['keywords']

            # Add pagination
            skip = (params['page'] - 1) * params['per_page']
            query_params.update({'skip': skip, 'limit': params['per_page']})

            # Execute query
            try:
                results, total_results = self.intelligence_hub.query_intelligence(**query_params)
                # Render HTML response
                return render_query_page(params, results, total_results)
            except Exception as e:
                error = f"Query error: {str(e)}"
                logger.error(error)
                return ''

        @app.route('/intelligence/<string:intelligence_uuid>', methods=['GET'])
        def intelligence_viewer_api(intelligence_uuid: str):
            try:
                intelligence = self.intelligence_hub.get_intelligence(intelligence_uuid)
                if intelligence:
                    return default_article_render(intelligence)
                else:
                    return jsonify({"error": "Intelligence not found"}), 404
            except Exception as e:
                # logger.error(f'intelligence_viewer_api() error: {str(e)}', stack_info=True)
                print(str(e))
                traceback.print_exc()
                return jsonify({"error": "Server error"}), 500

        # --------------------------------------------------------------------------------------------------------------

        @app.route('/statistics/score_distribution.html', methods=['GET'])
        @WebServiceAccessManager.login_required
        def score_distribution_page():
            return get_statistics_page('/statistics/score_distribution')

        @app.route('/statistics/intelligence_statistics.html', methods=['GET'])
        @WebServiceAccessManager.login_required
        def intelligence_distribution_page():
            return get_intelligence_statistics_page()

        @app.route('/maintenance/export_mongodb.html', methods=['GET'])
        def export_mongodb_page():
            return render_template('export_mongodb.html')

    # ------------------------------------------------------------------------------------------------------------------

        @app.route('/statistics/score_distribution', methods=['GET', 'POST'])
        @WebServiceAccessManager.login_required
        def get_score_distribution():
            try:
                # Get query parameters
                start_time_str = request.args.get('start_time')
                end_time_str = request.args.get('end_time')

                if not start_time_str or not end_time_str:
                    return jsonify({
                        "error": "Both start_time and end_time parameters are required"
                    }), 400

                # Convert to datetime objects
                start_time = datetime.datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                end_time = datetime.datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))

                stat_engine = self.intelligence_hub.get_statistics_engine()
                score_distribution = stat_engine.get_score_distribution(start_time, end_time)

                # Convert to array format for charting
                chart_data = [
                    {"score": score, "count": count}
                    for score, count in score_distribution.items()
                ]

                return jsonify({
                    "success": True,
                    "time_range": {
                        "start": start_time_str,
                        "end": end_time_str
                    },
                    "distribution": score_distribution,
                    "chart_data": chart_data,
                    "total_records": sum(score_distribution.values())
                })

            except ValueError:
                return jsonify({
                    "error": "Invalid time format. Please use ISO format (e.g., '2024-01-01T00:00:00Z')"
                }), 400
            except Exception as e:
                logger.error(f"Error processing request: {str(e)}")
                return jsonify({
                    "error": "Internal server error",
                    "message": str(e)
                }), 500

        @app.route('/statistics/intelligence_distribution/hourly', methods=['GET'])
        def get_hourly_stats():
            """Get record counts grouped by hour for the specified time range"""
            start_time, end_time = self.get_time_range_params()

            stat_engine = self.intelligence_hub.get_statistics_engine()
            result = stat_engine.get_hourly_stats(start_time, end_time)

            return jsonify(result)

        @app.route('/statistics/intelligence_distribution/daily', methods=['GET'])
        def get_daily_stats():
            """Get record counts grouped by day for the specified time range"""
            start_time, end_time = self.get_time_range_params()

            stat_engine = self.intelligence_hub.get_statistics_engine()
            result = stat_engine.get_daily_stats(start_time, end_time)

            return jsonify(result)

        @app.route('/statistics/intelligence_distribution/weekly', methods=['GET'])
        def get_weekly_stats():
            """Get record counts grouped by week for the specified time range"""
            start_time, end_time = self.get_time_range_params()

            stat_engine = self.intelligence_hub.get_statistics_engine()
            result = stat_engine.get_weekly_stats(start_time, end_time)

            return jsonify(result)

        @app.route('/statistics/intelligence_distribution/monthly', methods=['GET'])
        def get_monthly_stats():
            """Get record counts grouped by month for the specified time range"""
            start_time, end_time = self.get_time_range_params()

            stat_engine = self.intelligence_hub.get_statistics_engine()
            result = stat_engine.get_monthly_stats(start_time, end_time)

            return jsonify(result)

        @app.route('/statistics/intelligence_distribution/summary', methods=['GET'])
        def get_stats_summary():
            """Get overall statistics for the specified time range"""
            start_time, end_time = self.get_time_range_params()

            stat_engine = self.intelligence_hub.get_statistics_engine()
            total_count, informant_stats = stat_engine.get_stats_summary(start_time, end_time)

            return jsonify({
                "total_count": total_count,
                "time_range": {
                    "start": start_time,
                    "end": end_time
                },
                "top_informants": informant_stats
            })

        @app.route('/maintenance/export_mongodb', methods=['POST'])
        @WebServiceAccessManager.login_required
        def export_mongodb():
            """Handle export request"""
            try:
                # Get parameters from request
                data = request.get_json()
                start_date = data['startDate']
                end_date = data['endDate']

                # 确保日期时间包含时区信息（添加'Z'表示UTC时间）
                if 'Z' not in start_date:
                    start_date += 'Z'
                if 'Z' not in end_date:
                    end_date += 'Z'

                # Create query based on date range (使用正确的ISODate格式)
                date_query = {
                    f"APPENDIX.{APPENDIX_TIME_ARCHIVED}": {
                        "$gte": {"$date": start_date},
                        "$lte": {"$date": end_date}
                    }
                }

                # Generate filename with timestamp
                start_str = start_date.split('T')[0].replace('-', '')
                end_str = end_date.split('T')[0].replace('-', '')
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"intelligence_archived_{start_str}_{end_str}_{timestamp}.json"

                os.makedirs('exports', exist_ok=True)
                output_path = os.path.join('exports', filename)
                output_path_abs = os.path.abspath(output_path)

                # Execute export
                success, message = export_mongodb_data(
                    uri='mongodb://localhost:27017',
                    db='IntelligenceIntegrationSystem',
                    collection='intelligence_archived',
                    output_file=output_path_abs,
                    query=date_query,
                    export_format="json"
                )

                if success:
                    return jsonify({
                        'status': 'success',
                        'message': message,
                        'filename': filename,
                        'path': output_path
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': message
                    }), 500

            except Exception as e:
                return jsonify({
                    'status': 'error',
                    'message': f'Server error: {str(e)}'
                }), 500

        @app.route('/download/<filename>')
        @WebServiceAccessManager.login_required
        def download_file(filename):
            """Download exported file"""
            try:
                if 'intelligence_archived' in filename:
                    # MongoDB export
                    file_dir = 'exports'
                else:
                    file_dir = 'download'

                return send_file(
                    os.path.join(file_dir, filename),
                    as_attachment=True
                )
            except FileNotFoundError:
                return jsonify({
                    'status': 'error',
                    'message': 'File not found'
                }), 404

    # ----------------------------------------------------------------

    def handle_error(self, error: str):
        print(f'Handle error in IntelligenceHubWebService: {error}')

    def _articles_to_rss_items(self, articles: dict | List[dict]) -> List[FeedItem]:
        # Using a fixed default time, combined with a unique UUID,
        # prevents RSS readers from mistakenly identifying article duplicates.
        default_date = datetime.datetime(1970, 1, 1)

        if not isinstance(articles, list):
            articles = [articles]
        try:
            rss_items = []
            for doc in articles:
                if 'EVENT_BRIEF' in doc and 'UUID' in doc:
                    rss_item = FeedItem(
                        guid=doc['UUID'],
                        title=doc.get('EVENT_TITLE', doc['EVENT_BRIEF']),
                        link=f"/intelligence/{doc['UUID']}",
                        description=doc['EVENT_BRIEF'],
                        pub_date=doc.get('APPENDIX', {}).get(APPENDIX_TIME_ARCHIVED, default_date))
                    rss_items.append(rss_item)
                else:
                    logger.warning(f'Warning: archived data field missing.')
            return rss_items
        except Exception as e:
            logger.error(f"Article to rss items failed: {str(e)}")
            return []

    def get_rendered_md_post(self, post_name: str) -> str:
        try:
            # Sanitize input and construct safe file path
            safe_article = post_name.replace('..', '').strip('/')  # Prevent directory traversal
            md_file_path = os.path.join('posts', f"{safe_article}.md")

            # Generate HTML from Markdown
            rendered_html_path = generate_html_from_markdown(md_file_path)

            if rendered_html_path:
                # Extract relative template path (remove 'templates/' prefix)
                template_path = os.path.relpath(
                    rendered_html_path,
                    start='templates'
                ).replace('\\', '/')  # Windows compatibility

                return render_template(template_path)
            else:
                return ''
        except Exception as e:
            logger.error(f'Invalid post: {post_name}')
            return ''

    def get_time_range_params(self):
        """
        Extract and validate time range parameters from request
        Returns start_time and end_time as datetime objects
        """
        start_str = request.args.get('start')
        end_str = request.args.get('end')

        if start_str:
            start_time = dateutil.parser.parse(start_str)
        else:
            # Default to 24 hours ago if no start time provided
            start_time = datetime.datetime.now() - datetime.timedelta(hours=24)

        if end_str:
            end_time = dateutil.parser.parse(end_str)
        else:
            # Default to current time if no end time provided
            end_time = datetime.datetime.now()

        return start_time, end_time

