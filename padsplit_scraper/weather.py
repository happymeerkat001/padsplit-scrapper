"""Disabled-by-default weather boundary; location remains server-side."""
from datetime import date
import json
import math
import re
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES=65536
_COORD=re.compile(r'^-?(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?$')

class WeatherUnavailable(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

def configured(environ):
    """Return an explicit private region, or None while disabled."""
    if environ.get('PADSPLIT_WEATHER_ENABLE') != '1':return None
    def coordinate(name,maximum):
        value=environ.get(name)
        if not isinstance(value,str) or not _COORD.fullmatch(value):raise ValueError('Invalid weather configuration')
        parsed=float(value)
        if not math.isfinite(parsed) or abs(parsed)>maximum:raise ValueError('Invalid weather configuration')
        return value
    return (coordinate('PADSPLIT_WEATHER_LATITUDE',90),coordinate('PADSPLIT_WEATHER_LONGITUDE',180))

def fetch_weather(settings):
    """Read a bounded response from a fixed provider, without exposing location."""
    try:
        if not isinstance(settings,tuple) or len(settings)!=2:raise ValueError()
        lat,lon=settings
        # Revalidate even when called independently of configured().
        settings=configured({'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':lat,'PADSPLIT_WEATHER_LONGITUDE':lon})
        query=urllib.parse.urlencode({'latitude':settings[0],'longitude':settings[1],
            'daily':'temperature_2m_max,temperature_2m_min','temperature_unit':'fahrenheit','timezone':'UTC','forecast_days':'7'})
        request=urllib.request.Request('https://api.open-meteo.com/v1/forecast?'+query,headers={'Accept':'application/json'})
        opener=urllib.request.build_opener(NoRedirect())
        with opener.open(request,timeout=4) as response:
            if response.status!=200:raise ValueError()
            raw=response.read(MAX_RESPONSE_BYTES+1)
        if len(raw)>MAX_RESPONSE_BYTES:raise ValueError()
        data=json.loads(raw);daily=data['daily']; dates=daily['time']; highs=daily['temperature_2m_max']; lows=daily['temperature_2m_min']
        if not all(isinstance(x,list) for x in (dates,highs,lows)) or not 1<=len(dates)<=14 or len(dates)!=len(highs) or len(dates)!=len(lows):raise ValueError()
        result=[];seen=set()
        for day,high,low in zip(dates,highs,lows):
            if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day or day in seen:raise ValueError()
            if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>160 for v in (high,low)):raise ValueError()
            seen.add(day);result.append({'date':day,'high':float(high),'low':float(low)})
        return result
    except Exception:
        raise WeatherUnavailable('Weather service unavailable') from None
