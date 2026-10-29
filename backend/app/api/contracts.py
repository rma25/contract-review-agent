import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import get_contract_service
from app.schemas.contract import ContractRead
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
