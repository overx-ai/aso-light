from app.services.indices.base import IndexFetcher, IndexRecord
from app.services.indices.bigmac import BigMacFetcher
from app.services.indices.netflix import NetflixFetcher
from app.services.indices.ppp import PPPFetcher
from app.services.indices.refresh import IndexRefreshService
from app.services.indices.spotify import SpotifyFetcher

__all__ = [
    "BigMacFetcher",
    "IndexFetcher",
    "IndexRecord",
    "IndexRefreshService",
    "NetflixFetcher",
    "PPPFetcher",
    "SpotifyFetcher",
]
