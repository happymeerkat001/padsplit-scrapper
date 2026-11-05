"""Owner-only weather endpoint; disabled until explicitly configured."""
import os
from fastapi import APIRouter,Depends,HTTPException
from padsplit_scraper.weather import configured,fetch_weather


def make_weather_router(*,actor_dependency,repository_provider,run):
    router=APIRouter()
    @router.get('/v1/weather')
    def weather(identity=Depends(actor_dependency)):
        if identity.get('role')!='owner':raise HTTPException(403,'Access denied')
        settings=run(lambda:configured(os.environ))
        if settings is None:return {'status':'not_configured','days':[]}
        days=run(lambda:fetch_weather(settings))
        # Re-check after the external request so revoked access does not get a result.
        current=run(lambda:repository_provider().identity(identity['uid']))
        if not current or current.get('role')!='owner':raise HTTPException(403,'Access denied')
        return {'status':'ok','days':days}
    return router
