import httpx


url = 'https://sehuatang.org/forum-103-1.html'
proxies = 'http://127.0.0.1:7890'


headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Cookie": "cPNj_2132_saltkey=c9M8E7iS; cPNj_2132_lastvisit=1714789326; cPNj_2132_atarget=1; cPNj_2132_visitedfid=103; cf_clearance=_IGpDdKiEmTaiSTZfqFeA5h.0W3UqnxiqoT_XTH7C54-1714960590-1.0.1.1-b_kJg5EvoNRqQybLbH7BGsVWXZlkhJRJSUv84vUei6uZon7u7PNV.buQ_eWU3wX6IHr8weOKKIgKlGJQ9oO91Q; cf_clearance=ITAyrRi6FJcqXFZu8TmeNQ7jpgt3AaNBC_YgkXCI_dQ-1716342025-1.0.1.1-qOOkQl7hj42rCUEdCpP4.1sGMXhVPD8VFNKnIFnoetpDK3qhemIGVKaPUGxMCsHo3gHGmUTCiu2mgsbh0I934w; _safe=Cx0IS6BZ06htdbEX; cPNj_2132_lastact=1716343313%09forum.php%09forumdisplay; cPNj_2132_st_t=0%7C1716343313%7Ccabc50fc537ce5b0d2b5baeb77f9f1c0; cPNj_2132_forum_lastvisit=D_103_1716343313; cPNj_2132_lastfp=6d028cd238c818a921d75feb585fbcec"
    }

page_cookies = "cPNj_2132_saltkey=c9M8E7iS; cPNj_2132_lastvisit=1714789326; cPNj_2132_atarget=1; cPNj_2132_visitedfid=103; cf_clearance=_IGpDdKiEmTaiSTZfqFeA5h.0W3UqnxiqoT_XTH7C54-1714960590-1.0.1.1-b_kJg5EvoNRqQybLbH7BGsVWXZlkhJRJSUv84vUei6uZon7u7PNV.buQ_eWU3wX6IHr8weOKKIgKlGJQ9oO91Q; cf_clearance=ITAyrRi6FJcqXFZu8TmeNQ7jpgt3AaNBC_YgkXCI_dQ-1716342025-1.0.1.1-qOOkQl7hj42rCUEdCpP4.1sGMXhVPD8VFNKnIFnoetpDK3qhemIGVKaPUGxMCsHo3gHGmUTCiu2mgsbh0I934w; _safe=Cx0IS6BZ06htdbEX; cPNj_2132_lastact=1716343313%09forum.php%09forumdisplay; cPNj_2132_st_t=0%7C1716343313%7Ccabc50fc537ce5b0d2b5baeb77f9f1c0; cPNj_2132_forum_lastvisit=D_103_1716343313; cPNj_2132_lastfp=6d028cd238c818a921d75feb585fbcec"


# with httpx.Client(headers=headers,proxies=proxies) as client:
#     response = client.get(url)


response = httpx.get(url, proxies=proxies, headers=headers)

if response.status_code == 200:
    print('请求成功！')
    print('响应内容：', response.text)
else:
    print(f'请求失败，状态码：{response.status_code} {response.text}' )
