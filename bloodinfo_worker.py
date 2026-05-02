import datetime
import time
import json
import base64
import logging
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Util.Padding import pad
from curl_cffi import requests
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)

SALT_HEX = "3FF2EC019C627B945225DEBAD71A01B6985FE84C95A70EB132882F88C0A59A55"
PASSPHRASE = "bloodinfoNice123"
ITERATIONS = 10000
KEY_SIZE = 128

LOGIN_PAGE_URL = "https://bloodinfo.net/knrcbs/lo/login/loginPage.do"
LOGIN_ACTION_URL = "https://bloodinfo.net/knrcbs/lo/login/login.do"

DONATION_TYPE_FIELDS = {
    "whole_blood": ["RESERVABLECNT_50"],
    "plasma": ["RESERVABLECNT_71"],
    "platelet": ["RESERVABLECNT_72", "RESERVABLECNT_82"],
}

BASIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest"
}

    
class Worker:
    def __init__(self):
        self.session = requests.Session()

    def close(self):
        self.session.close()



    @staticmethod
    def encrypt_data(plain_text, iv_hex):
        salt = bytes.fromhex(SALT_HEX)
        iv = bytes.fromhex(iv_hex)
        key = PBKDF2(PASSPHRASE, salt, dkLen=KEY_SIZE // 8, count=ITERATIONS)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded_data = pad(plain_text.encode("utf-8"), AES.block_size)
        encrypted_bytes = cipher.encrypt(padded_data)
        return base64.b64encode(encrypted_bytes).decode("utf-8")

    def get_iv_and_mm(self):
        """
        로그인 페이지에 접속하여 bld_iv와 bld_iv_mm 값을 추출하는 함수입니다."""
        headers = BASIC_HEADERS.copy()
        headers["Referer"] = "https://bloodinfo.net/knrcbs/lo/login/loginPage.do?mi=1347"
        resp = self.session.get(LOGIN_PAGE_URL, headers=headers, allow_redirects=False)
        # 리다이렉트가 발생하면 수동으로 한 번만 따라감
        if 300 <= resp.status_code < 400 and "Location" in resp.headers:
            next_url = resp.headers["Location"]
            if not next_url.startswith("http"):
                next_url = "https://bloodinfo.net" + next_url
            resp = self.session.get(next_url, headers=headers, allow_redirects=False)
        soup = BeautifulSoup(resp.text, "html.parser")
        iv_input = soup.find("input", {"id": "bld_iv"})
        if not iv_input or not iv_input.get("value"):
            m = re.search(r"\$\('#bld_iv'\)\.val\('([0-9a-fA-F]+)'\)", resp.text)
            if m:
                iv = m.group(1)
            else:
                raise Exception("bld_iv 값을 찾을 수 없습니다.")
        else:
            iv = iv_input["value"]

        iv_mm_input = soup.find("input", {"id": "bld_iv_mm"})
        if not iv_mm_input or not iv_mm_input.get("value"):
            m_mm = re.search(r"\$\('#bld_iv_mm'\)\.val\('([0-9a-fA-F]+)'\)", resp.text)
            if m_mm:
                iv_mm = m_mm.group(1)
            else:
                raise Exception("bld_iv_mm 값을 찾을 수 없습니다.")
        else:
            iv_mm = iv_mm_input["value"]
        return iv, iv_mm

    def login(self, user_id: str, user_password: str) -> bool:
        iv, iv_mm = self.get_iv_and_mm()
        time.sleep(3)  # 너무 빠르게 요청하면 서버에서 차단할 수 있으므로 잠시 대기
        member_id_1 = self.encrypt_data(user_id, iv)
        member_pwd_1 = self.encrypt_data(user_password, iv)

        payload = {
            "agreAt": "",
            "sysId": "knrcbs",
            "loginType": "2",
            "bld_iv": iv,
            "bld_iv_mm": iv_mm,
            "member_id_1": member_id_1,
            "member_pwd_1": member_pwd_1,
            "security_level": "1",
            "member_id": "",
            "member_pwd": "",
        }

        headers = BASIC_HEADERS.copy()
        headers["Referer"] = "https://bloodinfo.net/knrcbs/lo/login/loginPage.do?mi=1347&security_level=1"
        resp = self.session.post(LOGIN_ACTION_URL, data=payload, headers=headers, allow_redirects=False)

        if resp.status_code != 200:
            logger.error("로그인 요청 실패: HTTP %s", resp.status_code)
            logger.error(resp.text)
            return False

        try:
            result = resp.json()
        except json.JSONDecodeError:
            logger.error("로그인 응답이 JSON이 아닙니다.")
            return False

        if result.get("resultCode") == -1:
            logger.error("로그인 실패: 아이디 또는 비밀번호 오류")
            return False

        logger.info("로그인 성공")
        return True

    def fetch_time_table(self, target_site: str, target_date: datetime.date) -> list[dict]:
        """
        헌혈의 집에서 예약 가능한 시간을 확인하는 함수입니다.
        target_site: 예약 가능한 헌혈의집 코드 (예: 부산 해운대센터 헌혈의집은 "51200558")
        target_date: 예약 가능한 날짜 (datetime.date 객체)
        """
        payload = {
            "selDt": target_date.strftime("%Y-%m-%d"),
            "sitecode": target_site,
            "nearNextBldDt": target_date.strftime("%Y-%m-%d"),
            "wbNextBldDt": target_date.strftime("%Y-%m-%d"),
            "plasmaNextBldDt": target_date.strftime("%Y-%m-%d"),
            "plateletNextBldDt": target_date.strftime("%Y-%m-%d"),
        }
        headers = BASIC_HEADERS.copy()
        headers["Referer"] = "https://bloodinfo.net/knrcbs/bh/resv/modResvBldHousInfoPage.do"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        resp = self.session.post("https://bloodinfo.net/knrcbs/bh/resv/setResvTimetbl.do", data=payload, headers=headers)

        if resp.status_code != 200:
            logger.error("예약 가능 시간 요청 실패: HTTP %s", resp.status_code)
            logger.error(resp.text)
            return []

        try:
            result = resp.json()
        except json.JSONDecodeError:
            logger.error("예약 가능 시간 응답이 JSON이 아닙니다.")
            return []

        if result.get("resultAt") != "Y":
            logger.warning("%s에 %s에서 예약 가능 여부를 확인할 수 없습니다.", target_date, target_site)
            return []

        return result.get("resvCheck", [])

    @staticmethod
    def find_available_slots(time_table: list[dict], donation_types: list[str]) -> list[str]:
        fields = []
        for donation_type in donation_types:
            fields.extend(DONATION_TYPE_FIELDS.get(donation_type, []))

        if not fields:
            return []

        available_times = []
        for slot in time_table:
            if any(slot.get(field, 0) > 0 for field in fields):
                available_times.append(slot.get("HHMM_TEXT", "알 수 없음"))
        return available_times


    @classmethod
    def get_sites_list(cls):
        """
        헌혈의집 목록을 가져오는 함수입니다.
        POST https://www.bloodinfo.net/knrcbs/bh/hous/selectBldHousListForMap.do
        referer https://www.bloodinfo.net/knrcbs/bh/hous/srchBldHousList.do
        """

        # payload reserveLocY=
        payload = {
            "reserveLocY": "",
        }
        headers = BASIC_HEADERS.copy()
        headers["Referer"] = "https://bloodinfo.net/knrcbs/bh/resv/modResvBldHousInfoPage.do"
        with requests.Session() as session:
            resp = session.post("https://bloodinfo.net/knrcbs/bh/hous/selectBldHousListForMap.do", data=payload, headers=headers)
            if resp.status_code == 200:
                try:
                    result = resp.json()
                    # {
                    #     "xssChk": "N",
                    #     "bldHousList": [
                    #         {
                    #             "sitecode": "51100001",
                    #             "sitename": "ì¤‘ì•™ì„¼í„°",
                    #             "orgcode": "001",
                    #             "orgname": "ì„œìš¸ì¤‘ì•™",
                    #             "telno": "02-6711-0185",
                    #             "address": "ì„œìš¸ ê°•ì„œêµ¬ ê³µí•­ëŒ€ë¡œ 591 ëŒ€í•œì ì‹­ìžì‚¬ ì„œìš¸ì¤‘ì•™í˜ˆì•¡ì› 3ì¸µ, ì—¼ì°½ì—­ 1ë²ˆ ì¶œêµ¬ ì§„í–‰ë°©í–¥ 200m",
                    #             "planYn": "N",
                    #             "latitude": "37.54813548333603",
                    #             "longitude": "126.8708456490883",
                    #             "bldproctypenames": "ì „í˜ˆ,í˜ˆìž¥,í˜ˆì†ŒíŒ,í˜ˆì†ŒíŒí˜ˆìž¥"
                    #         },
                    #         ...
                    #     ],
                    #    "resultAt": "Y",
                    #    "reserveLocY": ""
                    # }
                    bld_house_list = result.get("bldHousList", [])
                    return bld_house_list
                except json.JSONDecodeError:
                    logger.error("헌혈의집 목록 응답이 JSON이 아닙니다.")
                    return []
            else:
                logger.error("헌혈의집 목록 요청 실패: HTTP %s", resp.status_code)
                logger.error(resp.text)
                return []

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    # 테스트
    
    env_file = ".env"
    with open(env_file, "r") as f:
        # .env 파일에서 USER_ID와 USER_PASSWORD 값을 읽어옵니다.(json 아님)
        secret = {}
        for line in f:
            if line.strip() and not line.startswith("#"):
                key, value = line.strip().split("=", 1)
                secret[key] = value.strip('"').strip("'")  # 값에서 따옴표 제거

    user_id = secret["USER_ID"]
    user_pwd = secret["USER_PASSWORD"]
    target_date = datetime.date(2026, 4, 20)
    site_code = "51200558"  # 예시로 부산 해운대센터 헌혈의집 코드
    worker = Worker()
    try:
        worker.login(user_id, user_pwd)
        time_table = worker.fetch_time_table(site_code, target_date)
        available_slots = worker.find_available_slots(time_table, ["whole_blood", "plasma", "platelet"])
        print(f"{target_date}에 {site_code}에서 예약 가능한 시간대: {available_slots}")
    finally:
        worker.close()