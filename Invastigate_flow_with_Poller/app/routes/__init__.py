from app.routes.health import router as health_router
from app.routes.normalization import router as normalization_router
from app.routes.correlation import router as correlation_router
from app.routes.error_analysis import router as error_analysis_router
from app.routes.rca import router as rca_router
from app.routes.recommendation import router as recommendation_router
from app.routes.orchestrator import router as orchestrator_router
from app.routes.traces import router as traces_router
from app.routes.analyze import router as analyze_router
from app.routes.monitor import router as monitor_router

__all__ = [
    "health_router", "normalization_router", "correlation_router",
    "error_analysis_router", "rca_router", "recommendation_router",
    "orchestrator_router", "traces_router", "analyze_router",
    "monitor_router",
]
