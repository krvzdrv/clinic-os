#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-проверка новых форм: измерение с запятой + назначение из каталога."""
import sys, os, re, urllib.request, urllib.parse

# Обход прокси для localhost
proxy_handler = urllib.request.ProxyHandler({})
opener = urllib.request.build_opener(proxy_handler)

base = 'http://127.0.0.1:5566'

def get(url):
    return opener.open(url).read().decode()

def post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    return opener.open(req).read().decode()

html = get(base + '/')
pid = re.findall(r'/patient/(p-[a-f0-9]+)', html)[0]
page = get(base + '/patient/' + pid)
eids = re.findall(r'name="encounter_id" value="(e-[a-f0-9]+)"', page)
eid = eids[0]
print('patient', pid, 'encounter', eid)

# 1) Измерение с запятой: температура 38,4
post(base + '/patient/' + pid + '/observation',
     {'encounter_id': eid, 'code': '8310-5', 'value_numeric': '38,4', 'date': ''})
page2 = get(base + '/patient/' + pid)
print('temp 38.4 normalized+stored:', '38.4' in page2)
print('interpretation high:', 'badge red">\u2191' in page2)

# 2) Назначение из каталога: J01CA04, поля пустые -> авто-подстановка
post(base + '/patient/' + pid + '/medication',
     {'encounter_id': eid, 'code': 'J01CA04', 'display': '', 'dose': '',
      'frequency': '', 'route': 'oral', 'med_date': '', 'period_end': ''})
page3 = get(base + '/patient/' + pid)
print('amoxicillin stored:', '\u0410\u043c\u043e\u043a\u0441\u0438\u0446\u0438\u043b\u043b\u0438\u043d' in page3)
print('default dose 500 \u043c\u0433 filled:', '500 \u043c\u0433' in page3)
print('default freq filled:', '3 \u0440\u0430\u0437\u0430 \u0432 \u0434\u0435\u043d\u044c' in page3)

# 3) Проверим, что дашборд тоже грузится без ошибок
dash = get(base + '/')
print('dashboard ok, no traceback:', 'Traceback' not in dash and 'patients' in dash.lower())
print('DONE')
