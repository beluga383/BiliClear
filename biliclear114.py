import json
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from os import chdir, environ
from os.path import exists, dirname, abspath
import requests
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union

import biliauth
import gpt
import gui_config
import syscmds
import checker
from compatible_getpass import getpass

# Logging setup
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Exception handling
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        logging.info("^C")
        sys.exit(0)
    else:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = handle_exception

# Define data classes for configuration
@dataclass
class Config:
    sender_email: str
    sender_password: str
    headers: Dict[str, str]
    smtp_server: str
    smtp_port: int
    bili_report_api: bool
    csrf: str
    reply_limit: int
    enable_gpt: bool
    gpt_apibase: str
    gpt_proxy: str
    gpt_apikey: str
    gpt_model: str
    enable_email: bool
    enable_check_lv2avatarat: bool
    enable_check_replyimage: bool

    @classmethod
    def from_dict(cls, data: Dict[str, Union[str, int, bool]]):
        return cls(
            sender_email=data.get("sender_email", ""),
            sender_password=data.get("sender_password", ""),
            headers=data.get("headers", {}),
            smtp_server=data.get("smtp_server", ""),
            smtp_port=data.get("smtp_port", 465),
            bili_report_api=data.get("bili_report_api", False),
            csrf=data.get("csrf", ""),
            reply_limit=data.get("reply_limit", 100),
            enable_gpt=data.get("enable_gpt", False),
            gpt_apibase=data.get("gpt_apibase", gpt.openai.api_base),
            gpt_proxy=data.get("gpt_proxy", gpt.openai.proxy),
            gpt_apikey=data.get("gpt_apikey", ""),
            gpt_model=data.get("gpt_model", "gpt-4o-mini"),
            enable_email=data.get("enable_email", True),
            enable_check_lv2avatarat=data.get("enable_check_lv2avatarat", False),
            enable_check_replyimage=data.get("enable_check_replyimage", False),
        )

    def to_dict(self) -> Dict[str, Union[str, int, bool]]:
        return {
            "sender_email": self.sender_email,
            "sender_password": self.sender_password,
            "headers": self.headers,
            "smtp_server": self.smtp_server,
            "smtp_port": self.smtp_port,
            "bili_report_api": self.bili_report_api,
            "csrf": self.csrf,
            "reply_limit": self.reply_limit,
            "enable_gpt": self.enable_gpt,
            "gpt_apibase": self.gpt_apibase,
            "gpt_proxy": self.gpt_proxy,
            "gpt_apikey": self.gpt_apikey,
            "gpt_model": self.gpt_model,
            "enable_email": self.enable_email,
            "enable_check_lv2avatarat": self.enable_check_lv2avatarat,
            "enable_check_replyimage": self.enable_check_replyimage
        }

def save_config(config: Config):
    with open("./config.json", "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=4, ensure_ascii=False)

def load_config() -> Config:
    with open("./config.json", "r", encoding="utf-8") as f:
        return Config.from_dict(json.load(f))

def get_csrf(cookie: str) -> str:
    match = re.search(r"bili_jct=(.*?);", cookie)
    if match:
        return match.group(1)
    else:
        raise ValueError("Bilibili Cookie格式错误")

def check_smtp_password(config: Config) -> bool:
    try:
        with smtplib.SMTP_SSL(config.smtp_server, config.smtp_port) as smtp_con:
            smtp_con.login(config.sender_email, config.sender_password)
        return True
    except smtplib.SMTPAuthenticationError:
        logging.error("SMTP Authentication Error")
        return False

def get_cookie_from_user() -> str:
    if not environ.get("qt_gui", False):
        if input("\n是否使用二维码登录B站, 默认为是(y/n): ").lower() == "n":
            return getpass("Bilibili cookie: ")
        else:
            return biliauth.bilibiliAuth()
    else:
        return gui_config.get_cookie_from_gui()

def check_cookie(headers: Dict[str, str], csrf: str) -> bool:
    response = requests.get(
        "https://passport.bilibili.com/x/passport-login/web/cookie/info",
        headers=headers,
        data={"csrf": csrf}
    )
    result = response.json()
    return result["code"] == 0 and not result.get("data", {}).get("refresh", True)

def get_videos(headers: Dict[str, str]) -> List[str]:
    response = requests.get("https://app.bilibili.com/x/v2/feed/index", headers=headers)
    return [item["param"] for item in response.json().get("data", {}).get("items", []) if item.get("can_play", 0)]

def get_replies(avid: int, headers: Dict[str, str], reply_limit: int) -> List[Dict]:
    replies = []
    page = 1
    while page * 20 <= reply_limit:
        time.sleep(0.4)
        response = requests.get(
            f"https://api.bilibili.com/x/v2/reply?type=1&oid={avid}&nohot=1&pn={page}&ps=20",
            headers=headers
        )
        result = response.json()
        if not result["data"].get("replies"):
            break
        replies.extend(result["data"]["replies"])
        page += 1
    return replies

def is_porn(text: str, checker: checker.Checker) -> bool:
    return checker.check(text)

def req_bili_report_reply(data: Dict[str, str], rule: Optional[str], headers: Dict[str, str], csrf: str):
    response = requests.post(
        "https://api.bilibili.com/x/v2/reply/report",
        headers=headers,
        data={
            "type": 1,
            "oid": data["oid"],
            "rpid": data["rpid"],
            "reason": 0,
            "csrf": csrf,
            "content": f"""
举报原因: 色情, 或...
程序匹配到的规则: {rule}
(此举报信息自动生成, 可能会存在误报)
"""
        }
    )
    result = response.json()
    if result["code"] not in (0, 12019):
        logging.error(f"B站举报API调用失败, 返回体：{result}")
    elif result["code"] == 0:
        logging.info("Bilibili举报API调用成功")
    elif result["code"] == 12019:
        logging.warning("举报过于频繁, 等待60s")
        time.sleep(60)
        req_bili_report_reply(data, rule, headers, csrf)

def report_reply(data: Dict[str, str], r: Optional[str], config: Config):
    report_text = f"""
违规用户UID：{data["mid"]}
违规信息发布形式：评论, (动态)
问题描述：破坏了B站和互联网的和谐环境
诉求：移除违规内容，封禁账号

评论数据内容(B站API返回, x/v2/reply):
`
{json.dumps(data, ensure_ascii=False, indent=4)}
`

(此举报信息自动生成, 可能会存在误报)
评论内容匹配到的规则: {r}
"""
    logging.info(f"\n违规评论: {repr(data['content']['message'])}")
    logging.info(f"规则: {r}")

    if config.enable_email:
        msg = MIMEText(report_text, "plain", "utf-8")
        msg["From"] = Header("Report", "utf-8")
        msg["To"] = Header("Bilibili", "utf-8")
        msg["Subject"] = Header("违规内容举报", "utf-8")
        try:
            with smtplib.SMTP_SSL(config.smtp_server, config.smtp_port) as smtp_con:
                smtp_con.login(config.sender_email, config.sender_password)
                smtp_con.sendmail(config.sender_email, ["help@bilibili.com"], msg.as_string())
        except (smtplib.SMTPException, ssl.SSLError) as e:
            logging.error(f"邮件发送失败: {e}")
