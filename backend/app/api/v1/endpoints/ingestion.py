from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep
from app.schemas.data import DataStatusOut, IngestOut, IngestRequest

router = APIRouter(prefix="/data", tags=["data"])


@router.post(
    "/ingest",
    response_model=IngestOut,
    responses={502: {"model": IngestOut, "description": "Every requested dataset failed upstream"}},
)
async def ingest(container: ContainerDep, body: IngestRequest | None = None) -> IngestOut | JSONResponse:
    """Fetch and store FUELHH actuals and WINDFOR publications. Safe to repeat.

    Without dates, covers the default rolling window plus the forecast publication lookback.
    """
    body = body or IngestRequest()
    try:
        report = await container.ingestion.ingest(body.start_date, body.end_date, tuple(body.datasets))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    out = IngestOut.of(report)
    if out.status == "failed":
        return JSONResponse(status_code=502, content=out.model_dump(mode="json"))
    return out


@router.get("/status", response_model=DataStatusOut)
async def data_status(container: ContainerDep) -> DataStatusOut:
    """Stored data coverage, gaps, alignment status and readiness for the default analysis."""
    return DataStatusOut.of(container.status.status())
