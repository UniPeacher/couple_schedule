"""Scoped settings: independent validation and writes, no secret round-tripping."""
import re
from datetime import date
from urllib.parse import urlparse

class Error(ValueError):
    def __init__(self,message,status=400):
        super().__init__(message);self.status=status


def payload(conn,uid):
    users={r['uid']:dict(uid=r['uid'],name=r['name'],week1=r['week1'],wx_configured=bool(r['wx_uid']))
           for r in conn.execute('SELECT uid,name,week1,wx_uid FROM users')}
    meta={r['key']:r['value'] for r in conn.execute('SELECT key,value FROM meta')}
    prefs=dict(window_start=meta.get('window_start','08:00'),window_end=meta.get('window_end','22:00'),min_gap=int(meta.get('min_gap','20')))
    if uid=='a':
        prefs.update(llm_api_base=meta.get('llm_api_base',''),llm_model=meta.get('llm_model','deepseek-chat'),llm_key_configured=bool(meta.get('llm_api_key','')))
    return dict(ok=True,is_admin=uid=='a',users=users,settings=prefs)


def text(body,key,limit,required=False):
    v=body.get(key,'')
    if not isinstance(v,str) or len(v.strip())>limit:
        raise Error('字段 '+key+' 长度或类型无效')
    v=v.strip()
    if required and not v:raise Error('请填写昵称')
    return v


def flag(body,key):
    v=body.get(key,False)
    if type(v) is not bool:raise Error('开关须为布尔值')
    return v


def apply(conn,uid,body):
    if not isinstance(body,dict):raise Error('请求须为 JSON 对象')
    scope=body.get('scope')
    allowed={
        'profile':{'scope','name_a','name_b'},
        'schedule':{'scope','week1_a','week1_b','window_start','window_end','min_gap'},
        'notifications':{'scope','wx_token','clear_wx'},
        'ai':{'scope','llm_api_base','llm_api_key','llm_model','clear_llm_key'}
    }
    if scope not in allowed or set(body)-allowed[scope]:raise Error('设置分组或字段无效')
    updates=[]
    if scope=='profile':
        a,b=text(body,'name_a',30,True),text(body,'name_b',30,True)
        conn.execute("UPDATE users SET name=? WHERE uid='a'",(a,))
        conn.execute("UPDATE users SET name=? WHERE uid='b'",(b,))
    elif scope=='schedule':
        dates=[]
        for key in ('week1_a','week1_b'):
            v=text(body,key,10)
            if v:
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):raise ValueError
                    d=date.fromisoformat(v)
                except ValueError:raise Error('校历日期无效')
                if d.weekday()!=0:raise Error('第 1 周开始日期请选择周一')
            dates.append(v)
        start,end=text(body,'window_start',5),text(body,'window_end',5)
        def valid_time(v):return bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v))
        if not valid_time(start) or not valid_time(end) or start>=end:raise Error('统计结束时间必须晚于开始时间')
        gap=body.get('min_gap')
        if type(gap) is not int or not 5<=gap<=180:raise Error('最短空闲须为 5～180 的整数分钟')
        conn.execute("UPDATE users SET week1=? WHERE uid='a'",(dates[0],))
        conn.execute("UPDATE users SET week1=? WHERE uid='b'",(dates[1],))
        updates=[('window_start',start),('window_end',end),('min_gap',str(gap))]
    elif scope=='notifications':
        token=text(body,'wx_token',100);clear=flag(body,'clear_wx')
        if clear and token:raise Error('清除和填写新 Token 不能同时选择')
        if clear or token:conn.execute('UPDATE users SET wx_uid=? WHERE uid=?',('' if clear else token,uid))
    elif scope=='ai':
        if uid!='a':raise Error('仅管理员可修改 AI 配置',403)
        base=text(body,'llm_api_base',200);model=text(body,'llm_model',60);key=text(body,'llm_api_key',200)
        clear=flag(body,'clear_llm_key')
        if clear and key:raise Error('清除和填写新密钥不能同时选择')
        if base:
            p=urlparse(base)
            if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise Error('API 地址须为有效的 HTTP(S) 地址')
        updates=[('llm_api_base',base),('llm_model',model or 'deepseek-chat')]
        if clear or key:updates.append(('llm_api_key','' if clear else key))
        conn.execute('DELETE FROM summary_cache')
    for k,v in updates:
        conn.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
    return dict(ok=True,scope=scope)
