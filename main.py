import os, json, datetime, yaml, time, copy
# from lib.Log import logger
from flask import Flask, request, render_template
from gevent.pywsgi import WSGIServer
from jinja2 import Environment, FileSystemLoader
from dateutil import parser
# python3.6
from http import HTTPStatus
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote_plus
from urllib.error import HTTPError
from settings import *
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import ssl
except ImportError:
    ssl = None

append_times = copy.deepcopy(MAX_REQUEST)
append_message = str()


def time_zone_conversion(utctime):
    format_time = parser.parse(utctime).strftime('%Y-%m-%dT%H:%M:%SZ')
    time_format = datetime.datetime.strptime(format_time, "%Y-%m-%dT%H:%M:%SZ")
    return str(time_format + datetime.timedelta(hours=8))


app = Flask(__name__)


class Sender:
    _address = None

    @staticmethod
    def request(url, method='GET', headers=None, params=None, data=None, files=False):
        """
        :param url:
        :param method:
        :param headers:
        :param params:
        :param data:
        :param files:
        :return:
        """

        # 发送地址链接拼接
        if url.startswith('/'):
            url = url.lstrip('/')
        full_url = "?".join([url, urlencode(params)]) if params else url
        try:
            if files:
                headers = {}
                headers.update({'Content-Type': 'application/zip'})
                data = data
            else:
                data = bytes(data, 'utf8')
            # 初始化请求参数
            req = Request(
                url=full_url, data=data,
                headers=headers, method=method,
            )
            ctx = ssl.SSLContext()
            return urlopen(req, timeout=10, context=ctx)
        except HTTPError as e:
            if e.code in [HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.INTERNAL_SERVER_ERROR]:
                print("服务异常，请检查：{}".format(e.reason))
                return False
            else:
                print("严重异常，请检查：{}".format(e.reason))
                return False


class NoticeSender:
    _sender = None
    _sender_config = None
    _write_path = None
    _req = None

    def _get_sender_config(self):
        """
        :return:
        """
        try:
            NOTICE_SETTINGS
        except NameError:
            raise NameError("需要定义：NOTICE_SETTINGS")

        if isinstance(NOTICE_SETTINGS, dict):
            self._sender_config = [NOTICE_SETTINGS]
        elif isinstance(NOTICE_SETTINGS, list):
            self._sender_config = NOTICE_SETTINGS
        else:
            raise TypeError('告警通知配置文件错误，请检查！')
        self._check_notice_config()
        self._req = Sender()

    def _check_notice_config(self):
        """
        :return:
        """

        for config in self._sender_config:
            for key, value in config.items():
                if key not in ['token', 'secret', 'msg_type']:
                    raise KeyError('Error key in config dict!')
                if not value:
                    raise ValueError('Error value for key:{}!'.format(key))

    def dingtalk_sender(self, title, msg, settings: dict, mentioned=None, is_all=True):
        """
        :param title:
        :param msg:
        :param settings:
        :param mentioned:
        :param is_all:
        :return:
        """
        import time
        import base64
        import hmac
        import hashlib
        headers = {'Content-Type': 'application/json'}
        _url = "https://oapi.dingtalk.com/robot/send"
        params = {'access_token': settings['token']}
        if 'secret' in settings.keys():
            timestamp = int(round(time.time() * 1000))
            secret_enc = settings['secret'].encode('utf-8')
            string_to_sign = '{}\n{}'.format(timestamp, settings['secret'])
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = quote_plus(base64.b64encode(hmac_code))
            params['timestamp'] = timestamp
            params['sign'] = sign
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": """## {}\n\n{}""".format(title, msg)
            }
        }
        if is_all or (not is_all and not mentioned):
            at = {
                "isAtAll": is_all
            }
        else:
            if not isinstance(mentioned, list):
                raise TypeError("消息接收人必须为列表!")
            at = {
                "atMobiles": mentioned,
                "isAtAll": is_all
            }
        data['at'] = at

        res = self._req.request(
            url=_url, params=params, data=json.dumps(data),
            headers=headers, method='POST'
        )
        result = json.loads(res.read().decode("UTF-8"))
        if result['errcode'] != 0:
            print("请求异常：{}".format(result['errmsg']))
            return False
        else:
            print("请求成功：{}".format(result['errmsg']))
            return True

    def wechat_sender(self, msg, settings: dict):
        """
        :param msg:
        :param settings:
        :return:
        """

        _url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
        params = {'key': settings['token'], 'debug': 1}
        headers = {'Content-Type': 'application/json'}
        res = self._req.request(
            url=_url, params=params, data=json.dumps(msg, ensure_ascii=False), headers=headers, method='POST'
        )
        result = json.loads(res.read().decode("UTF-8"))
        if result['errcode'] != 0:
            print("请求异常：{}".format(result['errmsg']))
            return False
        else:
            print("请求成功：{}".format(result['errmsg']))
            return True

    def create_temp(self, message: str, filename):
        import time

        if not self._write_path:
            self._write_path = './'
        else:
            if not os.path.exists(self._write_path):
                os.makedirs(self._write_path)
        current_files = os.path.join(self._write_path, "{}-{}.txt".format(filename, int(time.time())))
        try:
            with open(current_files, 'w', encoding='utf-8') as fff:
                fff.write(message)
            return current_files
        except Exception as error:
            print("创建文件失败:{},{}".format(current_files, error))
            return False

    @staticmethod
    def get_wechat_media(media_file, settings: dict):
        import requests
        _upload_media_url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media'
        if not os.path.exists(media_file):
            raise Exception("文件不��在:{}".format(media_file))
        params = {'key': settings['token'], 'type': 'file', 'debug': 1}
        with open(media_file, 'r') as fff:
            try:
                res = requests.post(
                    url="?".join([_upload_media_url, urlencode(params)]) if params else _upload_media_url,
                    files={'file': fff}
                )
                print(res.json())
                if res.status_code != 200 or res.json()['errcode']:
                    raise Exception(res.json()['errmsg'])
                os.remove(media_file)
                return res.json()
            except Exception as error:
                os.remove(media_file)
                print("读取临时文件失败:{}".format(error))
                raise Exception("上传文件失败！")

    def wechat_file_sender(self, msg: str, settings: dict, filename: str, mentioned=None, is_all=True):
        if is_all:
            mentioned = ["@all"]
        elif mentioned and not is_all:
            mentioned = mentioned
        else:
            mentioned = []
        _url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
        params = {'key': settings['token'], 'type': 'file'}
        headers = {'Content-Type': 'application/json'}
        media_file = self.create_temp(message=msg, filename=filename)
        if not media_file:
            return False

        res = self.get_wechat_media(media_file=media_file, settings=settings)
        data = {
            "msgtype": "file",
            "file": {
                "media_id": res['media_id'],
                "mentioned_mobile_list": mentioned
            }
        }
        res = self._req.request(
            url=_url, method='POST', headers=headers,
            params=params, data=json.dumps(data)
        )
        if os.path.exists(media_file):
            os.remove(media_file)
        return res

    def dingtalk_file_sender(self):
        pass

    def sender(self, title, msg):
        """
        :param title:
        :param msg:
        :return:
        """
        thead_list = list()
        self._get_sender_config()
        for setting in self._sender_config:
            with ThreadPoolExecutor(max_workers=3) as worker:
                args = (msg, setting)
                if setting['msg_type'] == 'WECHAT_ROBOT':
                    res = worker.submit(self.wechat_sender, *args)
                elif setting['msg_type'] == 'DINGTALK_ROBOT':
                    res = worker.submit(self.dingtalk_sender, *args)
                else:
                    raise Exception('发送类型错误！')
                thead_list.append(res)

        for competed in as_completed(thead_list, timeout=10):
            print(competed.result())

    def sender_file(self, msg, filename, mentioned=None, is_all=True):
        """
        :param msg:
        :param mentioned:
        :param is_all:
        :param filename
        :return:
        """
        thead_list = list()
        self._get_sender_config()
        for setting in self._sender_config:
            with ThreadPoolExecutor(max_workers=3) as worker:
                args = (msg, setting, filename, mentioned, is_all)
                if setting['msg_type'] == 'WECHAT_ROBOT':
                    res = worker.submit(self.wechat_file_sender, *args)
                elif setting['msg_type'] == 'DINGTALK_ROBOT':
                    res = worker.submit(self.dingtalk_sender, *args)
                else:
                    raise Exception('发送类型错误！')
                thead_list.append(res)

        for competed in as_completed(thead_list, timeout=10):
            print(competed.result())


class ParseingTemplate:
    def __init__(self, templatefile):
        self.templatefile = templatefile

    def template(self, **kwargs):
        try:
            env = Environment(loader=FileSystemLoader('templates'))
            template = env.get_template(self.templatefile)
            template_content = template.render(kwargs)
            return template_content
        except Exception as error:
            raise error


def write_html_file(filename, content):
    """
    :param filename:
    :param content:
    :return:
    """
    try:
        with open(filename, 'w', encoding='utf-8') as fff:
            fff.write(content)
    except Exception as error:
        print("写入文件失败：{},原因:".format(filename, error))


def get_email_conf(file, email_name=None, action=0):
    """
    :param file: yaml格式的文件类型
    :param email_name: 发送的邮件列表名
    :param action: 操作类型，0: 查询收件人的邮件地址列表, 1: 查询收件人的列表名称, 2: 获取邮件账号信息
    :return: 根据action的值，返回不通的数据结构
    """
    try:
        with open(file, 'r', encoding='utf-8') as fr:
            read_conf = yaml.safe_load(fr)
            if action == 0:
                for email in read_conf['email']:
                    if email['name'] == email_name:
                        return email['receive_addr']
                    else:
                        print("%s does not match for %s" % (email_name, file))
                else:
                    print("No recipient address configured")
            elif action == 1:
                return [items['name'] for items in read_conf['email']]
            elif action == 2:
                return read_conf['send']
    except KeyError:
        print("%s not exist" % email_name)
        exit(-1)
    except FileNotFoundError:
        print("%s file not found" % file)
        exit(-2)
    except Exception as e:
        raise e


def count_alert(message, status="firing"):
    """
    :param message:
    :param status:
    :return:
    """
    result = 0
    if 'alerts' not in message:
        return result
    for items in message['alerts']:
        if items['status'] == status:
            result += 1
    return result


def format_message(message, full_url):
    """
    :param message:
    :param full_url:
    :return:
    """
    alert = count_alert(message=message)
    resolved = count_alert(message=message, status='resolved')
    if alert > 0:
        msg = "正在告警中：存在告警{}条，恢复正常{}条,请根据情况查看监控！".format(alert, resolved)
    else:
        msg = "告警已恢复：存在告警{}条，恢复正常{}条,请根据情况查看监控！".format(alert, resolved)

    data = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "news_notice",
            "source": {
                "icon_url": "https://www.kaiyihome.com/favicon.ico",
                "desc": "Prometheus-监控告警",
                "desc_color": 0
            },
            "main_title": {
                "title": "正在使用新TSP监控告警通知",
                "desc": "智能软件部-运维组维护"
            },
            "card_image": {
                "url": "https://grafana.com/api/dashboards/6417/logos/large",
                "aspect_ratio": 2.25
            },
            "vertical_content_list": [
                {
                    "title": "告警统计",
                    "desc": msg
                }
            ],
            "jump_list": [
                {
                    "type": 1,
                    "url": "https://grafana.newtsp.newcowin.com",
                    "title": "grafana地址"
                }, {
                    "type": 1,
                    "url": "https://prometheus.newtsp.newcowin.com",
                    "title": "prometheus地址"
                }, {
                    "type": 1,
                    "url": full_url,
                    "title": "告警内容展示地址"
                }

            ],
            "card_action": {
                "type": 1,
                "url": "https://grafana.newtsp.newcowin.com"
            }
        }
    }
    return data


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        n = NoticeSender()
        prometheus_data = json.loads(request.data)
        # 时间转换，转换成东八区时间
        for k, v in prometheus_data.items():
            if k == 'alerts':
                for items in v:
                    if items['status'] == 'firing':
                        items['startsAt'] = time_zone_conversion(items['startsAt'])
                    else:
                        items['startsAt'] = time_zone_conversion(items['startsAt'])
                        items['endsAt'] = time_zone_conversion(items['endsAt'])
        # team_name = prometheus_data["commonLabels"]["team"]
        team_name = "wechat_webhook"
        generate_html_template_subj = ParseingTemplate('html_template_firing.html')
        html_template_content = generate_html_template_subj.template(
            prometheus_monitor_info=prometheus_data
        )
        print("当前消息内容：{}".format(prometheus_data))
        filename = os.path.join('templates', "{}.html".format(int(time.time())))
        url = os.path.join('show', "{}".format(int(time.time())))
        full_url = os.path.join(HOST, url)
        write_html_file(filename=filename, content=html_template_content)
        data = format_message(message=prometheus_data, full_url=full_url)
        n.sender(title="新TSP生产环境告警", msg=data)
        return prometheus_data
    except Exception as e:
        raise e


@app.route('/graylog', methods=['POST'])
def graylog_alert():
    n = NoticeSender()
    global append_message, append_times
    json_data = request.json
    if "kubernetes_namespace" in json_data['event']['fields'].keys():
        namespace = json_data['event']['fields']['kubernetes_namespace']
    elif "filebeat_kubernetes_namespace" in json_data['event']['fields'].keys():
        namespace = json_data['event']['fields']['filebeat_kubernetes_namespace']
    else:
        namespace = "未知"
    if "kubernetes_container_name" in json_data['event']['fields'].keys():
        service_name = json_data['event']['fields']['kubernetes_container_name']
    elif "filebeat_kubernetes_container_name" in json_data['event']['fields'].keys():
        service_name = json_data['event']['fields']['filebeat_kubernetes_container_name']
    else:
        service_name = "未知"
    if "message" in json_data['event']['fields'].keys():
        message = json_data['event']['fields']['message']
    else:
        message = "未知"

    filename = "graylog_alert_{}".format(str(time.time()))
    if append_times > 0:
        append_message = "\n%s %s %s %s\n%s\n%s %s %s %s\n%s" % (
            20 * "+", namespace, service_name, 20 * "+",
            message,
            20 * "-", namespace, service_name, 20 * "-",
            append_message
        )
        append_times -= 1
        print("当前消息容量为：%d" % append_times)
        return json_data
    elif append_times == 0:
        print("消息容量已满，即将发送:%d" % append_times)
        try:
            append_times = copy.deepcopy(MAX_REQUEST)
            n.sender_file(msg=append_message, filename=filename)
            append_message = str()
            return json_data
        except Exception as e:
            return "上传和发送异常：{}".format(e)

    else:
        raise ValueError("Attempted to append_times")
    return json_data


@app.route('/graylog_time', methods=['POST'])
def graylog_alert_time():
    n = NoticeSender()
    json_data = request.json
    print(json_data)
    if "kubernetes_namespace" in json_data['event']['fields'].keys():
        namespace = json_data['event']['fields']['kubernetes_namespace']
    elif "filebeat_kubernetes_namespace" in json_data['event']['fields'].keys():
        namespace = json_data['event']['fields']['filebeat_kubernetes_namespace']
    else:
        namespace = "未知"
    if "kubernetes_container_name" in json_data['event']['fields'].keys():
        service_name = json_data['event']['fields']['kubernetes_container_name']
    elif "filebeat_kubernetes_container_name" in json_data['event']['fields'].keys():
        service_name = json_data['event']['fields']['filebeat_kubernetes_container_name']
    else:
        service_name = "未知"
    if json_data['backlog']:
        # if json_data['event']['backlog']:
        message = ""
        for x in json_data['backlog']:
            message += "\n%s".format(x['message'])
    else:
        if "message" in json_data['event']['fields'].keys():
            message = json_data['event']['fields']['message']
        else:
            message = "未知"

    filename = "{}_{}".format(namespace, service_name)
    try:
        n.sender_file(msg=message, filename=filename)
    except Exception as e:
        print(e)
    return json_data


@app.route("/show/<pages>")
def direct_show(pages):
    pages_params = pages.split('.')
    if len(pages_params) > 1:
        return "请求地址应该为：{}/{}".format(HOST, pages_params[0])
    else:
        if not os.path.exists(os.path.join('templates', pages)):
            return "请求内容不存在，或者记录已超时删除，请检查！"
        else:
            return render_template("{}.html".format(pages))


if __name__ == '__main__':
    print("Server started: 0.0.0.0:5000")
    WSGIServer(('0.0.0.0', 5000), app).serve_forever()
