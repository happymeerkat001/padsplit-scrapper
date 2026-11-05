"""Owner-only weather: no network by default and no location in responses."""
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.test_api import FakeRepository

class WeatherRouteTests(unittest.TestCase):
    def setUp(self):
        self.repo=FakeRepository()
        self.client=TestClient(create_app(repository=self.repo,verify_token=lambda t:t if t in self.repo.users else None,allowed_origins=['https://example.invalid']))
    def auth(self,uid):return {'Authorization':'Bearer '+uid}
    @patch.dict(os.environ, {'PADSPLIT_WEATHER_ENABLE':'0'}, clear=False)
    def test_default_disabled_no_network_and_other_roles_denied(self):
        with patch('backend.api.weather.fetch_weather') as fetch:
            self.assertEqual(self.client.get('/v1/weather').status_code,401)
            self.assertEqual(self.client.get('/v1/weather',headers=self.auth('viewer')).status_code,403)
            response=self.client.get('/v1/weather',headers=self.auth('owner'))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json(),{'status':'not_configured','days':[]})
            fetch.assert_not_called()
    @patch.dict(os.environ, {'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':'12.25','PADSPLIT_WEATHER_LONGITUDE':'-17.5'}, clear=False)
    def test_owner_success_no_location_and_revocation(self):
        data=[{'date':'2026-01-02','high':70.0,'low':42.0}]
        with patch('backend.api.weather.fetch_weather',return_value=data):
            response=self.client.get('/v1/weather',headers=self.auth('owner'))
        self.assertEqual(response.status_code,200);self.assertEqual(response.json(),{'status':'ok','days':data})
        self.assertNotIn('12.25',response.text);self.assertNotIn('-17.5',response.text)
        def revoke(_settings):
            self.repo.users['owner']['role']='viewer'
            return data
        with patch('backend.api.weather.fetch_weather',side_effect=revoke):
            self.assertEqual(self.client.get('/v1/weather',headers=self.auth('owner')).status_code,403)
    @patch.dict(os.environ, {'PADSPLIT_WEATHER_ENABLE':'1','PADSPLIT_WEATHER_LATITUDE':'bad','PADSPLIT_WEATHER_LONGITUDE':'-17.5'}, clear=False)
    def test_bad_configuration_fails_closed_and_redacts(self):
        with patch('backend.api.weather.fetch_weather') as fetch:
            response=self.client.get('/v1/weather',headers=self.auth('owner'))
            self.assertEqual(response.status_code,503);self.assertNotIn('-17.5',response.text);fetch.assert_not_called()

if __name__=='__main__':unittest.main()
