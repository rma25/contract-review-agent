import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from app.agent import AnalysisRunner
from app.api.deps import get_analysis_runner, get_analysis_service, get_contract_service
from app.repositories.contract_repository import InvalidStatusTransitionError
from app.schemas.contract import ContractRead, RiskFlagRead
from app.services.analysis_service import AnalysisService, ContractNotFoundError
from app.services.contract_service import ContractService
from app.services.storage import EmptyFileError, FileTooLargeError, UnsupportedFileTypeError

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.post("", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
def upload_contract(
    file: UploadFile = File(...),
    service: ContractService = Depends(get_contract_service),
) -> ContractRead:
    try:
        contract = service.upload(file.filename or "", file.file)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except EmptyFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ContractRead.model_validate(contract)


@router.get("/{contract_id}", response_model=ContractRead)
def get_contract(
    contract_id: uuid.UUID,
    service: ContractService = Depends(get_contract_service),
) -> ContractRead:
    contract = service.get(contract_id)
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return ContractRead.model_validate(contract)


@router.post(
    "/{contract_id}/analyze", response_model=ContractRead, status_code=status.HTTP_202_ACCEPTED
)
def analyze_contract(
    contract_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    service: AnalysisService = Depends(get_analysis_service),
    runner: AnalysisRunner = Depends(get_analysis_runner),
) -> ContractRead:
    """Start analysis in the background. Poll GET /contracts/{id} for the status."""
    try:
        contract = service.start(contract_id)
    except ContractNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found") from exc
    except InvalidStatusTransitionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Contract cannot be analyzed while {exc.current.value}"
        ) from exc
    background_tasks.add_task(runner, contract.id)
    return ContractRead.model_validate(contract)


@router.get("/{contract_id}/flags", response_model=list[RiskFlagRead])
def list_flags(
    contract_id: uuid.UUID,
    service: AnalysisService = Depends(get_analysis_service),
) -> list[RiskFlagRead]:
    try:
        flags = service.list_flags(contract_id)
    except ContractNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found") from exc
    return [RiskFlagRead.from_flag(flag) for flag in flags]
