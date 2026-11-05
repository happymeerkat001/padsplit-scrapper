"""Weather network boundary: fictional inputs, no live provider calls."""
import io
import json
import os
import unittest
from unittest.mock import patch
from padsplit_scraper import weather

class Response:
    status=200
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,size):return self.data[:size]

class WeatherBoundaryTests(unittest.TestCase):
    def test_disabled_and_invalid_configuration_never_contact_provider(self):
        with patch('padsplit_scraper.weather.urllib.request.build_opener') as opener:
            self.assertIsNone(weather.configured({}))
            for env in ({'PADSPLIT_WEATHER_ENABLE':'1'}, {'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':'nan','PADSPLIT_WEATHER_LONGITUDE':'0'}, {'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':'91','PADSPLIT_WEATHER_LONGITUDE':'0'}):
                with self.assertRaises(ValueError):weather.configured(env)
            opener.assert_not_called()
    def test_fixed_provider_validates_response_and_limits_size(self):
        settings=weather.configured({'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':'12.25','PADSPLIT_WEATHER_LONGITUDE':'-17.5'})
        sample={'daily':{'time':['2026-01-02'],'temperature_2m_max':[70],'temperature_2m_min':[42]}}
        with patch('padsplit_scraper.weather.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value=Response(json.dumps(sample).encode())
            self.assertEqual(weather.fetch_weather(settings),[{'date':'2026-01-02','high':70.0,'low':42.0}])
            self.assertEqual(factory.return_value.open.call_args.kwargs['timeout'],4)
            factory.return_value.open.return_value=Response(b'x'*(weather.MAX_RESPONSE_BYTES+1))
            with self.assertRaises(weather.WeatherUnavailable):weather.fetch_weather(settings)
    def test_invalid_provider_response_and_redirect_are_rejected(self):
        settings=weather.configured({'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':'0','PADSPLIT_WEATHER_LONGITUDE':'0'})
        with patch('padsplit_scraper.weather.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value=Response(b'{"daily":{"time":["2026-01-02"],"temperature_2m_max":[999],"temperature_2m_min":[1]}}')
            with self.assertRaises(weather.WeatherUnavailable):weather.fetch_weather(settings)
        handler=weather.NoRedirect()
        self.assertIsNone(handler.redirect_request(None,None,302,None,None,'https://unexpected.invalid/'))

if __name__=='__main__':unittest.main()
